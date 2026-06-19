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


def test_loop_is_flag_gated_and_market_hours():
    s = _loop_src()
    assert "THEMATIC_EXIT_LOOP" in s
    assert "_brain_market_open()" in s


def test_loop_is_registered_at_startup():
    src = APP.read_text()
    assert "create_task(_thematic_exit_loop())" in src
