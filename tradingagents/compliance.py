from dataclasses import dataclass
from typing import Any


LIVE_TRADING_HARD_BLOCKED = True

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
