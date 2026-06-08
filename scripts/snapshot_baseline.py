#!/usr/bin/env python3
"""Baseline snapshot — read-only. Never trains or swaps any model bundle.

Reads the existing training_report.json and runs validate_holdout.py on
the last 60 trading days, then writes a combined snapshot JSON.

Usage:
    python scripts/snapshot_baseline.py
    python scripts/snapshot_baseline.py --snapshot-date 2026-06-01 --output docs/snap.json
    python scripts/snapshot_baseline.py --dry-run
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TRADING_DAYS_PER_YEAR = 252


def _last_n_trading_days(end_date: str, n: int = 60) -> str:
    """Approximate start date for n trading days before end_date."""
    end = dt.date.fromisoformat(end_date)
    calendar_days = int(n * 1.45) + 10
    start = end - dt.timedelta(days=calendar_days)
    return start.isoformat()


def _read_training_report(bundle_path: Path) -> dict:
    report_path = bundle_path.parent / "training_report.json"
    if report_path.exists():
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _run_validate_holdout(
    bundle_path: Path,
    start: str,
    end: str,
    tickers: str,
    dry_run: bool,
) -> dict:
    if dry_run:
        return {"dry_run": True}

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "validate_holdout.py"),
        "--model-bundle", str(bundle_path),
        "--start", start,
        "--end", end,
        "--tickers", tickers,
        "--account-commission", "1.0",
        "--account-slippage-bps", "5.0",
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd += ["--output-dir", tmp_dir]
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
        )

    if result.returncode != 0:
        return {
            "error": f"validate_holdout exited {result.returncode}",
            "stderr": result.stderr[-2000:],
        }

    # Parse any backtest_results_*.json produced in the working dir
    import glob, os
    pattern = str(ROOT / "holdout_results_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        # Try to find in stdout
        for line in result.stdout.splitlines():
            if "Results saved" in line and ".json" in line:
                p = line.split("→")[-1].strip()
                if Path(p).exists():
                    files = [p]
                    break

    if files:
        try:
            data = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
            summary = data.get("summary", {})
            primary_hold = str(data.get("primary_hold", 3)) + "d"
            h_stats = summary.get("by_hold_period", {}).get(primary_hold, {})
            return {
                "holdout_roc": data.get("ml_analysis", {}).get("walk_forward", {}).get("roc_auc"),
                "holdout_brier": data.get("ml_analysis", {}).get("calibration", {}).get("brier_score"),
                "holdout_trade_count": h_stats.get("trade_count"),
                "holdout_win_rate": h_stats.get("win_rate"),
                "holdout_profit_factor": h_stats.get("profit_factor"),
                "holdout_sortino": h_stats.get("sortino_ratio"),
                "holdout_max_drawdown": h_stats.get("max_drawdown"),
                "result_file": files[-1],
            }
        except Exception as e:
            return {"error": f"parse error: {e}", "stderr": result.stderr[-500:]}

    return {"status": "no_output_file", "stdout_tail": result.stdout[-500:]}


def main():
    parser = argparse.ArgumentParser(description="Baseline snapshot — read-only")
    parser.add_argument("--snapshot-date", default=dt.date.today().isoformat())
    parser.add_argument("--output", default=None,
                        help="Output JSON path (default: docs/baseline_snapshot_<date>.json)")
    parser.add_argument("--tickers", default=str(ROOT / "all_tickers.txt"))
    parser.add_argument("--bundle", default=str(ROOT / "ml_models" / "latest" / "model_bundle.joblib"))
    parser.add_argument("--holdout-days", type=int, default=60,
                        help="Number of trading days for holdout window.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would run; do not execute.")
    args = parser.parse_args()

    snapshot_date = args.snapshot_date
    bundle_path = Path(args.bundle)
    output_path = Path(args.output) if args.output else (
        ROOT / "docs" / f"baseline_snapshot_{snapshot_date.replace('-', '')}.json"
    )

    if not bundle_path.exists():
        print(f"ERROR: bundle not found at {bundle_path}", file=sys.stderr)
        sys.exit(1)

    holdout_start = _last_n_trading_days(snapshot_date, args.holdout_days)
    holdout_end = snapshot_date

    print(f"Snapshot date   : {snapshot_date}")
    print(f"Bundle          : {bundle_path}")
    print(f"Holdout window  : {holdout_start} → {holdout_end}")
    print(f"Output          : {output_path}")

    if args.dry_run:
        print("\n[dry-run] Would run validate_holdout.py and write snapshot JSON.")
        snapshot = {
            "snapshot_date": snapshot_date,
            "bundle_path": str(bundle_path),
            "dry_run": True,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return

    # Step 1: read existing training report
    report = _read_training_report(bundle_path)
    wf = report.get("walk_forward", {})
    summary = report.get("summary", report.get("by_hold_period", {}))

    snapshot: dict = {
        "snapshot_date": snapshot_date,
        "bundle_path": str(bundle_path),
        "wf_roc": wf.get("roc_auc"),
        "brier": report.get("calibration", {}).get("brier_score"),
        "trade_count": wf.get("n_oos_rows"),
        "win_rate": wf.get("actual_win_rate"),
        "profit_factor": None,
        "sortino": None,
        "max_drawdown": None,
    }

    # Step 2: run holdout validation
    print("\nRunning holdout validation...")
    holdout = _run_validate_holdout(bundle_path, holdout_start, holdout_end, args.tickers, False)
    snapshot.update({
        "holdout_roc": holdout.get("holdout_roc"),
        "holdout_brier": holdout.get("holdout_brier"),
        "holdout_trade_count": holdout.get("holdout_trade_count"),
        "holdout_win_rate": holdout.get("holdout_win_rate"),
        "holdout_profit_factor": holdout.get("holdout_profit_factor"),
        "holdout_sortino": holdout.get("holdout_sortino"),
        "holdout_max_drawdown": holdout.get("holdout_max_drawdown"),
    })
    if "error" in holdout:
        snapshot["holdout_error"] = holdout["error"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(f"\nSnapshot written → {output_path.resolve()}")
    print(json.dumps(snapshot, indent=2, default=str))


if __name__ == "__main__":
    main()
