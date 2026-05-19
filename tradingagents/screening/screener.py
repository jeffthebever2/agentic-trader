"""Pre-screening engine: scores tickers on technical/momentum signals.

All signals use yfinance only — free, no API quota, no AI, no FMP calls.
Only tickers scoring at or above the threshold are passed to the full
TradingAgents AI pipeline, preserving LLM usage and FMP quota.

Scoring breakdown (max 100 pts):
  Trend     30 pts  — price above 20/50/200-day SMA (10 pts each)
  Momentum  25 pts  — 1M/3M/6M return vs SPY (8/9/8 pts)
  RSI       20 pts  — RSI in healthy uptrend zone (45-65)
  Volume    15 pts  — 10-day avg vol vs 30-day avg vol
  MACD      10 pts  — MACD line above signal line
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SignalBreakdown:
    trend: float = 0.0      # 0-30
    momentum: float = 0.0   # 0-25
    rsi: float = 0.0        # 0-20
    volume: float = 0.0     # 0-15
    macd: float = 0.0       # 0-10
    details: Dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.trend + self.momentum + self.rsi + self.volume + self.macd


@dataclass
class PriceTargets:
    entry: float       # suggested limit-order entry (current close)
    target: float      # take-profit price (entry + 1.5× ATR)
    stop: float        # stop-loss price   (entry − 0.8× ATR)
    atr: float         # 14-day ATR in dollars
    risk_reward: float # (target − entry) / (entry − stop)
    hold_days: str = "2-4"

    def as_summary(self) -> str:
        rr = f"{self.risk_reward:.1f}"
        return (
            f"Entry ≤ ${self.entry:.2f}  |  "
            f"Target ${self.target:.2f}  |  "
            f"Stop ${self.stop:.2f}  |  "
            f"R/R {rr}:1  |  Hold {self.hold_days} days"
        )


@dataclass
class ScreenResult:
    ticker: str
    score: float
    signals: SignalBreakdown
    passed: bool
    error: Optional[str] = None
    targets: Optional[PriceTargets] = None


class StockScreener:
    """Score a batch of tickers on cheap technical signals before AI analysis."""

    def __init__(self, threshold: float = 85.0):
        self.threshold = threshold

    def screen_batch(
        self,
        tickers: List[str],
        as_of_date: str,
        spy_ticker: str = "SPY",
    ) -> List[ScreenResult]:
        """Batch-download 1 year of price data and score every ticker.

        Returns results sorted by score descending. Tickers with insufficient
        history (< 60 trading days) or download errors are marked as failed.
        """
        import yfinance as yf

        tickers_upper = list(dict.fromkeys(t.upper() for t in tickers))
        all_tickers = list(dict.fromkeys(tickers_upper + [spy_ticker]))

        try:
            raw = yf.download(
                all_tickers,
                period="1y",
                end=as_of_date,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.error("Batch download failed: %s", exc)
            return [
                ScreenResult(t, 0.0, SignalBreakdown(), False, f"Download failed: {exc}")
                for t in tickers_upper
            ]

        close = self._extract_series(raw, all_tickers, "Close")
        volume = self._extract_series(raw, all_tickers, "Volume")
        spy_close = close.get(spy_ticker, pd.Series(dtype=float)).dropna()

        results: List[ScreenResult] = []
        for ticker in tickers_upper:
            series = close.get(ticker, pd.Series(dtype=float)).dropna()
            if len(series) < 60:
                results.append(
                    ScreenResult(
                        ticker, 0.0, SignalBreakdown(), False,
                        "Insufficient price history (< 60 days)",
                    )
                )
                continue
            try:
                vol_series = volume.get(ticker, pd.Series(dtype=float)).dropna()
                signals = self._compute_signals(series, vol_series, spy_close)
                score = round(min(signals.total, 100.0), 1)
                results.append(ScreenResult(ticker, score, signals, score >= self.threshold))
            except Exception as exc:
                logger.warning("Scoring failed for %s: %s", ticker, exc)
                results.append(ScreenResult(ticker, 0.0, SignalBreakdown(), False, str(exc)))

        return sorted(results, key=lambda r: r.score, reverse=True)

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_series(
        raw: pd.DataFrame, tickers: List[str], col: str
    ) -> Dict[str, pd.Series]:
        """Pull a per-ticker dict of Series from a (possibly MultiIndex) download."""
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                level0 = raw.columns.get_level_values(0)
                if col in level0:
                    block = raw[col]
                    return {t: block[t] for t in tickers if t in block.columns}
            elif col in raw.columns:
                # Single-ticker download (rare in batch context)
                return {tickers[0]: raw[col]}
        except Exception:
            pass
        return {}

    def _compute_signals(
        self,
        closes: pd.Series,
        volume: pd.Series,
        spy: pd.Series,
    ) -> SignalBreakdown:
        sig = SignalBreakdown()
        price = float(closes.iloc[-1])

        # ── Trend (30 pts) ──────────────────────────────────────────────
        for window, label in [(20, "SMA20"), (50, "SMA50"), (200, "SMA200")]:
            if len(closes) >= window:
                sma = float(closes.rolling(window).mean().iloc[-1])
                if price > sma:
                    sig.trend += 10.0
                    sig.details[label] = f"above {sma:.2f} ✓"
                else:
                    sig.details[label] = f"below {sma:.2f}"

        # ── Momentum vs SPY (25 pts) ────────────────────────────────────
        for days, pts, label in [(21, 8, "1M"), (63, 9, "3M"), (126, 8, "6M")]:
            if len(closes) > days and len(spy) > days:
                ret = float(closes.iloc[-1] / closes.iloc[-days] - 1)
                spy_ret = float(spy.iloc[-1] / spy.iloc[-days] - 1)
                diff = ret - spy_ret
                if diff > 0:
                    sig.momentum += pts
                sig.details[f"Mom{label}"] = f"{diff:+.1%} vs SPY"

        # ── RSI (20 pts) ────────────────────────────────────────────────
        rsi = self._rsi(closes)
        if 45 <= rsi <= 65:
            sig.rsi = 20.0
        elif (35 <= rsi < 45) or (65 < rsi <= 75):
            sig.rsi = 10.0
        sig.details["RSI"] = f"{rsi:.1f}"

        # ── Volume expansion (15 pts) ───────────────────────────────────
        if len(volume) >= 30:
            v10 = float(volume.iloc[-10:].mean())
            v30 = float(volume.iloc[-30:].mean())
            ratio = v10 / v30 if v30 > 0 else 1.0
            sig.volume = round(min(ratio * 10.0, 15.0), 1)
            sig.details["Vol10/30"] = f"{ratio:.2f}x"
        else:
            sig.volume = 7.5  # neutral when no volume data
            sig.details["Vol10/30"] = "n/a"

        # ── MACD (10 pts) ───────────────────────────────────────────────
        macd_val, signal_val = self._macd(closes)
        if macd_val > signal_val:
            sig.macd = 10.0
            sig.details["MACD"] = "bullish ✓"
        else:
            sig.details["MACD"] = "bearish"

        return sig

    @staticmethod
    def _rsi(closes: pd.Series, period: int = 14) -> float:
        delta = closes.diff().dropna()
        gains = delta.clip(lower=0).rolling(period).mean()
        losses = (-delta.clip(upper=0)).rolling(period).mean()
        last_loss = float(losses.iloc[-1])
        if last_loss == 0:
            return 100.0
        rs = float(gains.iloc[-1]) / last_loss
        return 100.0 - (100.0 / (1 + rs))

    @staticmethod
    def _macd(closes: pd.Series) -> Tuple[float, float]:
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return float(macd.iloc[-1]), float(signal.iloc[-1])


class SwingScreener(StockScreener):
    """Score tickers for 2-4 day swing trades and compute price targets.

    Scoring breakdown (max 100 pts) — designed to predict the NEXT move:
      Consolidation  25 pts  — tight 5-day range + volume dry-up (coiling)
      Breakout setup 25 pts  — price testing 10d high + RSI9 building
      Trend health   25 pts  — above 20/50-day SMA
      Volume trigger 25 pts  — today's volume expansion vs 20-day baseline

    Design principle: find stocks COILING before the move, not chasing after it.
    The VCP / base-and-break pattern: declining volatility + declining volume
    over recent days, then today's price tests resistance on expanding volume.
    This selects early breakouts rather than exhausted post-spike reversals.

    For every passing ticker the screener attaches a PriceTargets object with
    ATR-based entry, take-profit, and stop-loss levels.
    """

    # Multipliers for ATR-based targets
    _ATR_TARGET = 1.2   # upside = 1.2× ATR
    _ATR_STOP   = 0.7   # downside = 0.7× ATR

    def screen_batch(
        self,
        tickers: List[str],
        as_of_date: str,
        spy_ticker: str = "SPY",
    ) -> List[ScreenResult]:
        """Same interface as StockScreener but uses swing-trade scoring."""
        import yfinance as yf

        tickers_upper = list(dict.fromkeys(t.upper() for t in tickers))
        all_tickers = list(dict.fromkeys(tickers_upper + [spy_ticker]))

        try:
            raw = yf.download(
                all_tickers,
                period="1y",
                end=as_of_date,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.error("Batch download failed: %s", exc)
            return [
                ScreenResult(t, 0.0, SignalBreakdown(), False, f"Download failed: {exc}")
                for t in tickers_upper
            ]

        close  = self._extract_series(raw, all_tickers, "Close")
        high   = self._extract_series(raw, all_tickers, "High")
        low    = self._extract_series(raw, all_tickers, "Low")
        volume = self._extract_series(raw, all_tickers, "Volume")

        results: List[ScreenResult] = []
        for ticker in tickers_upper:
            series = close.get(ticker, pd.Series(dtype=float)).dropna()
            if len(series) < 60:
                results.append(
                    ScreenResult(ticker, 0.0, SignalBreakdown(), False,
                                 "Insufficient price history (< 60 days)")
                )
                continue
            try:
                h = high.get(ticker, pd.Series(dtype=float)).dropna()
                l = low.get(ticker, pd.Series(dtype=float)).dropna()
                v = volume.get(ticker, pd.Series(dtype=float)).dropna()
                signals = self._swing_signals(series, h, l, v)
                score = round(min(signals.total, 100.0), 1)
                targets = self._price_targets(series, h, l) if score >= self.threshold else None
                results.append(ScreenResult(ticker, score, signals, score >= self.threshold, targets=targets))
            except Exception as exc:
                logger.warning("Swing scoring failed for %s: %s", ticker, exc)
                results.append(ScreenResult(ticker, 0.0, SignalBreakdown(), False, str(exc)))

        return sorted(results, key=lambda r: r.score, reverse=True)

    def _swing_signals(
        self,
        closes: pd.Series,
        highs: pd.Series,
        lows: pd.Series,
        volume: pd.Series,
    ) -> SignalBreakdown:
        sig = SignalBreakdown()
        price = float(closes.iloc[-1])

        # ── Consolidation quality (25 pts) ─────────────────────────────────
        # Tight range + volume dry-up = coiling before a move (VCP pattern).
        # High score here means the stock is compressed and ready to expand.
        coil_pts = 0.0
        if len(highs) >= 20 and len(lows) >= 20:
            range_5d  = float(highs.iloc[-5:].max()  - lows.iloc[-5:].min())
            range_20d = float(highs.iloc[-20:].max() - lows.iloc[-20:].min())
            contraction = range_5d / range_20d if range_20d > 0 else 1.0
            sig.details["Contraction"] = f"{contraction:.2f}"
            if   contraction <= 0.30:  coil_pts += 15.0   # very tight coil
            elif contraction <= 0.45:  coil_pts += 10.0   # solid base
            elif contraction <= 0.60:  coil_pts +=  5.0   # moderate

        # Volume dry-up: last 3 days (before today) quieter than 20-day avg
        if len(volume) >= 5:
            vol_3d_prior = float(volume.iloc[-4:-1].mean())  # 3 days before today
            vol_20d      = float(volume.iloc[-20:].mean())
            dryup_ratio  = vol_3d_prior / vol_20d if vol_20d > 0 else 1.0
            sig.details["VolDryup"] = f"{dryup_ratio:.2f}× avg (prior 3d)"
            if   dryup_ratio <= 0.65:  coil_pts += 10.0   # institutions quietly exiting sellers
            elif dryup_ratio <= 0.80:  coil_pts +=  6.0
            elif dryup_ratio <= 0.95:  coil_pts +=  3.0

        sig.trend = round(min(coil_pts, 25.0), 1)

        # ── Breakout setup (25 pts) ────────────────────────────────────────
        # Price testing resistance while RSI is building — not yet overbought.
        brk_pts = 0.0
        if len(highs) >= 10:
            high_10 = float(highs.iloc[-10:].max())
            pct_from_high = (price - high_10) / high_10
            sig.details["Pct10DH"] = f"{pct_from_high:+.2%}"
            if   pct_from_high >= -0.005:  brk_pts += 15.0   # at or just above 10d high
            elif pct_from_high >= -0.02:   brk_pts += 10.0   # within 2%
            elif pct_from_high >= -0.04:   brk_pts +=  5.0   # within 4%

        rsi_9 = self._rsi(closes, period=9)
        sig.details["RSI9"] = f"{rsi_9:.1f}"
        if   52 <= rsi_9 <= 65:  brk_pts += 10.0   # ideal: momentum building, room to run
        elif 65 < rsi_9 <= 72:  brk_pts +=  5.0   # ok, slightly extended
        elif 45 <= rsi_9 < 52:  brk_pts +=  4.0   # recovering, momentum not yet there
        # <45 or >72: 0 pts

        sig.momentum = round(min(brk_pts, 25.0), 1)

        # ── Trend health (25 pts) ──────────────────────────────────────────
        trend_pts = 0.0
        for window, pts, label in [(20, 12, "SMA20"), (50, 13, "SMA50")]:
            if len(closes) >= window:
                sma = float(closes.rolling(window).mean().iloc[-1])
                if price > sma:
                    trend_pts += pts
                    sig.details[label] = f"above {sma:.2f} ✓"
                else:
                    sig.details[label] = f"below {sma:.2f}"
        sig.rsi = round(min(trend_pts, 25.0), 1)

        # ── Volume trigger today (25 pts) ──────────────────────────────────
        # Today's volume should be expanding vs baseline — confirms breakout.
        if len(volume) >= 20:
            v_today = float(volume.iloc[-1])
            v20     = float(volume.iloc[-20:].mean())
            ratio   = v_today / v20 if v20 > 0 else 1.0
            sig.details["VolToday"] = f"{ratio:.2f}× avg"
            if   ratio >= 2.0:  sig.volume = 25.0
            elif ratio >= 1.5:  sig.volume = 17.0
            elif ratio >= 1.2:  sig.volume = 10.0
            elif ratio >= 1.0:  sig.volume =  5.0

        # Store today's 1d return for signal analysis in backtest
        if len(closes) > 1:
            ret_1d = float(closes.iloc[-1] / closes.iloc[-2] - 1)
            sig.details["Ret1D"] = f"{ret_1d:+.2%}"

        return sig

    def _price_targets(
        self,
        closes: pd.Series,
        highs: pd.Series,
        lows: pd.Series,
        period: int = 14,
    ) -> PriceTargets:
        """ATR-based entry / take-profit / stop-loss for a 2-4 day hold."""
        entry = float(closes.iloc[-1])
        atr = self._atr(closes, highs, lows, period)
        target = entry + self._ATR_TARGET * atr
        stop   = entry - self._ATR_STOP   * atr
        risk_reward = (target - entry) / (entry - stop) if (entry - stop) > 0 else 0.0
        return PriceTargets(
            entry=round(entry, 2),
            target=round(target, 2),
            stop=round(stop, 2),
            atr=round(atr, 3),
            risk_reward=round(risk_reward, 2),
        )

    @staticmethod
    def _atr(closes: pd.Series, highs: pd.Series, lows: pd.Series, period: int = 14) -> float:
        """Wilder's Average True Range."""
        n = min(len(closes), len(highs), len(lows))
        c = closes.iloc[-n:].values
        h = highs.iloc[-n:].values
        l = lows.iloc[-n:].values
        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        return float(tr[-period:].mean()) if len(tr) >= period else float(tr.mean())
