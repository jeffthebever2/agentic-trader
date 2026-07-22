"""The live-order gate — the last thing between the system and real money.

Fidelity execution is browser automation, so it cannot be exercised end-to-end
without placing a real order. What CAN be pinned is every decision guard on that
path, against realistic page text: the pre-submit ticket verifier, the
post-submit confirmation reader, and the account guard.

These are adversarial by construction. Each test is a way the page could lie to
us — a stale ticket, a transposed quantity, a wrong account, confirmation text
that also contains an error — and the required answer is always the same:
**fail closed and do not submit.**
"""
from __future__ import annotations

import pytest

from tradingagents.brokers.order_verifier import (
    OrderIntent, verify_against_preview, verify_intent, verify_order_ticket,
)
from web.api.fidelity import _verify_fidelity_order_page


def _intent(**kw) -> OrderIntent:
    base = dict(account_mask="•••••2469", symbol="NVDA", side="buy",
                quantity=10, order_type="limit", limit_price=110.25,
                est_cost=1102.50)
    base.update(kw)
    return OrderIntent(**base)


#: What a real Fidelity preview looks like for the intent above.
GOOD_PREVIEW = """
Preview Order
Account  •••••2469  Individual
Symbol   NVDA  NVIDIA CORP
Action   Buy
Quantity 10 shares
Order Type  Limit
Limit Price  110.25
Time in Force  Day
Estimated Cost  $1,102.50
"""


# ── the ticket must match the intent ─────────────────────────────────────────

@pytest.mark.unit
def test_matching_ticket_passes():
    ok, reasons = verify_order_ticket(_intent(), GOOD_PREVIEW)
    assert ok, reasons


@pytest.mark.unit
def test_wrong_symbol_on_the_ticket_is_refused():
    """The nightmare: the ticket reverted to a previously-selected symbol, so we
    would buy the wrong company at our intended size."""
    ok, reasons = verify_order_ticket(_intent(symbol="AMD"), GOOD_PREVIEW)
    assert not ok and any("AMD" in r for r in reasons)


@pytest.mark.unit
def test_wrong_quantity_on_the_ticket_is_refused():
    ok, reasons = verify_order_ticket(_intent(quantity=100, est_cost=11025.0),
                                      GOOD_PREVIEW)
    assert not ok and any("quantity" in r.lower() for r in reasons)


@pytest.mark.unit
def test_wrong_limit_price_on_the_ticket_is_refused():
    ok, reasons = verify_order_ticket(_intent(limit_price=99.99), GOOD_PREVIEW)
    assert not ok and any("limit price" in r.lower() for r in reasons)


@pytest.mark.unit
def test_empty_page_fails_closed():
    """An unrendered page must never read as agreement."""
    for page in ("", "   ", "\n\n"):
        ok, reasons = verify_against_preview(_intent(), page)
        assert not ok and reasons


@pytest.mark.unit
def test_quantity_is_matched_as_a_whole_token_not_a_substring():
    """Quantity 10 must not be satisfied by the '10' inside '100' or '110.25'."""
    page = "Symbol NVDA\nAction Buy\nQuantity 100 shares\nLimit Price 110.25"
    ok, reasons = verify_against_preview(_intent(quantity=10), page)
    assert not ok, f"substring match let a 10x quantity error through: {reasons}"


@pytest.mark.unit
def test_symbol_is_matched_as_a_word_not_a_substring():
    """A one-letter ticker must not match a letter inside an ordinary word."""
    page = "Preview of your order\nAction Buy\nQuantity 10\nLimit Price 110.25"
    ok, _ = verify_against_preview(_intent(symbol="F"), page)
    assert not ok


# ── intent self-consistency ──────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("bad", [
    {"order_type": "market"},          # market orders are prohibited outright
    {"side": "short"},
    {"quantity": 0},
    {"quantity": -5},
    {"symbol": "NOT A TICKER"},
])
def test_self_inconsistent_intents_are_refused(bad):
    ok, reasons = verify_intent(_intent(**bad))
    assert not ok and reasons


@pytest.mark.unit
def test_valid_dotted_symbol_is_accepted():
    """BRK.B is a real ticker — the symbol validator must not reject it."""
    ok, _ = verify_intent(_intent(symbol="BRK.B"))
    assert ok


# ── post-submit confirmation reading ─────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("page", [
    "Your order has been received. Order number 12345.",
    "Order Received\nOrder Number: 987654321",
])
def test_genuine_confirmation_is_accepted(page):
    ok, msg = _verify_fidelity_order_page(page)
    assert ok, msg


@pytest.mark.unit
@pytest.mark.parametrize("page", [
    "We were unable to process your order at this time.",
    "Order rejected.",
    "Insufficient funds to complete this transaction.",
    "The market is closed.",
])
def test_genuine_rejection_is_caught(page):
    ok, _ = _verify_fidelity_order_page(page)
    assert not ok


@pytest.mark.unit
def test_error_text_beats_confirmation_text_on_the_same_page():
    """A page carrying BOTH must be treated as rejected — never assume the happy
    path when the page is self-contradictory."""
    page = "Order number 12345 cannot be processed."
    ok, _ = _verify_fidelity_order_page(page)
    assert not ok


@pytest.mark.unit
def test_unrecognised_page_is_not_a_confirmation():
    """Unknown page state must be 'status unknown', never success."""
    ok, msg = _verify_fidelity_order_page("Session timed out. Please sign in.")
    assert not ok
    assert "not found" in msg.lower() or "unknown" in msg.lower()


@pytest.mark.unit
@pytest.mark.parametrize("chrome", [
    "Buying power: $12,345.67",
    "Extended hours / after hours trading available",
    "aria-live-region error-template hidden",
    "Invalid entry hint hidden",
])
def test_ordinary_page_chrome_does_not_reject_a_live_order(chrome):
    """POST-submit false positives are the dangerous direction: the order is
    already live, so a spurious rejection leaves real shares that no part of the
    system tracks — no stop, no target, absent from the exit guard."""
    page = f"Your order has been received. Order number 12345.\n{chrome}"
    ok, msg = _verify_fidelity_order_page(page)
    assert ok, f"page chrome {chrome!r} falsely rejected a confirmed order: {msg}"


# ── account guard ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_protected_account_is_refused(monkeypatch):
    """A retirement account must be untouchable regardless of what is requested."""
    from fastapi import HTTPException
    from web.api import fidelity as fx
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    with pytest.raises(HTTPException) as exc:
        fx._assert_account_tradeable("262502469")
    assert exc.value.status_code == 403


@pytest.mark.unit
def test_default_account_is_refused_in_strict_mode(monkeypatch):
    """With no explicit account, strict mode must refuse rather than let the
    order land on whichever account Fidelity happens to have selected."""
    from fastapi import HTTPException
    from web.api import fidelity as fx
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    monkeypatch.setenv("FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", "true")
    with pytest.raises(HTTPException) as exc:
        fx._assert_account_tradeable(None)
    assert exc.value.status_code == 403
    assert "EXPLICIT" in str(exc.value.detail).upper()


# ── the supervised smoke script must never be able to submit ─────────────────

@pytest.mark.unit
def test_live_smoke_script_has_no_submit_path():
    """`scripts/live_execution_smoke.py` exists to exercise the real Fidelity DOM
    — every selector and every pre-submit gate — WITHOUT spending money. Its
    whole value is that it cannot place an order. If a submit path is ever added,
    it stops being a safe thing to run against a funded account.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "live_execution_smoke.py").read_text()

    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    # Strip the module docstring, which legitimately discusses Place Order.
    if code.count('"""') >= 2:
        first = code.index('"""')
        second = code.index('"""', first + 3)
        code = code[:first] + code[second + 3:]

    for forbidden in ('has-text("Place Order")', "place_btn", "place_order_btn",
                      'execute=True', '"execute": True'):
        assert forbidden not in code, (
            f"submit path leaked into the smoke script: {forbidden!r}"
        )


@pytest.mark.unit
def test_live_smoke_script_refuses_to_run_on_a_critical_preflight():
    """It must not drive a funded account in a configuration the preflight has
    already declared unsafe."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "live_execution_smoke.py").read_text()
    assert "run_preflight" in src
    assert "pf.critical" in src
