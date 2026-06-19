"""_parse_llm_action clamps an LLM JSON response into a safe Action. A NaN
fraction (JSON permits the bare NaN literal) passes float() without raising and
is NOT clamped by max/min — it would carry through to Action.fraction as a NaN
trim size. Must coerce non-finite to 0. Money-relevant clamp path."""
import math

from tradingagents.portfolio.holdings_brain import (
    _parse_llm_action, Holding, Action, ACTION_TRIM,
)


def _holding():
    return Holding(
        ticker="NVDA", shares=10, avg_cost=100.0, last=120.0,
        market_value=1200.0, pct_of_account=12.0, unrealized_pct=20.0,
    )


def _rule():
    return Action(ticker="NVDA", kind="HOLD", reason="rule", conviction=6)


def test_nan_fraction_coerced_to_zero():
    text = '{"action":"TRIM","fraction":NaN,"conviction":7}'
    act = _parse_llm_action(text, _holding(), _rule())
    assert act is not None
    assert math.isfinite(act.fraction)
    # TRIM with a zeroed fraction falls back to the hard-capped default, finite.
    assert 0.0 <= act.fraction <= 1.0


def test_normal_fraction_preserved():
    text = '{"action":"TRIM","fraction":0.25,"conviction":7}'
    act = _parse_llm_action(text, _holding(), _rule())
    assert act.kind == ACTION_TRIM
    assert act.fraction == 0.25
    assert act.conviction == 7


def test_garbage_fraction_safe():
    text = '{"action":"ADD","fraction":"lots","conviction":"n/a"}'
    act = _parse_llm_action(text, _holding(), _rule())
    assert act is not None
    assert math.isfinite(act.fraction) and 0.0 <= act.fraction <= 1.0
    assert 1 <= act.conviction <= 10  # falls back to rule conviction
