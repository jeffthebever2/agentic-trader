"""Price/volume *discovery* engine — pure, network-free.

The buzz scanner structurally cannot find a left-for-dead name with no social
chatter (the 'IREN at $5' problem): attention lags price by weeks on these. This
module surfaces such names from *price and volume alone* — relative strength vs a
benchmark, volume expansion, proximity to / break of 52-week highs, and
accumulation (up-volume vs down-volume) — none of which need buzz.

Every function is pure and deterministic; the caller injects OHLCV bars (oldest →
newest). Nothing here fetches data or places orders. Wiring (universe iteration +
fetch) lives in `web/api/thematic_auto.py` behind THEMATIC_DISCOVERY (default off).
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


def relative_strength(closes: Sequence[float], bench_closes: Sequence[float],
                      lookback: int = 63) -> Optional[float]:
    """Ratio of the name's lookback return to the benchmark's (≈3 months).
    >1 ⇒ outperforming. None if insufficient/at-zero data."""
    c, b = _finite(closes), _finite(bench_closes)
    if len(c) < lookback + 1 or len(b) < lookback + 1:
        return None
    c0, c1 = c[-(lookback + 1)], c[-1]
    b0, b1 = b[-(lookback + 1)], b[-1]
    if c0 <= 0 or b0 <= 0:
        return None
    name_ret = c1 / c0
    bench_ret = b1 / b0
    if bench_ret <= 0:
        return None
    return name_ret / bench_ret


def volume_expansion(volumes: Sequence[float], lookback: int = 20) -> Optional[float]:
    """Today's volume / average of the prior `lookback` days. None if thin data."""
    v = _finite(volumes)
    if len(v) < lookback + 1:
        return None
    prior = v[-(lookback + 1):-1]
    avg = sum(prior) / len(prior) if prior else 0.0
    return (v[-1] / avg) if avg > 0 else None


def high_proximity(closes: Sequence[float], highs: Sequence[float],
                   lookback: int = 252) -> dict:
    """{pct_of_high, new_high} — how close today's close is to the `lookback`-day
    high, and whether it's a fresh high. Uses what history is available (>= 20)."""
    c, h = _finite(closes), _finite(highs)
    n = min(len(c), len(h))
    if n < 20:
        return {"pct_of_high": 0.0, "new_high": False}
    window = min(lookback, n)
    prior_high = max(h[-window:-1]) if window > 1 else h[-1]
    today = c[-1]
    pct = (today / prior_high) if prior_high > 0 else 0.0
    return {"pct_of_high": round(pct, 4), "new_high": bool(prior_high > 0 and today >= prior_high)}


def accumulation(closes: Sequence[float], volumes: Sequence[float],
                 lookback: int = 20) -> float:
    """Net up-volume fraction over `lookback` days, in [-1, 1]. Positive ⇒ volume
    concentrated on up days (accumulation); negative ⇒ distribution."""
    c, v = _finite(closes), _finite(volumes)
    n = min(len(c), len(v))
    if n < lookback + 1:
        return 0.0
    up = down = 0.0
    for i in range(n - lookback, n):
        if i <= 0:
            continue
        vol = v[i]
        if vol <= 0:
            continue
        if c[i] > c[i - 1]:
            up += vol
        elif c[i] < c[i - 1]:
            down += vol
    total = up + down
    if total <= 0:
        return 0.0
    return round((up - down) / total, 4)


def discovery_score(*, rs: Optional[float], vol_exp: Optional[float],
                    high_prox: float, accum: float) -> float:
    """0-100 composite. Rewards outperformance + volume expansion + nearness to
    highs + accumulation. Missing RS/vol degrade to a neutral partial credit."""
    # Relative strength: 1.0 → 0, 1.3+ → full 35.
    if rs is None:
        rs_pts = 12.0
    else:
        rs_pts = max(0.0, min((rs - 1.0) / 0.3, 1.0)) * 35.0
    # Volume expansion: 1.0 → 0, 3x+ → full 30.
    if vol_exp is None:
        ve_pts = 8.0
    else:
        ve_pts = max(0.0, min((vol_exp - 1.0) / 2.0, 1.0)) * 30.0
    # High proximity: 0.90 → 0, >=1.0 → full 20.
    hp_pts = max(0.0, min((high_prox - 0.90) / 0.10, 1.0)) * 20.0
    # Accumulation: -1 → 0, +1 → full 15.
    ac_pts = max(0.0, min((accum + 1.0) / 2.0, 1.0)) * 15.0
    return round(rs_pts + ve_pts + hp_pts + ac_pts, 1)


def is_discovery_candidate(
    bars: dict,
    bench_closes: Sequence[float],
    *,
    min_score: float = 60.0,
    min_vol_exp: float = 1.5,
    min_rs: float = 1.05,
) -> dict:
    """Decide if a name is a price/volume discovery candidate from its OHLCV bars
    {highs, closes, volumes}. Requires a *real* breakout: near/at highs AND volume
    expansion AND outperformance AND a high composite — so a quiet drift or a
    high-volume churn that isn't leading doesn't qualify (keeps junk out)."""
    closes = bars.get("closes", []) if isinstance(bars, dict) else []
    highs = bars.get("highs", []) if isinstance(bars, dict) else []
    volumes = bars.get("volumes", []) if isinstance(bars, dict) else []
    rs = relative_strength(closes, bench_closes)
    ve = volume_expansion(volumes)
    hp = high_proximity(closes, highs)
    ac = accumulation(closes, volumes)
    score = discovery_score(rs=rs, vol_exp=ve, high_prox=hp["pct_of_high"], accum=ac)
    qualifies = bool(
        score >= min_score
        and (hp["new_high"] or hp["pct_of_high"] >= 0.97)
        and ve is not None and ve >= min_vol_exp
        and rs is not None and rs >= min_rs
    )
    return {
        "qualifies": qualifies,
        "score": score,
        "rs": round(rs, 3) if rs is not None else None,
        "vol_expansion": round(ve, 2) if ve is not None else None,
        "pct_of_high": hp["pct_of_high"],
        "new_high": hp["new_high"],
        "accumulation": ac,
    }
