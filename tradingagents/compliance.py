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
LIVE_TRADING_HARD_BLOCKED = True


def live_trading_enabled() -> bool:
    """Admin master toggle for live trading (default off). Read fresh each call
    so flipping it in the dashboard takes effect without a restart."""
    return os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

PROHIBITED_MARKET_ACTIONS = (
    "real broker order placement",
    "short selling",
    "margin trading",
    "options trading",
    "market orders",
    "after-hours orders",
    "orders based on material non-public information",
    "wash-sale/tax-loss harvesting automation",
    "pattern day trading automation",
)


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    reason: str
    blocked_actions: tuple[str, ...] = PROHIBITED_MARKET_ACTIONS


def validate_live_order(order: dict[str, Any] | None = None) -> ComplianceDecision:
    """Hard guard for real broker order placement.

    TradingAgents is allowed to analyze, backtest, and paper trade. It must not
    place live broker orders from this local app. Blocking the broker endpoint is
    the strongest protection against market-rule violations because all order
    details become irrelevant once live execution is disallowed.
    """
    return ComplianceDecision(
        allowed=False,
        reason=(
            "Live broker order placement is hard-disabled. This app may analyze, "
            "backtest, and paper trade only; it must not submit real market orders."
        ),
    )
