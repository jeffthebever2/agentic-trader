import os
from dataclasses import dataclass
from typing import Any


# Two independent layers gate real broker execution:
#   1. LIVE_TRADING_HARD_BLOCKED (this code constant) — the ultimate kill switch.
#      While True, NO real order can ever be placed, regardless of any setting.
#      This is intentionally a source-code change so it cannot be flipped from
#      the dashboard or .env.
#   2. LIVE_TRADING_ENABLED (admin .env toggle, default off) — the operational
#      master switch surfaced in the admin panel. Even after the hard block is
#      lifted in code, this must be turned on for live trading to function.
# Plus, per request, every broker endpoint already requires per-user step-up
# 2FA (require_step_up), so each user approves their own real trades.
# ── Kill switch ──────────────────────────────────────────────────────────────
# Set False to allow live trading. Still requires LIVE_TRADING_ENABLED=true in
# .env AND per-request step-up 2FA on every trade endpoint.
LIVE_TRADING_HARD_BLOCKED = False


def live_trading_enabled() -> bool:
    """Admin master toggle for live trading (default off). Read fresh each call
    so flipping it in the dashboard takes effect without a restart."""
    return os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

PROHIBITED_ORDER_TYPES = (
    "market",       # market orders — price slippage risk
    "short",        # short selling
    "margin",       # margin trading
    "options",      # options
)

MAX_SINGLE_ORDER_DOLLARS: float = 50_000.0    # hard cap per order
MAX_POSITION_PCT_OF_ACCOUNT: float = 10.0     # max 10% of account per position


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    reason: str


def validate_live_order(order: dict[str, Any] | None = None) -> ComplianceDecision:
    """Validate a live broker order against compliance rules.

    Checks:
      - Order type not in prohibited list (no market orders)
      - Action is Buy or Sell (no short/margin)
      - Dollar amount within per-order cap
      - Quantity > 0
    """
    if order is None:
        return ComplianceDecision(allowed=False, reason="Order dict is None — rejected.")

    action     = str(order.get("action", "")).lower()
    order_type = str(order.get("order_type", "")).lower()
    quantity   = float(order.get("quantity", 0) or 0)
    limit_px   = float(order.get("limit_price", 0) or 0)
    symbol     = str(order.get("symbol", "")).upper().strip()

    if action not in ("buy", "sell"):
        return ComplianceDecision(allowed=False, reason=f"Action '{action}' not allowed. Only Buy and Sell.")

    for blocked in PROHIBITED_ORDER_TYPES:
        if blocked in order_type:
            return ComplianceDecision(allowed=False, reason=f"Order type '{order_type}' is prohibited.")

    if quantity <= 0:
        return ComplianceDecision(allowed=False, reason="Quantity must be > 0.")

    if not symbol or not symbol.isalpha() or len(symbol) > 5:
        return ComplianceDecision(allowed=False, reason=f"Symbol '{symbol}' invalid.")

    if order_type == "limit" and limit_px > 0 and quantity > 0:
        order_value = limit_px * quantity
        if order_value > MAX_SINGLE_ORDER_DOLLARS:
            return ComplianceDecision(
                allowed=False,
                reason=f"Order value ${order_value:,.0f} exceeds per-order cap ${MAX_SINGLE_ORDER_DOLLARS:,.0f}."
            )

    return ComplianceDecision(allowed=True, reason="ok")
