"""Tripwire: every Fidelity order path must keep the full compliance kill-chain
wired. Real money — these four guards together are the only thing between an
approval and a live broker order. This test fails if any guard is removed from
any order function's body, catching an accidental (or careless) regression that
unit tests on validate_live_order alone would miss.

Guards required in each path:
  - LIVE_TRADING_HARD_BLOCKED   (source kill switch)
  - live_trading_enabled()      (.env master toggle)
  - validate_live_order(...)    (limit-only / caps / trusted-fresh quote)
  - _assert_account_tradeable() (Roth/IRA protected-account block)
"""
import inspect

import web.api.fidelity as f

_REQUIRED = (
    "LIVE_TRADING_HARD_BLOCKED",
    "live_trading_enabled",
    "validate_live_order",
    "_assert_account_tradeable",
)

_ORDER_FUNCS = (
    "fidelity_trade",
    "_fidelity_thematic_trade_inner",
    "_fidelity_thematic_exit_inner",
)


def test_every_order_path_keeps_the_killchain():
    for name in _ORDER_FUNCS:
        fn = getattr(f, name)
        src = inspect.getsource(fn)
        for guard in _REQUIRED:
            assert guard in src, f"{name} is missing compliance guard: {guard}"

# (Per-trade step-up 2FA on the routed order endpoints is covered separately by
#  tests/test_step_up_coverage.py.)
