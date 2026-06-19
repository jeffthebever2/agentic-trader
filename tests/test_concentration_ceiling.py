"""_concentration_ceiling — conviction-aware trim ceiling. Must clamp conviction
and fail safe on malformed input rather than crashing the trim decision (a bad
LLM conviction would otherwise blow up the whole holdings-brain cycle)."""
import math

from tradingagents.portfolio.holdings_brain import _concentration_ceiling as cc


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
