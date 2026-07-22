"""Broker capability registry — the first-class gate on what a (broker, provider)
pair is allowed to do.

The governing fact (SnapTrade's own Fidelity FAQ): **SnapTrade's Fidelity
integration does not place trades.** So the SnapTrade↔Fidelity pair is DATA-ONLY:
it may read accounts/balances/positions/orders/activities, and `place_equity_order`
is hard-`False`. Live Fidelity execution stays on the local Playwright path.

Capability must come from this pinned registry (or verified SnapTrade account
metadata), never from marketing copy. Order routing code MUST consult
`can_place_orders(...)` before ever calling a provider's place endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrokerCapability:
    broker: str            # "fidelity"
    provider: str          # "snaptrade" | "fidelity_playwright"
    read_accounts: bool = False
    read_balances: bool = False
    read_positions: bool = False
    read_transactions: bool = False
    read_orders: bool = False
    place_equity_order: bool = False
    data_delay: str = "unknown"     # e.g. "realtime", "up_to_24h_for_holdings"
    label: str = ""                 # human/UI label, e.g. "data only"

    def as_dict(self) -> dict:
        return {
            "broker": self.broker,
            "provider": self.provider,
            "read_accounts": self.read_accounts,
            "read_balances": self.read_balances,
            "read_positions": self.read_positions,
            "read_transactions": self.read_transactions,
            "read_orders": self.read_orders,
            "place_equity_order": self.place_equity_order,
            "data_delay": self.data_delay,
            "label": self.label,
        }


# Pinned registry. SnapTrade↔Fidelity = read everything, place NOTHING (data_only).
# Local Playwright = execution-only (reads exist but SnapTrade is preferred for data).
_REGISTRY: dict[tuple[str, str], BrokerCapability] = {
    ("fidelity", "snaptrade"): BrokerCapability(
        broker="fidelity",
        provider="snaptrade",
        read_accounts=True,
        read_balances=True,
        read_positions=True,
        read_transactions=True,
        read_orders=True,           # executed-only history for Fidelity
        place_equity_order=False,   # HARD: SnapTrade cannot place Fidelity trades
        data_delay="up_to_24h_for_holdings",
        label="data only",
    ),
    ("fidelity", "fidelity_playwright"): BrokerCapability(
        broker="fidelity",
        provider="fidelity_playwright",
        read_accounts=True,
        read_balances=True,
        read_positions=True,
        read_transactions=False,
        read_orders=True,
        place_equity_order=True,    # the local execution route
        data_delay="realtime",
        label="local execution",
    ),
    # SnapTrade↔Webull (US) DOES place live trades (OAuth, partnership Dec 2025) —
    # strictly better than the old unofficial read-only webull library, so this is
    # the Webull data AND execution route.
    ("webull", "snaptrade"): BrokerCapability(
        broker="webull",
        provider="snaptrade",
        read_accounts=True,
        read_balances=True,
        read_positions=True,
        read_transactions=True,
        read_orders=True,
        place_equity_order=True,    # live trading via SnapTrade /trade/place
        data_delay="realtime",
        label="data + trade",
    ),
}


def get_capability(broker: str, provider: str) -> BrokerCapability | None:
    return _REGISTRY.get((broker.lower().strip(), provider.lower().strip()))


def can_place_orders(broker: str, provider: str) -> bool:
    """True only if the pinned registry says this pair may place equity orders.
    Unknown pairs fail closed (False)."""
    cap = get_capability(broker, provider)
    return bool(cap and cap.place_equity_order)


def is_data_only(broker: str, provider: str) -> bool:
    """True if the pair can read data but must NOT place orders."""
    cap = get_capability(broker, provider)
    return bool(cap and not cap.place_equity_order)


def all_capabilities() -> list[dict]:
    return [c.as_dict() for c in _REGISTRY.values()]
