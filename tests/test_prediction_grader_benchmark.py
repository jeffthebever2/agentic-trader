"""Tests for SPY/QQQ benchmark fields added to PredictionGrader."""
import datetime as dt
import json
import dataclasses
from pathlib import Path
import pytest

from tradingagents.portfolio.prediction_grader import (
    GradeResult,
    PredictionGrader,
    _benchmark_return,
    _fetch_benchmark_closes,
)

# ── _benchmark_return unit tests ─────────────────────────────────────────────

def test_benchmark_return_basic():
    closes = {"2026-01-02": 100.0, "2026-01-05": 105.0}
    r = _benchmark_return(closes, "2026-01-02", "2026-01-05")
    assert r == pytest.approx(0.05, abs=1e-6)


def test_benchmark_return_negative():
    closes = {"2026-01-02": 100.0, "2026-01-09": 95.0}
    r = _benchmark_return(closes, "2026-01-02", "2026-01-09")
    assert r == pytest.approx(-0.05, abs=1e-6)


def test_benchmark_return_same_date_returns_none():
    closes = {"2026-01-02": 100.0}
    r = _benchmark_return(closes, "2026-01-02", "2026-01-02")
    assert r is None


def test_benchmark_return_exit_before_entry_returns_none():
    closes = {"2026-01-02": 100.0, "2026-01-01": 99.0}
    r = _benchmark_return(closes, "2026-01-02", "2026-01-01")
    assert r is None


def test_benchmark_return_walks_forward_for_entry():
    # Entry on weekend, first close on Monday
    closes = {"2026-01-05": 100.0, "2026-01-06": 101.0}
    r = _benchmark_return(closes, "2026-01-03", "2026-01-06")
    assert r == pytest.approx(0.01, abs=1e-6)


def test_benchmark_return_missing_data_returns_none():
    closes: dict = {}
    r = _benchmark_return(closes, "2026-01-02", "2026-01-09")
    assert r is None


# ── GradeResult benchmark fields ─────────────────────────────────────────────

def _make_grade(**overrides) -> GradeResult:
    defaults = dict(
        ticker="AAPL",
        trade_id="AAPL_2026-01-05T09:30:00",
        graded_at="2026-01-10T15:00:00",
        predicted_win_prob=0.65,
        predicted_return=0.03,
        predicted_ll_prob=0.10,
        alpha_tier="A",
        alpha_score=0.80,
        breakout_score=0.75,
        regime_at_entry="bull",
        model_version="c46",
        actual_win=True,
        actual_return=0.04,
        actual_max_drawdown=-0.02,
        stop_hit=False,
        target_hit=True,
        hold_days=5.0,
        regime_at_exit="bull",
        win_prediction_correct=True,
        return_error=-0.01,
        ll_prediction_correct=True,
        confidence_bucket="mid",
        return_bucket="gain",
    )
    defaults.update(overrides)
    return GradeResult(**defaults)


def test_grade_result_has_benchmark_fields():
    g = _make_grade(spy_return_over_hold=0.01, beat_spy=True, alpha_vs_spy=0.03)
    assert g.spy_return_over_hold == 0.01
    assert g.beat_spy is True
    assert g.alpha_vs_spy == 0.03


def test_grade_result_benchmark_fields_default_none():
    g = _make_grade()
    assert g.spy_return_over_hold is None
    assert g.qqq_return_over_hold is None
    assert g.beat_spy is None
    assert g.beat_qqq is None
    assert g.alpha_vs_spy is None


def test_grade_result_to_dict_includes_benchmarks():
    g = _make_grade(spy_return_over_hold=0.02, beat_spy=True, alpha_vs_spy=0.02)
    d = g.to_dict()
    assert "spy_return_over_hold" in d
    assert "beat_spy" in d
    assert "alpha_vs_spy" in d


# ── summary() benchmark aggregation ──────────────────────────────────────────

def _grader_from_grades(grades, tmp_path) -> PredictionGrader:
    grader = PredictionGrader(account_dir=tmp_path)
    return grader


def test_summary_includes_beat_spy_rate(tmp_path):
    grader = PredictionGrader(account_dir=tmp_path)
    grades = [
        _make_grade(actual_return=0.03, beat_spy=True, alpha_vs_spy=0.02),
        _make_grade(actual_return=-0.01, beat_spy=False, alpha_vs_spy=-0.02),
        _make_grade(actual_return=0.05, beat_spy=True, alpha_vs_spy=0.04),
    ]
    s = grader.summary(grades)
    assert "beat_spy_rate" in s
    assert s["beat_spy_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert s["beat_spy_n"] == 3


def test_summary_no_benchmark_data_omits_key(tmp_path):
    grader = PredictionGrader(account_dir=tmp_path)
    grades = [_make_grade(), _make_grade()]
    s = grader.summary(grades)
    assert "beat_spy_rate" not in s
    assert "avg_alpha_vs_spy" not in s


def test_summary_avg_alpha_vs_spy(tmp_path):
    grader = PredictionGrader(account_dir=tmp_path)
    grades = [
        _make_grade(alpha_vs_spy=0.04),
        _make_grade(alpha_vs_spy=-0.02),
    ]
    s = grader.summary(grades)
    assert s["avg_alpha_vs_spy"] == pytest.approx(0.01, abs=0.001)


# ── grade_all with fetch_benchmarks=False ────────────────────────────────────

def test_grade_all_fetch_benchmarks_false_skips_network(tmp_path):
    """grade_all(fetch_benchmarks=False) returns grades without benchmark fields."""
    event_dir = tmp_path / "2026-01-05"
    event_dir.mkdir()
    events = [
        {"type": "BUY", "ticker": "AAPL", "timestamp": "2026-01-05T09:30:00",
         "ml_probability": 0.68, "expected_return": 0.03},
        {"type": "SELL", "ticker": "AAPL", "entry_time": "2026-01-05T09:30:00",
         "timestamp": "2026-01-10T15:00:00", "pnl_pct": 0.04},
    ]
    (event_dir / "event_log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )
    grader = PredictionGrader(account_dir=tmp_path)
    grades = grader.grade_all(fetch_benchmarks=False)
    assert len(grades) == 1
    assert grades[0].beat_spy is None
    assert grades[0].spy_return_over_hold is None
