"""Drift detector: measure model degradation from multiple angles.

Checks:
  1. Calibration drift: mean predicted_prob vs actual win rate (rolling N)
  2. High-confidence failure rate: % of high-conf trades that lost
  3. Paper vs walk-forward win rate gap
  4. Regime performance collapse (handled via ReliabilityStats.alert_regimes)
  5. PSI feature drift (reads existing ml_drift.json from paper_trade_today)

Usage::

    detector = DriftDetector()
    report = detector.check(
        grades=grades,
        validation_summary_path="validation_summary.json",
        drift_log_path="paper_accounts/algorithm/ml_drift.json",
    )
    if report.has_drift:
        print(report.alerts)
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── DriftReport ───────────────────────────────────────────────────────────────

@dataclass
class DriftReport:
    """Output of DriftDetector.check()."""
    checked_at: str
    has_drift: bool
    alerts: List[str]                        # human-readable alerts
    calibration_drift: Optional[float]       # mean(pred_prob) - actual_wr
    high_conf_failure_rate: Optional[float]  # pct of high-conf trades that lost
    paper_vs_wf_gap: Optional[float]         # paper_wr - wf_wr (negative = paper worse)
    psi_max: Optional[float]                 # highest PSI across features
    psi_fail_count: int
    n_grades: int
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


# ── DriftDetector ─────────────────────────────────────────────────────────────

class DriftDetector:
    """Detect model drift from multiple signals.

    Parameters
    ----------
    calibration_drift_threshold : float
        Alert if |mean_predicted_prob - actual_win_rate| > this (default 0.08).
    high_conf_failure_threshold : float
        Alert if >this fraction of high-confidence trades lost (default 0.45 = 45%).
    paper_vs_wf_gap_threshold : float
        Alert if paper_wr < wf_wr - this (default 0.10).
    psi_threshold : float
        PSI above this for a feature = drift (default 0.20).
    min_trades_for_drift : int
        Need at least this many grades before firing calibration/hcf alerts.
    high_conf_min_prob : float
        "High confidence" threshold (default 0.70).
    """

    def __init__(
        self,
        calibration_drift_threshold: float = 0.08,
        high_conf_failure_threshold: float = 0.45,
        paper_vs_wf_gap_threshold: float = 0.10,
        psi_threshold: float = 0.25,  # Cycle 44 TC-6: unified with feature_monitor.PSI_SIGNIFICANT (deploy & runtime fail at same level)
        min_trades_for_drift: int = 15,
        high_conf_min_prob: float = 0.70,
    ):
        self.calibration_drift_threshold = calibration_drift_threshold
        self.high_conf_failure_threshold = high_conf_failure_threshold
        self.paper_vs_wf_gap_threshold = paper_vs_wf_gap_threshold
        self.psi_threshold = psi_threshold
        self.min_trades_for_drift = min_trades_for_drift
        self.high_conf_min_prob = high_conf_min_prob

    # ── Calibration drift ─────────────────────────────────────────────────────

    def _check_calibration(
        self,
        grades: list,
        window: int = 30,
    ) -> tuple[Optional[float], List[str]]:
        """Return (drift_value, alert_list)."""
        # DD-2: sort by graded_at so windowing uses the most recent trades, not file-glob order
        sorted_grades = sorted(grades, key=lambda g: getattr(g, "graded_at", ""), reverse=False)
        recent = sorted_grades[-window:] if len(sorted_grades) > window else sorted_grades
        n = len(recent)
        if n < self.min_trades_for_drift:
            return None, []

        mean_pred = sum(g.predicted_win_prob for g in recent) / n
        actual_wr = sum(1 for g in recent if g.actual_win) / n
        drift = mean_pred - actual_wr  # positive = model overconfident

        alerts = []
        if abs(drift) > self.calibration_drift_threshold:
            direction = "OVERCONFIDENT" if drift > 0 else "UNDERCONFIDENT"
            alerts.append(
                f"CALIBRATION_DRIFT ({direction}): "
                f"mean_pred_prob={mean_pred:.3f} vs actual_wr={actual_wr:.3f} "
                f"drift={drift:+.3f} > ±{self.calibration_drift_threshold} (n={n})"
            )
        return round(drift, 4), alerts

    # ── High-confidence failure rate ──────────────────────────────────────────

    def _check_high_conf_failures(
        self,
        grades: list,
        window: int = 30,
    ) -> tuple[Optional[float], List[str]]:
        """Return (failure_rate, alerts)."""
        # DD-2: sort chronologically before windowing
        sorted_grades = sorted(grades, key=lambda g: getattr(g, "graded_at", ""), reverse=False)
        recent = sorted_grades[-window:] if len(sorted_grades) > window else sorted_grades
        high_conf = [g for g in recent if g.predicted_win_prob >= self.high_conf_min_prob]
        n = len(high_conf)
        if n < max(5, self.min_trades_for_drift // 2):
            return None, []

        fail_rate = sum(1 for g in high_conf if not g.actual_win) / n
        alerts = []
        if fail_rate > self.high_conf_failure_threshold:
            alerts.append(
                f"HIGH_CONF_FAILURE_RATE: {fail_rate:.1%} of high-confidence "
                f"(prob>={self.high_conf_min_prob:.0%}) trades lost "
                f"(n={n}, threshold={self.high_conf_failure_threshold:.0%})"
            )
        return round(fail_rate, 4), alerts

    # ── Paper vs walk-forward gap ─────────────────────────────────────────────

    def _check_paper_vs_wf(
        self,
        grades: list,
        validation_summary_path: Optional[Path],
        window: int = 30,
    ) -> tuple[Optional[float], List[str]]:
        """Return (gap, alerts)."""
        if validation_summary_path is None or not validation_summary_path.exists():
            return None, []

        try:
            val = json.loads(validation_summary_path.read_text())
        except Exception:
            return None, []

        # DD-5: trainer emits actual_win_rate / high_conf_win_rate, never wf_win_rate
        wf_block = val.get("walk_forward") or val.get("train", {})
        wf_wr = (
            wf_block.get("actual_win_rate")
            or wf_block.get("high_conf_win_rate")
            or val.get("actual_win_rate")
            or val.get("wf_win_rate")  # legacy fallback
        )
        if wf_wr is None:
            return None, []

        # DD-2: sort chronologically before windowing
        sorted_grades = sorted(grades, key=lambda g: getattr(g, "graded_at", ""), reverse=False)
        recent = sorted_grades[-window:] if len(sorted_grades) > window else sorted_grades
        n = len(recent)
        if n < self.min_trades_for_drift:
            return None, []

        paper_wr = sum(1 for g in recent if g.actual_win) / n
        gap = paper_wr - float(wf_wr)  # negative = paper underperforming WF

        alerts = []
        if gap < -self.paper_vs_wf_gap_threshold:
            alerts.append(
                f"PAPER_VS_WF_GAP: paper_wr={paper_wr:.3f} vs wf_wr={wf_wr:.3f} "
                f"gap={gap:+.3f} < -{self.paper_vs_wf_gap_threshold} (n={n})"
            )
        return round(gap, 4), alerts

    # ── PSI drift (from existing ml_drift.json) ───────────────────────────────

    def _check_psi(
        self,
        drift_log_path: Optional[Path],
    ) -> tuple[Optional[float], int, List[str]]:
        """Return (max_psi, n_fail, alerts)."""
        if drift_log_path is None or not drift_log_path.exists():
            return None, 0, []

        try:
            drift_data = json.loads(drift_log_path.read_text())
        except Exception:
            return None, 0, []

        # Handle both list-of-features and dict formats
        feature_psi: Dict[str, float] = {}
        raw = drift_data.get("feature_psi") or drift_data.get("psi") or {}
        if isinstance(raw, dict):
            feature_psi = {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    fname = item.get("feature") or item.get("name", "?")
                    fpsi = item.get("psi") or item.get("value", 0.0)
                    feature_psi[fname] = float(fpsi)

        if not feature_psi:
            # Cycle 44 CR-2: the top-level `drift` field is win-rate calibration
            # drift (|pred_wr − actual_wr|), NOT a population stability index.
            # It is handled by _check_calibration; do not relabel it as PSI here.
            return None, 0, []

        max_psi = max(feature_psi.values())
        n_fail = sum(1 for v in feature_psi.values() if v > self.psi_threshold)
        worst = sorted(feature_psi.items(), key=lambda x: -x[1])[:3]

        alerts = []
        if n_fail > 0:
            worst_str = ", ".join(f"{k}={v:.3f}" for k, v in worst)
            alerts.append(
                f"FEATURE_DRIFT_PSI: {n_fail} feature(s) with PSI > {self.psi_threshold} "
                f"(worst: {worst_str})"
            )
        return round(max_psi, 4), n_fail, alerts

    # ── Main: check ───────────────────────────────────────────────────────────

    def check(
        self,
        grades: list,
        validation_summary_path: Optional[str | Path] = None,
        drift_log_path: Optional[str | Path] = None,
        window: int = 30,
    ) -> DriftReport:
        """Run all drift checks and return a DriftReport.

        Parameters
        ----------
        grades : list of GradeResult
        validation_summary_path : Path, optional
            validation_summary.json from latest training/validation run.
        drift_log_path : Path, optional
            ml_drift.json written by paper_trade_today.py PSI check.
        window : int
            Rolling window for calibration / high-conf / WF checks.
        """
        all_alerts: List[str] = []

        val_path = Path(validation_summary_path) if validation_summary_path else None
        psi_path = Path(drift_log_path) if drift_log_path else None

        # ── 1. Calibration drift ─────────────────────────────────────────
        cal_drift, cal_alerts = self._check_calibration(grades, window)
        all_alerts.extend(cal_alerts)

        # ── 2. High-confidence failure rate ──────────────────────────────
        hcf_rate, hcf_alerts = self._check_high_conf_failures(grades, window)
        all_alerts.extend(hcf_alerts)

        # ── 3. Paper vs walk-forward gap ──────────────────────────────────
        pv_gap, pv_alerts = self._check_paper_vs_wf(grades, val_path, window)
        all_alerts.extend(pv_alerts)

        # ── 4. PSI feature drift ──────────────────────────────────────────
        psi_max, psi_fail, psi_alerts = self._check_psi(psi_path)
        all_alerts.extend(psi_alerts)

        return DriftReport(
            checked_at=dt.datetime.now().isoformat(),
            has_drift=len(all_alerts) > 0,
            alerts=all_alerts,
            calibration_drift=cal_drift,
            high_conf_failure_rate=hcf_rate,
            paper_vs_wf_gap=pv_gap,
            psi_max=psi_max,
            psi_fail_count=psi_fail,
            n_grades=len(grades),
            details={
                "calibration_window": window,
                "high_conf_threshold": self.high_conf_min_prob,
                "psi_threshold": self.psi_threshold,
            },
        )
