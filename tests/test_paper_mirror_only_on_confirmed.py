"""Real-money invariant: the paper-book mirror of a Fidelity thematic trade must
fire ONLY after the broker order is confirmed executed. Otherwise a rejected /
unconfirmed live order would still book a phantom paper position, desyncing the
paper book from reality (and inflating tracked P&L). This source-level tripwire
fails if the mirror stops gating on order_status=='executed', or if 'executed'
is set before the confirmation check.
"""
import inspect

import web.api.fidelity as f


def test_paper_mirror_gated_on_confirmed_execution():
    src = inspect.getsource(f._fidelity_thematic_trade_inner)

    # The mirror must require an executed order.
    assert 'order_status == "executed"' in src, "paper mirror no longer gates on executed status"
    assert "also_paper_trade" in src

    # 'executed' must only be set AFTER the confirmation guard raises on failure.
    i_not_confirmed = src.find("if not confirmed")
    i_executed = src.find('order_status = "executed"')
    i_mirror = src.find("Mirror in paper account")

    assert i_not_confirmed != -1, "lost the confirmation guard"
    assert i_executed != -1, "lost the executed status assignment"
    assert i_not_confirmed < i_executed, "order marked executed before confirmation was verified"
    # The mirror block comes after the executed assignment.
    assert i_executed < i_mirror, "paper mirror appears before execution is confirmed"
