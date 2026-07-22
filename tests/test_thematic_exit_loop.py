"""The fast thematic exit loop must stay PAPER-ONLY: it may call
_check_thematic_exits (which writes the paper book + exit log) but must NEVER
place/exit a live broker order. Live exits go through the HIL + step-up path. This
source tripwire fails if an order-placing call leaks into the loop, or if the
flag-gate / registration is removed.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.py"


def _loop_src() -> str:
    src = APP.read_text()
    m = re.search(r"async def _thematic_exit_loop\(\).*?(?=\nasync def |\n_background_tasks)", src, re.DOTALL)
    assert m, "‎_thematic_exit_loop not found"
    return m.group(0)


def test_loop_is_paper_only():
    s = _loop_src()
    for forbidden in ("_fidelity_thematic_trade_inner", "_fidelity_thematic_exit_inner",
                      "fidelity_trade(", "place_order", "wb.place_order"):
        assert forbidden not in s, f"live-order call leaked into exit loop: {forbidden}"
    assert "_check_thematic_exits" in s     # it DOES enforce paper stops


def test_loop_is_flag_gated_and_session_gated():
    """Gated on the flag AND the calendar-aware regular session.

    Deliberately NOT the wider extended-hours risk window: `_check_thematic_exits`
    prices from yfinance DAILY bars, so out of hours it merely re-reads a close
    already evaluated at 15:59 — no new information for 2.5x the yfinance load,
    against a documented sqlite-fd leak. The calendar-awareness is the real fix
    (the old weekday+clock gate ran on market HOLIDAYS and evaluated stops
    against quotes that had not moved since the previous close).
    """
    s = _loop_src()
    assert "THEMATIC_EXIT_LOOP" in s
    assert "_brain_market_open()" in s


def test_risk_window_is_calendar_aware_and_never_narrower_than_execution():
    """The two gates must stay consistent: anything safe to EXECUTE in must also
    be watched, and neither may run on a holiday."""
    import datetime as _dt
    from tradingagents.market_calendar import (
        ET, is_extended_session, is_regular_session,
    )
    thanksgiving = _dt.datetime(2026, 11, 26, 10, 0, tzinfo=ET)
    assert not is_regular_session(thanksgiving)
    assert not is_extended_session(thanksgiving)

    t = _dt.datetime(2026, 7, 20, 0, 0, tzinfo=ET)
    for _ in range(7 * 24 * 4):
        if is_regular_session(t):
            assert is_extended_session(t), t
        t += _dt.timedelta(minutes=15)


def test_loop_is_registered_at_startup():
    src = APP.read_text()
    # Registered via the supervised-loop wrapper (auto-restarts on crash, D4).
    assert '_spawn_supervised_loop(_thematic_exit_loop, "thematic_exit")' in src
