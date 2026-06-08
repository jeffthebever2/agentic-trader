"""Tests for calibration bucket tracking in reliability_stats.py"""
import dataclasses
import datetime as dt
import json
import pytest
from pathlib import Path
from tradingagents.portfolio.reliability_stats import (
    CalibrationBucket,
    ReliabilityStats,
    StatsReport,
    _compute_calibration_curve,
)
from tradingagents.portfolio.prediction_grader import GradeResult


def _make_grade(prob: float, win: bool, **kwargs) -> GradeResult:
    defaults = dict(
        ticker="AAPL",
        trade_id=f"AAPL_2026-01-{int(prob*100):02d}T09:30:00",
        graded_at="2026-01-10T15:00:00",
        predicted_win_prob=prob,
        predicted_return=0.02,
        predicted_ll_prob=0.10,
        alpha_tier="A",
        alpha_score=0.80,
        breakout_score=0.75,
        regime_at_entry="bull",
        model_version="c46",
        actual_win=win,
        actual_return=0.03 if win else -0.02,
        actual_max_drawdown=-0.02,
        stop_hit=not win,
        target_hit=win,
        hold_days=5.0,
        regime_at_exit="bull",
        win_prediction_correct=(prob >= 0.60) == win,
        return_error=0.0,
        ll_prediction_correct=True,
        confidence_bucket="high" if prob >= 0.70 else "mid" if prob >= 0.60 else "low",
        return_bucket="gain" if win else "loss",
    )
    defaults.update(kwargs)
    return GradeResult(**defaults)


# ── _compute_calibration_curve ────────────────────────────────────────────────

def test_empty_grades_returns_empty():
    assert _compute_calibration_curve([]) == []


def test_buckets_sorted_by_prob_low():
    grades = [
        _make_grade(0.72, True),
        _make_grade(0.55, False),
        _make_grade(0.62, True),
    ]
    curve = _compute_calibration_curve(grades)
    lows = [b.prob_low for b in curve]
    assert lows == sorted(lows)


def test_correct_bucket_assignment():
    # 0.63 → [0.60–0.65), 0.64 → [0.60–0.65): both in same bin
    grades = [_make_grade(0.63, True), _make_grade(0.64, False)]
    curve = _compute_calibration_curve(grades)
    assert len(curve) == 1
    b = curve[0]
    assert b.prob_low == 0.60
    assert b.prob_high == 0.65
    assert b.n == 2
    assert b.actual_win_rate == pytest.approx(0.50, abs=0.01)


def test_calibration_error_computed():
    # prob=0.72, wins=0 → predicted ~0.72, actual_wr=0.0 → error ≈ 0.72
    grades = [_make_grade(0.72, False), _make_grade(0.73, False)]
    curve = _compute_calibration_curve(grades)
    high_bucket = [b for b in curve if 0.70 <= b.prob_low < 0.75][0]
    assert high_bucket.calibration_error == pytest.approx(
        abs(high_bucket.mean_predicted_prob - high_bucket.actual_win_rate), abs=0.001
    )


def test_custom_edges():
    grades = [_make_grade(0.55, True), _make_grade(0.75, False)]
    curve = _compute_calibration_curve(grades, edges=[0.50, 0.65, 1.01])
    assert len(curve) == 2
    assert curve[0].prob_low == 0.50
    assert curve[1].prob_low == 0.65


def test_below_threshold_excluded():
    # Grades with prob < 0.50 are not in default bins
    grades = [_make_grade(0.40, True), _make_grade(0.45, False)]
    curve = _compute_calibration_curve(grades)
    assert curve == []


def test_calibration_bucket_to_dict():
    b = CalibrationBucket(
        label="0.60–0.65", prob_low=0.60, prob_high=0.65,
        n=10, mean_predicted_prob=0.62, actual_win_rate=0.70, calibration_error=0.08,
    )
    d = b.to_dict()
    assert d["label"] == "0.60–0.65"
    assert d["n"] == 10


# ── StatsReport.calibration_curve field ──────────────────────────────────────

def test_compute_populates_calibration_curve():
    grades = [
        _make_grade(0.62, True),
        _make_grade(0.63, False),
        _make_grade(0.72, True),
        _make_grade(0.73, True),
        _make_grade(0.74, False),
    ]
    rs = ReliabilityStats()
    report = rs.compute(grades)
    assert isinstance(report.calibration_curve, list)
    assert len(report.calibration_curve) > 0
    for entry in report.calibration_curve:
        assert "label" in entry
        assert "n" in entry
        assert "actual_win_rate" in entry
        assert "calibration_error" in entry


def test_calibration_curve_in_to_dict():
    grades = [_make_grade(0.62, True), _make_grade(0.72, False)]
    rs = ReliabilityStats()
    report = rs.compute(grades)
    d = report.to_dict()
    assert "calibration_curve" in d
    assert isinstance(d["calibration_curve"], list)


def test_calibration_curve_in_saved_and_loaded(tmp_path):
    grades = [_make_grade(0.62, True), _make_grade(0.63, False)]
    rs = ReliabilityStats()
    report = rs.compute(grades)
    path = tmp_path / "stats.json"
    rs.save(report, path)
    loaded = rs.load(path)
    assert loaded is not None
    assert isinstance(loaded.calibration_curve, list)


def test_empty_grades_calibration_curve_empty():
    rs = ReliabilityStats()
    report = rs.compute([])
    assert report.calibration_curve == []
