#!/usr/bin/env python3
"""Train and save an HMM regime model — RD-1.

Fits a Gaussian HMM on SPY log-returns and saves the model bundle to
ml_models/hmm_regime/hmm_regime.joblib. The model is loaded at feature
engineering time to add regime probability columns to training data.

Usage:
    python3 scripts/train_hmm_regime.py --ticker SPY --start 2015-01-01 --end 2025-01-01
    python3 scripts/train_hmm_regime.py --dry-run    # test without saving
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description="Train HMM regime model on market returns.")
    parser.add_argument("--ticker", default="SPY", help="Benchmark ticker. Default: SPY.")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--n-components", type=int, default=3,
                        help="Number of HMM hidden states (regimes). Default: 3.")
    parser.add_argument("--output-dir", default="ml_models/hmm_regime",
                        help="Directory to save HMM bundle. Default: ml_models/hmm_regime.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fit and print summary, but do not save.")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main():
    args = parse_args()

    # ── Fetch returns ─────────────────────────────────────────────────────────
    print(f"Fetching {args.ticker} OHLCV: {args.start} → {args.end}")
    try:
        from tradingagents.data import YFinanceProvider
        import numpy as np
        import pandas as pd

        provider = YFinanceProvider()
        bars = provider.get_bars(args.ticker, args.start, args.end)
        if not bars:
            print(f"ERROR: No data fetched for {args.ticker}", file=sys.stderr)
            sys.exit(1)

        closes = pd.Series(
            [b.close for b in bars],
            index=[b.date for b in bars],
            name="close",
        )
        log_returns = np.log(closes / closes.shift(1)).dropna()
        print(f"  {len(log_returns):,} daily returns loaded")

    except ImportError:
        # Fallback: synthetic data for testing
        print("  yfinance not available — using synthetic returns for dry-run test")
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(args.seed)
        n = 500
        log_returns = pd.Series(
            rng.normal(0.0003, 0.01, n),
            index=pd.date_range(args.start, periods=n, freq="B").strftime("%Y-%m-%d"),
        )

    # ── Fit HMM ───────────────────────────────────────────────────────────────
    from tradingagents.ml.hmm_regime import HMMRegimeFeatures
    print(f"Fitting Gaussian HMM: n_components={args.n_components}, seed={args.seed}")
    hmm = HMMRegimeFeatures(n_components=args.n_components, seed=args.seed)
    hmm.fit(log_returns)

    # ── Evaluate ─────────────────────────────────────────────────────────────
    features = hmm.transform(log_returns)
    state_counts = features["hmm_state"].value_counts().sort_index().to_dict()
    print(f"  State distribution: {state_counts}")

    report = {
        "ticker": args.ticker,
        "start": args.start,
        "end": args.end,
        "n_components": args.n_components,
        "n_training_bars": len(log_returns),
        "state_distribution": {str(k): int(v) for k, v in state_counts.items()},
        "feature_columns": list(features.columns),
        "trained_at": datetime.now().isoformat()[:19],
    }
    print(f"  Report: {json.dumps(report, indent=2)}")

    if args.dry_run:
        print("\nDry-run: model NOT saved.")
        return

    # ── Save ─────────────────────────────────────────────────────────────────
    import joblib  # type: ignore
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bundle_path = output_dir / "hmm_regime.joblib"
    joblib.dump({"model": hmm, "report": report}, bundle_path)

    report_path = output_dir / "hmm_regime_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))

    print(f"\nHMM bundle saved: {bundle_path}")
    print(f"Report saved: {report_path}")


if __name__ == "__main__":
    main()
