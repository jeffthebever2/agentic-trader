#!/usr/bin/env python3
"""Post-retrain model analysis script.

Run after retrain completes to evaluate new model quality:
1. WF ROC and calibration from training report
2. Year-by-year HC WR analysis (was HC WR < base WR in 2026 with old model)
3. target_before_stop_probability discrimination vs win_probability
4. Feature importance comparison

Usage:
    python scripts/analyze_new_model.py
    python scripts/analyze_new_model.py --csv retrain_trades_20260529_202025.csv
"""

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import _ml_design_matrix, _ml_prepare_frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="ml_models/latest/model_bundle.joblib")
    ap.add_argument("--report", default="ml_models/latest/training_report.json")
    ap.add_argument("--csv", default=None, help="Retrain CSV for year-by-year analysis")
    ap.add_argument("--threshold", type=float, default=0.60)
    args = ap.parse_args()

    # ── 1. Training report summary ──────────────────────────────────────────
    report_path = ROOT / args.report
    if report_path.exists():
        rpt = json.loads(report_path.read_text())
        s = rpt.get("settings", {})
        wf = rpt.get("walk_forward", {})
        print("=" * 60)
        print("NEW MODEL TRAINING REPORT")
        print("=" * 60)
        print(f"Source: {s.get('source','?')}")
        print(f"Rows:   {s.get('rows_used','?')} (train={s.get('train_rows','?')}, test={s.get('test_rows','?')})")
        print(f"Hold:   {s.get('hold','?')} days")
        print(f"Calibrated: {s.get('calibrated','?')}")
        print(f"PSI pruned: {s.get('psi_pruned_count','?')} features")
        print()
        print("Walk-Forward Results:")
        print(f"  ROC:          {wf.get('roc_auc','?')}")
        print(f"  OOS rows:     {wf.get('n_oos_rows','?')}")
        print(f"  HC WR (>{args.threshold:.2f}): {wf.get('high_conf_win_rate','?')} (n={wf.get('high_conf_n','?')})")
        print(f"  Actual WR:    {wf.get('actual_win_rate','?')}")
        print()
        models = rpt.get("models", {})
        wp = models.get("win_probability", {})
        cal = wp.get("calibration", {})
        ens = wp.get("ensemble", {})
        print("Win Probability Model:")
        print(f"  Brier before: {cal.get('brier_before','?')}")
        print(f"  Brier after:  {cal.get('brier_after','?')}")
        print(f"  Ensemble:     enabled={ens.get('enabled','?')}, roc_xgb={ens.get('roc_xgb','?')}, roc_rf={ens.get('roc_rf','?')}, roc_ensemble={ens.get('roc_ensemble','?')}")
        print()
        psi = rpt.get("feature_psi", {}).get("summary", {})
        print(f"PSI: stable={psi.get('n_stable','?')} watch={psi.get('n_watch','?')} fail={psi.get('n_fail','?')}")
        print(f"PSI gate: {rpt.get('feature_psi',{}).get('passes_gate','?')}")
    else:
        print(f"Report not found: {report_path}")
        return

    # ── 2. Find CSV for year-by-year analysis ───────────────────────────────
    if args.csv:
        csv_path = Path(args.csv)
    else:
        # Find latest retrain CSV
        csvs = sorted(ROOT.glob("retrain_trades_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        csv_path = csvs[0] if csvs else None

    if csv_path is None or not csv_path.exists():
        print(f"\nNo retrain CSV found for year-by-year analysis.")
        return

    print(f"\n{'=' * 60}")
    print(f"YEAR-BY-YEAR ANALYSIS on {csv_path.name}")
    print(f"{'=' * 60}")

    # Load bundle and CSV
    bundle_path = ROOT / args.bundle
    if not bundle_path.exists():
        print(f"Bundle not found: {bundle_path}")
        return

    bundle = joblib.load(str(bundle_path))
    feat_names = bundle["feature_names"]
    numeric = bundle["numeric_features"]
    categorical = bundle["categorical_features"]
    imputer = bundle["imputer"]
    wp_model = bundle["models"]["win_probability"]
    tbs_model = bundle["models"].get("target_before_stop_probability")
    hold = bundle.get("hold", 10)

    df = pd.read_csv(str(csv_path))
    frame, _, _ = _ml_prepare_frame(df, hold=hold)

    if frame.empty:
        print("Empty frame after preparing")
        return

    x, _ = _ml_design_matrix(frame, numeric, categorical, feat_names)
    x_imp = imputer.transform(x)

    # Win probability
    wp_raw = wp_model.predict_proba(x_imp)[:, 1]
    # Ensemble if available
    rf_model = bundle["models"].get("win_probability_rf")
    if rf_model is not None:
        rf_raw = rf_model.predict_proba(x_imp)[:, 1]
        weights = bundle.get("ensemble_win_weights", {"xgb": 0.60, "rf": 0.40})
        wp_probs = wp_raw * weights.get("xgb", 0.60) + rf_raw * weights.get("rf", 0.40)
    else:
        wp_probs = wp_raw

    # TBS probability
    tbs_probs = None
    if tbs_model is not None:
        tbs_probs = tbs_model.predict_proba(x_imp)[:, 1]

    frame = frame.copy()
    frame["pred_wp"] = wp_probs
    if tbs_probs is not None:
        frame["pred_tbs"] = tbs_probs

    # Add year column from scan_date if missing
    if "year" not in frame.columns:
        if "_scan_dt" in frame.columns:
            frame["year"] = pd.to_datetime(frame["_scan_dt"], errors="coerce").dt.year
        elif "scan_date" in frame.columns:
            frame["year"] = pd.to_datetime(frame["scan_date"], errors="coerce").dt.year
        else:
            frame["year"] = 0  # unknown year fallback

    # Key label
    label_col = "_win_label"
    target_col = "_target_label" if "_target_label" in frame.columns else None

    print(f"\nLabel distribution:")
    print(f"  _win_label (return > 0.5%): {frame[label_col].mean():.3f} ({frame[label_col].sum()} / {len(frame)})")
    if target_col:
        print(f"  _target_label (TARGET_HIT): {frame[target_col].mean():.3f} ({frame[target_col].sum()} / {len(frame)})")

    print(f"\nYear-by-Year: win_probability discrimination (HC threshold={args.threshold:.2f})")
    print(f"{'Year':>6} {'n':>5} {'Base WR':>8} {'n_HC':>6} {'HC WR':>8} {'Lift':>8}")
    print("-" * 50)
    for yr in sorted(frame["year"].unique()):
        g = frame[frame["year"] == yr]
        n = len(g)
        base_wr = g[label_col].mean()
        hc = g[g["pred_wp"] >= args.threshold]
        n_hc = len(hc)
        hc_wr = hc[label_col].mean() if n_hc >= 5 else float("nan")
        lift = (hc_wr - base_wr) if not np.isnan(hc_wr) else float("nan")
        flag = " ← TEST" if yr == frame["year"].max() else ""
        flag += " *** NEGATIVE" if not np.isnan(lift) and lift < 0 else ""
        print(f"{yr:>6} {n:>5} {base_wr:>8.3f} {n_hc:>6} {hc_wr:>8.3f} {lift:>+8.3f}{flag}")

    if tbs_probs is not None and target_col:
        print(f"\nYear-by-Year: target_before_stop discrimination (HC threshold=0.45)")
        tbs_thr = 0.45
        print(f"{'Year':>6} {'n':>5} {'Base TgtHit':>12} {'n_HC':>6} {'HC TgtHit':>10} {'Lift':>8}")
        print("-" * 55)
        for yr in sorted(frame["year"].unique()):
            g = frame[frame["year"] == yr]
            n = len(g)
            base_wr = g[target_col].mean()
            hc = g[g["pred_tbs"] >= tbs_thr]
            n_hc = len(hc)
            hc_wr = hc[target_col].mean() if n_hc >= 5 else float("nan")
            lift = (hc_wr - base_wr) if not np.isnan(hc_wr) else float("nan")
            flag = " ← TEST" if yr == frame["year"].max() else ""
            print(f"{yr:>6} {n:>5} {base_wr:>12.3f} {n_hc:>6} {hc_wr:>10.3f} {lift:>+8.3f}{flag}")

    # ── Production filter analysis ───────────────────────────────────────────
    # Apply production filters to assess model quality on clean signals.
    # Filters: skip_thursday (day_of_week==3), skip_monday (day_of_week==0), CCI floor
    df_orig = pd.read_csv(str(csv_path))
    if "day_of_week" in df_orig.columns and "cci14_prev" in df_orig.columns:
        clean_mask = (
            (pd.to_numeric(df_orig["day_of_week"], errors="coerce") != 3) &
            (pd.to_numeric(df_orig["day_of_week"], errors="coerce") != 0) &  # skip Monday (Cycle 36)
            (pd.to_numeric(df_orig["cci14_prev"], errors="coerce") >= -100) &
            (pd.to_numeric(df_orig["rsi9"], errors="coerce") >= 44)          # skip RSI9<44 (Cycle 40)
        )
        vix_col = df_orig.get("vix_regime") if hasattr(df_orig, "get") else df_orig.get("vix_regime", None)
        if "vix_regime" in df_orig.columns:
            clean_mask &= (df_orig["vix_regime"] != "low_vol")
        clean_idx = df_orig[clean_mask].index
        # Get the frame rows corresponding to clean_mask
        frame_clean = frame[frame.index.isin(clean_idx)] if len(clean_idx) > 0 else frame.iloc[0:0]
        n_clean = len(frame_clean)
        print(f"\n{'='*60}")
        print(f"PRODUCTION GATE ANALYSIS (no-Thu + no-Mon + no-low_vol + CCI>=-100 + RSI9>=44)")
        print(f"{'='*60}")
        print(f"Filtered: {n_clean}/{len(frame)} signals ({n_clean/max(len(frame),1):.1%} kept)")
        if n_clean >= 20:
            base_wr_clean = frame_clean[label_col].mean()
            hc_clean = frame_clean[frame_clean["pred_wp"] >= args.threshold]
            hc_wr_clean = hc_clean[label_col].mean() if len(hc_clean) >= 5 else float("nan")
            print(f"Base WR (filtered): {base_wr_clean:.3f} (n={n_clean})")
            print(f"HC WR @{args.threshold:.2f} (filtered): {hc_wr_clean:.3f} (n={len(hc_clean)})")
            print(f"Lift: {(hc_wr_clean - base_wr_clean):+.3f}" if not np.isnan(hc_wr_clean) else "Lift: n/a")
            test_yr = frame["year"].max()
            f_test = frame_clean[frame_clean["year"] == test_yr]
            if len(f_test) >= 5:
                base_wr_test = f_test[label_col].mean()
                hc_test = f_test[f_test["pred_wp"] >= args.threshold]
                hc_wr_test = hc_test[label_col].mean() if len(hc_test) >= 3 else float("nan")
                print(f"Test year {test_yr} filtered: base={base_wr_test:.3f} "
                      f"HC={hc_wr_test:.3f} (n={len(hc_test)})")
    print()

    # Feature importance
    if hasattr(wp_model, "calibrated_classifiers_"):
        base_est = wp_model.calibrated_classifiers_[0].estimator
        if hasattr(base_est, "feature_importances_"):
            fi = base_est.feature_importances_
            idx = np.argsort(fi)[::-1][:15]
            print(f"\nTop 15 feature importances (primary model):")
            for rank, i in enumerate(idx, 1):
                if i < len(feat_names):
                    print(f"  {rank:>2}. {feat_names[i]:<30} {fi[i]:.4f}")

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
