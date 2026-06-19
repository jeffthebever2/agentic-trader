"""_concentration_ceiling — conviction-aware trim ceiling. Must clamp conviction
and fail safe on malformed input rather than crashing the trim decision (a bad
LLM conviction would otherwise blow up the whole holdings-brain cycle)."""
import math

from tradingagents.portfolio.holdings_brain import (
    _concentration_ceiling as cc,
    _min_stop_distance_pct as msd,
)


def test_low_conviction_returns_base():
    assert cc(1, 10.0) == 10.0
    assert cc(5, 10.0) == 10.0


def test_high_conviction_scales_up(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_CONC_MAX_PCT", "25")
    assert cc(10, 10.0) == 25.0       # full scale at conv 10
    assert 10.0 < cc(8, 10.0) < 25.0  # monotonic between


def test_out_of_range_conviction_clamped(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_CONC_MAX_PCT", "25")
    assert cc(99, 10.0) == cc(10, 10.0)
    assert cc(0, 10.0) == cc(1, 10.0)
    assert cc(-3, 10.0) == cc(1, 10.0)


def test_malformed_conviction_fails_safe():
    # None / NaN / non-numeric must not raise — fall back to base (conv 1).
    for bad in (None, float("nan"), "n/a", object()):
        out = cc(bad, 10.0)
        assert out == 10.0


def test_malformed_base_cap_fails_safe():
    # None / inf / NaN base_cap must yield a finite, non-negative ceiling.
    for bad in (None, float("inf"), float("nan")):
        out = cc(8, bad)
        assert math.isfinite(out) and out >= 0


# ── _min_stop_distance_pct — same conviction-coercion contract ───────────────
def test_min_stop_distance_scales_with_conviction(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_MIN_STOP_PCT", "8")
    assert msd(5) == 8.0          # base at conv ≤ 5
    assert msd(10) == 16.0        # base + 8 at conv 10
    assert msd(1) == 8.0


def test_min_stop_distance_clamps_and_fails_safe(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_MIN_STOP_PCT", "8")
    assert msd(99) == msd(10)
    # malformed conviction must not raise — falls back to base (conv 1)
    for bad in (None, float("nan"), "n/a", object()):
        out = msd(bad)
        assert math.isfinite(out) and out == 8.0
