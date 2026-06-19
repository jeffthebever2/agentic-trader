"""Market-regime engine — pure, network-free.

A pure long-momentum scanner has no awareness of *when* it is dangerous to buy
(risk-off / high-volatility / weak-breadth regimes). This module scores the
regime from SPY trend + realized/implied volatility + market breadth and exposes
an **adaptive threshold multiplier** so the scanner can demand stronger signals
(or stand down) when the market is hostile, without any hard-coded calendar or
look-ahead. Inputs are injected by the caller (bars / VIX / breadth) so every
function here is deterministic and unit-testable offline.

Wiring lives in `web/api/thematic_auto.py` (flag THEMATIC_REGIME_GATE, default
off). Nothing here places orders or fetches data.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def _finite(xs: Sequence) -> list[float]:
    out: list[float] = []
    for x in xs or []:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def sma(values: Sequence[float], n: int) -> Optional[float]:
    """Simple moving average of the last n values; None if insufficient/empty."""
    v = _finite(values)
    if n <= 0 or len(v) < n:
        return None
    return sum(v[-n:]) / n


def trend_state(closes: Sequence[float], *, fast: int = 50, slow: int = 200) -> str:
    """'up' | 'down' | 'neutral' | 'unknown' from price vs fast/slow SMAs.

    up   = price above the slow SMA AND fast SMA above slow (classic uptrend)
    down = price below the slow SMA AND fast SMA below slow
    neutral = mixed; unknown = not enough history for the slow SMA.
    """
    v = _finite(closes)
    if len(v) < slow:
        return "unknown"
    price = v[-1]
    sf, ss = sma(v, fast), sma(v, slow)
    if sf is None or ss is None or ss <= 0:
        return "unknown"
    if price > ss and sf >= ss:
        return "up"
    if price < ss and sf <= ss:
        return "down"
    return "neutral"


def realized_vol_annualized(closes: Sequence[float], lookback: int = 20) -> Optional[float]:
    """Annualized realized volatility (%) from daily log-ish returns over lookback."""
    v = _finite(closes)
    if len(v) < lookback + 1:
        return None
    rets = []
    for a, b in zip(v[-(lookback + 1):-1], v[-lookback:]):
        if a > 0 and b > 0:
            rets.append((b - a) / a)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def vol_regime(*, closes: Optional[Sequence[float]] = None, vix: Optional[float] = None) -> str:
    """'calm' | 'normal' | 'elevated' | 'high' | 'unknown'. Prefers an explicit
    VIX level; else derives from realized vol of `closes`."""
    level: Optional[float] = None
    if vix is not None:
        try:
            level = float(vix)
        except (TypeError, ValueError):
            level = None
        if level is not None and not math.isfinite(level):
            level = None
    if level is None and closes is not None:
        level = realized_vol_annualized(closes)
    if level is None:
        return "unknown"
    if level < 13:
        return "calm"
    if level < 20:
        return "normal"
    if level < 30:
        return "elevated"
    return "high"


def breadth_state(pct_above_50dma: Optional[float]) -> str:
    """'strong' | 'neutral' | 'weak' | 'unknown' from % of universe above its 50dma."""
    if pct_above_50dma is None:
        return "unknown"
    try:
        p = float(pct_above_50dma)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(p):
        return "unknown"
    if p >= 60:
        return "strong"
    if p >= 40:
        return "neutral"
    return "weak"


_TREND_PTS = {"up": 40.0, "neutral": 20.0, "down": 0.0, "unknown": 20.0}
_VOL_PTS = {"calm": 30.0, "normal": 22.0, "elevated": 10.0, "high": 0.0, "unknown": 18.0}
_BREADTH_PTS = {"strong": 30.0, "neutral": 18.0, "weak": 0.0, "unknown": 15.0}


def risk_on_score(*, trend: str, volatility: str, breadth: str) -> float:
    """0-100 composite. Higher = more risk-on (safer to buy momentum). Unknown
    inputs score a neutral middle so a missing feed degrades gracefully rather
    than forcing risk-off or risk-on."""
    return round(
        _TREND_PTS.get(trend, 20.0)
        + _VOL_PTS.get(volatility, 18.0)
        + _BREADTH_PTS.get(breadth, 15.0),
        1,
    )


def regime_threshold_multiplier(risk_score: float) -> float:
    """Multiplier applied to the buy-score gate. Risk-on (high score) → 1.0 (normal
    gate); risk-off → up to 1.5 (demand 50% stronger signals). Monotonic, clamped."""
    try:
        s = float(risk_score)
    except (TypeError, ValueError):
        return 1.25
    if not math.isfinite(s):
        return 1.25
    if s >= 70:
        return 1.0
    if s >= 50:
        return 1.1
    if s >= 30:
        return 1.25
    return 1.5


def assess_regime(
    *,
    spy_closes: Optional[Sequence[float]] = None,
    vix: Optional[float] = None,
    pct_above_50dma: Optional[float] = None,
) -> dict:
    """One-call regime snapshot for the scanner. All inputs optional/injected."""
    trend = trend_state(spy_closes or [])
    vol = vol_regime(closes=spy_closes, vix=vix)
    breadth = breadth_state(pct_above_50dma)
    score = risk_on_score(trend=trend, volatility=vol, breadth=breadth)
    return {
        "trend": trend,
        "volatility": vol,
        "breadth": breadth,
        "risk_on_score": score,
        "threshold_multiplier": regime_threshold_multiplier(score),
        "risk_off": score < 30,
    }
