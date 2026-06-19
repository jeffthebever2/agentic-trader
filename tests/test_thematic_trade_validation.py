"""Input validation on the thematic paper-trade request. Negative/zero entry or
an out-of-range stop/target would invert the stop/target or produce nonsense
share counts — reject at the model boundary."""
import pytest
from pydantic import ValidationError

from web.api.thematic_portfolio import ThematicTradeIn


def _ok(**kw):
    base = {"ticker": "nvda"}
    base.update(kw)
    return ThematicTradeIn(**base)


def test_defaults_and_ticker_upper():
    m = _ok()
    assert m.ticker == "NVDA"
    assert m.stop_pct == 5.0 and m.target_pct == 10.0
    assert m.entry_price is None  # None → fetch live price


def test_entry_price_must_be_positive():
    _ok(entry_price=12.5)  # ok
    for bad in (0, -1.0):
        with pytest.raises(ValidationError):
            _ok(entry_price=bad)


def test_stop_pct_range():
    _ok(stop_pct=8.0)  # ok
    for bad in (0, -5, 100, 250):
        with pytest.raises(ValidationError):
            _ok(stop_pct=bad)


def test_target_pct_range():
    _ok(target_pct=60.0)  # ok
    for bad in (0, -10, 1001):
        with pytest.raises(ValidationError):
            _ok(target_pct=bad)
