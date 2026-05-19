#!/usr/bin/env python3
"""Weekly ML model retraining on a rolling 18-month window.

Run manually or via cron. Adds the following cron entry:
    0 8 * * 0 /path/to/.venv/bin/python3 /path/to/scripts/retrain_weekly.py

Steps:
  1. Run backtest on tickers from all_tickers.txt, last 18 months
  2. Export trades CSV
  3. Train ML models from the CSV
  4. Swap the bundle into ml_models/latest/
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], label: str) -> int:
    print(f"\n{'='*60}")
    print(f"[retrain_weekly] {label}")
    print(f"CMD: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[retrain_weekly] ERROR: {label} failed (exit {result.returncode})")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Weekly rolling-window ML retrain.")
    parser.add_argument("--tickers", default="all_tickers.txt")
    parser.add_argument("--months", type=int, default=18, help="Rolling window in months.")
    parser.add_argument("--output-dir", default="ml_models/latest")
    parser.add_argument("--hold", type=int, default=3)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="Print commands only, don't run.")
    args = parser.parse_args()

    today = dt.date.today()
    start = today - dt.timedelta(days=args.months * 30)
    end = today - dt.timedelta(days=1)
    ts = today.strftime("%Y%m%d")
    csv_path = ROOT / f"retrain_trades_{ts}.csv"

    python = sys.executable

    backtest_cmd = [
        python, str(ROOT / "backtest.py"),
        "--tickers", args.tickers,
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--export-csv", str(csv_path),
        "--no-trades-json",
        "--no-generate-charts",
        "--batch-size", "50",
    ]

    train_cmd = [
        python, str(ROOT / "scripts" / "train_ml_models.py"),
        "--input", str(csv_path),
        "--output-dir", args.output_dir,
        "--hold", str(args.hold),
        "--n-estimators", str(args.n_estimators),
        "--max-depth", str(args.max_depth),
        "--ml-probability-threshold", "0.60",
        "--ml-large-loss-max", "1.0",
        "--ml-expected-return-min", "-99.0",
        "--min-samples-leaf", "30",
    ]

    if args.dry_run:
        print("[dry-run] backtest cmd:", " ".join(backtest_cmd))
        print("[dry-run] train cmd:   ", " ".join(train_cmd))
        return

    rc = run(backtest_cmd, f"Backtest {start} → {end}")
    if rc != 0:
        sys.exit(rc)

    if not csv_path.exists():
        print(f"[retrain_weekly] ERROR: CSV not found at {csv_path}")
        sys.exit(1)

    rc = run(train_cmd, f"Train ML models from {csv_path.name}")
    if rc != 0:
        sys.exit(rc)

    # Clean up CSV to save disk
    try:
        csv_path.unlink()
    except Exception:
        pass

    print(f"\n[retrain_weekly] Done. Model bundle at: {ROOT / args.output_dir / 'model_bundle.joblib'}")
    print("[retrain_weekly] Restart paper runner to pick up the new model.\n")


if __name__ == "__main__":
    main()
