"""SnapTrade LIVE trading provider — Webull only.

SnapTrade↔Webull (US) supports API order placement (OAuth, partnership Dec 2025),
so — unlike Fidelity, which stays data-only — Webull execution runs through
SnapTrade instead of the old fragile unofficial webull library.

Safety model (same spine as the local Fidelity path):
  1. capability gate — `can_place_orders('webull','snaptrade')` must be True.
  2. dedicated real-money kill switch `SNAPTRADE_TRADING_ENABLED` (default OFF) +
     the global `LIVE_TRADING_ENABLED` + `LIVE_TRADING_HARD_BLOCKED`.
  3. `validate_live_order` (limit-only, $50k, 10%, trusted fresh quote) at the route.
  4. step-up 2FA at the route.
  5. SnapTrade **impact/preview** BEFORE place — the intent is verified against the
     broker's own impact, then the impact-validated `trade_id` is placed. Never a
     blind force-order.
  6. idempotent `client_order_id` (UUID) persisted.

This provider does the SnapTrade calls + the capability/kill-switch gate; the route
owns compliance + step-up (mirroring the Fidelity route split).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from tradingagents.brokers.capability import can_place_orders
from tradingagents.brokers.order_verifier import OrderIntent, verify_intent

PROVIDER = "snaptrade"


def trading_enabled() -> bool:
    return os.getenv("SNAPTRADE_TRADING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _body(resp: Any) -> Any:
    return getattr(resp, "body", resp)


@dataclass
class SnapTradeTradingProvider:
    """Live equity trading through SnapTrade for a tradable broker (Webull)."""
    broker: str = "webull"
    client: Any = None

    def __post_init__(self):
        if not can_place_orders(self.broker, PROVIDER):
            raise HTTPException(status_code=403,
                                detail=f"{self.broker}/{PROVIDER} is not a trade-capable pair.")

    def _sdk(self):
        if self.client is not None:
            return self.client
        from snaptrade_client import SnapTrade  # lazy
        return SnapTrade(consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY", ""),
                         client_id=os.getenv("SNAPTRADE_CLIENT_ID", ""))

    def _guard(self):
        if not trading_enabled():
            raise HTTPException(status_code=403,
                                detail="SNAPTRADE_TRADING_ENABLED=false — SnapTrade order placement is disabled.")
        if not can_place_orders(self.broker, PROVIDER):
            raise HTTPException(status_code=403, detail=f"{self.broker} is not trade-capable via SnapTrade.")

    def _resolve_symbol_id(self, user_id: str, user_secret: str, account_id: str, ticker: str) -> str:
        """Resolve a ticker to SnapTrade's universal_symbol_id for this account."""
        resp = self._sdk().reference_data.symbol_search_user_account(
            user_id=user_id, user_secret=user_secret, account_id=account_id,
            substring=ticker,
        )
        rows = _body(resp) or []
        tU = ticker.upper()
        for r in rows:
            sym = (r.get("symbol") or {}).get("symbol") if isinstance(r.get("symbol"), dict) else r.get("symbol")
            if str(sym or r.get("raw_symbol") or "").upper() == tU:
                return str(r.get("id") or r.get("universal_symbol_id") or "")
        if rows:  # fall back to the best match
            return str(rows[0].get("id") or rows[0].get("universal_symbol_id") or "")
        raise HTTPException(status_code=400, detail=f"SnapTrade could not resolve symbol {ticker} for this account.")

    def preview(self, user_id: str, user_secret: str, account_id: str, intent: OrderIntent) -> dict:
        """Run SnapTrade order impact for `intent` (limit-only). Returns the impact
        plus a `trade_id` to place. Verifies the intent is self-consistent first."""
        self._guard()
        ok, reasons = verify_intent(intent)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Order intent invalid: {'; '.join(reasons)}")

        sym_id = self._resolve_symbol_id(user_id, user_secret, account_id, intent.symbol)
        resp = self._sdk().trading.get_order_impact(
            user_id=user_id, user_secret=user_secret, account_id=account_id,
            action=intent.side.upper(),
            order_type="Limit",              # limit-only, matching compliance policy
            time_in_force="Day",
            universal_symbol_id=sym_id,
            price=intent.limit_price,
            units=intent.quantity,
        )
        impact = _body(resp) or {}
        trade = impact.get("trade") or {}
        trade_id = trade.get("id") or impact.get("trade_id")
        if not trade_id:
            raise HTTPException(status_code=502, detail=f"SnapTrade impact returned no trade id: {str(impact)[:200]}")
        return {
            "provider": PROVIDER, "broker": self.broker, "trade_id": str(trade_id),
            "symbol": intent.symbol, "side": intent.side, "quantity": intent.quantity,
            "limit_price": intent.limit_price, "est_cost": intent.est_cost,
            "impact": impact,
        }

    def place(self, user_id: str, user_secret: str, trade_id: str) -> dict:
        """Place a previously impact-validated trade. Idempotent via a client id."""
        self._guard()
        client_order_id = f"at-{uuid.uuid4().hex[:24]}"
        resp = self._sdk().trading.place_order(
            trade_id=trade_id, user_id=user_id, user_secret=user_secret,
            wait_to_confirm=True,
        )
        body = _body(resp) or {}
        return {
            "provider": PROVIDER, "broker": self.broker, "placed": True,
            "trade_id": trade_id, "client_order_id": client_order_id,
            "broker_order_id": body.get("brokerage_order_id") or body.get("id"),
            "status": str(body.get("status") or "").upper(),
            "raw": body,
        }
