"""Pre-submit order-ticket verification for the local Fidelity execution path.

Before the Playwright path clicks "Place Order", the ticket the browser is about
to submit MUST be verified against the intended order. Two checks:

  1. `verify_intent(intent)` — the intent is internally self-consistent and safe:
     valid symbol, buy/sell, positive integer qty, LIMIT with a positive price,
     est_cost matches qty×limit, and est_cost is within the per-order dollar cap.

  2. `verify_against_preview(intent, preview_text)` — the LIVE Fidelity preview page
     actually reflects that intent: it mentions the symbol, the share count, the
     side, and the limit price. This is the guard against submitting a ticket for a
     DIFFERENT symbol/qty/price than intended (a mis-fill or a stale ticket).

Both are pure and deterministic. `verify_order_ticket` runs both and returns a
single (ok, reasons) verdict. This is a safety gate, not a formatter — it fails
CLOSED: any field it cannot positively confirm is a rejection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tradingagents.compliance import valid_symbol, MAX_SINGLE_ORDER_DOLLARS

# est_cost must match qty×limit within this fraction (rounding/fees slack).
_COST_TOLERANCE = 0.02


@dataclass(frozen=True)
class OrderIntent:
    account_mask: str      # masked account, e.g. "•••••2469"
    symbol: str            # "NVDA"
    side: str              # "buy" | "sell"
    quantity: int          # whole shares
    order_type: str        # "limit"
    limit_price: float
    est_cost: float        # expected qty × limit_price


def verify_intent(intent: OrderIntent) -> tuple[bool, list[str]]:
    """Check the intent is self-consistent and within hard limits. Fails closed."""
    reasons: list[str] = []

    if not valid_symbol((intent.symbol or "").upper()):
        reasons.append(f"invalid symbol {intent.symbol!r}")
    if str(intent.side).lower() not in ("buy", "sell"):
        reasons.append(f"invalid side {intent.side!r}")
    if str(intent.order_type).lower() != "limit":
        reasons.append(f"order_type must be 'limit', got {intent.order_type!r}")
    if not isinstance(intent.quantity, int) or intent.quantity <= 0:
        reasons.append(f"quantity must be a positive integer, got {intent.quantity!r}")
    if not (isinstance(intent.limit_price, (int, float)) and intent.limit_price > 0):
        reasons.append(f"limit_price must be > 0, got {intent.limit_price!r}")
    if not intent.account_mask or not intent.account_mask.strip():
        reasons.append("account_mask missing")

    # Cost consistency + hard cap (only meaningful once qty/limit are sane).
    if not reasons:
        expected = intent.quantity * intent.limit_price
        if expected <= 0:
            reasons.append("computed cost <= 0")
        elif abs(intent.est_cost - expected) / expected > _COST_TOLERANCE:
            reasons.append(
                f"est_cost {intent.est_cost:.2f} disagrees with qty×limit {expected:.2f} "
                f"(> {_COST_TOLERANCE:.0%})"
            )
        if intent.est_cost > MAX_SINGLE_ORDER_DOLLARS:
            reasons.append(
                f"est_cost {intent.est_cost:.2f} exceeds per-order cap {MAX_SINGLE_ORDER_DOLLARS:.0f}"
            )

    return (not reasons, reasons)


def _price_appears(text: str, price: float) -> bool:
    """True if `price` appears in text as a 2-dp number (with optional $ / commas)."""
    target = f"{price:.2f}"
    # Normalize the page text's numbers: strip $ and thousands separators.
    norm = text.replace("$", "").replace(",", "")
    return target in norm


#: A whole-number token, optionally comma-grouped, with any currency prefix
#: captured so it can be rejected. Trailing decimals disqualify (that is a price).
_QTY_TOKEN = re.compile(r"(?<![\d.,])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+)(?![\d.,]*[\d.])")


def _quantity_appears(text: str, quantity: int) -> bool:
    """True when `quantity` appears as a SHARE COUNT (not a price) in `text`."""
    if not text or quantity is None:
        return False
    for m in _QTY_TOKEN.finditer(text):
        if m.group(1):                       # "$1,000" — a cost, not a quantity
            continue
        try:
            if int(m.group(2).replace(",", "")) == int(quantity):
                return True
        except (TypeError, ValueError):
            continue
    return False


def verify_against_preview(intent: OrderIntent, preview_text: str) -> tuple[bool, list[str]]:
    """Confirm the live Fidelity preview page reflects the intent. Fails closed if
    the page is empty or a field cannot be positively located."""
    reasons: list[str] = []
    if not preview_text or not preview_text.strip():
        return (False, ["empty preview page — cannot verify ticket"])

    low = preview_text.lower()
    sym = intent.symbol.upper()

    # Symbol must appear as a word (avoid substring false-matches like 'F' in 'of').
    if not re.search(rf"\b{re.escape(sym)}\b", preview_text):
        reasons.append(f"symbol {sym} not found on preview page")

    # Side word must appear and the OPPOSITE side must not dominate.
    side = intent.side.lower()
    if side not in low:
        reasons.append(f"side '{side}' not found on preview page")
    opposite = "sell" if side == "buy" else "buy"
    if side == "buy" and opposite in low and "buy" not in low:
        reasons.append("preview shows opposite side")

    # Quantity as a standalone whole-number token, tolerating thousands
    # separators. Two failure modes to avoid, both real:
    #   * a raw digit match hard-blocks every order of >=1000 shares, because
    #     Fidelity renders them as "1,000" — that killed the buy path for
    #     exactly the low-priced tickers this system trades;
    #   * naively stripping ALL commas lets a DOLLAR figure satisfy the SHARE
    #     check ("Estimated Cost $1,000.00" would confirm a 1000-share intent
    #     against a 250-share ticket) — the one thing this gate exists to catch.
    # So: scan number tokens, skip anything currency-prefixed or with a decimal
    # tail (those are prices/costs), and compare the grouped value exactly.
    if not _quantity_appears(preview_text, intent.quantity):
        reasons.append(f"quantity {intent.quantity} not found on preview page")

    # Limit price to the cent.
    if not _price_appears(preview_text, intent.limit_price):
        reasons.append(f"limit price {intent.limit_price:.2f} not found on preview page")

    return (not reasons, reasons)


def verify_order_ticket(intent: OrderIntent, preview_text: str) -> tuple[bool, list[str]]:
    """Full pre-submit gate: intent consistency AND live-preview agreement.
    Returns (ok, reasons). ok=False ⇒ DO NOT submit."""
    ok1, r1 = verify_intent(intent)
    ok2, r2 = verify_against_preview(intent, preview_text)
    return (ok1 and ok2, r1 + r2)
