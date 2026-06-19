"""Input bounds on the Webull live order model. Compliance also rejects qty<=0 /
market buys, but reject obvious garbage at the model boundary too (clearer 422,
defense-in-depth on a real-money endpoint)."""
import pytest
from pydantic import ValidationError

from web.api.webull_portfolio import PlaceOrderRequest as Req


def _ok(**kw):
    base = {"ticker": "NVDA", "action": "BUY", "order_type": "LMT", "qty": 10, "price": 100.0}
    base.update(kw)
    return Req(**base)


def test_valid_order():
    m = _ok()
    assert m.qty == 10 and m.action == "BUY"


def test_action_must_be_buy_or_sell():
    assert _ok(action="SELL").action == "SELL"
    assert _ok(action="buy ").action == "BUY"   # normalized
    for bad in ("SHORT", "hold", ""):
        with pytest.raises(ValidationError):
            _ok(action=bad)


def test_qty_must_be_positive():
    for bad in (0, -5):
        with pytest.raises(ValidationError):
            _ok(qty=bad)


def test_price_positive_or_none():
    _ok(price=None)   # market/None allowed at model level
    for bad in (0, -1.0):
        with pytest.raises(ValidationError):
            _ok(price=bad)
