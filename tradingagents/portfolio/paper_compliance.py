"""Paper trading compliance rules.

Default model follows the Fidelity cash/Fidelity Youth interpretation from
``deep-research-report.md``: no old PDT trade-count block, but strict settled-cash
discipline. A legacy PDT-like rule remains opt-in for broker transition testing.

Enforces:
- Fidelity-style settled-cash buying power (no spending unsettled sale proceeds)
- GFV prevention (no selling positions bought with unsettled funds before T+1)
- Optional legacy PDT-like day trade limits for transition scenarios
- Position size limits
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradingagents.portfolio.paper_account import PaperPortfolioAccount, PaperComplianceEvent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class PaperComplianceConfig:
    """Small-account paper compliance rules (applied to every $10k portfolio).

    ``broker_rule_profile`` documents the intended interpretation:
    - ``fidelity_cash`` / ``fidelity_youth``: no PDT count cap; settlement is king.
    - ``legacy_pdt``: opt-in 3-in-5 blocker for broker transition simulations.
    """

    initial_cash: float = 10000.0
    max_day_trades_rolling_5_days: int = 3
    enforce_pdt_like_rule: bool = False
    broker_rule_profile: str = "fidelity_cash"
    enforce_cash_settlement: bool = True
    settlement_days: int = 1
    prevent_good_faith_violations: bool = True
    paper_only: bool = True


# Module default — used when an account has no explicit compliance config.
DEFAULT_COMPLIANCE = PaperComplianceConfig(
    broker_rule_profile=os.getenv("PAPER_BROKER_RULE_PROFILE", "fidelity_cash").strip().lower()
    or "fidelity_cash",
    enforce_pdt_like_rule=_env_bool("PAPER_ENFORCE_LEGACY_PDT", False),
)

# Canonical skip reasons (prefix-matched by the UI compliance filter).
SKIP_REASONS = (
    "PDT_LIMIT_REACHED",
    "GFV_RISK_UNSETTLED_FUNDS",
    "NO_SETTLED_CASH",
    "INSUFFICIENT_CASH",
    "ML_THRESHOLD_FAILED",
    "RISK_LIMIT_FAILED",
    "DUPLICATE_TICKER",
    "MAX_POSITIONS_REACHED",
    "QUOTE_STALE",
    "INVALID_PRICE",
)


def is_business_day(date: datetime) -> bool:
    """Check if date is a business day (Mon–Fri)."""
    return date.weekday() < 5


def get_business_days_ago(days: int) -> datetime:
    """Get datetime for N business days ago."""
    now = datetime.now(ZoneInfo("US/Eastern"))
    count = 0
    while count < days:
        now -= timedelta(days=1)
        if is_business_day(now):
            count += 1
    return now


def count_day_trades_last_5_business_days(account: PaperPortfolioAccount) -> int:
    """Count day trades (buy + sell same ticker same day) in last 5 business days."""
    cutoff = get_business_days_ago(5)
    count = 0
    for trade in account.trades:
        if trade.exit_date is None:
            continue  # Still open
        try:
            exit_dt = datetime.fromisoformat(trade.exit_date)
            if exit_dt < cutoff:
                continue
            # Same day buy and sell = day trade
            if trade.entry_date.split("T")[0] == trade.exit_date.split("T")[0]:
                count += 1
        except Exception:
            pass
    return count


def check_pdt_limit(
    account: PaperPortfolioAccount,
    ticker: str,
    compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
) -> tuple[bool, str]:
    """Check if opening a new trade risks the optional legacy PDT limit.

    Fidelity's current model no longer treats trade count as the blocking rule for
    standard margin accounts, and Fidelity cash/Youth accounts are settlement-
    constrained instead of PDT-constrained. Therefore this check is off by
    default and exists only for legacy/transition broker simulations.

    Returns: (is_allowed, reason)
    """
    if not compliance.enforce_pdt_like_rule:
        return True, ""

    max_day_trades = compliance.max_day_trades_rolling_5_days
    current_count = count_day_trades_last_5_business_days(account)

    if current_count >= max_day_trades:
        return False, f"PDT_LIMIT_REACHED ({current_count}/{max_day_trades} day trades in 5 biz days)"

    return True, ""


def check_max_positions(account: PaperPortfolioAccount, ticker: str) -> tuple[bool, str]:
    """Enforce the per-portfolio max_positions cap from its config (if set)."""
    cap = getattr(account.config, "max_positions", None)
    if cap is None:
        return True, ""
    if account.open_position_count() >= cap:
        return False, f"MAX_POSITIONS_REACHED ({account.open_position_count()}/{cap} open)"
    return True, ""


def check_gfv_risk(account: PaperPortfolioAccount, ticker: str, side: str, quantity: float, price: float) -> tuple[bool, str]:
    """Check GFV (good-faith violation) risk when selling.

    GFV occurs when:
    - You sell a position that was bought with unsettled funds
    - Before settlement completes (T+1)

    Returns: (is_allowed, reason)
    """
    if side.upper() != "SELL":
        return True, ""

    now = datetime.now(ZoneInfo("US/Eastern"))
    # A GFV happens when you sell a position that was itself bought with
    # not-yet-settled proceeds, before it settles. Paper buys only ever spend
    # *settled* cash, so bought_with_unsettled is normally False and this never
    # fires — GFV is prevented by construction. The check remains as a guard.
    for pos in account.positions:
        if pos.ticker == ticker and getattr(pos, "bought_with_unsettled", False):
            try:
                entry_dt = datetime.fromisoformat(pos.entry_timestamp)
                if (now - entry_dt).total_seconds() < 86400:  # < T+1
                    return False, "GFV_RISK_UNSETTLED_FUNDS (position bought with unsettled proceeds)"
            except Exception:
                pass

    return True, ""


def check_sufficient_cash(
    account: PaperPortfolioAccount,
    side: str,
    quantity: float,
    price: float,
    compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
) -> tuple[bool, str]:
    """Check if account has enough settled cash for the trade."""
    if side.upper() == "SELL":
        # Selling: check we own the shares (position checking is elsewhere)
        return True, ""

    if not compliance.enforce_cash_settlement:
        return True, ""

    cost = quantity * price
    if account.settled_cash < cost:
        return False, f"NO_SETTLED_CASH (need ${cost:.2f}, have ${account.settled_cash:.2f})"

    return True, ""


def check_duplicate_position(account: PaperPortfolioAccount, ticker: str, side: str) -> tuple[bool, str]:
    """Check if ticker is already open in this portfolio (one per portfolio)."""
    if side.upper() == "SELL":
        return True, ""

    for pos in account.positions:
        if pos.ticker == ticker:
            return False, f"DUPLICATE_TICKER (already open: {pos.shares} shares @ ${pos.entry_price:.2f})"

    return True, ""


def check_valid_price(price: float) -> tuple[bool, str]:
    """Reject non-finite / non-positive prices."""
    import math as _m
    if price is None or not _m.isfinite(price) or price <= 0:
        return False, f"INVALID_PRICE (price={price})"
    return True, ""


def can_enter_trade(
    account: PaperPortfolioAccount,
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
) -> tuple[bool, str]:
    """Master check: can we open/close this trade?

    Returns: (is_allowed, reason_if_denied). Order matters — cheapest/most-common
    rejections first so the logged reason is the most actionable one.
    """
    account.assert_paper_only()

    ok, reason = check_valid_price(price)
    if not ok:
        return False, reason

    # Buy checks
    if side.upper() == "BUY":
        for check in (
            lambda: check_duplicate_position(account, ticker, side),
            lambda: check_max_positions(account, ticker),
            lambda: check_pdt_limit(account, ticker, compliance),
            lambda: check_sufficient_cash(account, side, quantity, price, compliance),
        ):
            allowed, reason = check()
            if not allowed:
                return False, reason

    # Sell checks
    if side.upper() == "SELL":
        if compliance.prevent_good_faith_violations:
            allowed, reason = check_gfv_risk(account, ticker, side, quantity, price)
            if not allowed:
                return False, reason

    return True, ""


def log_compliance_event(
    account: PaperPortfolioAccount,
    ticker: str,
    action: str,
    reason: str,
    details: dict | None = None,
) -> PaperComplianceEvent:
    """Log a compliance event (skipped trade, warning, etc.)."""
    now = datetime.now(ZoneInfo("US/Eastern")).isoformat()
    event = PaperComplianceEvent(
        timestamp=now,
        portfolio_id=account.portfolio_id,
        ticker=ticker,
        action=action,
        reason=reason,
        details=details or {},
    )
    account.compliance_log.append(event)
    return event


def settle_cash(account: PaperPortfolioAccount, amount: float, settlement_days: int = 1):
    """Move unsettled cash to settled (after T+1 or T+2)."""
    moved = min(amount, account.unsettled_cash)
    account.unsettled_cash -= moved
    account.settled_cash += moved
