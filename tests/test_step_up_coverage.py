"""Tripwire: every real-money broker order endpoint must keep its per-trade
step-up 2FA gate (require_step_up). This guards against an accidental removal of
the `Depends(require_step_up)` that would let a live order place with only an
admin session. Real money — only ADD strictness here, never relax.

See web/api/fidelity.py, web/api/webull_portfolio.py and the live-leg
enforce_step_up call in web/api/thematic_auto.approve_signal.
"""
import inspect

from web.auth import require_step_up


def _admin_dependency(fn, param: str = "admin"):
    """Return the callable a route's auth param is wired to via Depends(...)."""
    default = inspect.signature(fn).parameters[param].default
    return getattr(default, "dependency", None)


def test_fidelity_order_endpoints_require_step_up():
    import web.api.fidelity as f

    for fn in (f.fidelity_trade, f.fidelity_thematic_trade, f.fidelity_thematic_exit):
        assert _admin_dependency(fn) is require_step_up, (
            f"{fn.__name__} lost its step-up 2FA gate"
        )


def test_webull_place_order_requires_step_up():
    import web.api.webull_portfolio as w

    assert _admin_dependency(w.wb_place_order) is require_step_up, (
        "wb_place_order lost its step-up 2FA gate"
    )


def test_thematic_approve_enforces_step_up_on_live_leg():
    """approve_signal calls enforce_step_up before any write when the approval
    routes to a real Fidelity order (body.fidelity_trade and execute_fidelity).
    Assert that call is still present in the source as the structural guard."""
    import web.api.thematic_auto as t

    src = inspect.getsource(t.approve_signal)
    assert "enforce_step_up" in src, "approve_signal dropped its live-leg step-up gate"
