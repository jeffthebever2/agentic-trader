"""Chart pattern detection for BreakoutScanner enrichment.

Algorithms adapted from PatternPy (twopirllc/PatternPy, MIT license).
Returns a compact pattern dict and a score delta for BreakoutComponents.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Pattern detection helpers ─────────────────────────────────────────────────

def _head_shoulder(df: pd.DataFrame, window: int) -> str:
    d = df.copy()
    d["_hr"] = d["High"].rolling(window).max()
    d["_lr"] = d["Low"].rolling(window).min()
    hs = (
        (d["_hr"] > d["High"].shift(1)) & (d["_hr"] > d["High"].shift(-1)) &
        (d["High"] < d["High"].shift(1)) & (d["High"] < d["High"].shift(-1))
    )
    ihs = (
        (d["_lr"] < d["Low"].shift(1)) & (d["_lr"] < d["Low"].shift(-1)) &
        (d["Low"] > d["Low"].shift(1)) & (d["Low"] > d["Low"].shift(-1))
    )
    col = pd.Series("", index=d.index)
    col[hs] = "Head and Shoulder"
    col[ihs] = "Inverse Head and Shoulder"
    last = col[col != ""]
    return last.iloc[-1] if len(last) else ""


def _double_top_bottom(df: pd.DataFrame, window: int, threshold: float = 0.05) -> str:
    d = df.copy()
    d["_hr"] = d["High"].rolling(window).max()
    d["_lr"] = d["Low"].rolling(window).min()
    dt_mask = (
        (d["_hr"] >= d["High"].shift(1)) & (d["_hr"] >= d["High"].shift(-1)) &
        (d["High"] < d["High"].shift(1)) & (d["High"] < d["High"].shift(-1)) &
        ((d["High"].shift(1) - d["Low"].shift(1)) <= threshold * (d["High"].shift(1) + d["Low"].shift(1)) / 2) &
        ((d["High"].shift(-1) - d["Low"].shift(-1)) <= threshold * (d["High"].shift(-1) + d["Low"].shift(-1)) / 2)
    )
    db_mask = (
        (d["_lr"] <= d["Low"].shift(1)) & (d["_lr"] <= d["Low"].shift(-1)) &
        (d["Low"] > d["Low"].shift(1)) & (d["Low"] > d["Low"].shift(-1)) &
        ((d["High"].shift(1) - d["Low"].shift(1)) <= threshold * (d["High"].shift(1) + d["Low"].shift(1)) / 2) &
        ((d["High"].shift(-1) - d["Low"].shift(-1)) <= threshold * (d["High"].shift(-1) + d["Low"].shift(-1)) / 2)
    )
    col = pd.Series("", index=d.index)
    col[dt_mask] = "Double Top"
    col[db_mask] = "Double Bottom"
    last = col[col != ""]
    return last.iloc[-1] if len(last) else ""


def _wedge(df: pd.DataFrame, window: int) -> str:
    d = df.copy()
    d["_hr"] = d["High"].rolling(window).max()
    d["_lr"] = d["Low"].rolling(window).min()
    d["_th"] = d["High"].rolling(window).apply(lambda x: 1 if x[-1] > x[0] else -1 if x[-1] < x[0] else 0, raw=True)
    d["_tl"] = d["Low"].rolling(window).apply(lambda x: 1 if x[-1] > x[0] else -1 if x[-1] < x[0] else 0, raw=True)
    wu = (d["_hr"] >= d["High"].shift(1)) & (d["_lr"] <= d["Low"].shift(1)) & (d["_th"] == 1) & (d["_tl"] == 1)
    wd = (d["_hr"] <= d["High"].shift(1)) & (d["_lr"] >= d["Low"].shift(1)) & (d["_th"] == -1) & (d["_tl"] == -1)
    col = pd.Series("", index=d.index)
    col[wu] = "Wedge Up"
    col[wd] = "Wedge Down"
    last = col[col != ""]
    return last.iloc[-1] if len(last) else ""


def _triangle(df: pd.DataFrame, window: int) -> str:
    d = df.copy()
    d["_hr"] = d["High"].rolling(window).max()
    d["_lr"] = d["Low"].rolling(window).min()
    asc = (d["_hr"] >= d["High"].shift(1)) & (d["_lr"] <= d["Low"].shift(1)) & (d["Close"] > d["Close"].shift(1))
    dsc = (d["_hr"] <= d["High"].shift(1)) & (d["_lr"] >= d["Low"].shift(1)) & (d["Close"] < d["Close"].shift(1))
    col = pd.Series("", index=d.index)
    col[asc] = "Ascending Triangle"
    col[dsc] = "Descending Triangle"
    last = col[col != ""]
    return last.iloc[-1] if len(last) else ""


def _channel(df: pd.DataFrame, window: int) -> str:
    d = df.copy()
    d["_hr"] = d["High"].rolling(window).max()
    d["_lr"] = d["Low"].rolling(window).min()
    d["_th"] = d["High"].rolling(window).apply(lambda x: 1 if x[-1] > x[0] else -1 if x[-1] < x[0] else 0, raw=True)
    d["_tl"] = d["Low"].rolling(window).apply(lambda x: 1 if x[-1] > x[0] else -1 if x[-1] < x[0] else 0, raw=True)
    cr = 0.1
    mid = (d["_hr"] + d["_lr"]) / 2
    tight = (d["_hr"] - d["_lr"]) <= cr * mid
    cu = (d["_hr"] >= d["High"].shift(1)) & (d["_lr"] <= d["Low"].shift(1)) & tight & (d["_th"] == 1) & (d["_tl"] == 1)
    cd = (d["_hr"] <= d["High"].shift(1)) & (d["_lr"] >= d["Low"].shift(1)) & tight & (d["_th"] == -1) & (d["_tl"] == -1)
    col = pd.Series("", index=d.index)
    col[cu] = "Channel Up"
    col[cd] = "Channel Down"
    last = col[col != ""]
    return last.iloc[-1] if len(last) else ""


def _pivots(df: pd.DataFrame) -> dict[str, int]:
    h = df["High"]
    lo = df["Low"]
    hd = h.diff()
    ld = lo.diff()
    counts: dict[str, int] = {"HH": 0, "HL": 0, "LH": 0, "LL": 0}
    recent = df.tail(20)
    h_r = recent["High"]
    l_r = recent["Low"]
    hd_r = h_r.diff()
    ld_r = l_r.diff()
    counts["HH"] = int(((hd_r > 0) & (hd_r.shift(-1) < 0)).sum())
    counts["LL"] = int(((ld_r < 0) & (ld_r.shift(-1) > 0)).sum())
    counts["LH"] = int(((hd_r < 0) & (hd_r.shift(-1) > 0)).sum())
    counts["HL"] = int(((ld_r > 0) & (ld_r.shift(-1) < 0)).sum())
    return counts


# ── Public API ────────────────────────────────────────────────────────────────

_BULLISH_CONFIRMS = frozenset({
    "Inverse Head and Shoulder", "Double Bottom",
    "Ascending Triangle", "Wedge Down", "Channel Up",
})
_BEARISH_WARNS = frozenset({
    "Head and Shoulder", "Double Top",
    "Descending Triangle", "Wedge Up", "Channel Down",
})


def detect_all_patterns(
    closes: "pd.Series",
    highs: "pd.Series",
    lows: "pd.Series",
    window: int = 5,
) -> dict:
    """
    Run all pattern detectors on the last window*6 bars.

    Args:
        closes, highs, lows: aligned price Series
        window: rolling window for detection (default 5)

    Returns:
        dict with pattern labels and pivot counts
    """
    n = window * 6
    df = pd.DataFrame({
        "Close": closes,
        "High": highs,
        "Low": lows,
    }).tail(n)

    if len(df) < window + 2:
        return {
            "head_shoulder": "", "double_pattern": "", "wedge": "",
            "triangle": "", "channel": "", "pivot_structure": "unknown",
            "hh_count": 0, "hl_count": 0, "lh_count": 0, "ll_count": 0,
        }

    try:
        hs = _head_shoulder(df, window)
    except Exception:
        hs = ""
    try:
        dbl = _double_top_bottom(df, window)
    except Exception:
        dbl = ""
    try:
        wdg = _wedge(df, window)
    except Exception:
        wdg = ""
    try:
        tri = _triangle(df, window)
    except Exception:
        tri = ""
    try:
        ch = _channel(df, window)
    except Exception:
        ch = ""
    try:
        piv = _pivots(df)
    except Exception:
        piv = {"HH": 0, "HL": 0, "LH": 0, "LL": 0}

    bullish = piv["HH"] + piv["HL"]
    bearish = piv["LH"] + piv["LL"]
    pivot_structure = "bullish" if bullish > bearish else ("bearish" if bearish > bullish else "mixed")

    return {
        "head_shoulder": hs,
        "double_pattern": dbl,
        "wedge": wdg,
        "triangle": tri,
        "channel": ch,
        "hh_count": piv["HH"],
        "hl_count": piv["HL"],
        "lh_count": piv["LH"],
        "ll_count": piv["LL"],
        "pivot_structure": pivot_structure,
    }


def pattern_score_delta(patterns: dict) -> float:
    """
    Additive score adjustment based on detected patterns.
    Range: [-0.10, +0.10] — applied to BreakoutComponents.total.
    Bearish patterns penalized harder than bullish bonus (trap risk > missed gain).
    """
    delta = 0.0
    for key in ("head_shoulder", "double_pattern", "wedge", "triangle", "channel"):
        val = patterns.get(key, "")
        if val in _BULLISH_CONFIRMS:
            delta += 0.02
        elif val in _BEARISH_WARNS:
            delta -= 0.03
    struct = patterns.get("pivot_structure", "")
    if struct == "bullish":
        delta += 0.02
    elif struct == "bearish":
        delta -= 0.02
    return round(max(-0.10, min(0.10, delta)), 4)
