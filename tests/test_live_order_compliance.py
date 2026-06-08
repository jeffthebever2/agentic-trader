import datetime as dt

from tradingagents.compliance import validate_live_order


NOW = dt.datetime(2026, 6, 6, 14, 0, 0)


def _order(**overrides):
    base = {
        "symbol": "AAPL",
        "action": "Buy",
        "order_type": "Limit",
        "quantity": 10,
        "limit_price": 100.0,
        "execute": True,
        "quote_price": 100.0,
        "quote_time": (NOW - dt.timedelta(seconds=1)).isoformat(),
        "now": NOW.isoformat(),
        "quote_source": "alpaca_iex",
        "bid": 99.99,
        "ask": 100.01,
        "market_open": True,
    }
    base.update(overrides)
    return base


def test_execute_requires_quote_time():
    decision = validate_live_order(_order(quote_time=None))
    assert not decision.allowed
    assert "quote_time" in decision.reason


def test_execute_rejects_yfinance_only_quote():
    decision = validate_live_order(_order(quote_source="yfinance", backup_sources=[]))
    assert not decision.allowed
    assert "provider_untrusted" in decision.reason


def test_execute_rejects_webull_market_abbreviation():
    decision = validate_live_order(_order(order_type="MKT"))
    assert not decision.allowed
    assert "prohibited" in decision.reason


def test_execute_rejects_stale_quote():
    decision = validate_live_order(
        _order(quote_time=(NOW - dt.timedelta(seconds=10)).isoformat())
    )
    assert not decision.allowed
    assert "stale_quote" in decision.reason


def test_execute_allows_trusted_fresh_quote():
    decision = validate_live_order(_order())
    assert decision.allowed


def test_preview_does_not_require_execution_quote():
    decision = validate_live_order({
        "symbol": "AAPL",
        "action": "Buy",
        "order_type": "Limit",
        "quantity": 10,
        "limit_price": 100.0,
        "execute": False,
    })
    assert decision.allowed


def test_yfinance_with_two_trusted_consensus_backups_can_pass():
    decision = validate_live_order(_order(
        quote_source="yfinance",
        backup_sources=["finnhub", "twelve_data"],
        consensus_ok=True,
    ))
    assert decision.allowed
