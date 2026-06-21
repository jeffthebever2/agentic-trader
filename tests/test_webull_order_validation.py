"""Input bounds on the Webull live order model. Compliance also rejects qty<=0 /
market buys, but reject obvious garbage at the model boundary too (clearer 422,
defense-in-depth on a real-money endpoint)."""
import pytest
from pydantic import ValidationError

from web.api.webull_portfolio import LoginRequest, MfaRequest, PlaceOrderRequest as Req, TradePinRequest


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
    _ok(order_type="MKT", price=None)   # market/None allowed at model level
    with pytest.raises(ValidationError):
        _ok(price=None)  # limit orders need a limit price before broker submission
    for bad in (0, -1.0):
        with pytest.raises(ValidationError):
            _ok(price=bad)


def test_ticker_order_type_and_tif_are_normalized():
    m = _ok(ticker="nvda", order_type="limit", time_in_force="day")
    assert m.ticker == "NVDA"
    assert m.order_type == "LMT"
    assert m.time_in_force == "DAY"


def test_ticker_order_type_and_tif_reject_bad_values():
    for field, value in (("ticker", "BRK.B"), ("ticker", "TOOLONG"), ("order_type", "STOP"), ("time_in_force", "IOC")):
        with pytest.raises(ValidationError):
            _ok(**{field: value})


def test_login_mfa_and_pin_requests_reject_empty_strings():
    with pytest.raises(ValidationError):
        LoginRequest(username=" ", password="x")
    with pytest.raises(ValidationError):
        LoginRequest(username="u", password="")
    with pytest.raises(ValidationError):
        MfaRequest(username=" ")
    with pytest.raises(ValidationError):
        TradePinRequest(trading_pin="")
