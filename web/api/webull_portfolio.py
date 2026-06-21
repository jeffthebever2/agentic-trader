"""Webull real-account portfolio integration (per-user isolated)."""
import datetime as dt
import hashlib
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from tradingagents.compliance import (
    LIVE_TRADING_HARD_BLOCKED,
    live_trading_enabled,
    validate_live_order,
)

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
    return bool(getattr(wb, "_access_token", None))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return default


def _pick_first_number(row: dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return _safe_float(value, default)
    return default


def _safe_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    norm = str(value).strip().lower()
    if norm in ("1", "true", "yes", "open", "opened", "regular"):
        return True
    if norm in ("0", "false", "no", "closed", "close"):
        return False
    return None


def _webull_quote_snapshot(wb: object, symbol: str) -> dict[str, Any]:
    """Fetch a broker-side quote snapshot using whichever method the installed
    Webull package exposes. The response shape varies by package version, so the
    parser accepts the common field names but refuses empty/non-price results."""
    quote = None
    errors: list[str] = []
    for name in ("get_quote", "get_stock_quote", "get_ticker_quote"):
        method = getattr(wb, name, None)
        if not callable(method):
            continue
        try:
            quote = method(symbol)
            break
        except TypeError:
            try:
                quote = method(stock=symbol)
                break
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if quote is None:
        detail = "; ".join(errors) if errors else "installed webull package exposes no quote method"
        raise HTTPException(status_code=502, detail=f"Unable to fetch Webull quote for {symbol}: {detail}")

    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    if not isinstance(quote, dict):
        raise HTTPException(status_code=502, detail=f"Unexpected Webull quote response for {symbol}")

    price = _pick_first_number(
        quote,
        ("pPrice", "price", "lastPrice", "close", "tradePrice", "mark", "marketValue"),
    )
    bid = _pick_first_number(quote, ("bid", "bidPrice", "bPrice"), default=0.0)
    ask = _pick_first_number(quote, ("ask", "askPrice", "aPrice"), default=0.0)
    if price <= 0:
        raise HTTPException(status_code=502, detail=f"Webull quote for {symbol} did not include a usable price")

    return {
        "quote_price": price,
        "quote_time": dt.datetime.utcnow().isoformat(),
        "quote_source": "broker",
        "bid": bid or None,
        "ask": ask or None,
        "market_open": _safe_bool(
            quote.get("market_open")
            or quote.get("marketOpen")
            or quote.get("isMarketOpen")
            or quote.get("marketStatus")
        ),
    }


# ── Models ──────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    trading_pin: Optional[str] = None
    mfa_code: Optional[str] = None

    @field_validator("username", "password")
    @classmethod
    def _required_text(cls, v: str) -> str:
        norm = v.strip()
        if not norm:
            raise ValueError("field is required")
        return norm


class MfaRequest(BaseModel):
    username: str

    @field_validator("username")
    @classmethod
    def _username_required(cls, v: str) -> str:
        norm = v.strip()
        if not norm:
            raise ValueError("username is required")
        return norm


class TradePinRequest(BaseModel):
    trading_pin: str

    @field_validator("trading_pin")
    @classmethod
    def _pin_required(cls, v: str) -> str:
        norm = v.strip()
        if not norm:
            raise ValueError("trading_pin is required")
        return norm


class PlaceOrderRequest(BaseModel):
    ticker: str
    action: str       # "BUY" or "SELL"
    order_type: str   # "MKT" or "LMT"
    qty: int
    price: Optional[float] = None
    time_in_force: str = "GTC"
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    backup_sources: list[str] = Field(default_factory=list)
    consensus_ok: Optional[bool] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    market_open: Optional[bool] = None

    # Input bounds (real money). Compliance (validate_live_order) also rejects
    # qty<=0 and market BUYs, but reject obvious garbage at the model boundary too.
    @field_validator("action")
    @classmethod
    def _action_valid(cls, v: str) -> str:
        norm = v.strip().upper()
        if norm not in ("BUY", "SELL"):
            raise ValueError("action must be BUY or SELL")
        return norm

    @field_validator("ticker")
    @classmethod
    def _ticker_valid(cls, v: str) -> str:
        norm = v.strip().upper()
        if not norm.isalpha() or len(norm) > 5:
            raise ValueError("ticker must be 1-5 letters")
        return norm

    @field_validator("order_type")
    @classmethod
    def _order_type_valid(cls, v: str) -> str:
        norm = v.strip().upper()
        if norm in ("LIMIT", "LMT"):
            return "LMT"
        if norm in ("MARKET", "MKT"):
            return "MKT"
        raise ValueError("order_type must be LMT or MKT")

    @field_validator("time_in_force")
    @classmethod
    def _tif_valid(cls, v: str) -> str:
        norm = v.strip().upper()
        if norm == "DAY":
            return "DAY"
        if norm == "GTC":
            return "GTC"
        raise ValueError("time_in_force must be DAY or GTC")

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("qty must be > 0")
        return v

    @field_validator("price")
    @classmethod
    def _price_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("price must be > 0 when provided")
        return v

    @model_validator(mode="after")
    def _limit_has_price(self) -> "PlaceOrderRequest":
        if self.order_type == "LMT" and self.price is None:
            raise ValueError("price is required for limit orders")
        return self


def _webull_compliance_order(req: PlaceOrderRequest, quote: Optional[dict[str, Any]] = None) -> dict:
    quote = quote or {}
    order_type = {
        "MKT": "Market",
        "LMT": "Limit",
    }.get(req.order_type, req.order_type)
    quote_price = _safe_float(quote.get("quote_price"), req.price or 0)
    return {
        "symbol": req.ticker,
        "action": req.action,
        "order_type": order_type,
        "quantity": req.qty,
        "limit_price": req.price or 0,
        "execute": True,
        "quote_price": quote_price,
        "quote_time": req.quote_time or quote.get("quote_time"),
        "quote_source": req.quote_source or quote.get("quote_source"),
        "backup_sources": req.backup_sources or quote.get("backup_sources", []),
        "consensus_ok": req.consensus_ok if req.consensus_ok is not None else quote.get("consensus_ok"),
        "bid": req.bid if req.bid is not None else quote.get("bid"),
        "ask": req.ask if req.ask is not None else quote.get("ask"),
        "market_open": req.market_open if req.market_open is not None else quote.get("market_open"),
    }


def _normalize_webull_account(acct: dict[str, Any], wb: object) -> dict[str, Any]:
    account = acct.get("account") if isinstance(acct.get("account"), dict) else acct
    if not isinstance(account, dict):
        account = {}
    summary = account.get("accountSummary") if isinstance(account.get("accountSummary"), dict) else {}
    data = {**account, **summary}
    return {
        "account": acct,
        "account_id": getattr(wb, "_account_id", None) or data.get("accountId") or data.get("account_id"),
        "net_liq": _pick_first_number(data, ("netLiquidation", "net_liq", "accountValue", "totalMarketValue")),
        "buying_power": _pick_first_number(data, ("buyingPower", "buying_power", "dayBuyingPower")),
        "cash": _pick_first_number(data, ("cashBalance", "cash", "settledCash")),
        "unrealized_pnl": _pick_first_number(data, ("unrealizedProfitLoss", "unrealized_pnl", "unrealizedPnl")),
        "token_expire": str(getattr(wb, "_token_expire", "")) if getattr(wb, "_token_expire", None) else None,
        "token_expires": str(getattr(wb, "_token_expire", "")) if getattr(wb, "_token_expire", None) else None,
    }


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
    connected = bool(getattr(wb, "_access_token", None))
    session = _load_session(user["email"])
    token_expire = str(getattr(wb, "_token_expire", "")) if getattr(wb, "_token_expire", None) else None
    return {
        "connected": connected,
        "username": session.get("username", ""),
        "account_id": getattr(wb, "_account_id", None) or session.get("account_id"),
        "token_expire": token_expire,
        "token_expires": token_expire,
        "has_trade_pin": bool(getattr(wb, "_trade_token", None)),
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

        if not getattr(wb, "_access_token", None):
            raise HTTPException(status_code=401, detail="Login failed — no access token returned. MFA may be required.")

        session = {"username": req.username, "account_id": getattr(wb, "_account_id", None)}
        _save_session(admin["email"], session)

        # Get trade token if PIN provided
        trade_token_ok = False
        if req.trading_pin:
            try:
                wb.get_trade_token(req.trading_pin)
                trade_token_ok = bool(getattr(wb, "_trade_token", None))
            except Exception:
                pass

        return {
            "success": True,
            "account_id": getattr(wb, "_account_id", None),
            "has_trade_pin": trade_token_ok,
            "trade_token_ok": trade_token_ok,
            "token_expire": str(getattr(wb, "_token_expire", "")),
            "token_expires": str(getattr(wb, "_token_expire", "")),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webull/trade-pin")
async def wb_trade_pin(req: TradePinRequest, admin: dict = Depends(require_admin)):
    wb = _get_wb(admin["email"])
    if not getattr(wb, "_access_token", None):
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        wb.get_trade_token(req.trading_pin)
        trade_token_ok = bool(getattr(wb, "_trade_token", None))
        return {"success": True, "trade_token_ok": trade_token_ok, "has_trade_pin": trade_token_ok}
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
    if not getattr(wb, "_access_token", None):
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        wb.refresh_login()
        token_expire = str(getattr(wb, "_token_expire", ""))
        return {"success": True, "token_expire": token_expire, "token_expires": token_expire}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/webull/account")
async def wb_account(user: dict = Depends(get_current_user)):
    wb = _get_wb(user["email"])
    if not getattr(wb, "_access_token", None):
        raise HTTPException(status_code=401, detail="Not connected to Webull")
    try:
        acct = wb.get_account()
        if not acct:
            raise HTTPException(status_code=502, detail="Empty account response from Webull")
        return _normalize_webull_account(acct, wb)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/webull/positions")
async def wb_positions(user: dict = Depends(get_current_user)):
    wb = _get_wb(user["email"])
    if not getattr(wb, "_access_token", None):
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
                "quantity": qty,
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
    if not getattr(wb, "_access_token", None):
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
                "quantity": o.get("totalQuantity", 0),
                "filled_qty": o.get("filledQuantity", 0),
                "price": o.get("lmtPrice") or o.get("auxPrice"),
                "avg_fill": o.get("avgFilledPrice"),
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
    if LIVE_TRADING_HARD_BLOCKED:
        raise HTTPException(status_code=403, detail="LIVE_TRADING_HARD_BLOCKED=True in compliance.py")
    if not live_trading_enabled():
        raise HTTPException(status_code=403, detail="LIVE_TRADING_ENABLED not set to true in .env")
    wb = _get_wb(admin["email"])
    if not getattr(wb, "_access_token", None):
        raise HTTPException(status_code=401, detail="Not connected")
    if not getattr(wb, "_trade_token", None):
        raise HTTPException(status_code=403, detail="Trading PIN not unlocked — call /webull/trade-pin first")
    quote = {}
    if not req.quote_time or not req.quote_source:
        quote = _webull_quote_snapshot(wb, req.ticker)
    decision = validate_live_order(_webull_compliance_order(req, quote))
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "LIVE_TRADING_BLOCKED",
                "message": decision.reason,
            },
        )
    try:
        result = wb.place_order(
            stock=req.ticker,
            action=req.action,
            orderType=req.order_type,
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
    if not getattr(wb, "_access_token", None):
        raise HTTPException(status_code=401, detail="Not connected")
    try:
        result = wb.cancel_order(order_id)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
