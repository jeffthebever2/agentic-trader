"""Money-sizing edge cases for thematic position sizers. These must fail closed:
malformed conviction / non-finite inputs can never inflate a position. Real
money flows through these via approve_signal — only ever tighten."""
import math

import web.api.thematic_auto as t


# ── _conviction_dollar ──────────────────────────────────────────────────────
def test_conviction_dollar_in_range():
    assert t._conviction_dollar(1000, 10) == 1500.0     # 10/10 → 1.5× (max)
    assert t._conviction_dollar(1000, 1) == 400.0       # 1/10 → 0.4× (min)
    # monotonic increasing, bounded by the 0.4×–1.5× endpoints
    mid = t._conviction_dollar(1000, 6)
    assert 400.0 < mid < 1500.0
    assert t._conviction_dollar(1000, 8) > mid


def test_conviction_dollar_clamps_out_of_range():
    # conviction above 10 must not exceed the 10/10 (1.5×) size
    assert t._conviction_dollar(1000, 15) == t._conviction_dollar(1000, 10)
    # conviction at/below 1 (incl. 0 and negative) floors at the 1/10 size
    assert t._conviction_dollar(1000, 0) == t._conviction_dollar(1000, 1)
    assert t._conviction_dollar(1000, -5) == t._conviction_dollar(1000, 1)


def test_conviction_dollar_bad_base_fails_closed():
    assert t._conviction_dollar(0, 8) == 0.0
    assert t._conviction_dollar(-100, 8) == 0.0
    assert t._conviction_dollar(float("nan"), 8) == 0.0
    assert t._conviction_dollar(float("inf"), 8) == 0.0
    assert t._conviction_dollar(None, 8) == 0.0


# ── _adaptive_dollar ────────────────────────────────────────────────────────
_HIL = {"base_position_pct": 4.0, "min_dollar": 25.0}


def test_adaptive_dollar_normal():
    d = t._adaptive_dollar(100_000, 85, 50, _HIL)
    assert d > 0 and math.isfinite(d)
    # never exceeds the 10% compliance cap
    assert d <= 100_000 * 0.10


def test_adaptive_dollar_nan_inputs_fail_closed():
    # NaN score/target must not poison the size into NaN
    d1 = t._adaptive_dollar(100_000, float("nan"), 50, _HIL)
    d2 = t._adaptive_dollar(100_000, 85, float("nan"), _HIL)
    for d in (d1, d2):
        assert math.isfinite(d)
        assert d >= _HIL["min_dollar"]
        assert d <= 100_000 * 0.10


def test_adaptive_dollar_no_account_returns_zero():
    assert t._adaptive_dollar(0, 85, 50, _HIL) == 0.0
    assert t._adaptive_dollar(float("nan"), 85, 50, _HIL) == 0.0
