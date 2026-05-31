"""Feature distribution stability monitoring for TradingAgents ML pipeline.

Uses Population Stability Index (PSI) to detect when a feature's distribution
has shifted significantly between a reference period (training) and current
production data. Large PSI → feature is behaving differently → model's learned
relationship may no longer hold.

PSI interpretation (standard thresholds):
  PSI < 0.10  — stable; no action needed
  PSI 0.10–0.20 — minor shift; monitor closely
  PSI > 0.20  — significant shift; investigate and consider retraining
  PSI > 0.25  — FAIL gate; block bundle deployment until resolved

Usage:
    from tradingagents.portfolio.feature_monitor import FeatureMonitor
    monitor = FeatureMonitor()
    report = monitor.compute_psi_report(
        reference_df=train_features_df,
        production_df=recent_candidates_df,
        feature_names=ML_NUMERIC_FEATURES,
    )
    unstable = monitor.unstable_features(report)
    if unstable:
        print(f"Unstable features (PSI > 0.20): {unstable}")
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# PSI thresholds
PSI_STABLE = 0.10      # < 0.10 → no action
PSI_WATCH = 0.20       # 0.10–0.20 → monitor
PSI_SIGNIFICANT = 0.25  # > 0.25 → fail gate


def _psi_one(ref_vals: np.ndarray, prod_vals: np.ndarray, n_bins: int = 10) -> float:
    """Compute PSI between reference and production arrays for one feature.

    PSI = Σ (actual_pct - expected_pct) × ln(actual_pct / expected_pct)

    Uses quantile-based binning on the reference distribution to handle skewed features.
    Both arrays are expected to be float arrays with NaNs dropped.
    """
    ref_clean = ref_vals[~np.isnan(ref_vals)]
    prod_clean = prod_vals[~np.isnan(prod_vals)]

    # Cycle 44 CR-1: require ≥30 production samples — PSI on a 10-bin histogram is
    # extremely noisy below that (a single obs moves a bin ~10%), causing false
    # drift. Below the floor we cannot assess, so return 0.0 (do not flag) and let
    # the caller treat scarce data as "unknown" rather than a drift signal.
    if len(ref_clean) < 50 or len(prod_clean) < 30:
        return 0.0  # not enough data to assess

    # Bin edges from reference distribution
    quantiles = np.linspace(0, 100, n_bins + 1)
    breakpoints = np.percentile(ref_clean, quantiles)
    # Ensure unique breakpoints
    breakpoints = np.unique(breakpoints)
    if len(breakpoints) < 3:
        return 0.0  # all values the same → constant feature, no shift

    # Clip to bin edges
    ref_binned = np.digitize(ref_clean, breakpoints[1:-1])
    prod_binned = np.digitize(prod_clean, breakpoints[1:-1])

    n_bins_actual = len(breakpoints) - 1

    def _pct(arr, n):
        counts = np.bincount(arr, minlength=n)[:n]
        pcts = counts / len(arr)
        return np.clip(pcts, 1e-4, None)  # avoid log(0)

    ref_pct = _pct(ref_binned, n_bins_actual)
    prod_pct = _pct(prod_binned, n_bins_actual)

    psi = float(np.sum((prod_pct - ref_pct) * np.log(prod_pct / ref_pct)))
    return round(psi, 6)


class FeatureMonitor:
    """Compute and report feature distribution stability.

    Parameters
    ----------
    warn_threshold : float
        PSI above this triggers a WARNING tag. Default 0.20.
    fail_threshold : float
        PSI above this triggers a FAIL tag (blocks bundle swap in retrain_weekly). Default 0.25.
    n_bins : int
        Number of bins for PSI computation. Default 10.
    """

    def __init__(
        self,
        warn_threshold: float = PSI_WATCH,
        fail_threshold: float = PSI_SIGNIFICANT,
        n_bins: int = 10,
    ):
        self.warn_threshold = warn_threshold
        self.fail_threshold = fail_threshold
        self.n_bins = n_bins

    def compute_psi_report(
        self,
        reference_df,  # pd.DataFrame
        production_df,  # pd.DataFrame
        feature_names: List[str],
    ) -> Dict[str, dict]:
        """Compute PSI for each numeric feature.

        Parameters
        ----------
        reference_df : DataFrame
            Training/reference period feature values.
        production_df : DataFrame
            Recent production/candidate feature values.
        feature_names : list of str
            Feature names to evaluate (numeric only).

        Returns
        -------
        dict: {feature_name: {"psi": float, "status": "stable"|"watch"|"fail"}}
        """
        report: Dict[str, dict] = {}
        for feat in feature_names:
            if feat not in reference_df.columns or feat not in production_df.columns:
                report[feat] = {"psi": None, "status": "missing"}
                continue
            try:
                ref_vals = reference_df[feat].to_numpy(dtype=float, na_value=float("nan"))
                prod_vals = production_df[feat].to_numpy(dtype=float, na_value=float("nan"))
                psi = _psi_one(ref_vals, prod_vals, self.n_bins)
            except Exception:
                report[feat] = {"psi": None, "status": "error"}
                continue

            if psi >= self.fail_threshold:
                status = "fail"
            elif psi >= self.warn_threshold:
                status = "watch"
            else:
                status = "stable"

            report[feat] = {"psi": psi, "status": status}

        return report

    def unstable_features(self, report: Dict[str, dict]) -> List[str]:
        """Return list of feature names with status 'fail'."""
        return [f for f, d in report.items() if d.get("status") == "fail"]

    def watched_features(self, report: Dict[str, dict]) -> List[str]:
        """Return list of feature names with status 'watch'."""
        return [f for f, d in report.items() if d.get("status") == "watch"]

    def summary(self, report: Dict[str, dict]) -> dict:
        """Return summary stats: counts per status, worst features."""
        n_stable = sum(1 for d in report.values() if d.get("status") == "stable")
        n_watch = sum(1 for d in report.values() if d.get("status") == "watch")
        n_fail = sum(1 for d in report.values() if d.get("status") == "fail")
        n_missing = sum(1 for d in report.values() if d.get("status") in ("missing", "error"))
        worst = sorted(
            [(f, d["psi"]) for f, d in report.items() if d.get("psi") is not None],
            key=lambda x: x[1], reverse=True
        )[:10]
        return {
            "n_stable": n_stable,
            "n_watch": n_watch,
            "n_fail": n_fail,
            "n_missing": n_missing,
            "total": len(report),
            "passes_gate": n_fail == 0,
            "worst_features": worst,
        }

    def save_report(self, report: Dict, output_path: Path) -> None:
        """Save PSI report to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2))

    def print_report(self, report: Dict, max_rows: int = 20) -> None:
        """Print feature PSI report to terminal."""
        rows = sorted(
            [(f, d) for f, d in report.items() if d.get("psi") is not None],
            key=lambda x: x[1].get("psi", 0), reverse=True
        )
        print(f"\n{'Feature':<30} {'PSI':>8}  Status")
        print("-" * 50)
        for feat, d in rows[:max_rows]:
            psi = d.get("psi", 0) or 0
            status = d.get("status", "?")
            icon = "✓" if status == "stable" else ("⚠" if status == "watch" else "✗")
            print(f"{feat:<30} {psi:>8.4f}  {icon} {status}")
        summary = self.summary(report)
        print(f"\n  Stable: {summary['n_stable']}  Watch: {summary['n_watch']}  FAIL: {summary['n_fail']}  Missing: {summary['n_missing']}")
        print(f"  Gate: {'PASS' if summary['passes_gate'] else 'FAIL (PSI > 0.25 features found)'}")
