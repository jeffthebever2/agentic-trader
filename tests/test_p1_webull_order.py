"""P1 Webull order-path stream (W1/W2/W3), webull client fully mocked — no network.

W1: broker snapshot never self-stamps quote_time; payload-derived naive-local only.
W2: execution evidence is built server-side only (client quote fields removed).
W3: per-(user,ticker,action) idempotency lock + WEBULL_PROTECTED_ACCOUNTS guard.
"""
import asyncio
import datetime as dt
import inspect

import pytest
from fastapi import HTTPException

from web.api import fidelity, webull_portfolio
from web.api.webull_portfolio import (
    PlaceOrderRequest,
    _assert_wb_account_tradeable,
    _webull_payload_quote_time,
    _webull_quote_snapshot,
)


class FakeWebull:
    _access_token = "access"
    _trade_token = "trade"
    _account_id = "acct"
    _token_expire = None

    def __init__(self):
        self.placed = None
        self.quote_calls = 0
        self.quote_payload = {"price": "100.01", "bid": "100.00", "ask": "100.02"}

    def get_quote(self, symbol):
        self.quote_calls += 1
        return self.quote_payload

    def place_order(self, **kwargs):
        self.placed = kwargs
        return {"orderId": "order-1"}


ADMIN = {"email": "u@example.com"}


def _req(**overrides):
    values = {"ticker": "AAPL", "action": "BUY", "order_type": "LMT", "qty": 10, "price": 100.0}
    values.update(overrides)
    return PlaceOrderRequest(**values)


def _gateway_quote(**overrides):
    now = dt.datetime.now()
    quote = {
        "quote_price": 100.01,
        "quote_time": (now - dt.timedelta(seconds=1)).isoformat(),
        "quote_source": "fmp",
        "backup_sources": [],
        "consensus_ok": None,
        "bid": 99.99,
        "ask": 100.01,
        "now": now.isoformat(),
        "max_quote_age_seconds": 120,
    }
    quote.update(overrides)
    return quote


def _arm(monkeypatch, fake, gateway_quote):
    """Open the master switches (flow past them is under test) and pin every
    external surface: fake broker, monkeypatched gateway, clean env."""
    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", False)
    monkeypatch.setattr(webull_portfolio, "live_trading_enabled", lambda: True)
    monkeypatch.setattr(webull_portfolio, "_get_wb", lambda email: fake)
    monkeypatch.setattr(
        webull_portfolio, "_trusted_quote_fields",
        lambda ticker: dict(gateway_quote) if gateway_quote else {},
    )
    monkeypatch.delenv("WEBULL_PROTECTED_ACCOUNTS", raising=False)
    monkeypatch.delenv("BROKER_QUOTE_MAX_AGE_SECONDS", raising=False)


@pytest.fixture(autouse=True)
def _clean_webull_locks():
    for key in [k for k in fidelity._ORDER_LOCKS if k.startswith("webull:")]:
        fidelity._ORDER_LOCKS.pop(key, None)
        fidelity._ORDER_LOCKS_META.pop(key, None)
    yield


def _spy_validate(monkeypatch):
    """Record the order dict handed to compliance while delegating to the real gate."""
    recorded = {}
    real = webull_portfolio.validate_live_order

    def spy(order):
        recorded.clear()
        recorded.update(order)
        return real(order)

    monkeypatch.setattr(webull_portfolio, "validate_live_order", spy)
    return recorded


# ── W2: server-side evidence only ──────────────────────────────────────────

@pytest.mark.unit
def test_forged_client_quote_fields_are_ignored(monkeypatch):
    fake = FakeWebull()  # payload has no timestamp → snapshot not execution-fresh
    _arm(monkeypatch, fake, gateway_quote=None)

    forged = PlaceOrderRequest(
        ticker="AAPL", action="BUY", order_type="LMT", qty=10, price=100.0,
        # pydantic extra="ignore" drops these — they must never count as evidence
        quote_time=dt.datetime.now().isoformat(),
        quote_source="fmp",
        quote_price=100.01,
        bid=99.99, ask=100.01, consensus_ok=True,
    )
    assert not hasattr(forged, "quote_time") and not hasattr(forged, "quote_source")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(forged, ADMIN))

    assert exc.value.status_code == 403
    assert "LIVE_TRADING_BLOCKED" in str(exc.value.detail)
    assert "quote_time" in str(exc.value.detail)
    assert fake.placed is None


@pytest.mark.unit
def test_execute_uses_server_side_gateway_quote(monkeypatch):
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())
    recorded = _spy_validate(monkeypatch)

    result = asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    assert result["success"] is True
    assert fake.placed == {
        "stock": "AAPL", "action": "BUY", "orderType": "LMT",
        "enforce": "GTC", "quant": 10, "price": 100.0,
    }
    assert recorded["quote_source"] == "fmp"
    assert fake.quote_calls == 0  # gateway is the source of truth — broker never asked


@pytest.mark.unit
def test_compliance_order_now_is_naive_local(monkeypatch):
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())
    recorded = _spy_validate(monkeypatch)

    asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    parsed = dt.datetime.fromisoformat(recorded["now"])
    assert parsed.tzinfo is None
    assert abs((dt.datetime.now() - parsed).total_seconds()) < 60


# ── W1: snapshot never self-stamps ──────────────────────────────────────────

@pytest.mark.unit
def test_snapshot_uses_payload_timestamp_not_self_stamp():
    fake = FakeWebull()
    payload_time = dt.datetime.now() - dt.timedelta(seconds=5)
    fake.quote_payload = {
        "price": "100.01", "bid": "100.00", "ask": "100.02",
        "tradeStamp": int(payload_time.timestamp() * 1000),  # epoch ms
    }

    snap = _webull_quote_snapshot(fake, "AAPL")

    assert snap["quote_source"] == "broker"
    parsed = dt.datetime.fromisoformat(snap["quote_time"])
    assert parsed.tzinfo is None
    assert abs((parsed - payload_time).total_seconds()) < 1.0  # payload time, not ~now
    snap_now = dt.datetime.fromisoformat(snap["now"])
    assert snap_now.tzinfo is None  # naive-local, like-for-like with quote_time
    assert 3 <= snap["max_quote_age_seconds"] <= 600


@pytest.mark.unit
def test_snapshot_without_timestamp_is_not_execution_fresh():
    fake = FakeWebull()  # price/bid/ask only, no timestamp field
    snap = _webull_quote_snapshot(fake, "AAPL")
    assert snap["quote_time"] is None

    # naive ISO with no timezone is zone-ambiguous → fail closed
    assert _webull_payload_quote_time({"tradeTime": "2026-07-05T10:00:00"}) is None


@pytest.mark.unit
def test_broker_payload_timestamp_fallback_allows_order(monkeypatch):
    fake = FakeWebull()
    fake.quote_payload = {
        "price": "100.01", "bid": "100.00", "ask": "100.02",
        "tradeStamp": int(dt.datetime.now().timestamp() * 1000),
    }
    _arm(monkeypatch, fake, gateway_quote=None)

    result = asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    assert result["success"] is True
    assert fake.placed is not None


@pytest.mark.unit
def test_stale_broker_payload_timestamp_blocks(monkeypatch):
    fake = FakeWebull()
    stale = dt.datetime.now() - dt.timedelta(minutes=30)
    fake.quote_payload = {
        "price": "100.01", "bid": "100.00", "ask": "100.02",
        "tradeStamp": int(stale.timestamp() * 1000),
    }
    _arm(monkeypatch, fake, gateway_quote=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    assert exc.value.status_code == 403
    assert "stale_quote" in str(exc.value.detail)
    assert fake.placed is None


# ── W3: idempotency lock ────────────────────────────────────────────────────

@pytest.mark.unit
def test_double_submit_blocked_by_order_lock(monkeypatch):
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())

    async def scenario():
        lock = webull_portfolio._get_order_lock("webull:u@example.com:AAPL:BUY")
        async with lock:
            with pytest.raises(HTTPException) as exc:
                await webull_portfolio.wb_place_order(_req(), ADMIN)
            assert exc.value.status_code == 429
            assert fake.placed is None
        # released → the same request now runs the full compliance chain
        result = await webull_portfolio.wb_place_order(_req(), ADMIN)
        assert result["success"] is True

    asyncio.run(scenario())


@pytest.mark.unit
def test_lock_key_scoped_per_user_and_ticker(monkeypatch):
    """One lock per (user, ticker) — across every action.

    Order locks used to live in four uncoordinated key namespaces for the same
    position, so buy/sell/exit/auto-exit took DIFFERENT locks and serialised
    nothing against each other. The dangerous pair was on the sell side: the
    armed autonomous exit executor ("auto-exit:") and a human approving the same
    proposal ("exit:") could both pass, both scrape the same share count, and
    both sell — turning a 100-share holding into 100 SHORT, invisible to
    compliance because each order is individually a valid "Sell 100".

    Keys are now canonicalised, so a second order on a ticker already in flight
    is rejected with a retryable 429. A DIFFERENT ticker is unaffected — that
    isolation is what keeps the tightening from serialising the whole book.
    """
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())

    async def scenario():
        lock = webull_portfolio._get_order_lock("webull:u@example.com:AAPL:BUY")
        async with lock:
            # Same ticker, opposite action → now serialised.
            with pytest.raises(HTTPException) as exc:
                await webull_portfolio.wb_place_order(_req(action="SELL"), ADMIN)
            assert exc.value.status_code == 429
            # Different ticker → still free to proceed.
            other = await webull_portfolio.wb_place_order(_req(ticker="MSFT"), ADMIN)
        assert other["success"] is True

    asyncio.run(scenario())


# ── W3: protected-account guard ─────────────────────────────────────────────

@pytest.mark.unit
def test_protected_account_blocks_order(monkeypatch):
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())
    monkeypatch.setenv("WEBULL_PROTECTED_ACCOUNTS", "acct123")
    fake._account_id = "acct123"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    assert exc.value.status_code == 403
    assert "WEBULL_PROTECTED_ACCOUNTS" in str(exc.value.detail)
    assert fake.placed is None


@pytest.mark.unit
def test_unknown_account_with_protected_list_blocks(monkeypatch):
    fake = FakeWebull()
    _arm(monkeypatch, fake, _gateway_quote())
    monkeypatch.setenv("WEBULL_PROTECTED_ACCOUNTS", "acct123")
    fake._account_id = None  # cannot prove the order avoids the protected account

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))

    assert exc.value.status_code == 403
    assert fake.placed is None


@pytest.mark.unit
def test_no_protected_list_is_noop(monkeypatch):
    monkeypatch.delenv("WEBULL_PROTECTED_ACCOUNTS", raising=False)
    assert _assert_wb_account_tradeable(None) is None
    assert _assert_wb_account_tradeable("anything") is None
    monkeypatch.setenv("WEBULL_PROTECTED_ACCOUNTS", "")
    assert _assert_wb_account_tradeable(None) is None


# ── kill-chain regression guards ────────────────────────────────────────────

@pytest.mark.unit
def test_step_up_dependency_present_on_order_route():
    route = next(
        r for r in webull_portfolio.router.routes
        if getattr(r, "path", "") == "/webull/orders" and "POST" in (getattr(r, "methods", None) or set())
    )

    def _dep_calls(dependant):
        out = set()
        for d in dependant.dependencies:
            out.add(d.call)
            out |= _dep_calls(d)
        return out

    # Match by module+name, not identity — test_auth_users.py reloads web.auth
    # mid-suite, which would otherwise break an `is` comparison.
    assert any(
        getattr(c, "__module__", "") == "web.auth" and getattr(c, "__name__", "") == "require_step_up"
        for c in _dep_calls(route.dependant)
    )


@pytest.mark.unit
def test_master_switch_order_preserved(monkeypatch):
    def _boom(email):
        raise AssertionError("wb accessed before master switches")
    monkeypatch.setattr(webull_portfolio, "_get_wb", _boom)

    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))
    assert exc.value.status_code == 403
    assert "LIVE_TRADING_HARD_BLOCKED" in str(exc.value.detail)

    monkeypatch.setattr(webull_portfolio, "LIVE_TRADING_HARD_BLOCKED", False)
    monkeypatch.setattr(webull_portfolio, "live_trading_enabled", lambda: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(webull_portfolio.wb_place_order(_req(), ADMIN))
    assert exc.value.status_code == 403
    assert "LIVE_TRADING_ENABLED" in str(exc.value.detail)
