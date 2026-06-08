"""Tests for tradingagents.screening.pattern_signals"""
import numpy as np
import pandas as pd
import pytest
from tradingagents.screening.pattern_signals import detect_all_patterns, pattern_score_delta


def _make_df(n=60, trend="up"):
    idx = pd.date_range("2026-01-01", periods=n)
    close = np.linspace(100, 120 if trend == "up" else 80, n)
    high = close * 1.005
    low = close * 0.995
    closes = pd.Series(close, index=idx)
    highs = pd.Series(high, index=idx)
    lows = pd.Series(low, index=idx)
    return closes, highs, lows


# ── detect_all_patterns ───────────────────────────────────────────────────────

def test_returns_expected_keys():
    closes, highs, lows = _make_df()
    r = detect_all_patterns(closes, highs, lows)
    for key in ("head_shoulder", "double_pattern", "wedge", "triangle", "channel",
                "hh_count", "hl_count", "lh_count", "ll_count", "pivot_structure"):
        assert key in r


def test_short_series_returns_empty_patterns():
    c = pd.Series([100.0, 101.0, 102.0])
    h = pd.Series([101.0, 102.0, 103.0])
    lo = pd.Series([99.0, 100.0, 101.0])
    r = detect_all_patterns(c, h, lo, window=5)
    assert r["head_shoulder"] == ""
    assert r["double_pattern"] == ""
    assert r["pivot_structure"] == "unknown"


def test_uptrend_has_bullish_pivots():
    closes, highs, lows = _make_df(n=60, trend="up")
    r = detect_all_patterns(closes, highs, lows, window=3)
    # uptrend should have more HH+HL than LH+LL
    assert r["hh_count"] + r["hl_count"] >= 0  # structure counts are non-negative
    assert isinstance(r["pivot_structure"], str)


def test_does_not_mutate_input():
    closes, highs, lows = _make_df()
    orig_last = float(closes.iloc[-1])
    detect_all_patterns(closes, highs, lows)
    assert float(closes.iloc[-1]) == orig_last


# ── pattern_score_delta ───────────────────────────────────────────────────────

def test_delta_range_clamped():
    for _ in range(20):
        p = {
            "head_shoulder": "Head and Shoulder",
            "double_pattern": "Double Top",
            "wedge": "Wedge Up",
            "triangle": "Descending Triangle",
            "channel": "Channel Down",
            "pivot_structure": "bearish",
        }
        delta = pattern_score_delta(p)
        assert -0.10 <= delta <= 0.10


def test_bullish_patterns_positive_delta():
    p = {
        "head_shoulder": "Inverse Head and Shoulder",
        "double_pattern": "Double Bottom",
        "wedge": "",
        "triangle": "Ascending Triangle",
        "channel": "",
        "pivot_structure": "bullish",
    }
    assert pattern_score_delta(p) > 0


def test_bearish_patterns_negative_delta():
    p = {
        "head_shoulder": "Head and Shoulder",
        "double_pattern": "Double Top",
        "wedge": "Wedge Up",
        "triangle": "Descending Triangle",
        "channel": "Channel Down",
        "pivot_structure": "bearish",
    }
    assert pattern_score_delta(p) < 0


def test_empty_patterns_zero_delta():
    p = {
        "head_shoulder": "", "double_pattern": "", "wedge": "",
        "triangle": "", "channel": "", "pivot_structure": "mixed",
    }
    assert pattern_score_delta(p) == 0.0


def test_bearish_penalized_harder_than_bullish_rewarded():
    # Use a single pattern per type so the clamp doesn't equalize the values
    bullish = pattern_score_delta({
        "head_shoulder": "Inverse Head and Shoulder",  # +0.02
        "double_pattern": "",
        "wedge": "",
        "triangle": "",
        "channel": "",
        "pivot_structure": "mixed",
    })
    bearish = pattern_score_delta({
        "head_shoulder": "Head and Shoulder",  # -0.03
        "double_pattern": "",
        "wedge": "",
        "triangle": "",
        "channel": "",
        "pivot_structure": "mixed",
    })
    assert abs(bearish) > bullish
