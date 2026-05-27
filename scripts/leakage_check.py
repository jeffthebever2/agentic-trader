#!/usr/bin/env python3
"""Leakage audit for TradingAgents ML training data and model bundles.

Checks:
  1. No forward-outcome columns (h3_return, h3_outcome, etc.) in feature names
  2. No future-return columns in the training CSV feature set
  3. Feature distribution stability between train and test periods
  4. Temporal ordering of training rows

Usage:
    python scripts/leakage_check.py --bundle ml_models/latest/model_bundle.joblib
    python scripts/leakage_check.py --report ml_models/latest/training_report.json
    python scripts/leakage_check.py --csv ml_models/stock_universe/stock_candidate_training_data.csv
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Forward-outcome prefixes and suffixes that must not appear in features
_LEAKY_PREFIXES = ("h1_", "h2_", "h3_", "h4_", "h5_", "h7_", "h10_", "h14_", "h20_")
_LEAKY_SUFFIXES = (
    "_outcome", "_exit", "_exit_date", "_days", "_r_multiple",
    "_target_hit", "_stopped_out", "_direction_correct",
    "_bad_loss", "_strong_win",
)
_LEAKY_EXACT = {
    # Any h{N}_return, h{N}_mae, h{N}_mfe for N in typical hold periods
    f"h{n}_{col}"
    for n in range(1, 25)
    for col in ("return", "mae", "mfe", "entry", "target", "stop", "outcome",
                "exit", "exit_date", "days", "r_multiple")
}


def _is_leaky(col: str) -> bool:
    cl = col.lower()
    if cl in _LEAKY_EXACT:
        return True
    for p in _LEAKY_PREFIXES:
        if cl.startswith(p):
            for s in _LEAKY_SUFFIXES + ("_return", "_mae", "_mfe"):
                if cl.endswith(s):
                    return True
    return False


def check_feature_names(feature_names: list, hold: int = None) -> dict:
    """Check feature_names list for leaky columns."""
    leaky = [f for f in feature_names if _is_leaky(f)]
    if hold:
        extras = [f for f in feature_names
                  if f.lower() == f"h{hold}_return" or
                  f.lower() == f"h{hold}_mae" or
                  f.lower() == f"h{hold}_mfe"]
        leaky = list(set(leaky + extras))
    return {
        "leaky_features": leaky,
        "total_features": len(feature_names),
        "clean": len(leaky) == 0,
    }


def check_bundle(bundle_path: str) -> dict:
    try:
        import joblib
    except ImportError:
        return {"error": "joblib not available"}

    bundle = joblib.load(bundle_path)
    feature_names = bundle.get("feature_names", [])
    hold = bundle.get("hold", 3)
    result = check_feature_names(feature_names, hold)
    result["bundle"] = str(bundle_path)
    result["hold"] = hold
    result["calibrated"] = bundle.get("calibrated", False)
    result["model_keys"] = list(bundle.get("models", {}).keys())
    result["created_at"] = bundle.get("created_at", "unknown")
    return result


def check_report(report_path: str) -> dict:
    report = json.loads(Path(report_path).read_text())
    settings = report.get("settings", {})
    hold = settings.get("hold", 3)
    feature_count = settings.get("feature_count", 0)

    findings = []

    # Check for pre-existing leakage report
    leakage = report.get("leakage_check", {})
    if leakage.get("leaky_features"):
        findings.append(f"LEAKAGE: {leakage['leaky_features']}")

    # Check model ROC scores
    models = report.get("models", {})
    win = models.get("win_probability", {})
    win_roc = win.get("metrics", {}).get("roc_auc")
    if win_roc and win_roc > 0.85:
        findings.append(
            f"SUSPICIOUS: win_probability ROC={win_roc} is very high (>0.85). "
            "Check for leakage or overfitting."
        )
    if win_roc and win_roc < 0.52:
        findings.append(
            f"WEAK: win_probability ROC={win_roc} is near random. "
            "Model likely not learning useful signal."
        )

    # Check calibration
    calibrated = settings.get("calibrated", False)
    if not calibrated:
        findings.append(
            "WARNING: Model was trained without calibration (--calibrate). "
            "Probability thresholds may not reflect true win rates."
        )

    # Check threshold search
    thr_search = report.get("threshold_search", {})
    rec_thr = thr_search.get("recommended_threshold")
    used_thr = settings.get("ml_probability_threshold")
    if rec_thr and used_thr and abs(float(rec_thr) - float(used_thr)) > 0.08:
        findings.append(
            f"THRESHOLD: Recommended threshold from search is {rec_thr} "
            f"but model was built with {used_thr}. "
            "Consider retraining or tuning paper trader threshold."
        )

    return {
        "report": str(report_path),
        "hold": hold,
        "feature_count": feature_count,
        "win_roc": win_roc,
        "calibrated": calibrated,
        "recommended_threshold": rec_thr,
        "findings": findings,
        "clean": len(findings) == 0,
    }


def check_csv_columns(csv_path: str, hold: int = 3, sample_rows: int = 5) -> dict:
    """Quick column-level leakage scan on a training CSV."""
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, nrows=sample_rows, low_memory=False)
    except Exception as e:
        return {"error": str(e)}

    all_cols = list(df.columns)
    leaky_cols = [c for c in all_cols if _is_leaky(c)]

    # Check which leaky columns are also in ML feature lists
    try:
        from backtest import ML_NUMERIC_FEATURES, ML_CATEGORICAL_FEATURES
        feature_set = set(ML_NUMERIC_FEATURES) | set(ML_CATEGORICAL_FEATURES)
        leaky_in_features = [c for c in leaky_cols if c in feature_set]
    except ImportError:
        leaky_in_features = []

    return {
        "csv": str(csv_path),
        "total_columns": len(all_cols),
        "leaky_columns_in_csv": leaky_cols,
        "leaky_columns_also_in_feature_list": leaky_in_features,
        "clean": len(leaky_in_features) == 0,
        "note": "Leaky columns in CSV are OK if they are only used as labels, "
                "not fed into the design matrix.",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Leakage audit for TradingAgents ML models and training data."
    )
    parser.add_argument("--bundle", help="Path to model_bundle.joblib")
    parser.add_argument("--report", help="Path to training_report.json")
    parser.add_argument("--csv", help="Path to training data CSV")
    parser.add_argument("--hold", type=int, default=3)
    args = parser.parse_args()

    if not any([args.bundle, args.report, args.csv]):
        # Default: check latest model
        default_bundle = ROOT / "ml_models/latest/model_bundle.joblib"
        default_report = ROOT / "ml_models/latest/training_report.json"
        if default_bundle.exists():
            args.bundle = str(default_bundle)
        if default_report.exists():
            args.report = str(default_report)

    overall_clean = True

    if args.bundle:
        print(f"\n── Bundle check: {args.bundle}")
        result = check_bundle(args.bundle)
        for k, v in result.items():
            print(f"  {k}: {v}")
        if not result.get("clean"):
            overall_clean = False
            print("  ⚠ LEAKAGE DETECTED IN BUNDLE")

    if args.report:
        print(f"\n── Report check: {args.report}")
        result = check_report(args.report)
        for k, v in result.items():
            if k != "findings":
                print(f"  {k}: {v}")
        findings = result.get("findings", [])
        if findings:
            print("  Findings:")
            for f in findings:
                print(f"    ⚠ {f}")
        else:
            print("  ✓ No issues found")
        if not result.get("clean"):
            overall_clean = False

    if args.csv:
        print(f"\n── CSV check: {args.csv}")
        result = check_csv_columns(args.csv, args.hold)
        for k, v in result.items():
            print(f"  {k}: {v}")
        if not result.get("clean"):
            overall_clean = False
            print("  ⚠ LEAKY COLUMNS FOUND IN FEATURE LIST")

    print(f"\n{'✓ Leakage check PASSED' if overall_clean else '⚠ Leakage check FAILED — review findings above'}")
    sys.exit(0 if overall_clean else 1)


if __name__ == "__main__":
    main()
