"""Post-submit fill reconciliation.

After the local Playwright path submits a Fidelity order, we confirm what actually
happened at the broker. SnapTrade provides executed-order history for Fidelity (up
to ~24h delayed), so when SnapTrade data is available we match our submitted intent
against SnapTrade's executed orders; otherwise the caller falls back to the local
Fidelity confirmation text.

Pure and deterministic — the caller supplies already-fetched SnapTrade orders.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconResult:
    matched: bool
    status: str                 # "filled" | "pending" | "not_found" | "no_data"
    broker_order_id: str | None
    discrepancies: list[str]
    source: str                 # "snaptrade" | "none"

    def as_dict(self) -> dict:
        return {
            "matched": self.matched,
            "status": self.status,
            "broker_order_id": self.broker_order_id,
            "discrepancies": self.discrepancies,
            "source": self.source,
        }


def _norm(s) -> str:
    return str(s or "").strip().upper()


def reconcile_fill(intent, snaptrade_orders: list[dict] | None) -> ReconResult:
    """Match `intent` (an OrderIntent) against SnapTrade executed orders.

    `snaptrade_orders`: normalized dicts with at least symbol, side, quantity,
    and optionally status, broker_order_id, price. None/empty ⇒ no SnapTrade data
    (dormant provider or lag) → `no_data`, caller uses local confirmation.
    """
    if not snaptrade_orders:
        return ReconResult(False, "no_data", None, [], "none")

    sym = _norm(intent.symbol)
    side = _norm(intent.side)
    qty = int(intent.quantity)

    # Prefer an exact symbol+side match; among those, the closest quantity.
    candidates = [
        o for o in snaptrade_orders
        if _norm(o.get("symbol")) == sym and _norm(o.get("side")) == side
    ]
    if not candidates:
        return ReconResult(False, "not_found", None, [f"no {side} {sym} order in SnapTrade history"], "snaptrade")

    def _qty(o) -> float:
        try:
            return float(o.get("quantity") or o.get("filled_quantity") or 0)
        except (TypeError, ValueError):
            return 0.0

    best = min(candidates, key=lambda o: abs(_qty(o) - qty))
    discrepancies: list[str] = []
    filled_qty = _qty(best)
    if abs(filled_qty - qty) > 0.5:
        discrepancies.append(f"quantity mismatch: intended {qty}, broker {filled_qty:g}")

    status_raw = _norm(best.get("status"))
    if status_raw in ("EXECUTED", "FILLED", "COMPLETE", "COMPLETED"):
        status = "filled"
    elif status_raw in ("PENDING", "OPEN", "SUBMITTED", "ACCEPTED", "QUEUED"):
        status = "pending"
    elif status_raw:
        status = status_raw.lower()
    else:
        status = "filled"  # SnapTrade Fidelity history is executed-only

    return ReconResult(
        matched=True,
        status=status,
        broker_order_id=best.get("broker_order_id") or best.get("id"),
        discrepancies=discrepancies,
        source="snaptrade",
    )
