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


# ── Market SELL gate (ALLOW_MARKET_SELL, sell-only) ──────────────────────────
import pytest as _pytest
from tradingagents.compliance import validate_live_order as _vlo


def test_market_sell_blocked_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_MARKET_SELL", raising=False)
    d = _vlo({"symbol": "NVDA", "action": "sell", "order_type": "Market",
              "quantity": 3, "execute": False})
    assert not d.allowed and "prohibited" in d.reason


def test_market_sell_allowed_with_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_MARKET_SELL", "true")
    d = _vlo({"symbol": "NVDA", "action": "sell", "order_type": "Market",
              "quantity": 3, "quote_price": 209.0, "execute": False})
    assert d.allowed, d.reason


def test_market_buy_always_blocked(monkeypatch):
    monkeypatch.setenv("ALLOW_MARKET_SELL", "true")
    d = _vlo({"symbol": "NVDA", "action": "buy", "order_type": "Market",
              "quantity": 3, "quote_price": 209.0, "execute": False})
    assert not d.allowed and "prohibited" in d.reason


def test_short_margin_options_still_blocked_with_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_MARKET_SELL", "true")
    for ot in ("short", "margin", "options"):
        d = _vlo({"symbol": "NVDA", "action": "sell", "order_type": ot,
                  "quantity": 3, "quote_price": 209.0, "execute": False})
        assert not d.allowed, ot


def test_market_sell_respects_dollar_cap(monkeypatch):
    monkeypatch.setenv("ALLOW_MARKET_SELL", "true")
    d = _vlo({"symbol": "NVDA", "action": "sell", "order_type": "Market",
              "quantity": 300, "quote_price": 209.0, "execute": False})
    assert not d.allowed and "cap" in d.reason


# ── Symbol validation (injection guard before Playwright fill) ───────────────
def test_valid_symbol_accepted():
    assert validate_live_order(_order(symbol="AAPL")).allowed


def test_empty_symbol_rejected():
    d = validate_live_order(_order(symbol=""))
    assert not d.allowed and "symbol" in d.reason.lower()


def test_nonalpha_symbol_rejected():
    # digits, punctuation, whitespace, injection-y payloads — all rejected before
    # the symbol could reach the broker Playwright fill.
    for bad in ("AA1", "AA.B", "A;DROP", "AA PL", "AA-PL", "<script>", "../../x"):
        d = validate_live_order(_order(symbol=bad))
        assert not d.allowed, f"expected reject for {bad!r}"


def test_overlong_symbol_rejected():
    d = validate_live_order(_order(symbol="TOOLONG"))
    assert not d.allowed and "symbol" in d.reason.lower()
