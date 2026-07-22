"""Did the order actually fill?

The local Playwright path commits internal state when Fidelity *accepts* an
order, not when it fills. Accepted is not filled: these are DAY limit orders, so
one that never trades simply expires at 16:00 with no notification. The system
meanwhile holds a position that does not exist — it marks it to market, computes
stops and sizing against it, books a fictitious P&L when it "exits", and the
copy-trade follower records the ticker as owned and never retries it.

SnapTrade would give executed-order history, but it is dormant (data-only, keys
pending), so ``brokers/reconcile.py`` cannot answer this today. What *is* proven
and already scraped every keepalive cycle is the real **holdings** list. Holdings
are the source of truth: if we believe we bought 300 shares and the broker never
shows them, the order did not fill.

This module is pure and synchronous — the caller supplies the ledger and a
holdings snapshot. Wiring lives in ``web/api/fidelity.py``.

Design notes:
  * A pending entry records the share count held BEFORE submission, so a fill is
    detected as a *delta*, not as mere presence — otherwise adding to an existing
    position could never be verified.
  * Partial fills are first-class: a smaller-than-intended delta resolves to
    ``partial`` with the real quantity, because sizing, stops and the 10%
    concentration cap must all be computed against shares we actually own.
  * Verdicts are conservative in the direction that protects capital. While a
    fill is unconfirmed we prefer to believe we DO own the shares (so a stop can
    still be raised against them); only an expired, still-absent order is
    declared unfilled and its phantom state removed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Terminal verdicts — the pending entry can be dropped from the ledger.
STATUS_FILLED = "filled"
STATUS_PARTIAL = "partial"
STATUS_UNFILLED = "unfilled"
#: Non-terminal — keep waiting.
STATUS_PENDING = "pending"
#: Holdings unavailable; we learned nothing. Never treat as evidence.
STATUS_UNKNOWN = "unknown"

TERMINAL_STATUSES = frozenset({STATUS_FILLED, STATUS_PARTIAL, STATUS_UNFILLED})


@dataclass(frozen=True)
class PendingFill:
    """One submitted-but-unconfirmed order."""
    ticker: str
    intended_shares: float
    shares_before: float = 0.0
    submitted_at: str = ""
    side: str = "buy"
    limit_price: Optional[float] = None
    order_id: str = ""

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker, "intended_shares": self.intended_shares,
            "shares_before": self.shares_before, "submitted_at": self.submitted_at,
            "side": self.side, "limit_price": self.limit_price, "order_id": self.order_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "PendingFill":
        return PendingFill(
            ticker=str(d.get("ticker", "")).upper(),
            intended_shares=_num(d.get("intended_shares")),
            shares_before=_num(d.get("shares_before")),
            submitted_at=str(d.get("submitted_at", "")),
            side=str(d.get("side", "buy")).lower(),
            limit_price=(None if d.get("limit_price") in (None, "")
                         else _num(d.get("limit_price"))),
            order_id=str(d.get("order_id", "")),
        )


@dataclass(frozen=True)
class FillVerdict:
    ticker: str
    status: str
    filled_shares: float = 0.0
    intended_shares: float = 0.0
    reason: str = ""
    discrepancies: list = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker, "status": self.status,
            "filled_shares": self.filled_shares, "intended_shares": self.intended_shares,
            "reason": self.reason, "discrepancies": list(self.discrepancies),
        }


def _num(v: Any, default: float = 0.0) -> float:
    """Coerce to a finite float. Garbage becomes ``default`` — a NaN share count
    must never propagate into sizing or a sell quantity.

    Handles the BROKER'S string formatting. The Fidelity positions snapshot
    stores `qty` as raw scraped innerText, so a 1,500-share holding arrives as
    the string ``"1,500"``. A bare ``float()`` raises on that and the holding
    reads as 0 shares — which `classify_fill` treats as "the broker holds
    nothing", flagging a real position as a phantom. Same normalisation the
    order path already applies at two other sites.
    """
    if v is None:
        return default
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
    else:
        s = str(v).strip().replace(",", "").replace("$", "")
        # Parenthesised negatives ("(50)") and stray unicode minus.
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        s = s.replace("−", "-").strip()
        if not s:
            return default
        try:
            f = float(s)
        except (TypeError, ValueError):
            return default
        if neg:
            f = -f
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return f


_UNPARSEABLE = object()


def _num_or_none(v: Any):
    """``_num`` but returns None when the value cannot be read at all.

    A row whose share count is unreadable must be SKIPPED, not recorded as 0 —
    a spurious 0 is indistinguishable from "sold everything" and would fabricate
    a phantom."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    sentinel = -1.234567e18
    got = _num(v, sentinel)
    return None if got == sentinel else got


#: Key aliases for the ticker and the share count. The Fidelity positions
#: snapshot writes `symbol`/`qty`; the normalized Holding dataclass exposes
#: `ticker`/`shares`. Reading only one dialect silently yields an EMPTY map,
#: which `classify_fill` would then treat as positive evidence that the broker
#: holds nothing — flagging every genuinely filled order as a phantom.
_TICKER_KEYS = ("ticker", "symbol")
_SHARE_KEYS = ("shares", "qty", "quantity")


def _first_attr(obj: Any, names: tuple) -> Any:
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return None


def holdings_share_map(holdings: Iterable[Any]) -> dict[str, float]:
    """``{TICKER: shares}`` from broker holdings (objects or dicts).

    Accepts both key dialects — see ``_TICKER_KEYS``/``_SHARE_KEYS``. A row whose
    ticker cannot be read is skipped; a row whose SHARE COUNT cannot be read is
    also skipped rather than recorded as 0, because a spurious 0 is
    indistinguishable from "sold everything" and would trigger a false phantom.
    """
    out: dict[str, float] = {}
    for h in holdings or []:
        ticker = _first_attr(h, _TICKER_KEYS)
        raw_shares = _first_attr(h, _SHARE_KEYS)
        key = str(ticker or "").strip().upper()
        if not key:
            continue
        shares = _num_or_none(raw_shares)
        if shares is None:
            # Unreadable ("—", "n/a", NaN) — skip rather than record 0, which
            # would be indistinguishable from "sold everything".
            continue
        out[key] = out.get(key, 0.0) + shares
    return out


def classify_fill(
    pending: PendingFill,
    holdings: dict[str, float] | None,
    *,
    expired: bool = False,
    tolerance_shares: float = 0.0,
) -> FillVerdict:
    """Decide what happened to one submitted order.

    ``holdings`` is ``{TICKER: shares}`` from the broker, or ``None`` when the
    snapshot could not be fetched. ``expired`` means the order can no longer fill
    (past the DAY order's session), which is what turns "still pending" into a
    definitive "never filled".
    """
    ticker = pending.ticker.upper()
    intended = _num(pending.intended_shares)

    if holdings is None:
        # No snapshot ⇒ no evidence. Must never be read as "not filled": that
        # would delete a real position on a transient scrape failure.
        return FillVerdict(ticker, STATUS_UNKNOWN, 0.0, intended,
                           "holdings unavailable — no evidence either way")

    if intended <= 0:
        return FillVerdict(ticker, STATUS_UNFILLED, 0.0, intended,
                           "intended share count is not positive")

    now_shares = _num(holdings.get(ticker))
    delta = now_shares - _num(pending.shares_before)

    if pending.side == "sell":
        # A sell fills when shares DECREASE. Same delta logic, opposite sign.
        sold = -delta
        if sold >= intended - tolerance_shares:
            return FillVerdict(ticker, STATUS_FILLED, min(sold, intended), intended,
                               f"position reduced by {sold:g} share(s)")
        if sold > tolerance_shares:
            return FillVerdict(ticker, STATUS_PARTIAL, sold, intended,
                               f"partial sell: {sold:g} of {intended:g}",
                               [f"quantity mismatch: intended {intended:g}, sold {sold:g}"])
        if expired:
            return FillVerdict(ticker, STATUS_UNFILLED, 0.0, intended,
                               "sell order expired with no reduction in position")
        return FillVerdict(ticker, STATUS_PENDING, 0.0, intended, "sell not yet reflected")

    # Buy side.
    if delta >= intended - tolerance_shares:
        return FillVerdict(ticker, STATUS_FILLED, min(delta, intended), intended,
                           f"position increased by {delta:g} share(s)")
    if delta > tolerance_shares:
        return FillVerdict(ticker, STATUS_PARTIAL, delta, intended,
                           f"partial fill: {delta:g} of {intended:g}",
                           [f"quantity mismatch: intended {intended:g}, filled {delta:g}"])
    if expired:
        # The decisive case: the order can no longer fill and the shares never
        # appeared. Whatever internal state was written on acceptance is a
        # phantom and must be removed.
        return FillVerdict(ticker, STATUS_UNFILLED, 0.0, intended,
                           "order expired and shares never appeared in holdings")
    return FillVerdict(ticker, STATUS_PENDING, 0.0, intended, "fill not yet reflected")


def reconcile_pending_fills(
    pendings: Iterable[PendingFill],
    holdings: dict[str, float] | None,
    *,
    expired_tickers: Iterable[str] = (),
    tolerance_shares: float = 0.0,
) -> list[FillVerdict]:
    """Classify a whole ledger. ``expired_tickers`` are those past their session."""
    expired = {str(t).upper() for t in (expired_tickers or ())}
    return [
        classify_fill(p, holdings,
                      expired=p.ticker.upper() in expired,
                      tolerance_shares=tolerance_shares)
        for p in pendings or []
    ]
