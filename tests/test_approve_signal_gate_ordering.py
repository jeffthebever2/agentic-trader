"""HIL ordering invariant: approve_signal must enforce step-up 2FA (for the live
Fidelity leg) BEFORE it writes any paper/portfolio state. Otherwise a missing or
expired token could abort *after* booking a paper position — desyncing the paper
book from reality and partially committing an approval that 2FA should have
stopped. This source-level tripwire fails if a refactor moves the paper write
above the gate.
"""
import inspect

import web.api.thematic_auto as ta


def test_step_up_precedes_paper_write_in_approve_signal():
    src = inspect.getsource(ta.approve_signal)

    i_stepup = src.find("enforce_step_up")
    i_load = src.find("_load(")     # reads the paper book
    i_save = src.find("_save(")     # persists the paper book

    assert i_stepup != -1, "approve_signal lost its enforce_step_up gate"
    assert i_save != -1, "expected a paper-state write (_save) in approve_signal"

    # The 2FA gate must appear before the book is loaded for mutation and before
    # it is persisted — nothing booked before the token is verified.
    assert i_stepup < i_load, "paper book is loaded before the step-up gate"
    assert i_stepup < i_save, "paper book is saved before the step-up gate"


def test_approve_signal_guards_live_leg_conditionally():
    # The live-leg gate is conditional on the real-Fidelity routing flags, so a
    # frictionless paper-only approval stays gate-free by design.
    src = inspect.getsource(ta.approve_signal)
    assert "fidelity_trade" in src and "execute_fidelity" in src
