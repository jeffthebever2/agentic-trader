import os
from dataclasses import dataclass
import datetime as dt
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
PROHIBITED_MARKET_ACTIONS = PROHIBITED_ORDER_TYPES

MAX_SINGLE_ORDER_DOLLARS: float = 50_000.0    # hard cap per order
MAX_POSITION_PCT_OF_ACCOUNT: float = 10.0     # max 10% of account per position
MAX_EXECUTION_QUOTE_AGE_SECONDS: int = 3
MAX_EXECUTION_SPREAD_BPS: float = 75.0


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
      - For execute=True orders, a trusted/fresh pre-trade quote is present
    """
    if order is None:
        return ComplianceDecision(allowed=False, reason="Order dict is None — rejected.")

    action     = str(order.get("action", "")).lower()
    raw_order_type = str(order.get("order_type", "")).lower().strip()
    order_type = {
        "mkt": "market",
        "lmt": "limit",
    }.get(raw_order_type, raw_order_type)
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

    # Preview/sizing requests may omit execution quote evidence. Any request that
    # can place money-moving live orders must prove it is not based on
    # yfinance/Yahoo/fallback-only data.
    if bool(order.get("execute", True)):
        quote_decision = _validate_execution_quote(order, symbol, limit_px)
        if not quote_decision.allowed:
            return quote_decision

    return ComplianceDecision(allowed=True, reason="ok")


def _parse_quote_time(raw: Any) -> dt.datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dt.datetime):
        return raw
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc).replace(tzinfo=None)
    if isinstance(raw, str):
        try:
            return dt.datetime.fromisoformat(raw.rstrip("Z")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _validate_execution_quote(order: dict[str, Any], symbol: str, fallback_price: float) -> ComplianceDecision:
    from tradingagents.portfolio.pretrade_gate import PreTradeGate

    quote_time = _parse_quote_time(order.get("quote_time") or order.get("price_snapshot_time"))
    if quote_time is None:
        return ComplianceDecision(
            allowed=False,
            reason="Execution quote missing quote_time; live orders require a fresh trusted quote.",
        )
    now = _parse_quote_time(order.get("now")) or dt.datetime.utcnow()

    backup_sources = order.get("backup_sources") or order.get("quote_backup_sources") or []
    if isinstance(backup_sources, str):
        backup_sources = [s.strip() for s in backup_sources.split(",") if s.strip()]

    price = float(order.get("quote_price") or order.get("last_price") or fallback_price or 0)
    bid = order.get("bid")
    ask = order.get("ask")
    gate = PreTradeGate(
        max_quote_age_seconds=int(order.get("max_quote_age_seconds") or MAX_EXECUTION_QUOTE_AGE_SECONDS),
        max_spread_bps=float(order.get("max_spread_bps") or MAX_EXECUTION_SPREAD_BPS),
        require_trusted_source=True,
        require_bid_ask=bool(order.get("require_bid_ask", False)),
    )
    result = gate.check(
        ticker=symbol,
        price_snapshot_time=quote_time,
        price=price,
        bid=float(bid) if bid not in (None, "") else None,
        ask=float(ask) if ask not in (None, "") else None,
        now=now,
        quote_source=order.get("quote_source") or order.get("price_source"),
        backup_sources=backup_sources,
        consensus_ok=order.get("consensus_ok"),
        market_open=order.get("market_open"),
    )
    if not result.ok:
        return ComplianceDecision(
            allowed=False,
            reason=f"Pre-trade quote gate failed: {result.reason} {result.detail}",
        )
    return ComplianceDecision(allowed=True, reason="ok")
