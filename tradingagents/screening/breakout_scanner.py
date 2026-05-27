"""Breakout Detection & Big-Move Prediction Scanner.

Finds stocks that are coiling (volatility compression) and showing early
signs of a breakout: volume expansion, resistance tests, trend alignment,
and relative strength vs SPY.

Design principles:
  - All features use only data available at scan time (no look-ahead)
  - Failed breakouts are as important as winners — penalise rejection signals
  - Hard liquidity gates ($5-500 price, >$5M ADV, ATR% < 8%)
  - Outputs ML-ready feature dict alongside the human-readable score

Scoring breakdown (max 100 pts):
  Compression    25 pts  — range contraction + volume dry-up (coiling)
  Confirmation   25 pts  — price at resistance + RSI zone + low wick
  Trend          25 pts  — above 20/50/200-day SMA
  Volume         25 pts  — today's volume vs 20-day baseline

Usage:
    from tradingagents.screening.breakout_scanner import BreakoutScanner
    scanner = BreakoutScanner(threshold=70.0)
    results = scanner.scan_batch(["AAPL", "MSFT", "NVDA"], as_of_date="2026-05-23")
    for r in results:
        if r.passed:
            print(r.ticker, r.score, r.breakout_type, r.report_dict())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Breakout type labels ──────────────────────────────────────────────────────
BREAKOUT_TYPES = (
    "range_breakout",           # near 20d/50d high + rising vol + tight prior range
    "volume_breakout",          # vol_surge > 2× + significant price move
    "gap_continuation",         # gap up > 1% + above all SMAs + vol confirm
    "trend_continuation",       # all SMAs aligned + moderate breakout + RS positive
    "failed_breakout_risk",     # prior rejection pattern + high upper wick
    "consolidation_setup",      # squeeze active but no breakout yet (watch list)
    "unknown",
)


@dataclass
class BreakoutComponents:
    """Decomposed score components for transparency."""
    compression_pts: float = 0.0   # 0-25: range + vol dry-up
    confirmation_pts: float = 0.0  # 0-25: at resistance + RSI + low wick
    trend_pts: float = 0.0         # 0-25: SMA alignment
    volume_pts: float = 0.0        # 0-25: today's vol vs baseline

    @property
    def total(self) -> float:
        return self.compression_pts + self.confirmation_pts + self.trend_pts + self.volume_pts


@dataclass
class BreakoutResult:
    """Full result for one breakout candidate."""
    ticker: str
    score: float                               # 0–100
    passed: bool
    scan_date: Optional[str] = None            # ISO date string of scan
    breakout_type: str = "unknown"
    confidence: str = "low"                    # low / medium / high
    # Price levels
    entry: Optional[float] = None
    stop: Optional[float] = None
    take_profit: Optional[float] = None
    invalidation_level: Optional[float] = None
    risk_reward: Optional[float] = None
    # ML probability outputs (populated by ML enrichment step)
    breakout_success_probability: Optional[float] = None
    failed_breakout_probability: Optional[float] = None
    large_loss_probability: Optional[float] = None
    expected_move_3d: Optional[float] = None
    expected_move_5d: Optional[float] = None
    expected_move_10d: Optional[float] = None
    # ML feature dict (raw numeric values for model input)
    features: Dict[str, Any] = field(default_factory=dict)
    # Decomposed score
    score_components: Optional[BreakoutComponents] = None
    # Alias for backward compat
    components: Optional[BreakoutComponents] = None
    # Human-readable
    signal_reasons: List[str] = field(default_factory=list)
    warning_flags: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def report_dict(self) -> Dict[str, Any]:
        """Return JSON-serialisable report dict for logging / dashboard."""
        sc = self.score_components or self.components
        return {
            "ticker": self.ticker,
            "scan_date": self.scan_date,
            "breakout_score": self.score,
            "breakout_type": self.breakout_type,
            "confidence": self.confidence,
            "passed": self.passed,
            "entry": self.entry,
            "stop": self.stop,
            "take_profit": self.take_profit,
            "invalidation_level": self.invalidation_level,
            "risk_reward": self.risk_reward,
            "breakout_success_probability": self.breakout_success_probability,
            "failed_breakout_probability": self.failed_breakout_probability,
            "large_loss_probability": self.large_loss_probability,
            "expected_move_5d": self.expected_move_5d,
            "expected_move_10d": self.expected_move_10d,
            "score_components": {
                "compression_pts": sc.compression_pts if sc else None,
                "confirmation_pts": sc.confirmation_pts if sc else None,
                "trend_pts": sc.trend_pts if sc else None,
                "volume_pts": sc.volume_pts if sc else None,
            },
            "signal_reasons": self.signal_reasons,
            "warning_flags": self.warning_flags,
            "error": self.error,
        }


class BreakoutScanner:
    """Detects breakout candidates with rule-based scoring + ML feature extraction.

    Parameters
    ----------
    threshold:
        Minimum breakout_score for a candidate to pass (default 68).
    min_price:
        Minimum stock price in dollars (default 5.0).
    max_price:
        Maximum stock price in dollars (default 600.0).
    min_adv_m:
        Minimum Average Daily Value traded in millions (default 5.0).
    max_atr_pct:
        Maximum ATR % of price — filter out highly volatile penny stocks (default 0.08).
    atr_target_mult:
        Target = entry + atr_target_mult × ATR (default 1.5).
    atr_stop_mult:
        Stop = entry − atr_stop_mult × ATR (default 0.75).
    """

    def __init__(
        self,
        threshold: float = 68.0,
        min_price: float = 5.0,
        max_price: float = 600.0,
        min_adv_m: float = 5.0,
        max_atr_pct: float = 0.08,
        atr_target_mult: float = 1.5,
        atr_stop_mult: float = 0.75,
    ):
        self.threshold = threshold
        self.min_price = min_price
        self.max_price = max_price
        self.min_adv_m = min_adv_m * 1_000_000
        self.max_atr_pct = max_atr_pct
        self.atr_target_mult = atr_target_mult
        self.atr_stop_mult = atr_stop_mult

    # ── Public API ────────────────────────────────────────────────────────────

    def scan_batch(
        self,
        tickers: List[str],
        as_of_date: str,
        spy_ticker: str = "SPY",
    ) -> List[BreakoutResult]:
        """Download 1y of data for all tickers + SPY and score each.

        Returns list sorted by score descending.
        Tickers with insufficient history or errors are returned with passed=False.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance required: pip install yfinance")

        tickers_upper = list(dict.fromkeys(t.upper() for t in tickers))
        all_tickers = list(dict.fromkeys(tickers_upper + [spy_ticker]))

        try:
            raw = yf.download(
                all_tickers,
                period="2y",   # 2y for 52w high + enough history for features
                end=as_of_date,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.error("Batch download failed: %s", exc)
            return [
                BreakoutResult(t, 0.0, False, error=f"Download failed: {exc}")
                for t in tickers_upper
            ]

        close  = self._extract(raw, all_tickers, "Close")
        high   = self._extract(raw, all_tickers, "High")
        low    = self._extract(raw, all_tickers, "Low")
        volume = self._extract(raw, all_tickers, "Volume")
        spy_c  = close.get(spy_ticker, pd.Series(dtype=float)).dropna()

        results: List[BreakoutResult] = []
        for ticker in tickers_upper:
            c = close.get(ticker, pd.Series(dtype=float)).dropna()
            h = high.get(ticker, pd.Series(dtype=float)).dropna()
            lo = low.get(ticker, pd.Series(dtype=float)).dropna()
            v  = volume.get(ticker, pd.Series(dtype=float)).dropna()
            if len(c) < 60:
                results.append(BreakoutResult(
                    ticker, 0.0, False,
                    error="Insufficient price history (< 60 days)",
                ))
                continue
            try:
                result = self.score_one(ticker, c, h, lo, v, spy_c)
                result.scan_date = as_of_date
                results.append(result)
            except Exception as exc:
                logger.warning("Breakout scoring failed for %s: %s", ticker, exc)
                results.append(BreakoutResult(ticker, 0.0, False,
                                              scan_date=as_of_date, error=str(exc)))

        return sorted(results, key=lambda r: r.score, reverse=True)

    def score_one(
        self,
        ticker: str,
        closes: pd.Series,
        highs: pd.Series,
        lows: pd.Series,
        volume: pd.Series,
        spy_closes: pd.Series,
    ) -> BreakoutResult:
        """Score a single ticker. Returns BreakoutResult with full feature dict.

        Leakage guarantee: uses only data available at the LAST bar of each series.
        No forward-looking references. The caller must ensure the series ends at
        the scan date and does NOT include future bars.
        """
        price = float(closes.iloc[-1])
        comp = BreakoutComponents()
        reasons: List[str] = []
        warnings: List[str] = []
        features: Dict[str, Any] = {}

        # ── Hard liquidity gates ───────────────────────────────────────────
        atr = self._atr(closes, highs, lows)
        atr_pct = atr / price if price > 0 else 1.0
        vol_20d = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.mean())
        adv = price * vol_20d

        features["price"] = round(price, 2)
        features["atr"] = round(atr, 3)
        features["atr_pct"] = round(atr_pct, 4)
        features["dollar_vol_20d"] = round(adv, 0)

        if price < self.min_price or price > self.max_price:
            return BreakoutResult(ticker, 0.0, False,
                                  warning_flags=[f"price={price:.2f} out of range"],
                                  features=features)
        if adv < self.min_adv_m:
            return BreakoutResult(ticker, 0.0, False,
                                  warning_flags=[f"ADV=${adv/1e6:.1f}M < {self.min_adv_m/1e6:.0f}M"],
                                  features=features)
        if atr_pct > self.max_atr_pct:
            return BreakoutResult(ticker, 0.0, False,
                                  warning_flags=[f"ATR%={atr_pct:.2%} > {self.max_atr_pct:.0%}"],
                                  features=features)

        # ── Precompute rolling series ──────────────────────────────────────
        sma20  = _safe_sma(closes, 20)
        sma50  = _safe_sma(closes, 50)
        sma200 = _safe_sma(closes, 200)
        ema9   = float(closes.ewm(span=9,  adjust=False).mean().iloc[-1])

        high_10d  = float(highs.iloc[-11:-1].max())   if len(highs) >= 11  else price
        high_20d  = float(highs.iloc[-21:-1].max())   if len(highs) >= 21  else price
        high_50d  = float(highs.iloc[-51:-1].max())   if len(highs) >= 51  else price
        high_52w  = float(highs.iloc[-253:-1].max())  if len(highs) >= 253 else float(highs.max())
        low_52w   = float(lows.iloc[-253:-1].min())   if len(lows)  >= 253 else float(lows.min())

        bb_mid, bb_upper, bb_lower = _bb(closes, 20)
        kelt_upper, kelt_lower = _keltner(closes, highs, lows, 20)
        rsi14 = _rsi(closes, 14)
        rsi9  = _rsi(closes, 9)
        rsi14_3d_ago = _rsi(closes.iloc[:-3], 14) if len(closes) > 17 else rsi14
        macd_h = _macd_hist(closes)
        macd_h_3d = _macd_hist(closes.iloc[:-3]) if len(closes) > 30 else macd_h

        vol_today   = float(volume.iloc[-1])
        vol_3d_avg  = float(volume.iloc[-4:-1].mean()) if len(volume) >= 4 else vol_20d
        vol_5d_avg  = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else vol_20d
        vol_surge_1d = vol_today / vol_20d if vol_20d > 0 else 1.0
        vol_surge_3d = vol_3d_avg / vol_20d if vol_20d > 0 else 1.0
        vol_dryup_5d = vol_5d_avg / vol_20d if vol_20d > 0 else 1.0

        range_5d  = float(highs.iloc[-6:-1].max() - lows.iloc[-6:-1].min()) if len(highs) >= 6 else atr
        range_20d = float(highs.iloc[-21:-1].max() - lows.iloc[-21:-1].min()) if len(highs) >= 21 else atr * 5
        range_contraction = range_5d / range_20d if range_20d > 0 else 1.0

        atr_5d = _atr(closes.iloc[-6:], highs.iloc[-6:], lows.iloc[-6:]) if len(closes) >= 6 else atr
        atr_20d_avg = _atr(closes.iloc[-21:], highs.iloc[-21:], lows.iloc[-21:]) if len(closes) >= 21 else atr
        atr_compression = atr_5d / atr_20d_avg if atr_20d_avg > 0 else 1.0
        atr_expansion_ratio = atr / atr_20d_avg if atr_20d_avg > 0 else 1.0

        bb_width = float(bb_upper - bb_lower) / float(bb_mid) if bb_mid and bb_mid > 0 else 0.0
        keltner_squeeze = int(bb_upper < kelt_upper and bb_lower > kelt_lower)

        # Candlestick analysis for today
        day_high = float(highs.iloc[-1])
        day_low  = float(lows.iloc[-1])
        day_range = day_high - day_low
        upper_wick = (day_high - price) / day_range if day_range > 0 else 0.0
        close_loc  = (price - day_low) / day_range if day_range > 0 else 0.5
        ret_1d = float(closes.pct_change(1).iloc[-1]) if len(closes) > 1 else 0.0
        ret_3d = float(closes.pct_change(3).iloc[-1]) if len(closes) > 3 else 0.0
        ret_5d = float(closes.pct_change(5).iloc[-1]) if len(closes) > 5 else 0.0
        ret_20d = float(closes.pct_change(20).iloc[-1]) if len(closes) > 20 else 0.0

        # OBV slope
        obv = _obv(closes, volume)
        obv_5d_slope = float(obv.diff(5).iloc[-1]) if len(obv) >= 5 else 0.0
        obv_slope_5d_norm = obv_5d_slope / (vol_20d * price) if vol_20d > 0 and price > 0 else 0.0

        # Relative strength vs SPY
        rel_strength_20d, rel_strength_5d = _relative_strength(closes, spy_closes)
        rs_improving = int(rel_strength_5d > rel_strength_20d)

        # Prior failed breakout detection: prior vol surge that returned to base
        prev_breakout_failed = self._detect_prior_failure(closes, highs, lows, volume, vol_20d)
        # Consecutive failed pushes (price made new N-bar high but closed lower)
        consec_failed_highs = self._count_failed_highs(closes, highs, lows)

        # Price proximity to key levels
        pct_from_20d_high  = (price - high_20d)  / high_20d  if high_20d  > 0 else 0.0
        pct_from_50d_high  = (price - high_50d)  / high_50d  if high_50d  > 0 else 0.0
        pct_from_52w_high  = (price - high_52w)  / high_52w  if high_52w  > 0 else 0.0
        pct_from_52w_low   = (price - low_52w)   / low_52w   if low_52w   > 0 else 0.0
        price_vs_ema9      = (price - ema9)       / atr       if atr > 0 else 0.0
        sma50_slope_5d     = (sma50 - _safe_sma(closes.iloc[:-5], 50)) / atr if atr > 0 else 0.0

        above_sma20  = int(price > sma20) if sma20 is not None else 0
        above_sma50  = int(price > sma50) if sma50 is not None else 0
        above_sma200 = int(price > sma200) if sma200 is not None else 0
        sma_alignment = int(
            sma20 is not None and sma50 is not None and sma200 is not None
            and sma20 > sma50 > sma200
        )

        # ── Store all features ─────────────────────────────────────────────
        features.update({
            # Compression
            "range_contraction_5_20": round(range_contraction, 4),
            "atr_compression":        round(atr_compression, 4),
            "atr_expansion":          round(atr_expansion_ratio, 4),
            "bb_width":               round(bb_width, 4),
            "keltner_squeeze":        keltner_squeeze,
            "vol_dryup_5d":           round(vol_dryup_5d, 4),
            # Confirmation
            "pct_from_10d_high":      round((price - high_10d) / high_10d, 4) if high_10d > 0 else None,
            "pct_from_20d_high":      round(pct_from_20d_high, 4),
            "pct_from_50d_high":      round(pct_from_50d_high, 4),
            "pct_from_52w_high":      round(pct_from_52w_high, 4),
            "pct_from_52w_low":       round(pct_from_52w_low, 4),
            "rsi14":                  round(rsi14, 2),
            "rsi9":                   round(rsi9, 2),
            "rsi_slope_3d":           round(rsi14 - rsi14_3d_ago, 3),
            "upper_wick":             round(upper_wick, 4),
            "close_loc":              round(close_loc, 4),
            # Trend
            "above_sma20":            above_sma20,
            "above_sma50":            above_sma50,
            "above_sma200":           above_sma200,
            "sma_alignment":          sma_alignment,
            "sma50_slope_5d":         round(sma50_slope_5d, 4),
            "price_vs_ema9":          round(price_vs_ema9, 4),
            # Volume
            "vol_surge_1d":           round(vol_surge_1d, 4),
            "vol_surge_3d":           round(vol_surge_3d, 4),
            "vol_dryup_5d":           round(vol_dryup_5d, 4),
            "obv_slope_5d":           round(obv_slope_5d_norm, 6),
            # Momentum
            "macd_hist":              round(macd_h, 6),
            "macd_hist_slope3":       round(macd_h - macd_h_3d, 6),
            "ret_1d":                 round(ret_1d, 5),
            "ret_3d":                 round(ret_3d, 5),
            "ret_5d":                 round(ret_5d, 5),
            "ret_20d":                round(ret_20d, 5),
            # Relative strength
            "rel_strength_20d":       round(rel_strength_20d, 5),
            "rel_strength_5d":        round(rel_strength_5d, 5),
            "rs_improving":           rs_improving,
            # Failure risk
            "prev_breakout_failed":   prev_breakout_failed,
            "consec_failed_highs":    consec_failed_highs,
            # Context
            "dollar_vol_20d":         round(adv, 0),
            "sma200_rising":          int(_safe_sma(closes, 200) is not None and
                                          _safe_sma(closes.iloc[:-20], 200) is not None and
                                          sma200 > _safe_sma(closes.iloc[:-20], 200)),
        })

        # ── Score compression (25 pts) ─────────────────────────────────────
        if range_contraction <= 0.30:
            comp.compression_pts += 12.0; reasons.append("tight_5d_coil")
        elif range_contraction <= 0.45:
            comp.compression_pts += 8.0;  reasons.append("moderate_coil")
        elif range_contraction <= 0.60:
            comp.compression_pts += 4.0

        if keltner_squeeze:
            comp.compression_pts += 5.0; reasons.append("keltner_squeeze")

        if vol_dryup_5d <= 0.65:
            comp.compression_pts += 8.0; reasons.append("vol_dryup_strong")
        elif vol_dryup_5d <= 0.80:
            comp.compression_pts += 5.0; reasons.append("vol_dryup_moderate")
        elif vol_dryup_5d <= 0.95:
            comp.compression_pts += 2.0

        comp.compression_pts = min(comp.compression_pts, 25.0)

        # ── Score confirmation (25 pts) ────────────────────────────────────
        # Penalty: large upper wick on today's candle = rejection signal
        if upper_wick > 0.60:
            warnings.append("large_upper_wick_rejection")
            comp.confirmation_pts -= 5.0

        if pct_from_20d_high >= -0.005:
            comp.confirmation_pts += 15.0; reasons.append("at_20d_high")
        elif pct_from_20d_high >= -0.020:
            comp.confirmation_pts += 10.0; reasons.append("near_20d_high")
        elif pct_from_20d_high >= -0.040:
            comp.confirmation_pts += 5.0

        if 50 <= rsi14 <= 65:
            comp.confirmation_pts += 10.0; reasons.append("rsi_healthy_zone")
        elif (45 <= rsi14 < 50) or (65 < rsi14 <= 72):
            comp.confirmation_pts += 5.0
        elif rsi14 > 75:
            warnings.append("rsi_overbought")

        if close_loc >= 0.70:
            comp.confirmation_pts += 3.0; reasons.append("strong_close")
        elif close_loc < 0.40:
            warnings.append("weak_close_location")

        # Near 52w high = very significant resistance breakout
        if pct_from_52w_high >= -0.01:
            comp.confirmation_pts += 5.0; reasons.append("near_52w_high_breakout")

        comp.confirmation_pts = max(0.0, min(comp.confirmation_pts, 25.0))

        # ── Score trend (25 pts) ───────────────────────────────────────────
        if above_sma20:
            comp.trend_pts += 8.0; reasons.append("above_sma20")
        if above_sma50:
            comp.trend_pts += 9.0; reasons.append("above_sma50")
        if above_sma200:
            comp.trend_pts += 8.0; reasons.append("above_sma200")
        comp.trend_pts = min(comp.trend_pts, 25.0)

        # ── Score volume (25 pts) ──────────────────────────────────────────
        if vol_surge_1d >= 2.0:
            comp.volume_pts = 25.0; reasons.append(f"vol_surge_{vol_surge_1d:.1f}x")
        elif vol_surge_1d >= 1.5:
            comp.volume_pts = 17.0; reasons.append(f"vol_surge_{vol_surge_1d:.1f}x")
        elif vol_surge_1d >= 1.2:
            comp.volume_pts = 10.0
        elif vol_surge_1d >= 1.0:
            comp.volume_pts = 5.0
        else:
            comp.volume_pts = 0.0; warnings.append("vol_below_avg")

        # Bonus: OBV rising confirms institutional accumulation
        if obv_slope_5d_norm > 0 and vol_surge_1d >= 1.2:
            comp.volume_pts = min(comp.volume_pts + 3.0, 25.0)
            reasons.append("obv_accumulation")

        # ── Failure risk adjustments ───────────────────────────────────────
        if prev_breakout_failed:
            warnings.append("prior_breakout_failed"); comp.compression_pts *= 0.7
        if consec_failed_highs >= 2:
            warnings.append("multiple_failed_pushes"); comp.confirmation_pts *= 0.7

        # ── Final score ────────────────────────────────────────────────────
        score = round(min(comp.total, 100.0), 1)
        features["breakout_score"] = score

        # ── Breakout type ──────────────────────────────────────────────────
        bt = self._classify_type(features, comp, warnings)
        features["breakout_type"] = bt

        # ── Confidence ────────────────────────────────────────────────────
        if score >= 80 and not warnings:
            confidence = "high"
        elif score >= 68 and len(warnings) <= 1:
            confidence = "medium"
        else:
            confidence = "low"

        # ── Price levels ──────────────────────────────────────────────────
        entry       = round(price, 2)
        tp          = round(price + self.atr_target_mult * atr, 2)
        stop        = round(price - self.atr_stop_mult * atr, 2)
        inval       = round(low_52w if pct_from_52w_high < -0.10 else min(
            float(lows.iloc[-5:].min()), price - atr * 1.2), 2)
        rr          = round((tp - entry) / max(entry - stop, 0.001), 2)

        passed = score >= self.threshold

        return BreakoutResult(
            ticker=ticker,
            score=score,
            passed=passed,
            breakout_type=bt,
            confidence=confidence,
            entry=entry,
            stop=stop,
            take_profit=tp,
            invalidation_level=inval,
            risk_reward=rr,
            features=features,
            score_components=comp,
            components=comp,  # backward compat alias
            signal_reasons=reasons,
            warning_flags=warnings,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _classify_type(
        self,
        features: Dict[str, Any],
        comp: BreakoutComponents,
        warnings: List[str],
    ) -> str:
        """Rule-based breakout type classifier.

        Uses feature thresholds to assign the most appropriate label.
        Multiple types can be true; return the highest-priority match.
        """
        vs1 = features.get("vol_surge_1d", 1.0) or 1.0
        pct_20h = features.get("pct_from_20d_high", -0.5) or -0.5
        pct_52w = features.get("pct_from_52w_high", -0.5) or -0.5
        ks = features.get("keltner_squeeze", 0)
        rc = features.get("range_contraction_5_20", 1.0) or 1.0
        r1d = features.get("ret_1d", 0.0) or 0.0
        abv_all = (features.get("above_sma200", 0) and
                   features.get("above_sma50", 0) and
                   features.get("above_sma20", 0))
        uw = features.get("upper_wick", 0.0) or 0.0
        cl = features.get("close_loc", 0.5) or 0.5

        if "prior_breakout_failed" in warnings or ("multiple_failed_pushes" in warnings and uw > 0.40):
            return "failed_breakout_risk"

        # Gap continuation: opened with a gap (1%+) + above all SMAs
        if r1d > 0.010 and abv_all:
            return "gap_continuation"

        # Range/52w high breakout: near major resistance + volume surge
        if pct_52w >= -0.015 and vs1 >= 1.5:
            return "range_breakout"

        # Volume-led breakout: massive vol surge is the defining feature
        if vs1 >= 2.0 and pct_20h >= -0.02:
            return "volume_breakout"

        # Range breakout: testing 20d high with some volume confirmation
        if pct_20h >= -0.02 and vs1 >= 1.2:
            return "range_breakout"

        # Trend continuation: all aligned, moderate breakout, RS positive
        if abv_all and pct_20h >= -0.04 and vs1 >= 1.0:
            return "trend_continuation"

        # Consolidation setup: squeeze active, vol drying up — not yet breaking
        if ks and rc <= 0.50:
            return "consolidation_setup"

        return "unknown"

    @staticmethod
    def _detect_prior_failure(
        closes: pd.Series,
        highs: pd.Series,
        lows: pd.Series,
        volume: pd.Series,
        vol_20d: float,
        lookback: int = 20,
        vol_thresh: float = 1.5,
        pullback_days: int = 5,
        pullback_frac: float = 0.75,
    ) -> int:
        """Detect if there was a high-volume day in the past `lookback` days
        that was followed by price giving back most of the gain.

        Returns 1 if prior breakout attempt failed, else 0.
        This is a leading indicator of a recurring failure pattern.

        Leakage-free: uses only historical data (not today's action).
        """
        if len(closes) < lookback + pullback_days + 2 or vol_20d <= 0:
            return 0
        c = closes.iloc[-(lookback + pullback_days + 2):-1].values
        v = volume.iloc[-(lookback + pullback_days + 2):-1].values
        if len(c) < pullback_days + lookback:
            return 0
        for i in range(1, min(lookback, len(c) - pullback_days)):
            if v[i] >= vol_thresh * vol_20d:
                # Was there a notable price gain on that day?
                if i > 0 and (c[i] / c[i-1] - 1) >= 0.005:
                    # Did price pull back to near the prior candle's close within pullback_days?
                    prior = c[i-1]
                    future_window = c[i+1: i+1+pullback_days]
                    if len(future_window) and np.min(future_window) <= prior * (1 + 0.002):
                        return 1
        return 0

    @staticmethod
    def _count_failed_highs(
        closes: pd.Series,
        highs: pd.Series,
        lows: pd.Series,
        lookback: int = 10,
    ) -> int:
        """Count days in prior `lookback` sessions where price made a new 5d high
        but then closed below the prior close — a failed upside push.

        Returns count (0 = clean, ≥2 = repeated rejection pattern).
        """
        if len(closes) < lookback + 6:
            return 0
        c = closes.iloc[-(lookback + 6):-1].values
        h = highs.iloc[-(lookback + 6):-1].values
        count = 0
        for i in range(5, len(c) - 1):
            if h[i] == np.max(h[i-5:i]):        # new 5-day high
                if c[i] < c[i-1]:               # but closed below prior close
                    count += 1
        return count

    @staticmethod
    def _atr(closes: pd.Series, highs: pd.Series, lows: pd.Series, period: int = 14) -> float:
        """Wilder's ATR using last `period` bars."""
        n = min(len(closes), len(highs), len(lows))
        if n < 2:
            return float(closes.iloc[-1]) * 0.02
        c = closes.iloc[-n:].values
        h = highs.iloc[-n:].values
        lo = lows.iloc[-n:].values
        pc = np.roll(c, 1); pc[0] = c[0]
        tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
        return float(tr[-period:].mean()) if len(tr) >= period else float(tr.mean())

    @staticmethod
    def _extract(
        raw: pd.DataFrame, tickers: List[str], col: str
    ) -> Dict[str, pd.Series]:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                lvl = raw.columns.get_level_values(0)
                if col in lvl:
                    blk = raw[col]
                    return {t: blk[t] for t in tickers if t in blk.columns}
            elif col in raw.columns:
                return {tickers[0]: raw[col]}
        except Exception:
            pass
        return {}


# ── Module-level helpers ───────────────────────────────────────────────────────

def _safe_sma(closes: pd.Series, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    v = float(closes.rolling(period).mean().iloc[-1])
    return v if np.isfinite(v) else None


def _atr(closes: pd.Series, highs: pd.Series, lows: pd.Series, period: int = 14) -> float:
    return BreakoutScanner._atr(closes, highs, lows, period)


def _rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff().dropna()
    gains  = delta.clip(lower=0).rolling(period).mean()
    losses = (-delta.clip(upper=0)).rolling(period).mean()
    last_loss = float(losses.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gains.iloc[-1]) / last_loss
    return float(100.0 - 100.0 / (1 + rs))


def _macd_hist(closes: pd.Series) -> float:
    if len(closes) < 26:
        return 0.0
    ema12   = float(closes.ewm(span=12, adjust=False).mean().iloc[-1])
    ema26   = float(closes.ewm(span=26, adjust=False).mean().iloc[-1])
    macd    = ema12 - ema26
    signal  = float(pd.Series([macd]).ewm(span=9, adjust=False).mean().iloc[-1])
    return float(macd - signal)


def _bb(closes: pd.Series, period: int = 20) -> Tuple[float, float, float]:
    if len(closes) < period:
        mid = float(closes.mean())
        return mid, mid, mid
    sma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    mid  = float(sma.iloc[-1])
    uppr = float((sma + 2 * std).iloc[-1])
    lowr = float((sma - 2 * std).iloc[-1])
    return mid, uppr, lowr


def _keltner(closes: pd.Series, highs: pd.Series, lows: pd.Series,
             period: int = 20) -> Tuple[float, float]:
    """EMA ± 2×ATR Keltner channels."""
    if len(closes) < period:
        mid = float(closes.mean())
        return mid, mid
    atr = _atr(closes, highs, lows, period)
    ema = float(closes.ewm(span=period, adjust=False).mean().iloc[-1])
    return ema + 2 * atr, ema - 2 * atr


def _obv(closes: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    sign = np.sign(closes.diff().fillna(0))
    return (sign * volume).cumsum()


def _relative_strength(
    closes: pd.Series, spy: pd.Series,
) -> Tuple[float, float]:
    """Return (rel_strength_20d, rel_strength_5d) vs SPY.
    Uses only overlapping history. Returns (0, 0) if insufficient data.
    """
    try:
        common = closes.index.intersection(spy.index)
        c = closes.loc[common].dropna()
        s = spy.loc[common].dropna()
        common2 = c.index.intersection(s.index)
        c = c.loc[common2]; s = s.loc[common2]
        if len(c) < 5:
            return 0.0, 0.0
        rs20 = float(c.pct_change(20).iloc[-1] - s.pct_change(20).iloc[-1]) if len(c) > 20 else 0.0
        rs5  = float(c.pct_change(5).iloc[-1]  - s.pct_change(5).iloc[-1])  if len(c) > 5  else 0.0
        return rs20, rs5
    except Exception:
        return 0.0, 0.0
