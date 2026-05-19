#!/usr/bin/env python3
"""Apply pre-trained ML bundle to a backtest CSV and compute win rate by threshold.

Usage:
    python scripts/score_candidates_with_ml.py \
        --input /tmp/april2026_all_candidates.csv \
        --bundle ml_models/latest/model_bundle.joblib \
        --threshold 0.65
"""

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import _ml_design_matrix, _ml_prepare_frame


def score_with_bundle(df: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    feature_names = bundle["feature_names"]
    numeric = bundle["numeric_features"]
    categorical = bundle["categorical_features"]
    imputer = bundle["imputer"]
    win_model = bundle["models"]["win_probability"]

    frame, _, _ = _ml_prepare_frame(df, hold=bundle.get("hold", 3))
    x, _ = _ml_design_matrix(frame, numeric, categorical, feature_names)
    x_imp = imputer.transform(x)

    frame = frame.copy()
    frame["ml_win_prob"] = win_model.predict_proba(x_imp)[:, 1]
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--bundle", default="ml_models/latest/model_bundle.joblib")
    ap.add_argument("--threshold", type=float, default=0.65)
    ap.add_argument("--hold", type=int, default=3)
    args = ap.parse_args()

    bundle_path = ROOT / args.bundle
    print(f"Loading bundle: {bundle_path}")
    bundle = joblib.load(bundle_path)
    print(f"  Features: {len(bundle['feature_names'])}")
    print(f"  Models: {list(bundle['models'].keys())}")

    print(f"Loading candidates: {args.input}")
    df = pd.read_csv(args.input, low_memory=False)
    print(f"  Rows: {len(df):,}")

    ret_col = f"h{args.hold}_return"
    if ret_col not in df.columns:
        for c in df.columns:
            if "return" in c.lower() or "ret" in c.lower():
                print(f"  Available return cols: {c}")
        sys.exit(f"Missing return column '{ret_col}'")

    scored = score_with_bundle(df, bundle)
    scored["_win"] = pd.to_numeric(scored[ret_col], errors="coerce") > 0

    print(f"\n--- Win rate by threshold ---")
    for t in [0.50, 0.55, 0.60, 0.63, 0.65, 0.67, 0.70, 0.72, 0.75, 0.80]:
        sub = scored[scored["ml_win_prob"] >= t]
        if len(sub) < 5:
            break
        wr = sub["_win"].mean()
        print(f"  threshold={t:.2f}: trades={len(sub):5,}  win_rate={wr:.1%}")

    # Monthly breakdown at chosen threshold
    chosen = scored[scored["ml_win_prob"] >= args.threshold].copy()
    print(f"\n--- Monthly at threshold={args.threshold:.2f} (N={len(chosen):,} trades) ---")
    if "scan_date" in chosen.columns:
        chosen["_month"] = pd.to_datetime(chosen["scan_date"]).dt.to_period("M")
        for month, grp in chosen.groupby("_month"):
            wr = grp["_win"].mean()
            print(f"  {month}: trades={len(grp):4,}  win_rate={wr:.1%}")
    elif "_scan_dt" in chosen.columns:
        chosen["_month"] = pd.to_datetime(chosen["_scan_dt"]).dt.to_period("M")
        for month, grp in chosen.groupby("_month"):
            wr = grp["_win"].mean()
            print(f"  {month}: trades={len(grp):4,}  win_rate={wr:.1%}")


if __name__ == "__main__":
    main()
