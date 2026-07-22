"""Tests for the SnapTrade migration: capability gate, order-ticket verifier,
fill reconciler, and the dormant data-only provider."""
import pytest

from tradingagents.brokers import capability as cap
from tradingagents.brokers.order_verifier import (
    OrderIntent, verify_intent, verify_against_preview, verify_order_ticket,
)
from tradingagents.brokers.reconcile import reconcile_fill
from web.broker import snaptrade_data as sd


# ── Capability registry ────────────────────────────────────────────────────────

def test_snaptrade_fidelity_is_data_only():
    assert cap.is_data_only("fidelity", "snaptrade") is True
    assert cap.can_place_orders("fidelity", "snaptrade") is False
    c = cap.get_capability("fidelity", "snaptrade")
    assert c.read_positions and c.read_balances and c.read_orders
    assert c.place_equity_order is False
    assert c.label == "data only"


def test_playwright_can_place():
    assert cap.can_place_orders("fidelity", "fidelity_playwright") is True


def test_unknown_pair_fails_closed():
    assert cap.can_place_orders("fidelity", "nonesuch") is False
    assert cap.is_data_only("fidelity", "nonesuch") is False
    assert cap.get_capability("x", "y") is None


# ── Order-ticket verifier: intent consistency ──────────────────────────────────

def _intent(**kw):
    base = dict(account_mask="•••••2469", symbol="NVDA", side="buy",
                quantity=10, order_type="limit", limit_price=100.0, est_cost=1000.0)
    base.update(kw)
    return OrderIntent(**base)


def test_verify_intent_good():
    ok, reasons = verify_intent(_intent())
    assert ok, reasons


@pytest.mark.parametrize("kw,frag", [
    (dict(symbol="A;DROP"), "symbol"),
    (dict(side="short"), "side"),
    (dict(order_type="market"), "order_type"),
    (dict(quantity=0), "quantity"),
    (dict(quantity=-5), "quantity"),
    (dict(limit_price=0), "limit_price"),
    (dict(account_mask=""), "account_mask"),
    (dict(est_cost=5000.0), "est_cost"),          # 5000 vs 10*100=1000
    (dict(quantity=1000, est_cost=100000.0), "cap"),  # 1000*100=100k > 50k cap
])
def test_verify_intent_rejects(kw, frag):
    ok, reasons = verify_intent(_intent(**kw))
    assert not ok
    assert any(frag in r for r in reasons), reasons


def test_verify_intent_allows_class_share():
    ok, _ = verify_intent(_intent(symbol="BRK.B"))
    assert ok


# ── Order-ticket verifier: preview agreement ───────────────────────────────────

_GOOD_PREVIEW = "Preview order\nBuy 10 shares NVDA\nLimit price $100.00\nEstimated cost $1,000.00\nPlace Order"


def test_verify_against_preview_good():
    ok, reasons = verify_against_preview(_intent(), _GOOD_PREVIEW)
    assert ok, reasons


def test_verify_against_preview_empty_fails_closed():
    ok, reasons = verify_against_preview(_intent(), "")
    assert not ok and "empty" in reasons[0]


def test_verify_against_preview_wrong_symbol():
    ok, reasons = verify_against_preview(_intent(symbol="AMD"), _GOOD_PREVIEW)
    assert not ok
    assert any("symbol" in r for r in reasons)


def test_verify_against_preview_wrong_qty():
    ok, reasons = verify_against_preview(_intent(quantity=99, est_cost=9900.0), _GOOD_PREVIEW)
    assert not ok
    assert any("quantity" in r for r in reasons)


def test_verify_against_preview_wrong_price():
    p = "Buy 10 shares NVDA Limit price $250.00 Place Order"
    ok, reasons = verify_against_preview(_intent(), p)
    assert not ok
    assert any("limit price" in r for r in reasons)


def test_verify_order_ticket_combined():
    ok, reasons = verify_order_ticket(_intent(), _GOOD_PREVIEW)
    assert ok, reasons
    # A bad intent OR a bad preview blocks.
    ok2, _ = verify_order_ticket(_intent(symbol="AMD"), _GOOD_PREVIEW)
    assert not ok2


# ── Fill reconciler ────────────────────────────────────────────────────────────

def test_reconcile_no_data():
    r = reconcile_fill(_intent(), None)
    assert r.status == "no_data" and r.source == "none" and not r.matched


def test_reconcile_match_filled():
    orders = [{"symbol": "NVDA", "side": "buy", "quantity": 10, "status": "EXECUTED", "id": "abc123"}]
    r = reconcile_fill(_intent(), orders)
    assert r.matched and r.status == "filled" and r.broker_order_id == "abc123"
    assert r.discrepancies == []


def test_reconcile_not_found():
    orders = [{"symbol": "AMD", "side": "buy", "quantity": 10, "status": "EXECUTED"}]
    r = reconcile_fill(_intent(), orders)
    assert not r.matched and r.status == "not_found" and r.source == "snaptrade"


def test_reconcile_quantity_discrepancy():
    orders = [{"symbol": "NVDA", "side": "buy", "quantity": 7, "status": "EXECUTED"}]
    r = reconcile_fill(_intent(), orders)
    assert r.matched and any("quantity mismatch" in d for d in r.discrepancies)


# ── Dormant provider ───────────────────────────────────────────────────────────

def test_provider_dormant_by_default(monkeypatch):
    monkeypatch.delenv("SNAPTRADE_ENABLED", raising=False)
    p = sd.SnapTradeDataProvider(client=object())
    assert p.available() is False
    out = p.get_positions("u", "s", "acct")
    assert out["enabled"] is False and out["label"] == "data only"


def test_provider_has_no_place_method():
    p = sd.SnapTradeDataProvider(client=object())
    assert not hasattr(p, "place_order")
    assert not hasattr(p, "preview_order")


def test_normalize_position_and_stale():
    import time
    now = time.time()
    fresh = __import__("datetime").datetime.fromtimestamp(now).isoformat()
    old = __import__("datetime").datetime.fromtimestamp(now - 48 * 3600).isoformat()
    p_fresh = sd.normalize_position({"symbol": {"symbol": "nvda"}, "units": 3, "price": 100}, "acct", fresh, now)
    assert p_fresh.symbol == "NVDA" and p_fresh.quantity == 3 and p_fresh.market_value == 300.0
    assert p_fresh.stale is False
    p_old = sd.normalize_position({"symbol": "AMD", "units": 1, "price": 50}, "acct", old, now)
    assert p_old.stale is True
    # Unknown freshness ⇒ stale (fail safe)
    p_unk = sd.normalize_position({"symbol": "F", "units": 1, "price": 10}, "acct", None, now)
    assert p_unk.stale is True


def test_normalize_order():
    o = sd.normalize_order({"symbol": "NVDA", "action": "BUY", "units": 5,
                            "status": "EXECUTED", "brokerage_order_id": "z9"}, "acct", None)
    assert o.symbol == "NVDA" and o.side == "buy" and o.quantity == 5
    assert o.status == "EXECUTED" and o.broker_order_id == "z9"


# ── Mocked SnapTrade SDK ────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body): self.body = body


class _FakeAuth:
    def register_snap_trade_user(self, body=None, user_id=None):
        return _Resp({"userId": body["userId"], "userSecret": "secret-xyz"})
    def login_snap_trade_user(self, **kw):
        assert kw.get("connection_type") == "read"   # data-only scope enforced
        return _Resp({"redirectURI": "https://app.snaptrade.com/connect/abc"})
    def delete_snap_trade_user(self, user_id=None):
        return _Resp({"status": "deleted"})


class _FakeAcctInfo:
    def list_user_accounts(self, user_id=None, user_secret=None):
        return _Resp([{"id": "acct1", "name": "Individual", "number": "262502469"}])
    def get_user_account_positions(self, user_id=None, user_secret=None, account_id=None):
        return _Resp([{"symbol": {"symbol": "NVDA"}, "units": 10, "price": 100}])
    def get_user_account_orders(self, user_id=None, user_secret=None, account_id=None):
        return _Resp([{"symbol": "NVDA", "action": "BUY", "units": 10,
                       "status": "EXECUTED", "brokerage_order_id": "o1"}])
    def get_user_account_balance(self, user_id=None, user_secret=None, account_id=None):
        return _Resp([{"cash": 500, "buying_power": 500, "total_value": 1500}])
    def get_account_activities(self, user_id=None, user_secret=None, account_id=None):
        return _Resp([])


class _FakeConns:
    def list_brokerage_authorizations(self, user_id=None, user_secret=None):
        return _Resp([{"id": "auth1"}])
    def refresh_brokerage_authorization(self, **kw):
        return _Resp({})


class _FakeSDK:
    authentication = _FakeAuth()
    account_information = _FakeAcctInfo()
    connections = _FakeConns()


@pytest.fixture
def linked(tmp_path, monkeypatch):
    from web.broker import snaptrade_store as st
    monkeypatch.setattr(st, "_STORE_DIR", tmp_path / "snaptrade")
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    monkeypatch.setenv("SNAPTRADE_ENABLED", "true")
    monkeypatch.setenv("BROKER_SESSION_KEY", "test-passphrase-for-secure-store-xxxxxxxx")
    monkeypatch.setattr(st, "client", lambda: _FakeSDK())
    return st


def test_store_link_and_credentials(linked):
    st = linked
    email = "wt@example.com"
    assert st.is_linked(email) is False
    res = st.link_user(email)
    assert res["linked"] and res["user_id"].startswith("agentictrader_")
    assert st.is_linked(email) is True
    uid, secret = st.get_credentials(email)
    assert secret == "secret-xyz"
    # Idempotent — second link reuses, does not re-register.
    assert st.link_user(email)["already"] is True


def test_store_connect_url_is_read_only(linked):
    url = linked.connect_url("wt@example.com")
    assert url.startswith("https://app.snaptrade.com/connect/")


def test_store_secret_encrypted_at_rest(linked, tmp_path):
    st = linked
    email = "wt@example.com"
    st.link_user(email)
    raw = st._cred_path(email).read_bytes()
    assert b"secret-xyz" not in raw   # Fernet-encrypted, not plaintext


def test_store_unlink(linked):
    st = linked
    email = "wt@example.com"
    st.link_user(email)
    out = st.unlink_user(email)
    assert out["unlinked"] and out["deleted_remote"] is True
    assert st.is_linked(email) is False


def test_provider_enabled_reads(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_ENABLED", "true")
    p = sd.SnapTradeDataProvider(client=_FakeSDK())
    accts = p.list_accounts("u", "s")
    assert accts["enabled"] and accts["accounts"][0]["account_mask"].endswith("2469")
    pos = p.get_positions("u", "s", "acct1")
    assert pos["positions"][0]["symbol"] == "NVDA" and pos["positions"][0]["market_value"] == 1000.0
    orders = p.get_orders("u", "s", "acct1")
    assert orders["orders"][0]["side"] == "buy" and orders["orders"][0]["status"] == "EXECUTED"


def test_reconcile_wiring_matches_snaptrade(linked, monkeypatch):
    """The fidelity._reconcile_fill helper matches a submitted intent against
    SnapTrade executed orders when SnapTrade is enabled + linked."""
    import web.api.fidelity as fid
    from tradingagents.brokers.order_verifier import OrderIntent
    linked.link_user("wt@example.com")
    monkeypatch.setattr(fid, "_snaptrade_recent_orders",
                        lambda email: [{"symbol": "NVDA", "side": "buy", "quantity": 10,
                                        "status": "EXECUTED", "id": "o1"}])
    intent = OrderIntent(account_mask="•••••2469", symbol="NVDA", side="buy",
                         quantity=10, order_type="limit", limit_price=100.0, est_cost=1000.0)
    r = fid._reconcile_fill("wt@example.com", intent)
    assert r["matched"] and r["status"] == "filled" and r["source"] == "snaptrade"


# ── Graceful degradation (no/invalid key must never break the app) ─────────────

def test_verify_credentials_no_keys(monkeypatch):
    from web.broker import snaptrade_store as st
    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
    ok, reason = st.verify_credentials(force=True)
    assert ok is False and "keys not set" in reason


def test_effective_available_false_when_disabled(monkeypatch):
    from web.broker import snaptrade_store as st
    monkeypatch.setenv("SNAPTRADE_ENABLED", "false")
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "x")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "y")
    assert st.effective_available() is False


def test_verify_credentials_invalid_key_degrades(monkeypatch):
    """An invalid key returns (False, reason) — never raises — so the app falls back
    to local Fidelity data."""
    from web.broker import snaptrade_store as st

    class _Boom:
        class authentication:
            @staticmethod
            def list_snap_trade_users():
                e = Exception("bad")
                e.status = 401
                e.body = {"detail": "Invalid clientId"}
                raise e
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "bad")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "bad")
    monkeypatch.setattr(st, "client", lambda: _Boom())
    ok, reason = st.verify_credentials(force=True)
    assert ok is False and "401" in reason


def test_recent_orders_safe_when_disabled(monkeypatch):
    """fidelity._snaptrade_recent_orders returns [] (never raises) when SnapTrade
    is off — the local execution path is unaffected."""
    import web.api.fidelity as fid
    monkeypatch.setenv("SNAPTRADE_ENABLED", "false")
    assert fid._snaptrade_recent_orders("anyone@example.com") == []


# ── Webull capability + SnapTrade trading provider ─────────────────────────────

def test_webull_is_tradable_fidelity_is_not():
    assert cap.can_place_orders("webull", "snaptrade") is True
    assert cap.is_data_only("webull", "snaptrade") is False
    assert cap.can_place_orders("fidelity", "snaptrade") is False
    c = cap.get_capability("webull", "snaptrade")
    assert c.place_equity_order and c.label == "data + trade"


class _FakeRef:
    def symbol_search_user_account(self, user_id=None, user_secret=None, account_id=None, substring=None):
        return _Resp([{"id": "usym_NVDA", "symbol": {"symbol": "NVDA"}}])


class _FakeTrading:
    def get_order_impact(self, **kw):
        assert kw["order_type"] == "Limit"          # limit-only enforced
        return _Resp({"trade": {"id": "trade_123"}, "trade_impact": {"estimated_cost": 1000}})
    def place_order(self, trade_id=None, user_id=None, user_secret=None, wait_to_confirm=None):
        return _Resp({"brokerage_order_id": "wb_o1", "status": "EXECUTED"})


class _FakeTradeSDK(_FakeSDK):
    reference_data = _FakeRef()
    trading = _FakeTrading()


def _tp(monkeypatch, enabled=True):
    from web.broker import snaptrade_trading as tr
    monkeypatch.setenv("SNAPTRADE_TRADING_ENABLED", "true" if enabled else "false")
    return tr.SnapTradeTradingProvider(broker="webull", client=_FakeTradeSDK())


def test_trading_provider_rejects_fidelity():
    from web.broker.snaptrade_trading import SnapTradeTradingProvider
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        SnapTradeTradingProvider(broker="fidelity")   # not trade-capable


def test_trading_disabled_blocks(monkeypatch):
    from fastapi import HTTPException
    from tradingagents.brokers.order_verifier import OrderIntent
    prov = _tp(monkeypatch, enabled=False)
    intent = OrderIntent(account_mask="x", symbol="NVDA", side="buy", quantity=10,
                         order_type="limit", limit_price=100.0, est_cost=1000.0)
    with pytest.raises(HTTPException) as e:
        prov.preview("u", "s", "acct", intent)
    assert e.value.status_code == 403


def test_trading_preview_then_place(monkeypatch):
    from tradingagents.brokers.order_verifier import OrderIntent
    prov = _tp(monkeypatch, enabled=True)
    intent = OrderIntent(account_mask="x", symbol="NVDA", side="buy", quantity=10,
                         order_type="limit", limit_price=100.0, est_cost=1000.0)
    prev = prov.preview("u", "s", "acct", intent)
    assert prev["trade_id"] == "trade_123" and prev["broker"] == "webull"
    res = prov.place("u", "s", prev["trade_id"])
    assert res["placed"] and res["broker_order_id"] == "wb_o1" and res["status"] == "EXECUTED"
    assert res["client_order_id"].startswith("at-")   # idempotent id


def test_trading_preview_rejects_bad_intent(monkeypatch):
    from fastapi import HTTPException
    from tradingagents.brokers.order_verifier import OrderIntent
    prov = _tp(monkeypatch, enabled=True)
    bad = OrderIntent(account_mask="x", symbol="NVDA", side="buy", quantity=0,   # qty 0
                      order_type="limit", limit_price=100.0, est_cost=0.0)
    with pytest.raises(HTTPException):
        prov.preview("u", "s", "acct", bad)


def test_webull_data_provider(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_ENABLED", "true")
    p = sd.SnapTradeDataProvider(client=_FakeSDK(), broker="webull")
    out = p.list_accounts("u", "s")
    assert out["broker"] == "webull" and out["label"] == "data + trade"


def test_provider_allows_any_broker():
    """Reads are broker-agnostic — constructing for an arbitrary brokerage is fine."""
    p = sd.SnapTradeDataProvider(client=object(), broker="schwab")
    assert p.broker == "schwab"   # no capability assert / no raise


def test_generic_connect_omits_broker(linked):
    """connect_url(broker=None) → SnapTrade portal shows all brokers (no broker
    kwarg passed) and defaults to read scope."""
    seen = {}

    class _Auth2(_FakeAuth):
        def login_snap_trade_user(self, **kw):
            seen.update(kw)
            return _Resp({"redirectURI": "https://app.snaptrade.com/connect/generic"})

    class _SDK2(_FakeSDK):
        authentication = _Auth2()

    linked_store = linked
    import web.broker.snaptrade_store as st
    st.client = lambda: _SDK2()   # type: ignore
    url = st.connect_url("wt@example.com", broker=None)
    assert url.endswith("/generic")
    assert "broker" not in seen           # portal shows ALL brokers
    assert seen.get("connection_type") == "read"


def test_specific_webull_connect_uses_trade_scope(linked):
    seen = {}

    class _Auth3(_FakeAuth):
        def login_snap_trade_user(self, **kw):
            seen.update(kw)
            return _Resp({"redirectURI": "https://app.snaptrade.com/connect/wb"})

    class _SDK3(_FakeSDK):
        authentication = _Auth3()

    import web.broker.snaptrade_store as st
    st.client = lambda: _SDK3()   # type: ignore
    st.connect_url("wt@example.com", broker="webull")
    assert seen.get("broker") == "WEBULL"
    assert seen.get("connection_type") == "trade"   # tradable → trade scope


def test_normalize_deeply_nested_symbol():
    """SnapTrade's real shape: position.symbol.symbol.symbol (3 dict levels). The
    ticker must extract to a plain string, not the nested object (which crashed the
    portfolio render)."""
    raw = {
        "symbol": {"symbol": {"symbol": "onds", "raw_symbol": "ONDS", "description": "Ondas"}},
        "units": 50.124, "price": 7.82,
        "currency": {"code": "USD", "name": "US Dollar", "id": "x"},
    }
    p = sd.normalize_position(raw, "acct", None, None)
    assert p.symbol == "ONDS" and isinstance(p.symbol, str)
    assert p.quantity == 50.124 and p.price == 7.82


def test_extract_currency_nested():
    assert sd._extract_currency({"code": "USD", "name": "US Dollar"}) == "USD"
    assert sd._extract_currency("USD") == "USD"
    assert sd._extract_currency(None) == "USD"
