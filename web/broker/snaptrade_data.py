"""SnapTrade Fidelity DATA provider — read-only, dormant until vendor-verified.

SnapTrade's Fidelity integration is data-only (its FAQ: "does not offer the ability
to place trades"). This provider therefore exposes ONLY reads — accounts, balances,
positions, orders (executed history), activities — and has **no place-order method
at all**. Live execution stays on the local Playwright path.

Dormancy: gated by `SNAPTRADE_ENABLED` (default false). Until the plan's Phase 0
(vendor keys + a connected Fidelity account) is done, every method returns a clear
disabled/empty result instead of crashing. The `snaptrade` SDK is imported lazily
so the module loads even when the dependency is absent.

The `normalize_*` functions are pure and fully testable without the SDK or network.
Fidelity holdings can lag up to ~24h, so every normalized record carries a
`freshness` timestamp and a `stale` flag the read consumers must surface.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from tradingagents.brokers.capability import get_capability, is_data_only

BROKER = "fidelity"
PROVIDER = "snaptrade"

# Fidelity holdings can be delayed; older than this ⇒ mark stale.
_STALE_AFTER_SECONDS = 24 * 3600


def is_enabled() -> bool:
    return os.getenv("SNAPTRADE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _mask_account_number(num: str | None) -> str:
    s = "" if num is None else str(num).strip()
    if len(s) <= 4:
        return "••••"
    return "•" * (len(s) - 4) + s[-4:]


# ── Normalized canonical models (never expose raw SDK objects) ─────────────────

@dataclass(frozen=True)
class BrokerPosition:
    provider: str
    broker: str
    account_id: str
    symbol: str
    quantity: float
    price: float
    market_value: float
    freshness: str | None
    stale: bool
    avg_cost: float = 0.0
    cost_basis: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pct: float = 0.0
    raw_id: str | None = None


@dataclass(frozen=True)
class BrokerBalance:
    provider: str
    broker: str
    account_id: str
    cash: float
    buying_power: float
    total_value: float
    currency: str
    freshness: str | None
    stale: bool


@dataclass(frozen=True)
class BrokerOrder:
    provider: str
    broker: str
    account_id: str
    symbol: str
    side: str
    quantity: float
    status: str
    broker_order_id: str | None
    freshness: str | None


def _is_stale(freshness: str | None, now_ts: float | None) -> bool:
    if not freshness or now_ts is None:
        return True  # unknown freshness ⇒ treat as stale (fail safe for money decisions)
    try:
        import datetime as _dt
        ts = _dt.datetime.fromisoformat(freshness.replace("Z", "+00:00")).timestamp()
        return (now_ts - ts) > _STALE_AFTER_SECONDS
    except Exception:
        return True


def _extract_symbol(raw: dict) -> str:
    """SnapTrade nests the ticker deeply: position.symbol.symbol.symbol (universal
    symbol) — and each level is a dict. Drill down to the ticker string; tolerate
    string-or-dict at any level and fall back to raw_symbol."""
    s: Any = raw.get("symbol")
    for _ in range(4):
        if isinstance(s, dict):
            s = s.get("symbol") or s.get("raw_symbol") or s.get("ticker")
        else:
            break
    if not s:
        s = raw.get("universal_symbol") or raw.get("raw_symbol") or raw.get("ticker") or ""
    return str(s).upper().strip()


def _extract_currency(v: Any) -> str:
    """Currency may be a plain code ('USD') or a nested {code,name,id} dict."""
    if isinstance(v, dict):
        return str(v.get("code") or v.get("name") or "USD")
    return str(v or "USD")


def normalize_position(raw: dict, account_id: str, freshness: str | None, now_ts: float | None,
                       broker: str = BROKER) -> BrokerPosition:
    """Pure: map a SnapTrade position dict → BrokerPosition. Tolerant of missing keys."""
    sym = _extract_symbol(raw)
    try:
        qty = float(raw.get("units") if raw.get("units") is not None else raw.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        price = float(raw.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0

    def _f(*keys):
        for k in keys:
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0

    mv = round(qty * price, 2)
    avg_cost = _f("average_purchase_price", "avg_cost", "average_price")
    cost_basis = round(qty * avg_cost, 2) if avg_cost else 0.0
    # Prefer SnapTrade's own open_pnl; else derive market_value − cost_basis.
    pnl = _f("open_pnl") if raw.get("open_pnl") is not None else (round(mv - cost_basis, 2) if cost_basis else 0.0)
    pnl_pct = round(pnl / cost_basis * 100, 2) if cost_basis else 0.0

    return BrokerPosition(
        provider=PROVIDER, broker=broker, account_id=account_id,
        symbol=str(sym).upper().strip(), quantity=qty, price=price,
        market_value=mv,
        freshness=freshness, stale=_is_stale(freshness, now_ts),
        avg_cost=round(avg_cost, 4), cost_basis=cost_basis,
        unrealized_pnl=round(pnl, 2), unrealized_pct=pnl_pct,
        raw_id=str(raw.get("id")) if raw.get("id") is not None else None,
    )


def normalize_balance(raw: dict, account_id: str, freshness: str | None, now_ts: float | None,
                      broker: str = BROKER) -> BrokerBalance:
    def _f(*keys):
        for k in keys:
            v = raw.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0
    return BrokerBalance(
        provider=PROVIDER, broker=broker, account_id=account_id,
        cash=_f("cash", "cash_balance"),
        buying_power=_f("buying_power", "buyingPower"),
        total_value=_f("total_value", "total", "totalValue"),
        currency=_extract_currency(raw.get("currency")),
        freshness=freshness, stale=_is_stale(freshness, now_ts),
    )


def normalize_order(raw: dict, account_id: str, freshness: str | None, broker: str = BROKER) -> BrokerOrder:
    sym = _extract_symbol(raw)
    try:
        qty = float(raw.get("units") if raw.get("units") is not None else raw.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    return BrokerOrder(
        provider=PROVIDER, broker=BROKER, account_id=account_id,
        symbol=str(sym).upper().strip(),
        side=str(raw.get("action") or raw.get("side") or "").lower(),
        quantity=qty,
        status=str(raw.get("status") or "").upper(),
        broker_order_id=str(raw.get("brokerage_order_id") or raw.get("id") or "") or None,
        freshness=freshness,
    )


# ── Provider (dormant, capability-enforced, read-only) ─────────────────────────

@dataclass
class SnapTradeDataProvider:
    """Read-only SnapTrade data access for a given broker (default 'fidelity';
    'webull' also supported). Constructed with an optional injected `client` (for
    tests); otherwise lazily builds the real SDK client. This class only READS —
    it has NO place-order method. Trading (Webull only) lives in the separate
    SnapTradeTradingProvider, gated by capability + compliance + step-up."""
    client: Any = None
    broker: str = "any"

    def __post_init__(self):
        # Reads are broker-agnostic — SnapTrade returns all of a user's accounts
        # regardless of brokerage. `broker` is just a label/capability hint, so any
        # value is allowed here. (Trading is gated separately by capability.)
        pass

    def available(self) -> bool:
        return is_enabled()

    def capability(self) -> dict:
        cap = get_capability(self.broker, PROVIDER)
        return cap.as_dict() if cap else {}

    def _sdk(self):
        if self.client is not None:
            return self.client
        # Lazy import — module must load even when the SDK isn't installed.
        from snaptrade_client import SnapTrade  # type: ignore
        return SnapTrade(
            consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY", ""),
            client_id=os.getenv("SNAPTRADE_CLIENT_ID", ""),
        )

    def _disabled(self) -> dict:
        return {"provider": PROVIDER, "broker": self.broker, "enabled": False,
                "label": self.capability().get("label","data only"), "reason": "SNAPTRADE_ENABLED is off (vendor Phase 0 pending)"}

    def list_accounts(self, user_id: str, user_secret: str) -> dict:
        if not self.available():
            return self._disabled()
        raw = self._sdk().account_information.list_user_accounts(
            user_id=user_id, user_secret=user_secret
        )
        rows = getattr(raw, "body", raw) or []
        accounts = []
        for a in rows:
            num = a.get("number") or a.get("account_number") or ""
            institution = (a.get("institution_name")
                           or (a.get("brokerage_authorization") or {}).get("brokerage", {}).get("name")
                           if isinstance(a.get("brokerage_authorization"), dict) else a.get("institution_name")) or ""
            accounts.append({
                "provider": PROVIDER, "broker": self.broker,
                "account_id": str(a.get("id") or ""),
                "name": a.get("name") or a.get("institution_name") or "",
                "institution": str(institution),
                "account_mask": _mask_account_number(str(num)),
                "raw_id": str(a.get("id") or ""),
            })
        return {"provider": PROVIDER, "broker": self.broker, "enabled": True,
                "label": self.capability().get("label", "data only"), "accounts": accounts}

    def get_activities(self, user_id: str, user_secret: str, account_id: str) -> dict:
        if not self.available():
            return self._disabled()
        raw = self._sdk().account_information.get_account_activities(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
        rows = getattr(raw, "body", raw) or []
        return {"provider": PROVIDER, "broker": self.broker, "enabled": True,
                "label": self.capability().get("label", "data only"), "activities": list(rows)}

    def get_positions(self, user_id: str, user_secret: str, account_id: str) -> dict:
        if not self.available():
            return self._disabled()
        import time as _t
        now = _t.time()
        raw = self._sdk().account_information.get_user_account_positions(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
        rows = getattr(raw, "body", raw) or []
        freshness = None  # SnapTrade returns a sync timestamp per account; caller sets it
        return {
            "provider": PROVIDER, "broker": self.broker, "enabled": True, "label": self.capability().get("label","data only"),
            "positions": [normalize_position(r, account_id, freshness, now, self.broker).__dict__ for r in rows],
        }

    def get_balances(self, user_id: str, user_secret: str, account_id: str) -> dict:
        if not self.available():
            return self._disabled()
        import time as _t
        now = _t.time()
        raw = self._sdk().account_information.get_user_account_balance(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
        rows = getattr(raw, "body", raw) or []
        row = rows[0] if isinstance(rows, list) and rows else (rows or {})
        return {
            "provider": PROVIDER, "broker": self.broker, "enabled": True, "label": self.capability().get("label","data only"),
            "balance": normalize_balance(row, account_id, None, now, self.broker).__dict__,
        }

    def get_orders(self, user_id: str, user_secret: str, account_id: str) -> dict:
        if not self.available():
            return self._disabled()
        raw = self._sdk().account_information.get_user_account_orders(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
        rows = getattr(raw, "body", raw) or []
        return {
            "provider": PROVIDER, "broker": self.broker, "enabled": True, "label": self.capability().get("label","data only"),
            "orders": [normalize_order(r, account_id, None, self.broker).__dict__ for r in rows],
        }

    # NOTE: intentionally NO place_order / preview_order method. SnapTrade↔Fidelity
    # is data-only; execution is the local Playwright path.
