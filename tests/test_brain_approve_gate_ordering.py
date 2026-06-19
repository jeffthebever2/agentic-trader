"""HIL ordering invariant for the Holdings-Brain approve endpoint: it must call
step-up 2FA (enforce_step_up) BEFORE it routes to either real-money Fidelity
order inner. Every path that reaches _fidelity_thematic_*_inner is a live broker
order; the 2FA gate has to come first. This source-ordering tripwire fails if a
refactor moves an order call above the gate.
"""
import inspect

import web.api.holdings_brain as hb


def test_brain_approve_gates_step_up_before_order():
    src = inspect.getsource(hb.brain_approve)

    i_gate = src.find("enforce_step_up")
    i_trade = src.find("_fidelity_thematic_trade_inner(")
    i_exit = src.find("_fidelity_thematic_exit_inner(")

    assert i_gate != -1, "brain_approve lost its enforce_step_up gate"
    assert i_trade != -1 and i_exit != -1, "expected both order inners in brain_approve"
    assert i_gate < i_trade, "trade order placed before step-up gate"
    assert i_gate < i_exit, "exit order placed before step-up gate"
