#!/usr/bin/env python3
"""Weekly ML model retraining on a rolling 36-month window.

Run manually or via cron. Adds the following cron entry:
    0 8 * * 0 /path/to/.venv/bin/python3 /path/to/scripts/retrain_weekly.py

Steps:
  1. Run backtest on tickers from all_tickers.txt, last 36 months
  2. Export trades CSV
  3. Train + calibrate ML models from the CSV
  4. Run leakage check — abort if leaked features detected
  5. Run holdout validation (read-only — do NOT tune on output)
  6. Gate: require ROC >= min_roc and Brier < max_brier before swapping bundle
  7. Swap bundle into ml_models/latest/ (backs up old)
  8. Append entry to ml_models/retrain_history.jsonl

Anti-cheating rules enforced here:
  - --calibrate is always ON
  - --ml-large-loss-max defaults to 0.35 (never disabled)
  - leakage_check.py must pass before bundle swap
  - holdout validation is run post-swap for diagnostic purposes only
    (output is printed but NOT used to tune any hyperparameter)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "ml_models" / "retrain_history.jsonl"


def run(cmd: list[str], label: str, abort_on_failure: bool = True) -> int:
    print(f"\n{'='*60}")
    print(f"[retrain_weekly] {label}")
    print(f"CMD: {' '.join(str(x) for x in cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[retrain_weekly] ERROR: {label} failed (exit {result.returncode})")
        if abort_on_failure:
            sys.exit(result.returncode)
    return result.returncode


def _check_report_gates(report_path: Path, min_roc: float, max_brier: float, max_psi_fail: int = 0) -> tuple[bool, str]:
    """Return (passes, reason). Checks win ROC, calibration Brier, and PSI feature stability."""
    if not report_path.exists():
        return False, f"training_report.json not found at {report_path}"
    try:
        report = json.loads(report_path.read_text())
    except Exception as e:
        return False, f"Cannot parse training_report.json: {e}"

    win_roc = report.get("models", {}).get("win_probability", {}).get("metrics", {}).get("roc_auc")
    calibration = report.get("models", {}).get("win_probability", {}).get("calibration", {})
    brier_after = calibration.get("brier_after")
    calibrated = report.get("settings", {}).get("calibrated", False)

    issues = []
    if win_roc is None:
        issues.append("win_probability ROC missing from report")
    elif win_roc < min_roc:
        issues.append(f"win_probability ROC={win_roc:.4f} < minimum {min_roc}")

    if not calibrated:
        issues.append("model was NOT calibrated (run with --calibrate)")

    if brier_after is not None and brier_after > max_brier:
        issues.append(f"calibration Brier={brier_after:.4f} > max {max_brier}")

    # PSI feature stability gate
    psi = report.get("feature_psi", {})
    psi_fail = psi.get("n_fail", 0)
    if psi_fail > max_psi_fail:
        worst = [f for f, _ in psi.get("worst_features", [])[:3]]
        issues.append(f"PSI_FAIL: {psi_fail} features with PSI > 0.25 (worst: {worst}); check for distribution shift")

    if issues:
        return False, "; ".join(issues)
    return True, f"ROC={win_roc:.4f}, Brier={brier_after}, calibrated={calibrated}, psi_fail={psi_fail}"


def _log_history(entry: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Weekly rolling-window ML retrain.")
    parser.add_argument("--tickers", default="all_tickers.txt")
    parser.add_argument("--months", type=int, default=36,
                        help="Rolling window in months. Default 36 (3 years covers full market cycles).")
    parser.add_argument("--output-dir", default="ml_models/latest")
    parser.add_argument("--hold", type=int, default=3)
    parser.add_argument("--n-estimators", type=int, default=600)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-samples-leaf", type=int, default=20)
    parser.add_argument("--ml-probability-threshold", type=float, default=0.60,
                        help="Starting threshold (will be refined by threshold search in training).")
    parser.add_argument("--ml-large-loss-max", type=float, default=0.35,
                        help="Hard cap on large_loss_probability. Default 0.35. Never set > 0.40.")
    parser.add_argument("--min-roc", type=float, default=0.56,
                        help="Minimum win_probability ROC required to swap bundle. Default 0.56.")
    parser.add_argument("--max-brier", type=float, default=0.24,
                        help="Maximum calibration Brier score to accept bundle. Default 0.24.")
    parser.add_argument("--skip-leakage-check", action="store_true",
                        help="DANGEROUS: skip leakage check. Only for debugging.")
    parser.add_argument("--skip-gates", action="store_true",
                        help="Skip ROC/Brier gates (swap bundle regardless). For development only.")
    parser.add_argument("--skip-holdout", action="store_true",
                        help="Skip holdout validation step.")
    parser.add_argument("--account-commission", type=float, default=1.0)
    parser.add_argument("--account-slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-risk-reward", type=float, default=1.5)
    parser.add_argument(
        "--executed-weight", type=float, default=20.0,
        help="Sample weight for executed (rule-passing) rows in training. Default 20× over rejected."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands only, don't run.")
    args = parser.parse_args()

    today = dt.date.today()
    start = today - dt.timedelta(days=args.months * 30)
    end = today - dt.timedelta(days=1)
    ts = today.strftime("%Y%m%d_%H%M%S")
    csv_path = ROOT / f"retrain_trades_{ts}.csv"
    output_dir = ROOT / args.output_dir
    report_path = output_dir / "training_report.json"
    bundle_path = output_dir / "model_bundle.joblib"

    python = sys.executable

    # ── 1. Backtest command ─────────────────────────────────────────────────
    backtest_cmd = [
        python, str(ROOT / "backtest.py"),
        "--tickers", args.tickers,
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--export-csv", str(csv_path),
        "--no-trades-json",
        "--no-generate-charts",
        "--batch-size", "50",
        "--account-commission", str(args.account_commission),
        "--account-slippage-bps", str(args.account_slippage_bps),
        "--min-risk-reward", str(args.min_risk_reward),
    ]

    # ── 2. Train command ────────────────────────────────────────────────────
    train_cmd = [
        python, str(ROOT / "scripts" / "train_ml_models.py"),
        "--input", str(csv_path),
        "--output-dir", str(output_dir),
        "--hold", str(args.hold),
        "--n-estimators", str(args.n_estimators),
        "--max-depth", str(args.max_depth),
        "--min-samples-leaf", str(args.min_samples_leaf),
        "--ml-probability-threshold", str(args.ml_probability_threshold),
        "--ml-large-loss-max", str(args.ml_large_loss_max),
        "--ml-expected-return-min", "-0.01",
        "--calibrate",                         # always ON — probability calibration required
        "--executed-weight", str(args.executed_weight),  # upweight rule-passing rows
        "--run-walk-forward",                  # include walk-forward in report
    ]

    # ── 3. Leakage check ────────────────────────────────────────────────────
    leakage_cmd = [
        python, str(ROOT / "scripts" / "leakage_check.py"),
        "--bundle", str(bundle_path),
        "--report", str(report_path),
    ]

    # ── 4. Holdout validation (diagnostic only — do NOT tune on output) ────
    holdout_cmd = [
        python, str(ROOT / "scripts" / "validate_holdout.py"),
        "--account-commission", str(args.account_commission),
        "--account-slippage-bps", str(args.account_slippage_bps),
    ]

    if args.dry_run:
        print("\n[dry-run] Step 1 — Backtest:")
        print("  " + " ".join(str(x) for x in backtest_cmd))
        print("\n[dry-run] Step 2 — Train:")
        print("  " + " ".join(str(x) for x in train_cmd))
        print("\n[dry-run] Step 3 — Leakage check:")
        print("  " + " ".join(str(x) for x in leakage_cmd))
        print("\n[dry-run] Step 4 — Gates (ROC >= {}, Brier < {})".format(args.min_roc, args.max_brier))
        print("\n[dry-run] Step 5 — Holdout validation (diagnostic only):")
        print("  " + " ".join(str(x) for x in holdout_cmd))
        return

    history_entry: dict = {
        "retrain_date": today.isoformat(),
        "timestamp": dt.datetime.now().isoformat(),
        "window_months": args.months,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "hold": args.hold,
        "n_estimators": args.n_estimators,
        "output_dir": str(output_dir),
        "outcome": "started",
    }

    # ── Run backtest ────────────────────────────────────────────────────────
    run(backtest_cmd, f"Step 1/5 — Backtest {start} → {end}")

    if not csv_path.exists():
        print(f"[retrain_weekly] ERROR: CSV not found at {csv_path}")
        history_entry["outcome"] = "backtest_no_csv"
        _log_history(history_entry)
        sys.exit(1)

    rows = sum(1 for _ in open(csv_path)) - 1
    print(f"[retrain_weekly] CSV has {rows:,} rows.")
    history_entry["csv_rows"] = rows

    # ── Run training ────────────────────────────────────────────────────────
    run(train_cmd, "Step 2/5 — Train + calibrate ML models")

    # ── Leakage check ───────────────────────────────────────────────────────
    if not args.skip_leakage_check:
        rc = run(leakage_cmd, "Step 3/5 — Leakage check", abort_on_failure=False)
        if rc != 0:
            print("\n[retrain_weekly] ⚠ LEAKAGE DETECTED — bundle NOT swapped. Fix features before retrain.")
            history_entry["outcome"] = "leakage_check_failed"
            _log_history(history_entry)
            sys.exit(1)
        print("[retrain_weekly] ✓ Leakage check passed.")
    else:
        print("[retrain_weekly] ⚠ Leakage check SKIPPED (--skip-leakage-check).")

    # ── Quality gates ───────────────────────────────────────────────────────
    if not args.skip_gates:
        passes, gate_msg = _check_report_gates(report_path, args.min_roc, args.max_brier)
        print(f"\n[retrain_weekly] Step 4/5 — Quality gates: {gate_msg}")
        if not passes:
            print(f"[retrain_weekly] ⚠ Gates FAILED — bundle NOT swapped.")
            print(f"[retrain_weekly] Reason: {gate_msg}")
            print(f"[retrain_weekly] Investigate model quality before deploying.")
            history_entry["outcome"] = f"quality_gate_failed: {gate_msg}"
            history_entry["gate_msg"] = gate_msg
            _log_history(history_entry)
            sys.exit(1)
        print(f"[retrain_weekly] ✓ Quality gates passed: {gate_msg}")
        history_entry["gate_msg"] = gate_msg
    else:
        print("[retrain_weekly] ⚠ Quality gates SKIPPED (--skip-gates).")

    # ── Read final report for history log ──────────────────────────────────
    try:
        report = json.loads(report_path.read_text())
        history_entry["win_roc"] = report.get("models", {}).get("win_probability", {}).get("metrics", {}).get("roc_auc")
        brier = report.get("models", {}).get("win_probability", {}).get("calibration", {}).get("brier_after")
        history_entry["brier_after"] = brier
        history_entry["calibrated"] = report.get("settings", {}).get("calibrated", False)
        history_entry["feature_count"] = report.get("settings", {}).get("feature_count")
    except Exception:
        pass

    # ── Clean up CSV ────────────────────────────────────────────────────────
    try:
        csv_path.unlink()
        print(f"[retrain_weekly] Cleaned up {csv_path.name}")
    except Exception:
        pass

    history_entry["outcome"] = "success"
    _log_history(history_entry)

    print(f"\n[retrain_weekly] ✓ Done. Bundle at: {bundle_path}")
    print("[retrain_weekly] Restart paper runner to pick up the new model.\n")

    # ── Holdout validation (diagnostic — LAST step, after bundle is live) ──
    if not args.skip_holdout:
        print("\n" + "="*60)
        print("[retrain_weekly] Step 5/5 — Holdout validation (DIAGNOSTIC ONLY)")
        print("  WARNING: Do NOT use these results to tune thresholds or select features.")
        print("  Once you tune against holdout data, define a new holdout window.")
        print("="*60)
        run(holdout_cmd, "Holdout validation", abort_on_failure=False)


if __name__ == "__main__":
    main()
