"""Rolling reliability statistics computed from graded paper trades.

Computes win rates, average returns, and prediction accuracy broken down by:
  - overall
  - by_ticker
  - by_regime
  - by_alpha_tier
  - by_confidence_bucket
  - by_model_version

Usage::

    from tradingagents.portfolio.prediction_grader import PredictionGrader
    from tradingagents.portfolio.reliability_stats import ReliabilityStats

    grader = PredictionGrader("paper_accounts/algorithm")
    grades = grader.grade_all()
    stats = ReliabilityStats()
    report = stats.compute(grades, window=50)
    stats.save(report, "paper_accounts/algorithm/reliability_stats.json")
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Slice stats ───────────────────────────────────────────────────────────────

@dataclass
class SliceStats:
    n: int
    win_rate: float
    avg_return: float
    avg_predicted_prob: float
    calibration_error: float      # |mean_predicted_prob - actual_win_rate|
    avg_return_error: float       # mean |predicted_return - actual_return|
    stop_rate: float
    target_rate: float
    win_prediction_accuracy: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _compute_slice(grades: list) -> Optional[SliceStats]:
    n = len(grades)
    if n == 0:
        return None
    win_rate = sum(1 for g in grades if g.actual_win) / n
    avg_ret = sum(g.actual_return for g in grades) / n
    avg_prob = sum(g.predicted_win_prob for g in grades) / n
    cal_err = abs(avg_prob - win_rate)
    avg_ret_err = sum(abs(g.return_error) for g in grades) / n
    stop_rate = sum(1 for g in grades if g.stop_hit) / n
    target_rate = sum(1 for g in grades if g.target_hit) / n
    win_pred_acc = sum(1 for g in grades if g.win_prediction_correct) / n
    return SliceStats(
        n=n,
        win_rate=round(win_rate, 4),
        avg_return=round(avg_ret, 4),
        avg_predicted_prob=round(avg_prob, 4),
        calibration_error=round(cal_err, 4),
        avg_return_error=round(avg_ret_err, 4),
        stop_rate=round(stop_rate, 4),
        target_rate=round(target_rate, 4),
        win_prediction_accuracy=round(win_pred_acc, 4),
    )


# ── StatsReport ───────────────────────────────────────────────────────────────

@dataclass
class StatsReport:
    """Rolling reliability statistics across multiple slices."""
    computed_at: str
    window_trades: int                     # how many grades used
    overall: Optional[SliceStats] = None
    by_ticker: Dict[str, Dict] = field(default_factory=dict)
    by_regime: Dict[str, Dict] = field(default_factory=dict)
    by_tier: Dict[str, Dict] = field(default_factory=dict)
    by_confidence_bucket: Dict[str, Dict] = field(default_factory=dict)
    by_model_version: Dict[str, Dict] = field(default_factory=dict)
    by_return_bucket: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "computed_at": self.computed_at,
            "window_trades": self.window_trades,
            "overall": self.overall.to_dict() if self.overall else {},
            "by_ticker": self.by_ticker,
            "by_regime": self.by_regime,
            "by_tier": self.by_tier,
            "by_confidence_bucket": self.by_confidence_bucket,
            "by_model_version": self.by_model_version,
            "by_return_bucket": self.by_return_bucket,
        }
        return d

    def summary_str(self) -> str:
        if not self.overall:
            return f"n={self.window_trades} no stats"
        o = self.overall
        return (
            f"n={o.n} wr={o.win_rate:.1%} avg_ret={o.avg_return:.2%} "
            f"cal_err={o.calibration_error:.3f} pred_acc={o.win_prediction_accuracy:.1%}"
        )


# ── ReliabilityStats ──────────────────────────────────────────────────────────

class ReliabilityStats:
    """Compute rolling reliability statistics from GradeResult objects.

    Parameters
    ----------
    min_n_for_slice : int
        Minimum number of grades in a slice to include it in the report.
    """

    def __init__(self, min_n_for_slice: int = 5):
        self.min_n_for_slice = min_n_for_slice

    def compute(self, grades: list, window: int = 50) -> StatsReport:
        """Compute StatsReport from the most recent N grades.

        Parameters
        ----------
        grades : list of GradeResult
        window : int
            Rolling window — use only the last N grades.
        """
        # Sort by graded_at and take last N
        try:
            grades_sorted = sorted(grades, key=lambda g: g.graded_at)
        except Exception:
            grades_sorted = list(grades)

        recent = grades_sorted[-window:] if window > 0 else grades_sorted
        n = len(recent)

        if n == 0:
            return StatsReport(
                computed_at=dt.datetime.now().isoformat(),
                window_trades=0,
            )

        # ── Overall ───────────────────────────────────────────────────────
        overall = _compute_slice(recent)

        # ── By ticker ─────────────────────────────────────────────────────
        by_ticker: Dict[str, Dict] = {}
        tickers = set(g.ticker for g in recent)
        for t in tickers:
            sl = _compute_slice([g for g in recent if g.ticker == t])
            if sl and sl.n >= self.min_n_for_slice:
                by_ticker[t] = sl.to_dict()

        # ── By regime ─────────────────────────────────────────────────────
        by_regime: Dict[str, Dict] = {}
        regimes = set(g.regime_at_entry for g in recent)
        for r in regimes:
            sl = _compute_slice([g for g in recent if g.regime_at_entry == r])
            if sl and sl.n >= self.min_n_for_slice:
                by_regime[r] = sl.to_dict()

        # ── By tier ───────────────────────────────────────────────────────
        by_tier: Dict[str, Dict] = {}
        for tier in ("A+", "A", "B", "C"):
            sl = _compute_slice([g for g in recent if g.alpha_tier == tier])
            if sl and sl.n >= self.min_n_for_slice:
                by_tier[tier] = sl.to_dict()

        # ── By confidence bucket ──────────────────────────────────────────
        by_conf: Dict[str, Dict] = {}
        for bucket in ("high", "mid", "low"):
            sl = _compute_slice([g for g in recent if g.confidence_bucket == bucket])
            if sl and sl.n >= self.min_n_for_slice:
                by_conf[bucket] = sl.to_dict()

        # ── By model version ──────────────────────────────────────────────
        by_mv: Dict[str, Dict] = {}
        versions = set(g.model_version for g in recent)
        for v in versions:
            sl = _compute_slice([g for g in recent if g.model_version == v])
            if sl and sl.n >= self.min_n_for_slice:
                by_mv[v] = sl.to_dict()

        # ── By return bucket ──────────────────────────────────────────────
        by_ret: Dict[str, Dict] = {}
        for bucket in ("gain", "small_gain", "loss"):
            sl = _compute_slice([g for g in recent if g.return_bucket == bucket])
            if sl and sl.n >= self.min_n_for_slice:
                by_ret[bucket] = sl.to_dict()

        return StatsReport(
            computed_at=dt.datetime.now().isoformat(),
            window_trades=n,
            overall=overall,
            by_ticker=by_ticker,
            by_regime=by_regime,
            by_tier=by_tier,
            by_confidence_bucket=by_conf,
            by_model_version=by_mv,
            by_return_bucket=by_ret,
        )

    def save(self, report: StatsReport, path: str | Path) -> None:
        """Write report to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2))

    def load(self, path: str | Path) -> Optional[StatsReport]:
        """Load a previously saved StatsReport."""
        p = Path(path)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text())
            overall_d = d.get("overall") or {}
            overall = SliceStats(**overall_d) if overall_d else None
            return StatsReport(
                computed_at=d.get("computed_at", ""),
                window_trades=d.get("window_trades", 0),
                overall=overall,
                by_ticker=d.get("by_ticker", {}),
                by_regime=d.get("by_regime", {}),
                by_tier=d.get("by_tier", {}),
                by_confidence_bucket=d.get("by_confidence_bucket", {}),
                by_model_version=d.get("by_model_version", {}),
                by_return_bucket=d.get("by_return_bucket", {}),
            )
        except Exception:
            return None

    def alert_regimes(self, report: StatsReport, min_n: int = 5, wr_threshold: float = 0.35) -> List[str]:
        """Return alert strings for regimes with very low win rates."""
        alerts = []
        for regime, stats_d in report.by_regime.items():
            n = stats_d.get("n", 0)
            wr = stats_d.get("win_rate", 1.0)
            if n >= min_n and wr < wr_threshold:
                alerts.append(f"REGIME_COLLAPSE: {regime} win_rate={wr:.1%} (n={n}) < {wr_threshold:.0%}")
        return alerts

    def alert_calibration(self, report: StatsReport, threshold: float = 0.08) -> List[str]:
        """Return alert strings for severe calibration drift."""
        alerts = []
        if report.overall and report.overall.n >= 10:
            ce = report.overall.calibration_error
            if ce > threshold:
                alerts.append(
                    f"CALIBRATION_DRIFT: error={ce:.3f} > {threshold} "
                    f"(predicted_prob={report.overall.avg_predicted_prob:.3f} "
                    f"vs actual_wr={report.overall.win_rate:.3f})"
                )
        return alerts

    def alert_monotonicity(self, grades: list) -> List[str]:
        """RS-1 (GC-7): check that high-confidence trades win more than low-confidence.

        A constant-0.6 predictor with zero discrimination passes calibration_error≈0
        but the high-confidence bucket wins no better than the low-confidence bucket.
        Alert when: high_conf.WR ≤ low_conf.WR  OR  high_conf.WR ≤ base_rate.
        """
        if not grades or len(grades) < 10:
            return []
        base_wr = sum(1 for g in grades if g.actual_win) / len(grades)
        low_conf  = [g for g in grades if g.predicted_win_prob < 0.60]
        high_conf = [g for g in grades if g.predicted_win_prob >= 0.70]
        alerts = []
        if len(high_conf) >= 5 and len(low_conf) >= 5:
            high_wr = sum(1 for g in high_conf if g.actual_win) / len(high_conf)
            low_wr  = sum(1 for g in low_conf  if g.actual_win) / len(low_conf)
            if high_wr <= low_wr:
                alerts.append(
                    f"MONOTONICITY_FAIL: high_conf WR={high_wr:.1%} (n={len(high_conf)}) "
                    f"≤ low_conf WR={low_wr:.1%} (n={len(low_conf)}) — model not discriminating"
                )
            if high_wr <= base_wr:
                alerts.append(
                    f"MONOTONICITY_FAIL: high_conf WR={high_wr:.1%} (n={len(high_conf)}) "
                    f"≤ base_rate={base_wr:.1%} — high-confidence trades underperform random"
                )
        return alerts
