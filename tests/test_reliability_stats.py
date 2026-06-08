"""Tests for ReliabilityStats.alert_monotonicity — FE-3."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.portfolio.prediction_grader import GradeResult
from tradingagents.portfolio.reliability_stats import ReliabilityStats


def _grade(predicted_win_prob: float, actual_win: bool, n: int = 1) -> list:
    """Build a list of minimal GradeResult objects for testing."""
    results = []
    for i in range(n):
        results.append(GradeResult(
            ticker="AAPL",
            trade_id=f"AAPL_2024-01-{i+1:02d}",
            graded_at="2024-01-15T10:00:00",
            predicted_win_prob=predicted_win_prob,
            predicted_return=0.02,
            predicted_ll_prob=0.05,
            alpha_tier="A",
            alpha_score=0.8,
            breakout_score=0.6,
            regime_at_entry="bull",
            model_version="20240101",
            actual_win=actual_win,
            actual_return=0.03 if actual_win else -0.02,
            actual_max_drawdown=0.01,
            stop_hit=not actual_win,
            target_hit=actual_win,
            hold_days=3.0,
            regime_at_exit="bull",
            win_prediction_correct=(predicted_win_prob >= 0.60) == actual_win,
            return_error=0.01,
            ll_prediction_correct=True,
            confidence_bucket="high" if predicted_win_prob >= 0.70 else ("mid" if predicted_win_prob >= 0.60 else "low"),
            return_bucket="gain" if actual_win else "loss",
        ))
    return results


class TestAlertMonotonicity:
    def test_fires_when_high_conf_wins_less_than_low(self):
        """Alert fires: high-confidence WR ≤ low-confidence WR (model not discriminating)."""
        # 10 high-conf (>=0.70) trades that all LOSE
        high_lose = _grade(0.75, False, 10)
        # 10 low-conf (<0.60) trades that all WIN
        low_win = _grade(0.45, True, 10)

        grades = high_lose + low_win
        rs = ReliabilityStats()
        alerts = rs.alert_monotonicity(grades)

        assert len(alerts) >= 1, f"Expected monotonicity alert, got: {alerts}"
        assert any("MONOTONICITY_FAIL" in a for a in alerts)

    def test_no_alert_when_high_conf_wins_more(self):
        """No alert when high-confidence trades outperform low-confidence."""
        high_win = _grade(0.80, True, 10)
        low_lose = _grade(0.45, False, 10)

        grades = high_win + low_lose
        rs = ReliabilityStats()
        alerts = rs.alert_monotonicity(grades)

        monoton_alerts = [a for a in alerts if "MONOTONICITY_FAIL" in a]
        assert len(monoton_alerts) == 0, f"Unexpected alert: {monoton_alerts}"

    def test_no_alert_insufficient_data(self):
        """Fewer than 10 total grades → no alerts (can't compute reliable stats)."""
        grades = _grade(0.75, False, 3)
        rs = ReliabilityStats()
        alerts = rs.alert_monotonicity(grades)
        assert alerts == []

    def test_no_alert_bucket_too_small(self):
        """High or low conf bucket < 5 → no alert (avoid spurious firing on tiny samples)."""
        # Only 3 high-conf trades, 20 low-conf trades
        high_few = _grade(0.75, False, 3)
        low_many = _grade(0.45, True, 20)

        grades = high_few + low_many
        rs = ReliabilityStats()
        alerts = rs.alert_monotonicity(grades)
        # Should not fire because high_conf bucket has only 3 samples
        assert alerts == []

    def test_alert_when_high_conf_below_base_rate(self):
        """Alert fires: high-confidence WR ≤ overall base rate."""
        # 10 high-conf trades that lose (0% WR)
        high_lose = _grade(0.75, False, 10)
        # 10 low-conf trades that also lose — base rate = 0%
        # That makes both equal, which triggers the ≤ base_rate check
        low_lose = _grade(0.45, False, 10)
        # Add a few wins to push base_rate above 0
        some_wins = _grade(0.50, True, 5)

        grades = high_lose + low_lose + some_wins
        rs = ReliabilityStats()
        alerts = rs.alert_monotonicity(grades)

        # high_conf (all 10 lose → 0% WR), base_rate = 5/25 = 20% → should fire
        assert any("MONOTONICITY_FAIL" in a for a in alerts), f"Expected alert, got: {alerts}"

    def test_alert_calibration_fires_on_high_error(self):
        """alert_calibration fires when calibration error exceeds threshold."""
        from tradingagents.portfolio.reliability_stats import (
            ReliabilityStats, StatsReport, SliceStats,
        )

        high_cal_err = SliceStats(
            n=30, win_rate=0.40, avg_return=0.01, avg_predicted_prob=0.75,
            calibration_error=0.35,  # 75% predicted, 40% actual
            avg_return_error=0.02, stop_rate=0.30, target_rate=0.20,
            win_prediction_accuracy=0.55,
        )
        report = StatsReport(
            computed_at="2024-01-15T10:00:00",
            window_trades=50,
            overall=high_cal_err,
        )
        rs = ReliabilityStats()
        alerts = rs.alert_calibration(report, threshold=0.08)

        assert len(alerts) >= 1, f"Expected calibration alert, got: {alerts}"
        assert any("CALIBRATION" in a.upper() for a in alerts)

    def test_alert_calibration_silent_on_good_calibration(self):
        """alert_calibration silent when error is within threshold."""
        from tradingagents.portfolio.reliability_stats import (
            ReliabilityStats, StatsReport, SliceStats,
        )

        ok_cal = SliceStats(
            n=30, win_rate=0.55, avg_return=0.02, avg_predicted_prob=0.57,
            calibration_error=0.02,
            avg_return_error=0.01, stop_rate=0.20, target_rate=0.30,
            win_prediction_accuracy=0.65,
        )
        report = StatsReport(
            computed_at="2024-01-15T10:00:00",
            window_trades=50,
            overall=ok_cal,
        )
        rs = ReliabilityStats()
        alerts = rs.alert_calibration(report, threshold=0.08)
        assert alerts == []
