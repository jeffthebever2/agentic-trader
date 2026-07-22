"""Provider-neutral broker endpoints (multi-user).

Each authenticated user connects and reads THEIR OWN broker via SnapTrade:
connect flow, accounts, balances, positions, orders, activities, refresh.

Capability split:
  * Fidelity ↔ SnapTrade = DATA-ONLY (SnapTrade can't place Fidelity trades);
    Fidelity execution stays on the local compliance-gated Playwright path.
  * Webull ↔ SnapTrade = DATA + LIVE TRADE (OAuth API). Webull execution runs
    through SnapTrade (impact→place), gated by SNAPTRADE_TRADING_ENABLED +
    validate_live_order + step-up 2FA — same kill-chain as local Fidelity.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web.auth import get_current_user, require_admin, require_step_up
from tradingagents.brokers import capability as cap
from web.broker.snaptrade_data import SnapTradeDataProvider, is_enabled
from web.broker import snaptrade_store as store

router = APIRouter(prefix="/broker", tags=["broker"])

def _norm_broker(broker: str) -> str:
    # Reads are broker-agnostic; `broker` is a label only. Default "any" returns
    # every connected account across all brokerages.
    return (broker or "any").strip().lower()


def _provider(broker: str = "any") -> SnapTradeDataProvider:
    return SnapTradeDataProvider(broker=broker)


def _require_ready(email: str) -> tuple[str, str]:
    """Enabled + keyed + credentials valid + user linked → return creds, else a
    clear 4xx so the caller falls back to the local path."""
    if not is_enabled():
        raise HTTPException(status_code=403, detail="SNAPTRADE_ENABLED is off — using local broker data.")
    if not store.keys_configured():
        raise HTTPException(status_code=400, detail="SnapTrade API keys not set.")
    ok, reason = store.verify_credentials()
    if not ok:
        raise HTTPException(status_code=400, detail=f"SnapTrade credentials invalid: {reason}")
    creds = store.get_credentials(email)
    if not creds:
        raise HTTPException(status_code=409, detail="Not linked — use /broker/connect-url and complete the portal.")
    return creds


# ── Capability / status ────────────────────────────────────────────────────────

@router.get("/capabilities")
async def get_capabilities(_user: dict = Depends(get_current_user)):
    return {"capabilities": cap.all_capabilities()}


@router.get("/status")
async def broker_status(user: dict = Depends(get_current_user)):
    enabled = is_enabled()
    keyed = store.keys_configured()
    creds_valid, creds_reason = (store.verify_credentials() if (enabled and keyed) else (False, "not enabled/keyed"))
    return {
        "snaptrade_enabled": enabled,
        "keys_configured": keyed,
        "credentials_valid": creds_valid,
        "credentials_reason": creds_reason,
        "linked": store.is_linked(user["email"]),
        "brokers": {
            "fidelity": {**(cap.get_capability("fidelity", "snaptrade").as_dict()),
                         "execution": "local Playwright (SnapTrade data only)"},
            "webull": {**(cap.get_capability("webull", "snaptrade").as_dict()),
                       "execution": "SnapTrade live (impact→place)"},
        },
    }


# Back-compat alias for the earlier Fidelity-specific status path.
@router.get("/fidelity/status")
async def fidelity_status(user: dict = Depends(get_current_user)):
    return await broker_status(user)


# ── Connection lifecycle (per-user) ────────────────────────────────────────────

class ConnectBody(BaseModel):
    # None/empty → SnapTrade portal shows ALL supported brokerages (user picks).
    broker: str | None = None
    custom_redirect: str | None = None


@router.post("/connect-url")
async def connect_url(body: ConnectBody, user: dict = Depends(get_current_user)):
    """Mint a SnapTrade connection-portal URL for the current user. No broker →
    the portal lists every SnapTrade brokerage. A specific slug pre-selects it
    (Webull → trade scope; everything else → read/data-only). Requires only being
    signed in + SnapTrade enabled — NOT trading."""
    broker = (body.broker or "").strip().lower() or None
    if not is_enabled():
        raise HTTPException(status_code=403, detail="SNAPTRADE_ENABLED is off.")
    if not store.keys_configured():
        raise HTTPException(status_code=400, detail="SnapTrade API keys not set.")
    try:
        url = store.connect_url(user["email"], broker=broker, custom_redirect=body.custom_redirect)
    except store.SnapTradeConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade connect failed: {e}")
    return {"redirect_uri": url, "broker": broker or "any",
            "scope": "trade" if (broker and cap.can_place_orders(broker, "snaptrade")) else "read"}


@router.post("/snaptrade/verify")
async def snaptrade_verify(admin: dict = Depends(require_admin)):
    if not store.keys_configured():
        return {"ok": False, "reason": "keys not set in .env"}
    ok, reason = store.verify_credentials(force=True)
    return {"ok": ok, "reason": reason, "enabled": is_enabled()}


@router.get("/connections")
async def connections(user: dict = Depends(get_current_user)):
    _require_ready(user["email"])
    try:
        return {"connections": store.list_connections(user["email"])}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade list failed: {e}")


@router.delete("/disconnect")
async def disconnect(user: dict = Depends(get_current_user)):
    return store.unlink_user(user["email"])


# ── Read data (per-user; broker=fidelity|webull) ───────────────────────────────

@router.get("/accounts")
async def accounts(broker: str = Query("any"), user: dict = Depends(get_current_user)):
    b = _norm_broker(broker)
    uid, secret = _require_ready(user["email"])
    try:
        return _provider(b).list_accounts(uid, secret)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade accounts failed: {e}")


@router.get("/accounts/{account_id}/positions")
async def positions(account_id: str, broker: str = Query("any"), user: dict = Depends(get_current_user)):
    b = _norm_broker(broker)
    uid, secret = _require_ready(user["email"])
    try:
        return _provider(b).get_positions(uid, secret, account_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade positions failed: {e}")


@router.get("/accounts/{account_id}/balances")
async def balances(account_id: str, broker: str = Query("any"), user: dict = Depends(get_current_user)):
    b = _norm_broker(broker)
    uid, secret = _require_ready(user["email"])
    try:
        return _provider(b).get_balances(uid, secret, account_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade balances failed: {e}")


@router.get("/accounts/{account_id}/orders")
async def orders(account_id: str, broker: str = Query("any"), user: dict = Depends(get_current_user)):
    b = _norm_broker(broker)
    uid, secret = _require_ready(user["email"])
    try:
        return _provider(b).get_orders(uid, secret, account_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade orders failed: {e}")


@router.post("/accounts/{account_id}/refresh")
async def refresh(account_id: str, user: dict = Depends(get_current_user)):
    uid, secret = _require_ready(user["email"])
    try:
        conns = store.list_connections(user["email"])
        auth_id = conns[0].get("id") if conns else None
        if not auth_id:
            raise HTTPException(status_code=409, detail="No SnapTrade connection to refresh.")
        store.client().connections.refresh_brokerage_authorization(
            authorization_id=auth_id, user_id=uid, user_secret=secret)
        return {"refreshed": True, "authorization_id": auth_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SnapTrade refresh failed: {e}")


# ── Webull LIVE trading via SnapTrade (compliance + step-up gated) ─────────────

class TradeBody(BaseModel):
    account_id: str
    ticker: str
    side: str            # buy | sell
    quantity: int
    limit_price: float | None = None   # None → server derives from trusted quote


def _build_compliance_order(ticker: str, side: str, qty: int, limit_price: float) -> dict:
    """Build + validate a live order dict through the SAME compliance kill-chain the
    local Fidelity path uses (trusted fresh quote, limit-only, $50k, symbol)."""
    from tradingagents.compliance import validate_live_order
    from web.api.fidelity import _trusted_quote_fields, _apply_execution_quote
    now_iso = _dt.datetime.now().isoformat(timespec="seconds")
    order = {
        "symbol": ticker, "action": side.capitalize(), "broker": "webull",
        "order_type": "Limit", "quantity": qty,
        "limit_price": limit_price, "execute": True,
        "quote_price": limit_price, "now": now_iso,
    }
    tq = _trusted_quote_fields(ticker)
    ref = _apply_execution_quote(order, tq, limit_factor=None)  # stamps trusted quote fields
    if limit_price is None:
        order["limit_price"] = round(ref * (1.002 if side.lower() == "buy" else 0.998), 2)
    decision = validate_live_order(order)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=f"Compliance: {decision.reason}")
    return order


@router.post("/webull/orders/preview")
async def webull_preview(body: TradeBody, admin: dict = Depends(require_admin)):
    """Compliance-validate + SnapTrade order-impact a Webull equity order. Returns a
    trade_id to place. No order is placed here."""
    from web.broker.snaptrade_trading import SnapTradeTradingProvider
    from tradingagents.brokers.order_verifier import OrderIntent
    uid, secret = _require_ready(admin["email"])
    ticker = body.ticker.upper().strip()
    order = _build_compliance_order(ticker, body.side, int(body.quantity), body.limit_price)
    lp = float(order["limit_price"])
    intent = OrderIntent(account_mask="snaptrade", symbol=ticker, side=body.side.lower(),
                         quantity=int(body.quantity), order_type="limit",
                         limit_price=lp, est_cost=round(int(body.quantity) * lp, 2))
    prov = SnapTradeTradingProvider(broker="webull")
    return prov.preview(uid, secret, body.account_id, intent)


class PlaceBody(BaseModel):
    trade_id: str


@router.post("/webull/orders/place")
async def webull_place(body: PlaceBody, admin: dict = Depends(require_step_up)):
    """Place a previously previewed (impact-validated) Webull trade. Real money —
    full step-up 2FA + SNAPTRADE_TRADING_ENABLED + capability gate enforced."""
    from web.broker.snaptrade_trading import SnapTradeTradingProvider
    uid, secret = _require_ready(admin["email"])
    prov = SnapTradeTradingProvider(broker="webull")
    return prov.place(uid, secret, body.trade_id)
