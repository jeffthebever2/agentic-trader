"""_adoption_stop must never return a non-finite protective stop. A NaN price or
NaN atr_stop slips past `price <= 0` (NaN compares False) and would leave a
managed holding effectively unprotected. Money-relevant — only tighten."""
import math

from tradingagents.portfolio.holdings_brain import _adoption_stop as ads


def test_normal_takes_wider_stop(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_MIN_STOP_PCT", "8")
    # conv 8 → min stop distance 12.8% → floor = 87.2. atr_stop 90 is tighter →
    # the wider (lower) floor wins.
    out = ads(100.0, 8, 90.0)
    assert out == 87.2


def test_zero_atr_uses_floor(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_MIN_STOP_PCT", "8")
    assert ads(100.0, 8, 0.0) == 87.2


def test_nonpositive_price_returns_atr():
    assert ads(0.0, 8, 90.0) == 90.0
    assert ads(-5.0, 8, 90.0) == 90.0


def test_nan_inputs_never_return_nonfinite():
    for price, atr in [
        (float("nan"), 90.0),
        (float("nan"), float("nan")),
        (100.0, float("nan")),
        (float("inf"), 90.0),
        (100.0, float("inf")),
    ]:
        out = ads(price, 8, atr)
        assert math.isfinite(out)

    # NaN price + finite atr → returns the finite atr
    assert ads(float("nan"), 8, 90.0) == 90.0
    # NaN price + NaN atr → safe 0.0 (no usable stop)
    assert ads(float("nan"), 8, float("nan")) == 0.0
