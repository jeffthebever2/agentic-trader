"""Thematic signal replay/tuning is pure and point-in-time aligned."""
from tradingagents.backtesting.thematic_replay import (
    evaluate_thresholds,
    forward_return,
    load_score_history_lines,
)


def test_load_score_history_lines():
    lines = [
        '{"ts":"2026-06-19T10:00:00","ranked":[["IREN",72.5]],"breakdown":{"IREN":{"discovery":15}}}',
        "not-json",
    ]
    events = load_score_history_lines(lines)
    assert len(events) == 1
    assert events[0].ticker == "IREN"
    assert events[0].breakdown["discovery"] == 15


def test_forward_return_requires_future_window():
    assert abs(forward_return([10, 11, 12], 0, 2) - 0.2) < 1e-9
    assert forward_return([10, 11], 0, 2) is None
    assert forward_return([0, 11, 12], 0, 1) is None


def test_evaluate_thresholds_uses_explicit_event_index():
    events = load_score_history_lines([
        '{"ts":"t1","ranked":[["AAA",80],["BBB",45]],"breakdown":{}}',
    ])
    prices = {"AAA": [10, 12, 13], "BBB": [20, 19, 18]}
    event_index = {("t1", "AAA"): 0, ("t1", "BBB"): 0}
    rows = evaluate_thresholds(events, prices, event_index, thresholds=[40, 70], horizon=2)

    assert rows[0]["n"] == 2
    assert rows[0]["hit_rate"] == 0.5
    assert rows[1]["n"] == 1
    assert rows[1]["mean_return"] == 0.3
