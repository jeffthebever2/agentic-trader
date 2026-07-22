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


def test_fidelity_youth_rejects_penny_stock_buy():
    d = _vlo(_order(
        broker="fidelity",
        account_rule_profile="fidelity_youth",
        limit_price=5.0,
        quote_price=5.0,
    ))
    assert not d.allowed
    assert "penny" in d.reason.lower()


def test_fidelity_youth_allows_penny_stock_sell_exit():
    d = _vlo(_order(
        broker="fidelity",
        account_rule_profile="fidelity_youth",
        action="Sell",
        limit_price=5.0,
        quote_price=5.0,
    ))
    assert d.allowed, d.reason


def test_fidelity_youth_rejects_prohibited_product():
    d = _vlo(_order(
        broker="fidelity",
        account_rule_profile="fidelity_youth",
        product_type="leveraged_etf",
    ))
    assert not d.allowed
    assert "youth" in d.reason.lower()


def test_fidelity_youth_allows_regular_equity():
    d = _vlo(_order(
        broker="fidelity",
        account_rule_profile="fidelity_youth",
        product_type="equity",
    ))
    assert d.allowed, d.reason


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
    for bad in ("AA1", "A;DROP", "AA PL", "AA-PL", "<script>", "../../x", "BRK.BB", "BRK..B", ".B"):
        d = validate_live_order(_order(symbol=bad))
        assert not d.allowed, f"expected reject for {bad!r}"


def test_class_share_symbol_allowed():
    # Class shares (single-letter suffix) are valid, injection-safe tickers.
    for good in ("BRK.B", "BF.B", "AAPL", "F"):
        d = validate_live_order(_order(symbol=good))
        assert d.allowed, f"expected allow for {good!r}: {d.reason}"


def test_overlong_symbol_rejected():
    d = validate_live_order(_order(symbol="TOOLONG"))
    assert not d.allowed and "symbol" in d.reason.lower()


def test_execute_without_reference_price_fails_closed():
    """P0: a live (execute) order lacking both limit_price and quote_price must be
    REJECTED — the $50k cap cannot be enforced without a price. Previously the cap
    was silently skipped when ref_px == 0."""
    from tradingagents.compliance import validate_live_order
    order = _order(symbol="AAPL", quantity=1)
    order["execute"] = True
    order["limit_price"] = 0
    order["quote_price"] = 0
    d = validate_live_order(order)
    assert not d.allowed
    assert "reference price" in d.reason.lower()


def test_preview_without_price_still_allowed():
    """Preview/sizing (execute=False) may omit price — the cap only matters for
    real execution, so previews are not blocked by the fail-closed rule."""
    from tradingagents.compliance import validate_live_order
    order = _order(symbol="AAPL", quantity=1)
    order["execute"] = False
    order["limit_price"] = 0
    order["quote_price"] = 0
    d = validate_live_order(order)
    assert d.allowed, d.reason


def test_hard_block_enforced_inside_validator(monkeypatch):
    """P0: LIVE_TRADING_HARD_BLOCKED must be enforced INSIDE validate_live_order,
    not only at the endpoint call sites."""
    import tradingagents.compliance as C
    monkeypatch.setattr(C, "LIVE_TRADING_HARD_BLOCKED", True)
    order = _order(symbol="AAPL", quantity=1, limit_price=100)
    order["execute"] = True
    d = C.validate_live_order(order)
    assert not d.allowed
    assert "hard_block" in d.reason.lower() or "hard block" in d.reason.lower()
    # Preview still allowed even when hard-blocked (UI can price).
    order["execute"] = False
    assert C.validate_live_order(order).allowed
