"""Propose-only invariant: the autonomous Holdings-Brain loop functions
(run_brain_cycle, run_exit_guard) — invoked by the background loops in app.py —
must NEVER place or exit a real order. Execution lives only in the separate,
step-up-2FA-gated approve endpoint. This tripwire fails if an order-placing call
or execute=True ever leaks into a loop function, which is how an autonomous
real-money trade would slip past the human-in-the-loop gate."""
import inspect

import web.api.holdings_brain as hb
import web.api.thematic_auto as ta

# Calls that actually place/exit a live broker order.
_FORBIDDEN_IN_LOOPS = (
    "_fidelity_thematic_trade_inner(",
    "_fidelity_thematic_exit_inner(",
    "fidelity_trade(",
    "execute=True",
)


def test_brain_loop_functions_never_execute():
    for name in ("run_brain_cycle", "run_exit_guard"):
        src = inspect.getsource(getattr(hb, name))
        for bad in _FORBIDDEN_IN_LOOPS:
            assert bad not in src, f"{name} contains an order-execution path: {bad!r}"


def test_check_thematic_exits_defaults_to_propose_only():
    # The exit scan must default to execute=False so the background/status callers
    # only ever propose; execution requires an explicit execute=True from a gated
    # caller.
    sig = inspect.signature(ta._check_thematic_exits)
    assert sig.parameters["execute"].default is False
