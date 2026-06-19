"""Volatility-aware stop distance. A flat % stop is too tight on a high-ATR
small-cap (shaken out) and too wide on a low-ATR mega-cap (oversized loss).
_atr_stop_pct sizes to k×ATR clamped to [floor, cap], and falls back to the flat
% when ATR is unusable so a missing ATR never changes behavior."""
import web.api.thematic_auto as t


def test_high_atr_widens_stop():
    # $50 small-cap, ATR $4 (8%): 2×ATR = 16% → clamped to cap 15% (> flat 7%)
    eff = t._atr_stop_pct(50.0, 4.0, 7.0)
    assert eff > 7.0 and eff <= 15.0


def test_low_atr_tightens_stop():
    # $400 mega-cap, ATR $4 (1%): 2×ATR = 2% → floored to 4% (< flat 7%)
    eff = t._atr_stop_pct(400.0, 4.0, 7.0)
    assert eff == 4.0


def test_mid_atr_in_range():
    # $100, ATR $3 (3%): 2×ATR = 6% → between floor and cap
    eff = t._atr_stop_pct(100.0, 3.0, 7.0)
    assert 4.0 <= eff <= 15.0 and abs(eff - 6.0) < 1e-9


def test_clamped_to_bounds():
    assert t._atr_stop_pct(10.0, 50.0, 7.0) == 15.0   # huge ATR → cap
    assert t._atr_stop_pct(1000.0, 0.5, 7.0) == 4.0   # tiny ATR → floor


def test_unusable_atr_falls_back_to_base():
    for atr in (0.0, -1.0, float("nan"), float("inf"), None):
        assert t._atr_stop_pct(100.0, atr, 7.0) == 7.0
    assert t._atr_stop_pct(0.0, 3.0, 7.0) == 7.0       # bad price → base
