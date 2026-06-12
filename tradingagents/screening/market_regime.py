"""
market_regime.py — Probabilistic market-regime prediction engine.

Replaces the coarse spy_regime string + 3-bucket size factor with a full
quantitative regime state that controls aggression, sizing, ML threshold,
and no-trade enforcement.

Key outputs (MarketRegimeState):
  regime              — primary label (bull/bear/crash_risk/high_vol_bull/…)
  regime_score        — [0,1] quality multiplier for candidate ranking
  crash_risk_score    — [0,1] tail-risk probability
  regime_confidence   — [0,1] how clearly defined the current regime is
  no_trade            — True in crash/extreme-vol conditions
  size_factor         — multiply base position size by this (0.0–1.0)
  ml_threshold        — minimum ML probability to accept trade
  stop_mult           — ATR stop distance multiplier (>1 = wider stops)
  tp_mult             — ATR take-profit multiplier
  max_open_trades     — cap on concurrent open positions
  features            — raw numeric dict for logging and ML feature additions

Usage:
    engine = MarketRegimeEngine()
    state  = engine.compute("2026-05-26")
    print(state.regime, state.no_trade, state.size_factor)

    # From pre-downloaded DataFrames (faster, avoids re-download):
    state = engine.compute_from_dataframes(spy_df, vix_df, vix3m_df, sector_dfs)
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Regime rules table ────────────────────────────────────────────────────────
# Each regime maps to a dict of trading parameters.
# size_factor: multiply base size (0 = no new trades, 1.0 = full)
# ml_delta:    add to base ML probability threshold (e.g. base=0.60 + delta=0.05 → 0.65)
# stop_mult:   multiply ATR stop distance (>1 = wider stop, more tolerance)
# tp_mult:     multiply ATR take-profit (>1 = wider target, hold longer)
# max_trades:  cap on concurrent open positions
_REGIME_RULES: Dict[str, Dict[str, Any]] = {
    "bull":           {"size_factor": 1.00, "ml_delta": 0.00, "stop_mult": 1.00, "tp_mult": 1.00, "max_trades": 8,  "no_trade": False},
    "uptrend":        {"size_factor": 0.90, "ml_delta": 0.00, "stop_mult": 1.00, "tp_mult": 1.00, "max_trades": 7,  "no_trade": False},
    "sideways":       {"size_factor": 0.75, "ml_delta": 0.03, "stop_mult": 1.00, "tp_mult": 0.90, "max_trades": 5,  "no_trade": False},
    "downtrend":      {"size_factor": 0.60, "ml_delta": 0.04, "stop_mult": 1.10, "tp_mult": 0.85, "max_trades": 4,  "no_trade": False},
    "bear":           {"size_factor": 0.50, "ml_delta": 0.05, "stop_mult": 1.15, "tp_mult": 0.80, "max_trades": 3,  "no_trade": False},
    "high_vol_bull":  {"size_factor": 0.65, "ml_delta": 0.03, "stop_mult": 1.20, "tp_mult": 0.90, "max_trades": 5,  "no_trade": False},
    "high_vol_bear":  {"size_factor": 0.30, "ml_delta": 0.08, "stop_mult": 1.25, "tp_mult": 0.75, "max_trades": 2,  "no_trade": False},
    "crash_risk":     {"size_factor": 0.00, "ml_delta": 0.99, "stop_mult": 1.50, "tp_mult": 0.70, "max_trades": 0,  "no_trade": True},
    "crash_rebound":  {"size_factor": 0.55, "ml_delta": 0.02, "stop_mult": 1.15, "tp_mult": 1.10, "max_trades": 4,  "no_trade": False},
    "unknown":        {"size_factor": 0.80, "ml_delta": 0.00, "stop_mult": 1.00, "tp_mult": 1.00, "max_trades": 6,  "no_trade": False},
}

# Regime score for candidate ranking (maps to [0.0,1.0])
REGIME_QUALITY_SCORE: Dict[str, float] = {
    "bull":          1.00,
    "uptrend":       1.00,
    "sideways":      0.75,
    "downtrend":     0.50,
    "bear":          0.50,
    "high_vol_bull": 0.65,
    "high_vol_bear": 0.30,
    "crash_risk":    0.05,
    "crash_rebound": 0.60,
    "unknown":       0.80,
}

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY",
               "XLP", "XLU", "XLB", "XLRE", "XLC"]


@dataclass
class MarketRegimeState:
    """Full market regime context for a single date.

    Use state.no_trade to gate new entries.
    Use state.size_factor to scale position sizes.
    Use state.ml_threshold (base + delta) as the ML gate threshold.
    Use state.regime_score in CandidateRanker.
    """
    # Primary label
    regime: str = "unknown"

    # Probabilistic scores (0–1 each; not required to sum to 1)
    prob_bull:     float = 0.0   # probability: bull/uptrend
    prob_bear:     float = 0.0   # probability: bear/downtrend
    prob_chop:     float = 0.0   # probability: sideways/chop
    prob_high_vol: float = 0.0   # probability: elevated volatility
    prob_crash:    float = 0.0   # probability: crash / tail event
    prob_rebound:  float = 0.0   # probability: post-crash rebound
    prob_risk_on:  float = 0.0   # risk-on environment
    prob_risk_off: float = 0.0   # risk-off environment

    # Aggregate quality scores
    regime_score:      float = 0.80  # [0,1] for CandidateRanker
    crash_risk_score:  float = 0.00  # [0,1] higher = more tail risk
    regime_confidence: float = 0.50  # [0,1] how clearly defined the regime is

    # Derived trading rules (computed from regime + raw scores)
    no_trade:         bool  = False
    size_factor:      float = 0.80
    ml_threshold:     float = 0.60  # absolute threshold (base + delta)
    stop_mult:        float = 1.00
    tp_mult:          float = 1.00
    max_open_trades:  int   = 6

    # Raw features (all numeric, for event log + ML feature additions)
    features: Dict[str, float] = field(default_factory=dict)

    # Metadata
    as_of_date: str = ""

    def to_log_dict(self) -> Dict[str, Any]:
        """Compact dict for event log inclusion with every trade."""
        return {
            "regime": self.regime,
            "regime_score": round(self.regime_score, 3),
            "crash_risk_score": round(self.crash_risk_score, 3),
            "regime_confidence": round(self.regime_confidence, 3),
            "no_trade": self.no_trade,
            "size_factor": round(self.size_factor, 3),
            "ml_threshold": round(self.ml_threshold, 3),
            "stop_mult": round(self.stop_mult, 3),
            "tp_mult": round(self.tp_mult, 3),
            "max_open_trades": self.max_open_trades,
            "prob_bull": round(self.prob_bull, 3),
            "prob_bear": round(self.prob_bear, 3),
            "prob_crash": round(self.prob_crash, 3),
            "prob_rebound": round(self.prob_rebound, 3),
            "prob_risk_on": round(self.prob_risk_on, 3),
            "prob_risk_off": round(self.prob_risk_off, 3),
            "as_of_date": self.as_of_date,
        }

    def summary_str(self) -> str:
        """One-line human summary for terminal logging."""
        nt = " [NO-TRADE]" if self.no_trade else ""
        return (
            f"Regime: {self.regime}{nt} | score={self.regime_score:.2f} "
            f"crash={self.crash_risk_score:.2f} conf={self.regime_confidence:.2f} "
            f"size={self.size_factor:.2f}× ml≥{self.ml_threshold:.2f}"
        )


class MarketRegimeEngine:
    """Computes probabilistic market regime state for a given date.

    Parameters
    ----------
    ml_base_threshold : float
        Base ML probability gate. Regime delta is added on top.
        E.g. base=0.60 + bear delta=0.05 → threshold=0.65.
    spy_ticker : str
        SPY ETF ticker for downloading market data.
    vix_ticker : str
        VIX ticker (default '^VIX').
    vix3m_ticker : str
        3-month VIX futures proxy (default '^VIX3M').
    download_period : str
        yfinance period string for downloading history (default '2y').
    """

    def __init__(
        self,
        ml_base_threshold: float = 0.60,
        spy_ticker: str = "SPY",
        vix_ticker: str = "^VIX",
        vix3m_ticker: str = "^VIX3M",
        download_period: str = "2y",
    ):
        self.ml_base_threshold = ml_base_threshold
        self.spy_ticker = spy_ticker
        self.vix_ticker = vix_ticker
        self.vix3m_ticker = vix3m_ticker
        self.download_period = download_period

    # ── Public API ────────────────────────────────────────────────────────────

    def compute(self, as_of_date: Optional[str] = None) -> MarketRegimeState:
        """Download latest market data and compute regime state.

        Parameters
        ----------
        as_of_date : str, optional
            ISO date string (e.g. '2026-05-26'). Defaults to today.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance required: pip install yfinance")

        if as_of_date is None:
            as_of_date = str(dt.date.today())

        tickers_to_dl = [self.spy_ticker, self.vix_ticker, self.vix3m_ticker] + SECTOR_ETFS
        try:
            raw = yf.download(
                tickers_to_dl,
                period=self.download_period,
                end=as_of_date,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.warning("MarketRegimeEngine: download failed (%s), returning unknown state", exc)
            return self._unknown_state(as_of_date)

        def extract_close(ticker: str) -> Optional[pd.Series]:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    return raw["Close"][ticker].dropna()
                else:
                    return raw["Close"].dropna()
            except Exception:
                return None

        spy_close  = extract_close(self.spy_ticker)
        vix_close  = extract_close(self.vix_ticker)
        vix3m_close = extract_close(self.vix3m_ticker)

        sector_closes: Dict[str, pd.Series] = {}
        for s in SECTOR_ETFS:
            c = extract_close(s)
            if c is not None and len(c) > 20:
                sector_closes[s] = c

        if spy_close is None or len(spy_close) < 200:
            logger.warning("MarketRegimeEngine: insufficient SPY history")
            return self._unknown_state(as_of_date)

        # Reconstruct minimal DataFrames expected by backtest helpers
        spy_df = pd.DataFrame({"Close": spy_close})
        vix_df = pd.DataFrame({"Close": vix_close}) if vix_close is not None else None
        vix3m_df = pd.DataFrame({"Close": vix3m_close}) if vix3m_close is not None else None
        sector_dfs = {s: pd.DataFrame({"Close": c}) for s, c in sector_closes.items()}

        return self.compute_from_dataframes(spy_df, vix_df, vix3m_df, sector_dfs, as_of_date)

    def compute_from_dataframes(
        self,
        spy_df: pd.DataFrame,
        vix_df: Optional[pd.DataFrame],
        vix3m_df: Optional[pd.DataFrame],
        sector_dfs: Optional[Dict[str, pd.DataFrame]],
        as_of_date: Optional[str] = None,
    ) -> MarketRegimeState:
        """Compute regime from pre-downloaded DataFrames (faster, backtest-compatible).

        DataFrames are truncated to rows at/before `as_of_date` so the state is
        point-in-time. Without this, a caller passing full-period frames (e.g.
        the backtest scanning historical dates) would silently compute every
        date's regime from the END of the data — a lookahead leak.
        """
        if as_of_date is None:
            as_of_date = str(dt.date.today())

        def _truncate(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
            if df is None or not isinstance(df.index, pd.DatetimeIndex):
                return df
            try:
                cutoff = pd.Timestamp(as_of_date)
                if df.index.tz is not None:
                    cutoff = cutoff.tz_localize(df.index.tz)
                return df.loc[df.index <= cutoff]
            except Exception:
                return df

        spy_df = _truncate(spy_df)
        vix_df = _truncate(vix_df)
        vix3m_df = _truncate(vix3m_df)
        if sector_dfs:
            sector_dfs = {s: _truncate(df) for s, df in sector_dfs.items()}

        features = self._compute_features(spy_df, vix_df, vix3m_df, sector_dfs)
        return self._classify(features, as_of_date)

    # ── Feature extraction ────────────────────────────────────────────────────

    def _compute_features(
        self,
        spy_df: pd.DataFrame,
        vix_df: Optional[pd.DataFrame],
        vix3m_df: Optional[pd.DataFrame],
        sector_dfs: Optional[Dict[str, pd.DataFrame]],
    ) -> Dict[str, float]:
        """Compute all raw regime features from DataFrames.

        All features use only data at/before the last row — no lookahead.
        """
        feats: Dict[str, float] = {}
        spy = spy_df["Close"].dropna()

        if len(spy) < 50:
            return feats

        # ── SPY trend features ────────────────────────────────────────────
        sma50  = spy.rolling(50).mean()
        sma200 = spy.rolling(200).mean()
        price  = float(spy.iloc[-1])

        feats["spy_close"]          = round(price, 2)
        feats["spy_sma50"]          = round(float(sma50.iloc[-1]), 2) if pd.notna(sma50.iloc[-1]) else 0.0
        feats["spy_sma200"]         = round(float(sma200.iloc[-1]), 2) if pd.notna(sma200.iloc[-1]) else 0.0
        feats["spy_above_sma50"]    = 1.0 if (pd.notna(sma50.iloc[-1]) and price > float(sma50.iloc[-1])) else 0.0
        feats["spy_above_sma200"]   = 1.0 if (pd.notna(sma200.iloc[-1]) and price > float(sma200.iloc[-1])) else 0.0

        if pd.notna(sma50.iloc[-1]) and pd.notna(sma200.iloc[-1]):
            feats["spy_golden_cross"] = 1.0 if float(sma50.iloc[-1]) > float(sma200.iloc[-1]) else 0.0
        else:
            feats["spy_golden_cross"] = 0.0

        # SPY returns
        feats["spy_ret5"]  = round(float(spy.pct_change(5).iloc[-1]),  5) if len(spy) > 5  else 0.0
        feats["spy_ret20"] = round(float(spy.pct_change(20).iloc[-1]), 5) if len(spy) > 20 else 0.0
        feats["spy_ret60"] = round(float(spy.pct_change(60).iloc[-1]), 5) if len(spy) > 60 else 0.0

        # Drawdown from recent highs
        high_20d = float(spy.rolling(20).max().iloc[-1]) if len(spy) >= 20 else price
        high_252d = float(spy.rolling(252).max().iloc[-1]) if len(spy) >= 252 else price
        low_20d   = float(spy.rolling(20).min().iloc[-1]) if len(spy) >= 20 else price

        feats["spy_drawdown_20d"]     = round((price - high_20d) / high_20d, 4) if high_20d > 0 else 0.0
        feats["spy_drawdown_from_ath"] = round((price - high_252d) / high_252d, 4) if high_252d > 0 else 0.0
        feats["spy_rebound_pct"]      = round((price - low_20d) / low_20d, 4) if low_20d > 0 else 0.0

        # SPY 1d gap (today's open vs yesterday's close)
        if "Open" in spy_df.columns:
            o = spy_df["Open"].dropna()
            c = spy_df["Close"].dropna()
            if len(o) >= 2 and len(c) >= 2:
                feats["spy_gap_pct"] = round(float(o.iloc[-1]) / float(c.iloc[-2]) - 1.0, 4)
            else:
                feats["spy_gap_pct"] = 0.0
        else:
            feats["spy_gap_pct"] = 0.0

        # ── VIX features ─────────────────────────────────────────────────
        vix_val = None
        if vix_df is not None and len(vix_df) > 20:
            vix = vix_df["Close"].dropna()
            if len(vix) > 20:
                vix_val = float(vix.iloc[-1])
                vix_prev = float(vix.iloc[-2]) if len(vix) >= 2 else vix_val
                vix_5d_ago = float(vix.iloc[-6]) if len(vix) >= 6 else vix_prev

                vix_mean20 = float(vix.rolling(20).mean().iloc[-1])
                vix_std20  = float(vix.rolling(20).std().iloc[-1])

                feats["vix_level"]    = round(vix_val, 2)
                feats["vix_1d_chg"]   = round((vix_val - vix_prev) / max(vix_prev, 1.0), 4)
                feats["vix_5d_chg"]   = round((vix_val - vix_5d_ago) / max(vix_5d_ago, 1.0), 4)
                feats["vix_20d_zscore"] = round((vix_val - vix_mean20) / max(vix_std20, 0.01), 3)
                feats["vol_expansion"]   = 1.0 if feats["vix_20d_zscore"] > 1.5 else 0.0
                feats["vol_compression"] = 1.0 if feats["vix_20d_zscore"] < -1.0 else 0.0

                # VIX term structure
                if vix3m_df is not None and len(vix3m_df) > 0:
                    vix3m = vix3m_df["Close"].dropna()
                    if len(vix3m) > 0 and vix_val > 0:
                        feats["vix_ts"] = round(float(vix3m.iloc[-1]) / vix_val, 4)
                    else:
                        feats["vix_ts"] = 1.0
                else:
                    feats["vix_ts"] = 1.0

                # Crash recovery: VIX dropped >20% from 10d max
                vix_max10 = float(vix.rolling(10).max().iloc[-1]) if len(vix) >= 10 else vix_val
                feats["crash_recovery_score"] = (
                    1.0 if (vix_max10 > 35 and vix_val < vix_max10 * 0.80 and vix_val <= 35) else 0.0
                )
        else:
            feats["vix_level"]          = 20.0  # neutral default
            feats["vix_1d_chg"]         = 0.0
            feats["vix_5d_chg"]         = 0.0
            feats["vix_20d_zscore"]     = 0.0
            feats["vol_expansion"]      = 0.0
            feats["vol_compression"]    = 0.0
            feats["vix_ts"]             = 1.0
            feats["crash_recovery_score"] = 0.0

        # ── Sector breadth ────────────────────────────────────────────────
        if sector_dfs:
            closes = {}
            for s, df in sector_dfs.items():
                if "Close" in df.columns:
                    c = df["Close"].dropna()
                    if len(c) > 20:
                        closes[s] = c

            if closes:
                all_c = pd.DataFrame(closes).ffill()
                ret20 = all_c.pct_change(20)
                last  = ret20.iloc[-1]
                valid = last.notna().sum()
                feats["sector_breadth"] = round(float((last > 0).sum()) / max(valid, 1), 3)

                # Hidden weakness: breadth weak while SPY above SMA200
                feats["spy_breadth_diverge"] = (
                    1.0 if (feats["sector_breadth"] < 0.45 and feats["spy_above_sma200"] > 0.5) else 0.0
                )
            else:
                feats["sector_breadth"] = 0.55  # neutral default
                feats["spy_breadth_diverge"] = 0.0
        else:
            feats["sector_breadth"] = 0.55
            feats["spy_breadth_diverge"] = 0.0

        # ── Risk-on / risk-off ────────────────────────────────────────────
        vol_exp = feats.get("vol_expansion", 0.0)
        breadth = feats.get("sector_breadth", 0.55)
        abv200  = feats.get("spy_above_sma200", 0.0)

        feats["risk_on_score"]  = round(float(breadth * abv200 * (1.0 - vol_exp)), 3)
        feats["risk_off_score"] = round(float((1.0 - breadth) * (1.0 + vol_exp) * 0.5), 3)

        return feats

    # ── Regime classification ─────────────────────────────────────────────────

    def _classify(self, f: Dict[str, float], as_of_date: str) -> MarketRegimeState:
        """Translate raw features into a MarketRegimeState."""

        vix = f.get("vix_level", 20.0)
        vix_ts = f.get("vix_ts", 1.0)
        abv200 = f.get("spy_above_sma200", 0.0)
        abv50  = f.get("spy_above_sma50", 0.0)
        golden = f.get("spy_golden_cross", 0.0)
        ret5   = f.get("spy_ret5", 0.0)
        ret20  = f.get("spy_ret20", 0.0)
        ret60  = f.get("spy_ret60", 0.0)
        dd_20  = f.get("spy_drawdown_20d", 0.0)
        dd_ath = f.get("spy_drawdown_from_ath", 0.0)
        breadth = f.get("sector_breadth", 0.55)
        vol_exp = f.get("vol_expansion", 0.0)
        vol_cmp = f.get("vol_compression", 0.0)
        recovery = f.get("crash_recovery_score", 0.0)
        vix_zscore = f.get("vix_20d_zscore", 0.0)
        risk_on  = f.get("risk_on_score", 0.5)
        risk_off = f.get("risk_off_score", 0.2)

        # ── Determine primary label ───────────────────────────────────────
        # Mirrors build_combined_regime() logic with added nuance
        if vix > 35 and abv200 < 0.5:
            regime = "crash_risk"
        elif recovery > 0.5 and abv200 > 0.0:
            regime = "crash_rebound"
        elif vix > 25 and abv200 < 0.5:
            regime = "high_vol_bear"
        elif vix > 25 and abv200 > 0.5:
            regime = "high_vol_bull"
        elif abv200 > 0.5 and golden > 0.5:
            regime = "bull"
        elif abv200 > 0.5 and abv50 > 0.5:
            regime = "uptrend"
        elif abv200 > 0.5:
            # At or slightly above SMA200 — could be sideways
            if dd_20 < -0.03 and ret20 < 0:
                regime = "sideways"
            else:
                regime = "uptrend"
        elif abv200 < 0.5 and abs(f.get("spy_close", 0) - f.get("spy_sma200", f.get("spy_close", 0))) / max(f.get("spy_sma200", 1.0), 1.0) < 0.05:
            regime = "sideways"
        elif abv200 < 0.5 and f.get("spy_close", 0) < f.get("spy_sma200", f.get("spy_close", 0)) * 0.95:
            regime = "bear"
        else:
            regime = "downtrend"

        # ── Probabilistic scores ──────────────────────────────────────────

        # Bull probability: above both SMAs, golden cross, positive returns, breadth
        prob_bull = float(np.clip(
            0.20 * abv200
            + 0.20 * abv50
            + 0.15 * golden
            + 0.15 * float(ret20 > 0.02)
            + 0.15 * float(ret60 > 0.05)
            + 0.15 * float(breadth > 0.65)
            - 0.20 * vol_exp
            - 0.15 * float(vix > 20),
            0.0, 1.0,
        ))

        # Bear probability: below SMA200, negative returns, low breadth, high vol
        prob_bear = float(np.clip(
            0.25 * float(abv200 < 0.5)
            + 0.20 * float(ret20 < -0.03)
            + 0.20 * float(ret60 < -0.08)
            + 0.15 * float(breadth < 0.35)
            + 0.15 * float(vix > 22)
            + 0.10 * float(vix_ts < 0.95),
            0.0, 1.0,
        ))

        # Chop probability: near SMA200, mixed returns, middling breadth
        prob_chop = float(np.clip(
            0.30 * float(abs(dd_ath) < 0.05 and abs(ret20) < 0.03)
            + 0.20 * float(0.40 < breadth < 0.65)
            + 0.20 * float(vol_cmp > 0.5)
            + 0.15 * float(abs(ret5) < 0.01)
            + 0.15 * float(15 < vix < 22),
            0.0, 1.0,
        ))

        # High-vol probability: VIX elevated/expanding
        prob_high_vol = float(np.clip(
            0.35 * float(vix > 25)
            + 0.25 * vol_exp
            + 0.20 * float(vix_zscore > 1.0)
            + 0.20 * float(vix > 20 and vix_ts < 1.0),
            0.0, 1.0,
        ))

        # Crash probability: VIX crisis + bear + backwardation + breadth collapse
        prob_crash = float(np.clip(
            0.35 * float(vix > 30)
            + 0.25 * float(vix_ts < 0.90)
            + 0.20 * float(ret20 < -0.07)
            + 0.20 * float(breadth < 0.20),
            0.0, 1.0,
        ))

        # Rebound probability: was in crash, now recovering
        prob_rebound = float(np.clip(
            recovery
            + 0.3 * float(ret5 > 0.03)
            + 0.2 * float(vix > 30 and f.get("vix_5d_chg", 0.0) < -0.05),
            0.0, 1.0,
        ))

        # Risk-on / risk-off
        prob_risk_on  = float(np.clip(risk_on, 0.0, 1.0))
        prob_risk_off = float(np.clip(risk_off, 0.0, 1.0))

        # ── crash_risk_score ─────────────────────────────────────────────
        # Composite of crash probability + VIX crisis + backwardation
        crash_risk_score = float(np.clip(
            0.40 * prob_crash
            + 0.25 * float(vix > 35)
            + 0.20 * float(vix_ts < 0.90)
            + 0.15 * float(prob_bear > 0.6),
            0.0, 1.0,
        ))

        # ── regime_confidence ────────────────────────────────────────────
        # How unambiguous is the signal? High confidence = strong bull or strong crash.
        # Low confidence = sideways, mixed signals.
        # MR-3: max_prob was computed but unused (prob_sorted already computes the maximum).
        # Removed dead variable. Also ensure prob_rebound is included in prob_sorted (was
        # inconsistently included in max_prob but not the full probability set — use one list).
        prob_sorted = sorted(
            [prob_bull, prob_bear, prob_chop, prob_high_vol, prob_crash, prob_rebound,
             prob_risk_on, prob_risk_off],
            reverse=True,
        )
        # Confidence: gap between top and second-highest probability
        gap = prob_sorted[0] - prob_sorted[1] if len(prob_sorted) >= 2 else prob_sorted[0]
        regime_confidence = float(np.clip(0.5 + gap * 2.0, 0.05, 1.0))

        # ── regime_score ─────────────────────────────────────────────────
        regime_score = REGIME_QUALITY_SCORE.get(regime, 0.80)

        # Soft-adjust regime_score by probabilities for more nuance within a label
        # e.g. "bull" with crash_risk_score=0.30 gets slight quality penalty
        regime_score_adj = float(np.clip(
            regime_score
            - 0.20 * crash_risk_score   # crash risk reduces quality
            - 0.10 * prob_risk_off      # risk-off reduces quality
            + 0.05 * float(prob_bull > 0.70 and crash_risk_score < 0.10),  # very clean bull bonus
            0.05, 1.0,
        ))

        # ── Regime rules ─────────────────────────────────────────────────
        rules = _REGIME_RULES.get(regime, _REGIME_RULES["unknown"])
        no_trade    = bool(rules["no_trade"])
        size_factor = float(rules["size_factor"])
        ml_delta    = float(rules["ml_delta"])
        stop_mult   = float(rules["stop_mult"])
        tp_mult     = float(rules["tp_mult"])
        max_trades  = int(rules["max_trades"])
        ml_threshold = min(0.95, self.ml_base_threshold + ml_delta)

        # MR-1: continuous taper — replace hard binary no_trade cliff with bounded ramp.
        # Previously: crash_risk_score > 0.70 flipped the whole book off (hard cliff).
        # Now: smooth linear taper from score=0.40 (begin penalty) to score=0.85 (force stop).
        # Hard no_trade only fires at ≥0.85 (genuine extreme; label="crash_risk" still fires too).
        # At [0.40, 0.85]: size_factor tapers linearly toward 0; system keeps exiting positions
        # but progressively shrinks new entry size rather than stopping all at once.
        _TAPER_LO = 0.40   # taper begins here
        _TAPER_HI = 0.85   # hard no_trade threshold (raised from 0.70)
        if crash_risk_score >= _TAPER_HI and not no_trade:
            no_trade     = True
            size_factor  = 0.0
            ml_threshold = 0.95
            max_trades   = 0
        elif not no_trade and crash_risk_score > _TAPER_LO:
            # Linear ramp: at score=_TAPER_LO factor=1.0; at score=_TAPER_HI factor=0.0
            taper = 1.0 - (crash_risk_score - _TAPER_LO) / (_TAPER_HI - _TAPER_LO)
            size_factor = float(size_factor * max(0.0, taper))

        return MarketRegimeState(
            regime=regime,
            prob_bull=round(prob_bull, 3),
            prob_bear=round(prob_bear, 3),
            prob_chop=round(prob_chop, 3),
            prob_high_vol=round(prob_high_vol, 3),
            prob_crash=round(prob_crash, 3),
            prob_rebound=round(prob_rebound, 3),
            prob_risk_on=round(prob_risk_on, 3),
            prob_risk_off=round(prob_risk_off, 3),
            regime_score=round(regime_score_adj, 4),
            crash_risk_score=round(crash_risk_score, 4),
            regime_confidence=round(regime_confidence, 4),
            no_trade=no_trade,
            size_factor=round(size_factor, 4),
            ml_threshold=round(ml_threshold, 4),
            stop_mult=round(stop_mult, 4),
            tp_mult=round(tp_mult, 4),
            max_open_trades=max_trades,
            features={k: (round(v, 6) if isinstance(v, float) else v) for k, v in f.items()},
            as_of_date=as_of_date,
        )

    def _unknown_state(self, as_of_date: str) -> MarketRegimeState:
        """Return safe unknown state when data download fails or SPY history insufficient.

        MR-6: previously returned size_factor=0.80 and no_trade=False — a data-truncation
        or download failure silently dropped into 0.8× size instead of halting. A system that
        can't determine the regime must not trade.
        """
        return MarketRegimeState(
            regime="unknown",
            prob_bull=0.0, prob_bear=0.0, prob_chop=0.0,
            prob_high_vol=0.0, prob_crash=0.0, prob_rebound=0.0,
            prob_risk_on=0.5, prob_risk_off=0.5,
            regime_score=0.0,
            crash_risk_score=0.0,
            regime_confidence=0.0,
            no_trade=True,      # MR-6: halt on unknown regime (was False → 0.8× size)
            size_factor=0.0,    # MR-6: no position sizing when regime is unknown
            ml_threshold=0.95,  # MR-6: effectively blocks all signals
            stop_mult=1.0,
            tp_mult=1.0,
            max_open_trades=0,  # MR-6: block new entries
            features={},
            as_of_date=as_of_date,
        )

    # ── Per-regime performance validation ────────────────────────────────────

    @staticmethod
    def per_regime_validation(
        trades_df: "pd.DataFrame",  # noqa: F821
        hold: int,
    ) -> Dict[str, Dict]:
        """Compute per-regime performance metrics from a backtest trades DataFrame.

        Metrics: n_trades, win_rate, avg_return, max_drawdown, roc_auc, brier_score.

        Parameters
        ----------
        trades_df : DataFrame
            Backtest or paper-trade log with columns:
              spy_regime, h{hold}_return, h{hold}_outcome, [ml_probability]
        hold : int
            Hold period matching the return column.

        Returns
        -------
        dict mapping regime_label → metrics dict
        """
        ret_col = f"h{hold}_return"
        if ret_col not in trades_df.columns:
            return {}

        df = trades_df.copy()
        df["_return"] = pd.to_numeric(df[ret_col], errors="coerce")
        df = df.dropna(subset=["spy_regime", "_return"])

        results: Dict[str, Dict] = {}
        for regime_label, grp in df.groupby("spy_regime"):
            rets = grp["_return"]
            n = len(rets)
            if n < 5:
                continue

            wins = (rets > 0.005).sum()
            win_rate = float(wins / n)
            avg_ret  = float(rets.mean())
            # Max drawdown on cumulative product
            cum = (1 + rets).cumprod()
            rolling_max = cum.cummax()
            dd = ((rolling_max - cum) / rolling_max.replace(0, float("nan")))
            max_dd = float(dd.max()) if len(dd) > 0 else 0.0

            entry = {
                "n_trades":     n,
                "win_rate":     round(win_rate, 4),
                "avg_return":   round(avg_ret, 5),
                "max_drawdown": round(max_dd, 4),
            }

            # ROC AUC and Brier if ML probabilities are available
            if "ml_probability" in grp.columns:
                probs = pd.to_numeric(grp["ml_probability"], errors="coerce").fillna(0.5)
                labels = (rets > 0.005).astype(int)
                try:
                    from sklearn.metrics import roc_auc_score, brier_score_loss
                    if labels.nunique() >= 2:
                        entry["roc_auc"]     = round(float(roc_auc_score(labels, probs)), 4)
                        entry["brier_score"] = round(float(brier_score_loss(labels, probs)), 4)
                except Exception:
                    pass

            results[str(regime_label)] = entry

        return results


# ── Convenience function ─────────────────────────────────────────────────────

def get_market_regime_state(
    as_of_date: Optional[str] = None,
    ml_base_threshold: float = 0.60,
) -> MarketRegimeState:
    """One-call convenience function: download data and return MarketRegimeState."""
    engine = MarketRegimeEngine(ml_base_threshold=ml_base_threshold)
    return engine.compute(as_of_date)
