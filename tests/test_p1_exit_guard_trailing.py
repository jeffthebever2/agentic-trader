"""Tests for the exit-guard trailing-stop ratchet (P1 stream).

Covers ``holdings_brain.ratchet_stops``: high-water trail + breakeven lock on the
live managed-plan store, monotonic-raise-only, fail-closed on missing inputs, and
the two non-negotiable invariants — a stop is NEVER lowered, and the ratchet can
NEVER produce autonomous-executor-eligible output.
"""
import copy
import json

import pytest

from tradingagents.portfolio import holdings_brain as hb
from tradingagents.portfolio.exit_manager import ExitManager
from tradingagents.portfolio.holdings_brain import Holding, check_stops, ratchet_stops
from tradingagents.portfolio.short_hold_exits import BREAKEVEN_LOCK_ATR

# The flag set web/api/holdings_brain._proposal_is_auto_live_exit_eligible matches
# on. Guard output must never intersect it (regression lock — see test at bottom).
AUTONOMOUS_ELIGIBLE_FLAGS = {
    "below_managed_stop", "regime_crash_risk", "trailing_stop_breach", "target_breach",
}


def _holding(**kw) -> Holding:
    base = dict(
        ticker="AAPL", shares=10, avg_cost=90.0, last=110.0,
        market_value=1100.0, pct_of_account=5.0, unrealized_pct=22.2,
    )
    base.update(kw)
    return Holding(**base)


def _plan(**kw) -> dict:
    base = dict(status="managed", stop=95.0, target=200.0, conviction=6, trail_high=100.0)
    base.update(kw)
    return base


# ── ratchet math ────────────────────────────────────────────────────────────────
def test_ratchet_raises_stop_on_new_high():
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=90.0)], store, quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0},
    )
    # ExitManager default trail: peak - 0.5*atr = 110 - 1 = 109
    assert store["AAPL"]["stop"] == 109.0
    assert store["AAPL"]["trail_high"] == 110.0
    assert store["AAPL"]["stop_source"] == "trail_ratchet"
    assert "stop_raised_at" in store["AAPL"]
    assert len(changes) == 1
    assert changes[0]["ticker"] == "AAPL"
    assert changes[0]["old_stop"] == 95.0
    assert changes[0]["new_stop"] == 109.0
    assert changes[0]["stop_raised"] is True


def test_ratchet_never_lowers_stop():
    # Computed trail (120 - 0.5*2 = 119) equals the current stop → no change.
    store = {"AAPL": _plan(stop=119.0, trail_high=120.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=90.0, last=100.0)], store,
        quotes={"AAPL": 100.0}, atr_map={"AAPL": 2.0},
    )
    assert store["AAPL"]["stop"] == 119.0
    assert changes == []

    # Huge ATR → computed trail far below the stop; still never lowered.
    store2 = {"AAPL": _plan(stop=119.0, trail_high=120.0)}
    changes2 = ratchet_stops(
        [_holding(avg_cost=90.0, last=100.0)], store2,
        quotes={"AAPL": 100.0}, atr_map={"AAPL": 50.0},
    )
    assert store2["AAPL"]["stop"] == 119.0
    assert changes2 == []


def test_breakeven_lock_at_0_6_atr():
    entry, atr = 100.0, 2.0
    price = entry + BREAKEVEN_LOCK_ATR * atr  # 101.2 — below +1 ATR trail activation
    store = {"AAPL": _plan(stop=94.0, trail_high=100.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=entry, last=price)], store,
        quotes={"AAPL": price}, atr_map={"AAPL": atr},
    )
    # Locked to entry exactly — NOT trailed to peak - 0.5*atr (trail not active yet).
    assert store["AAPL"]["stop"] == entry
    assert len(changes) == 1 and changes[0]["stop_raised"] is True


def test_below_breakeven_dead_zone_untouched():
    entry, atr = 100.0, 2.0
    price = 100.9  # < entry + 0.6*atr = 101.2, and trail_high < activation (102)
    store = {"AAPL": _plan(stop=94.0, trail_high=100.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=entry, last=price)], store,
        quotes={"AAPL": price}, atr_map={"AAPL": atr},
    )
    assert store["AAPL"]["stop"] == 94.0
    # trail_high still ratchets up even in the dead zone.
    assert store["AAPL"]["trail_high"] == 100.9
    assert len(changes) == 1 and changes[0]["stop_raised"] is False


# ── trail_high migration / monotonicity ─────────────────────────────────────────
def test_missing_trail_high_migrated_from_price():
    plan = _plan(stop=94.0)
    del plan["trail_high"]  # pre-existing store record without a high-water mark
    store = {"AAPL": plan}
    changes = ratchet_stops(
        [_holding(avg_cost=100.0, last=95.0)], store,
        quotes={"AAPL": 95.0}, atr_map={"AAPL": 2.0},
    )
    # Seeded from the observed price, never from avg_cost.
    assert store["AAPL"]["trail_high"] == 95.0
    # Quote below entry → no fabricated peak → no stop raise.
    assert store["AAPL"]["stop"] == 94.0
    assert len(changes) == 1 and changes[0]["stop_raised"] is False


def test_stale_trail_high_ratchets_up_only():
    # Quote below the stored high-water: trail_high never lowered.
    store = {"AAPL": _plan(stop=104.0, trail_high=105.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=100.0, last=103.0)], store,
        quotes={"AAPL": 103.0}, atr_map={"AAPL": 2.0},
    )
    assert store["AAPL"]["trail_high"] == 105.0
    assert store["AAPL"]["stop"] == 104.0
    assert changes == []

    # New high: trail_high and stop both ratchet.
    changes2 = ratchet_stops(
        [_holding(avg_cost=100.0, last=108.0)], store,
        quotes={"AAPL": 108.0}, atr_map={"AAPL": 2.0},
    )
    assert store["AAPL"]["trail_high"] == 108.0
    assert store["AAPL"]["stop"] == 107.0  # 108 - 0.5*2
    assert len(changes2) == 1 and changes2[0]["stop_raised"] is True


# ── fail-closed skips ───────────────────────────────────────────────────────────
def test_plan_without_stop_never_gets_one():
    plan = {"status": "managed", "target": 130.0, "conviction": 6}
    store = {"AAPL": plan}
    changes = ratchet_stops(
        [_holding(avg_cost=90.0, last=110.0)], store,
        quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0},
    )
    assert changes == []
    assert "stop" not in store["AAPL"]  # stop creation stays HIL
    assert "trail_high" not in store["AAPL"]


def test_inactive_or_unmanaged_skipped():
    closed = _plan(status="closed", stop=95.0, trail_high=100.0)
    store = {"AAPL": dict(closed)}
    changes = ratchet_stops(
        [_holding(ticker="AAPL", last=110.0), _holding(ticker="MSFT", last=200.0)],
        store, quotes={"AAPL": 110.0, "MSFT": 200.0},
    )
    assert changes == []
    assert store["AAPL"] == closed          # inactive plan untouched
    assert set(store.keys()) == {"AAPL"}    # unmanaged holding never gains an entry


def test_zero_price_and_missing_entry_fail_closed():
    # No quote and no last price → skipped entirely.
    plan = _plan(stop=95.0, trail_high=100.0)
    store = {"AAPL": dict(plan)}
    assert ratchet_stops([_holding(last=0.0)], store, quotes={}) == []
    assert store["AAPL"] == plan

    # No entry (avg_cost=0) with a valid price → stop unchanged, trail_high may rise.
    store2 = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=0.0, last=110.0)], store2,
        quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0},
    )
    assert store2["AAPL"]["stop"] == 95.0
    assert store2["AAPL"]["trail_high"] == 110.0
    assert len(changes) == 1 and changes[0]["stop_raised"] is False


def test_ratchet_idempotent():
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    holdings = [_holding(avg_cost=90.0, last=110.0)]
    quotes = {"AAPL": 110.0}
    atr = {"AAPL": 2.0}
    first = ratchet_stops(holdings, store, quotes=quotes, atr_map=atr)
    assert len(first) == 1
    snapshot = json.dumps(store, sort_keys=True)
    second = ratchet_stops(holdings, store, quotes=quotes, atr_map=atr)
    assert second == []
    assert json.dumps(store, sort_keys=True) == snapshot


# ── reuse contracts (don't duplicate math) ──────────────────────────────────────
def test_parity_with_exit_manager_math():
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    ratchet_stops(
        [_holding(avg_cost=90.0)], store, quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0},
    )
    expected = round(ExitManager().update_trailing_stop(
        current_stop=95.0, peak_price=110.0, atr=2.0), 2)
    assert store["AAPL"]["stop"] == expected


def test_trail_atr_mult_override_clamped_semantics():
    # Wider trail: stop = peak - 2*atr = 110 - 4 = 106.
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    ratchet_stops(
        [_holding(avg_cost=90.0)], store,
        quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0}, trail_atr_mult=2.0,
    )
    assert store["AAPL"]["stop"] == 106.0

    # Still never lowered: an already-tighter stop survives a wide trail.
    store2 = {"AAPL": _plan(stop=108.0, trail_high=110.0)}
    changes = ratchet_stops(
        [_holding(avg_cost=90.0)], store2,
        quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0}, trail_atr_mult=2.0,
    )
    assert store2["AAPL"]["stop"] == 108.0
    assert changes == []


# ── integration with check_stops (the E1 round-trip) ────────────────────────────
def test_ratchet_then_check_stops_fires_at_raised_level():
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    holdings = [_holding(avg_cost=90.0)]
    ratchet_stops(holdings, store, quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0})
    assert store["AAPL"]["stop"] == 109.0
    # Round-trip to 108 — previously safe vs the stale 95 stop, now a breach.
    breaches = check_stops(holdings, store, quotes={"AAPL": 108.0})
    assert len(breaches) == 1
    assert breaches[0].reason == "stop_hit"
    assert breaches[0].level == 109.0


def test_no_autonomous_eligible_output():
    store = {"AAPL": _plan(stop=95.0, trail_high=100.0)}
    holdings = [_holding(avg_cost=90.0)]
    ratchet_stops(holdings, store, quotes={"AAPL": 110.0}, atr_map={"AAPL": 2.0})
    breaches = check_stops(holdings, store, quotes={"AAPL": 108.0})
    # Breach reasons stay outside the autonomous-executor flag set...
    for b in breaches:
        assert b.reason not in AUTONOMOUS_ELIGIBLE_FLAGS
    # ...and the ratchet writes no plan field that could ever match it.
    for plan in store.values():
        for v in plan.values():
            assert v not in AUTONOMOUS_ELIGIBLE_FLAGS
