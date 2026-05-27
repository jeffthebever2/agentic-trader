"""Webull real-account portfolio integration (per-user isolated)."""
import hashlib
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from tradingagents.compliance import validate_live_order

from web.auth import require_admin, require_step_up, get_current_user
from web.secure_store import is_encrypted_path, read_encrypted_json, write_encrypted_json

router = APIRouter()

# Per-user Webull session: each user's broker login is isolated by email.
# Instance objects and live tokens are cached in-process; durable metadata is encrypted.
_wb_instances: dict[str, object] = {}
_WEBULL_SESSION_PURPOSE = "webull-session-metadata"


def _wb_state_path(email: str) -> Path:
    digest = hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    return ROOT / f".webull_session_{digest}.json"


def _wb_owner_hash(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()[:12]


def _get_wb(email: str):
    try:
        from webull import webull
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Optional webull package is not installed.") from exc
    inst = _wb_instances.get(email)
    if inst is None:
        inst = webull()
        _wb_instances[email] = inst
    return inst


def _load_session(email: str) -> dict:
    path = _wb_state_path(email)
    if path.exists():
        try:
            session = read_encrypted_json(path, _WEBULL_SESSION_PURPOSE)
            if not is_encrypted_path(path):
                write_encrypted_json(path, session, _WEBULL_SESSION_PURPOSE)
            return session
        except Exception:
            pass
    return {}


def _save_session(email: str, data: dict):
    path = _wb_state_path(email)
    write_encrypted_json(path, data, _WEBULL_SESSION_PURPOSE)


def _clear_session(email: str):
    path = _wb_state_path(email)
    if path.exists():
        path.unlink()
    _wb_instances.pop(email, None)


def _is_connected(email: str) -> bool:
    wb = _get_wb(email)
    return bool(wb._access_token)


# ── Models ──────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    trading_pin: Optional[str] = None
    mfa_code: Optional[str] = None


class MfaRequest(BaseModel):
    username: str


class TradePinRequest(BaseModel):
    trading_pin: str


class PlaceOrderRequest(BaseModel):
    ticker: str
    action: str       # "BUY" or "SELL"
    order_type: str   # "MKT" or "LMT"
    qty: int
    price: Optional[float] = None
    time_in_force: str = "GTC"


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/webull/status")
async def wb_status(user: dict = Depends(get_current_user)):
    try:
        wb = _get_wb(user["email"])
    except HTTPException as exc:
        return {
            "connected": False,
            "username": "",
            "account_id": None,
            "token_expire": None,
            "error": exc.detail,
            "session_scope": "per_user",
            "session_owner_hash": _wb_owner_hash(user["email"]),
        }
    connected = bool(wb._access_token)
    session = _load_session(user["email"])
    return {
        "connected": connected,
        "username": session.get("username", ""),
        "account_id": wb._account_id or session.get("account_id"),
        "token_expire": str(wb._token_expire) if wb._token_expire else None,
        "session_file": _wb_state_path(user["email"]).exists(),
        "session_encrypted": is_encrypted_path(_wb_state_path(user["email"])),
        "session_scope": "per_user",
        "session_owner_hash": _wb_owner_hash(user["email"]),
    }


@router.post("/webull/request-mfa")
async def request_mfa(req: MfaRequest, admin: dict = Depends(require_admin)):
    """Send MFA code to user's registered email/phone."""
    wb = _get_wb(admin["email"])
    try:
        result = wb.get_mfa(req.username)
        return {"success": True, "detail": "MFA code sent", "result": str(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webull/login")
async def wb_login(req: LoginRequest, admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    try:
        if req.mfa_code:
            result = wb.login(
                username=req.username,
                password=req.password,
                device_name="TradingAgents",
                mfa=req.mfa_code,
                save_token=False,
            )
        else:
            result = wb.login(
                username=req.username,
                password=req.password,
                device_name="TradingAgents",
                save_token=False,
            )

        if not wb._access_token:
            raise HTTPException(status_code=401, detail="Login failed — no access token returned. MFA may be required.")

        session = {"username": req.username, "account_id": wb._account_id}
        _save_session(admin["email"], session)

        # Get trade token if PIN provided
        trade_token_ok = False
        if req.trading_pin:
            try:
                wb.get_trade_token(req.trading_pin)
                trade_token_ok = bool(wb._trade_token)
            except Exception:
                pass

        return {
            "success": True,
            "account_id": wb._account_id,
            "trade_token_ok": trade_token_ok,
            "token_expire": str(wb._token_expire),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webull/trade-pin")
async def wb_trade_pin(req: TradePinRequest, admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        wb.get_trade_token(req.trading_pin)
        return {"success": True, "trade_token_ok": bool(wb._trade_token)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webull/logout")
async def wb_logout(admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    try:
        wb.logout()
    except Exception:
        pass
    _clear_session(admin["email"])
    return {"success": True}


@router.post("/webull/refresh")
async def wb_refresh(admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        wb.refresh_login()
        return {"success": True, "token_expire": str(wb._token_expire)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/webull/account")
async def wb_account(user: dict = Depends(get_current_user)):
    wb = _get_wb(user["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not connected to Webull")
    try:
        acct = wb.get_account()
        if not acct:
            raise HTTPException(status_code=502, detail="Empty account response from Webull")
        return {"account": acct}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/webull/positions")
async def wb_positions(user: dict = Depends(get_current_user)):
    wb = _get_wb(user["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not connected to Webull")
    try:
        positions = wb.get_positions() or []
        enriched = []
        for p in positions:
            ticker = p.get("ticker", {})
            symbol = ticker.get("symbol") or p.get("symbol", "")
            qty = float(p.get("position", 0))
            cost = float(p.get("costPrice", 0))
            last = float(p.get("lastPrice", 0) or p.get("marketPrice", 0))
            market_val = qty * last
            cost_basis = qty * cost
            pnl = market_val - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
            enriched.append({
                "symbol": symbol,
                "qty": qty,
                "cost_price": cost,
                "last_price": last,
                "market_value": round(market_val, 2),
                "cost_basis": round(cost_basis, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "today_pnl": float(p.get("unrealizedProfitLoss", 0) or 0),
                "ticker_id": ticker.get("tickerId"),
                "raw": p,
            })
        enriched.sort(key=lambda x: abs(x["market_value"]), reverse=True)
        return {"positions": enriched}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/webull/orders")
async def wb_orders(status: str = "Working", user: dict = Depends(get_current_user)):
    """status: Working | Filled | Cancelled | All"""
    wb = _get_wb(user["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not connected to Webull")
    try:
        if status == "Working":
            orders = wb.get_current_orders() or []
        else:
            orders = wb.get_history_orders(status=status, count=50) or []
        normalized = []
        for o in orders:
            ticker = o.get("ticker", {})
            normalized.append({
                "order_id": o.get("orderId"),
                "symbol": ticker.get("symbol") or o.get("symbol", ""),
                "action": o.get("action", ""),
                "order_type": o.get("orderType", ""),
                "qty": o.get("totalQuantity", 0),
                "filled_qty": o.get("filledQuantity", 0),
                "price": o.get("lmtPrice") or o.get("auxPrice"),
                "avg_fill_price": o.get("avgFilledPrice"),
                "status": o.get("status", ""),
                "time_in_force": o.get("timeInForce", ""),
                "create_time": o.get("createTime", ""),
                "filled_time": o.get("filledTime", ""),
            })
        return {"orders": normalized}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/webull/orders")
async def wb_place_order(req: PlaceOrderRequest, admin: dict = Depends(require_step_up)):
    decision = validate_live_order(req.model_dump())
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "LIVE_TRADING_BLOCKED",
                "message": decision.reason,
                "blocked_actions": list(decision.blocked_actions),
            },
        )
    wb = _get_wb(admin["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not connected")
    if not wb._trade_token:
        raise HTTPException(status_code=403, detail="Trading PIN not unlocked — call /webull/trade-pin first")
    try:
        result = wb.place_order(
            stock=req.ticker.upper(),
            action=req.action.upper(),
            orderType=req.order_type.upper(),
            enforce=req.time_in_force,
            quant=req.qty,
            price=req.price,
        )
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/webull/orders/{order_id}")
async def wb_cancel_order(order_id: str, admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    if not wb._access_token:
        raise HTTPException(status_code=401, detail="Not connected")
    try:
        result = wb.cancel_order(order_id)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
