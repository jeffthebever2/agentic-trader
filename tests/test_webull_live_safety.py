import asyncio
import datetime as dt

import pytest
from fastapi import HTTPException

from tradingagents.compliance import validate_live_order
from web.api import webull_portfolio
from web.api.webull_portfolio import PlaceOrderRequest, _webull_compliance_order


NOW = dt.datetime(2026, 6, 6, 14, 0, 0)


def _request(**overrides):
    values = {
        "ticker": "AAPL",
        "action": "BUY",
        "order_type": "LMT",
        "qty": 10,
        "price": 100.0,
        "quote_time": (NOW - dt.timedelta(seconds=1)).isoformat(),
        "quote_source": "alpaca_iex",
        "bid": 99.99,
        "ask": 100.01,
        "market_open": True,
    }
    values.update(overrides)
    return PlaceOrderRequest(**values)


@pytest.mark.unit
def test_webull_order_maps_to_shared_compliance_contract():
    order = _webull_compliance_order(_request())
    order["now"] = NOW.isoformat()

    decision = validate_live_order(order)

    assert decision.allowed
    assert order["symbol"] == "AAPL"
    assert order["quantity"] == 10
    assert order["order_type"] == "Limit"
    assert order["execute"] is True


@pytest.mark.unit
def test_webull_market_order_abbreviation_is_blocked():
    order = _webull_compliance_order(_request(order_type="MKT"))
    order["now"] = NOW.isoformat()

    decision = validate_live_order(order)

    assert not decision.allowed
    assert "prohibited" in decision.reason


@pytest.mark.unit
def test_webull_yfinance_only_execution_quote_is_blocked():
    order = _webull_compliance_order(_request(quote_source="yfinance"))
    order["now"] = NOW.isoformat()

    decision = validate_live_order(order)

    assert not decision.allowed
    assert "provider_untrusted" in decision.reason


@pytest.mark.unit
def test_webull_endpoint_hard_block_prevents_order(monkeypatch):
    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_request(), {"email": "u@example.com"}))

    assert exc.value.status_code == 403
    assert "LIVE_TRADING_HARD_BLOCKED" in str(exc.value.detail)


@pytest.mark.unit
def test_webull_endpoint_requires_live_trading_enabled(monkeypatch):
    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", False)
    monkeypatch.setattr(webull_portfolio, "live_trading_enabled", lambda: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_request(), {"email": "u@example.com"}))

    assert exc.value.status_code == 403
    assert "LIVE_TRADING_ENABLED" in str(exc.value.detail)


class _FakeWebull:
    _access_token = "access"
    _trade_token = "trade"
    _account_id = "acct"
    _token_expire = None

    def __init__(self):
        self.placed = None

    def get_quote(self, symbol):
        return {"price": "100.01", "bid": "100.00", "ask": "100.02"}

    def place_order(self, **kwargs):
        self.placed = kwargs
        return {"orderId": "order-1"}


@pytest.mark.unit
def test_webull_endpoint_enriches_missing_quote_from_broker(monkeypatch):
    fake = _FakeWebull()
    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", False)
    monkeypatch.setattr(webull_portfolio, "live_trading_enabled", lambda: True)
    monkeypatch.setattr(webull_portfolio, "_get_wb", lambda email: fake)

    req = _request(quote_time=None, quote_source=None)
    result = asyncio.run(webull_portfolio.wb_place_order(req, {"email": "u@example.com"}))

    assert result["success"] is True
    assert fake.placed == {
        "stock": "AAPL",
        "action": "BUY",
        "orderType": "LMT",
        "enforce": "GTC",
        "quant": 10,
        "price": 100.0,
    }


@pytest.mark.unit
def test_webull_endpoint_blocks_when_broker_quote_unusable(monkeypatch):
    fake = _FakeWebull()
    fake.get_quote = lambda symbol: {"price": 0}
    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", False)
    monkeypatch.setattr(webull_portfolio, "live_trading_enabled", lambda: True)
    monkeypatch.setattr(webull_portfolio, "_get_wb", lambda email: fake)

    req = _request(quote_time=None, quote_source=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(req, {"email": "u@example.com"}))

    assert exc.value.status_code == 502
    assert fake.placed is None
