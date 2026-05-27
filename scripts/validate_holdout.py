#!/usr/bin/env python3
"""Holdout validation runner for TradingAgents ML models.

Runs the backtest engine on a specified date range (the unseen holdout window)
using a pre-trained model bundle. NEVER trains or tunes on this data — it is
purely a read-only evaluation.

Usage:
    # Default: use last-updated model, holdout = 2026-05-08 to today
    python scripts/validate_holdout.py

    # Custom holdout window
    python scripts/validate_holdout.py \
        --start 2026-05-08 --end 2026-05-26 \
        --model-bundle ml_models/latest/model_bundle.joblib \
        --tickers all_tickers.txt

    # With realistic costs (recommended)
    python scripts/validate_holdout.py \
        --start 2026-05-08 --end 2026-05-26 \
        --account-commission 1.0 \
        --account-slippage-bps 5.0

WARNING: Do not use this script's output to tune thresholds or select features.
         Once you tune on holdout data, it becomes training data.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def detect_model_data_end(bundle_path: Path = None, report_path: Path = None) -> str:
    """Detect when the model's training data ends from the bundle or report."""
    # Try training report first
    if report_path and report_path.exists():
        try:
            rpt = json.loads(report_path.read_text())
            src = rpt.get("settings", {}).get("source", "")
            # Look for date in source path
            import re
            dates = re.findall(r"(\d{4}-\d{2}-\d{2})", src)
            if dates:
                return max(dates)
        except Exception:
            pass

    # Try to load bundle and inspect source field
    if bundle_path and bundle_path.exists():
        try:
            import joblib
            bundle = joblib.load(str(bundle_path))
            src = bundle.get("source", "")
            import re
            dates = re.findall(r"(\d{4}-\d{2}-\d{2})", src)
            if dates:
                return max(dates)
            # Check created_at
            created = bundle.get("created_at", "")
            if created:
                return created[:10]
        except Exception:
            pass

    # Default fallback
    return "2026-05-07"


def next_trading_day(date_str: str, offset_days: int = 1) -> str:
    """Get the next trading day after date_str (naive: skip weekends only)."""
    import datetime as dt
    d = dt.date.fromisoformat(date_str) + dt.timedelta(days=offset_days)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d += dt.timedelta(days=1)
    return d.isoformat()


def main():
    parser = argparse.ArgumentParser(
        description="Run backtest on unseen holdout window. Read-only. Never tune on this output."
    )
    parser.add_argument(
        "--start", default=None,
        help="Holdout start date (default: day after training data end)."
    )
    parser.add_argument(
        "--end", default=dt.date.today().isoformat(),
        help=f"Holdout end date (default: today = {dt.date.today()})."
    )
    parser.add_argument(
        "--model-bundle", default=str(ROOT / "ml_models/latest/model_bundle.joblib"),
        help="Path to model bundle (default: ml_models/latest/model_bundle.joblib)."
    )
    parser.add_argument(
        "--tickers", default=str(ROOT / "all_tickers.txt"),
        help="Tickers file (default: all_tickers.txt)."
    )
    parser.add_argument("--threshold", type=float, default=70.0)
    parser.add_argument("--score-mode", default="confirmed_pullback")
    parser.add_argument("--account-size", type=float, default=10000.0)
    parser.add_argument("--account-commission", type=float, default=1.0)
    parser.add_argument("--account-slippage-bps", type=float, default=5.0)
    parser.add_argument("--ml-probability-threshold", type=float, default=None,
                        help="Override model bundle threshold (default: use bundle value).")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory for holdout backtest results (default: auto-named in cwd)."
    )
    args = parser.parse_args()

    bundle_path = Path(args.model_bundle)
    report_path = bundle_path.parent / "training_report.json"

    # Detect holdout start
    if args.start is None:
        data_end = detect_model_data_end(bundle_path, report_path)
        holdout_start = next_trading_day(data_end, offset_days=1)
        print(f"Auto-detected training data end: {data_end}")
    else:
        holdout_start = args.start

    holdout_end = args.end

    print(f"\nHoldout validation window: {holdout_start} → {holdout_end}")
    print(f"Model bundle: {bundle_path}")
    print(f"Commission: ${args.account_commission} | Slippage: {args.account_slippage_bps} bps/side")
    print(
        "\n⚠ WARNING: Do NOT use these results to tune thresholds or select features.\n"
        "   Once tuned against this window, it becomes training data.\n"
    )

    # Resolve ML threshold from bundle if not overridden
    ml_threshold = args.ml_probability_threshold
    if ml_threshold is None and bundle_path.exists():
        try:
            import joblib
            bundle = joblib.load(str(bundle_path))
            ml_threshold = bundle.get("thresholds", {}).get("ml_probability_threshold", 0.60)
            print(f"Using bundle threshold: {ml_threshold}")
        except Exception:
            ml_threshold = 0.60

    # Build output directory
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.output_dir or str(ROOT / f"holdout_results_{ts}")

    # Build backtest command
    cmd = [
        sys.executable, str(ROOT / "backtest.py"),
        "--tickers", args.tickers,
        "--start", holdout_start,
        "--end", holdout_end,
        "--threshold", str(args.threshold),
        "--score-mode", args.score_mode,
        "--hold-periods", "3", "5", "10",
        "--primary-hold", "3",
        "--account-size", str(args.account_size),
        "--account-commission", str(args.account_commission),
        "--account-slippage-bps", str(args.account_slippage_bps),
        "--ml-probability-threshold", str(ml_threshold),
        "--no-generate-charts",
        "--diagnostics",
    ]
    if args.no_cache:
        cmd.append("--no-cache")

    print(f"\nRunning: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print(f"\n⚠ Backtest exited with code {result.returncode}")
        sys.exit(result.returncode)

    print(
        f"\n── Holdout validation complete ──\n"
        f"IMPORTANT: These results are for diagnostic purposes only.\n"
        f"Do NOT use them to select thresholds, features, or model variants.\n"
        f"The moment you tune against holdout data, you need a new holdout window.\n"
    )


if __name__ == "__main__":
    main()
