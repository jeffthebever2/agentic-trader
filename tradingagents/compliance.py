import os
import re
from dataclasses import dataclass
import datetime as dt
from typing import Any

# Injection-safe symbol pattern: 1-5 uppercase letters, plus an optional
# single-letter class suffix (BRK.B, BF.B). Only [A-Z] and one dot are ever
# accepted, so a validated symbol is safe to interpolate into a Playwright
# selector / page.evaluate string (no quotes, brackets, spaces, or JS).
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def valid_symbol(symbol: str) -> bool:
    """True if `symbol` is a well-formed, injection-safe equity ticker.

    Accepts plain tickers (AAPL, NVDA) and class shares (BRK.B). Rejects empty,
    lowercase-unnormalized, over-length, and anything with unsafe characters."""
    return bool(_SYMBOL_RE.match(symbol or ""))


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


# ── Layer 3: startup preflight block ─────────────────────────────────────────
# Set by the startup preflight (tradingagents/preflight.py) when the running
# CONFIGURATION is unsafe for real money — e.g. live trading armed with no stop
# watcher, or with no trusted quote provider. Individual flags are all validated;
# their dangerous COMBINATIONS were not, and every one of those combinations
# booted cleanly and reported healthy.
#
# This can only ever make live_trading_enabled() MORE restrictive — it is never
# consulted to permit anything. Default False so a process that never runs the
# preflight (tests, CLI tools) behaves exactly as before.
_PREFLIGHT_BLOCK: bool = False
_PREFLIGHT_REASON: str = ""


def block_live_trading_for_preflight(reason: str) -> None:
    """Latch live trading off because the configuration is unsafe. Idempotent."""
    global _PREFLIGHT_BLOCK, _PREFLIGHT_REASON
    _PREFLIGHT_BLOCK = True
    _PREFLIGHT_REASON = str(reason or "preflight failed")[:500]


def clear_preflight_block() -> None:
    """Lift the preflight latch. Intended for tests and for a re-run of the
    preflight after the operator has corrected the configuration."""
    global _PREFLIGHT_BLOCK, _PREFLIGHT_REASON
    _PREFLIGHT_BLOCK = False
    _PREFLIGHT_REASON = ""


def preflight_block_reason() -> str:
    return _PREFLIGHT_REASON if _PREFLIGHT_BLOCK else ""


def preflight_blocks_entries() -> bool:
    """True when the startup preflight latched NEW RISK off.

    Deliberately scoped to entries. The latch must never gate a SELL: an unsafe
    configuration is a reason to stop opening positions, never a reason to trap
    the ones you already hold. Blocking exits would also disable
    production_safety's force-flatten, which routes through the same
    compliance-gated endpoints — turning a config typo into an inability to
    reduce risk."""
    return _PREFLIGHT_BLOCK


def live_trading_enabled() -> bool:
    """Admin master toggle for live trading (default off). Read fresh each call
    so flipping it in the dashboard takes effect without a restart.

    NOT gated on the preflight latch — the exit endpoints consult this, and a
    latched config must still be able to close positions. Entry blocking is
    enforced in validate_live_order, which can see the order's side."""
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
# Order types that stay blocked even when market sells are explicitly allowed.
ALWAYS_PROHIBITED_ORDER_TYPES = ("short", "margin", "options")
FIDELITY_YOUTH_PROHIBITED_PRODUCTS = (
    "option",
    "options",
    "margin",
    "short",
    "crypto",
    "cryptocurrency",
    "bond",
    "treasury",
    "cd",
    "convertible",
    "ipo",
    "mutual_fund",
    "leveraged_etf",
    "inverse_etf",
    "leveraged_inverse_etf",
)


def market_sell_allowed() -> bool:
    """True only when ALLOW_MARKET_SELL is explicitly enabled. Market BUYS are
    never allowed; this gate is scoped to sells/exits (slippage on an exit is a
    smaller, opt-in risk than on an entry)."""
    return os.environ.get("ALLOW_MARKET_SELL", "").strip().lower() in ("1", "true", "yes", "on")


def _account_rule_profile(order: dict[str, Any]) -> str:
    """Return the broker/account rule profile for subtractive checks.

    Fidelity cash/margin no longer relies on old PDT trade counts. Fidelity Youth
    is stricter: cash-only equities, no margin/options/shorts, no penny stocks,
    no IPOs, no leveraged/inverse ETFs, and no non-equity products.
    """
    raw = (
        order.get("account_rule_profile")
        or order.get("account_profile")
        or order.get("account_type")
        or os.environ.get("FIDELITY_ACCOUNT_RULE_PROFILE")
        or ""
    )
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def _validate_fidelity_product_rules(
    order: dict[str, Any],
    symbol: str,
    action: str,
    ref_px: float,
) -> ComplianceDecision:
    profile = _account_rule_profile(order)
    broker = str(order.get("broker") or order.get("broker_name") or "").strip().lower()
    if "youth" not in profile:
        return ComplianceDecision(True, "ok")
    if broker and broker != "fidelity":
        return ComplianceDecision(True, "ok")

    product = str(
        order.get("product_type")
        or order.get("security_type")
        or order.get("asset_type")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    if product in FIDELITY_YOUTH_PROHIBITED_PRODUCTS:
        return ComplianceDecision(
            allowed=False,
            reason=f"Fidelity Youth prohibits {product.replace('_', ' ')} trading.",
        )

    if bool(order.get("leveraged_etf") or order.get("inverse_etf") or order.get("ipo")):
        return ComplianceDecision(
            allowed=False,
            reason="Fidelity Youth prohibits leveraged/inverse ETFs and IPO participation.",
        )

    # Fidelity Youth FAQ bars penny stocks valued at $5/share or less. Apply this
    # only to buys so exits can reduce risk in an already-held name.
    if action == "buy" and ref_px > 0 and ref_px <= 5.0:
        return ComplianceDecision(
            allowed=False,
            reason=f"Fidelity Youth prohibits penny stocks ($5/share or less): {symbol} at ${ref_px:.2f}.",
        )

    return ComplianceDecision(True, "ok")

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

    # Ultimate kill switch, enforced INSIDE the validator (not only at the call
    # sites) so no execution path can bypass it by forgetting the endpoint check.
    # Preview/sizing (execute=False) is still allowed so the UI can price orders.
    if LIVE_TRADING_HARD_BLOCKED and bool(order.get("execute", True)):
        return ComplianceDecision(
            allowed=False,
            reason="LIVE_TRADING_HARD_BLOCKED is set in source — all live execution is disabled.",
        )

    # Startup-preflight latch, enforced here for the same reason: an unsafe
    # CONFIGURATION (e.g. live trading armed with no stop watcher) must not
    # depend on every endpoint remembering to check. Only ever denies.
    #
    # ENTRIES ONLY. An unsafe config is a reason to stop taking on new risk, not
    # a reason to trap existing positions — blocking sells would also disable
    # force-flatten, which routes through this same validator. So a SELL is
    # always allowed through the latch and still faces every other gate below.
    if (_PREFLIGHT_BLOCK
            and bool(order.get("execute", True))
            and str(order.get("action", "")).lower() != "sell"):
        return ComplianceDecision(
            allowed=False,
            reason=(f"Startup preflight blocked new positions: {_PREFLIGHT_REASON} "
                    f"(exits remain permitted)"),
        )

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

    # Market SELL is permitted only when explicitly enabled (ALLOW_MARKET_SELL);
    # market BUY and short/margin/options are always blocked.
    is_market_sell = "market" in order_type and action == "sell" and market_sell_allowed()
    for blocked in PROHIBITED_ORDER_TYPES:
        if blocked in order_type:
            if blocked == "market" and is_market_sell and not any(
                p in order_type for p in ALWAYS_PROHIBITED_ORDER_TYPES
            ):
                continue  # allowed market sell
            return ComplianceDecision(allowed=False, reason=f"Order type '{order_type}' is prohibited.")

    if quantity <= 0:
        return ComplianceDecision(allowed=False, reason="Quantity must be > 0.")

    if not valid_symbol(symbol):
        return ComplianceDecision(allowed=False, reason=f"Symbol '{symbol}' invalid.")

    # Per-order dollar cap. Market sells have no limit price → use the trusted
    # quote price for the cap so a market exit can't exceed the $50k limit either.
    ref_px = limit_px if limit_px > 0 else float(order.get("quote_price", 0) or 0)
    product_decision = _validate_fidelity_product_rules(order, symbol, action, ref_px)
    if not product_decision.allowed:
        return product_decision

    execute = bool(order.get("execute", True))

    # For LIVE execution a reference price MUST exist, or the per-order dollar cap
    # cannot be enforced. Fail closed — a missing limit/quote price must never
    # produce an uncapped live order (previously the cap was silently skipped when
    # ref_px == 0).
    if execute and ref_px <= 0:
        return ComplianceDecision(
            allowed=False,
            reason="No reference price (limit or quote) — cannot enforce the per-order dollar cap; refusing to execute.",
        )

    if ref_px > 0 and quantity > 0:
        order_value = ref_px * quantity
        if order_value > MAX_SINGLE_ORDER_DOLLARS:
            return ComplianceDecision(
                allowed=False,
                reason=f"Order value ${order_value:,.0f} exceeds per-order cap ${MAX_SINGLE_ORDER_DOLLARS:,.0f}."
            )

    # Preview/sizing requests may omit execution quote evidence. Any request that
    # can place money-moving live orders must prove it is not based on
    # yfinance/Yahoo/fallback-only data.
    if execute:
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
