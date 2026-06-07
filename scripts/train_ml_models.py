#!/usr/bin/env python3
"""Train TradingAgents ML gate models from backtest trade data.

Examples:
    python scripts/train_ml_models.py --input backtest_results_20260506_161240.json
    python scripts/train_ml_models.py --input trades.csv --output-dir ml_models/latest

The input must contain trade rows. For JSON, that means the backtest must have
been run without --no-trades-json. For CSV, use backtest.py --export-csv.
"""

import argparse
import datetime as dt
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*sklearn.utils.parallel.*", category=UserWarning)

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

try:
    from xgboost import XGBClassifier, XGBRegressor
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False


def _make_clf(y_labels, n_estimators=500, max_depth=6, min_samples_leaf=15, seed=42):
    if _XGB_AVAILABLE:
        pos = int(y_labels.sum())
        neg = int(len(y_labels) - pos)
        spw = (neg / pos) if pos > 0 else 1.0
        return XGBClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=spw, tree_method="hist",
            eval_metric="logloss", verbosity=0, n_jobs=-1, random_state=seed,
        )
    return RandomForestClassifier(
        n_estimators=n_estimators, max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        class_weight="balanced_subsample", random_state=seed, n_jobs=-1,
    )


def _make_reg(n_estimators=400, max_depth=6, min_samples_leaf=15, seed=42):
    if _XGB_AVAILABLE:
        return XGBRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            tree_method="hist", verbosity=0, n_jobs=-1, random_state=seed,
        )
    return RandomForestRegressor(
        n_estimators=n_estimators, max_depth=max_depth,
        min_samples_leaf=min_samples_leaf, random_state=seed, n_jobs=-1,
    )

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import (
    _build_gate_analysis,
    _classification_metrics,
    _feature_importance,
    _ml_design_matrix,
    _ml_prepare_frame,
    _ml_purged_walk_forward,
    _ml_time_split,
    _regression_metrics,
    ML_NUMERIC_FEATURES_BREAKOUT,
)


_VIX_CACHE = Path("/tmp/vix_sector_features.csv")
_VIX_COLS = ["vix_ts", "sector_breadth", "vix_1d_chg"]


def _enrich_vix_features(df: pd.DataFrame) -> pd.DataFrame:
    """Merge cached VIX/sector features if the CSV columns are all-NaN."""
    missing = [c for c in _VIX_COLS if c not in df.columns or df[c].isna().all()]
    if not missing or not _VIX_CACHE.exists():
        return df
    try:
        vix = pd.read_csv(_VIX_CACHE)
        vix["scan_date"] = vix["scan_date"].astype(str)
        df = df.drop(columns=[c for c in _VIX_COLS if c in df.columns], errors="ignore")
        df = df.merge(vix[["scan_date"] + _VIX_COLS], on="scan_date", how="left")
        filled = sum(df[c].notna().sum() for c in _VIX_COLS)
        print(f"  VIX enrichment: merged {filled:,} values from {_VIX_CACHE}")
    except Exception as e:
        print(f"  VIX enrichment skipped: {e}")
    return df


def _load_rows(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, low_memory=False)
        return _enrich_vix_features(df)

    data = json.loads(path.read_text())
    rows = data.get("all_trades")
    if rows:
        return pd.DataFrame(rows)

    raise SystemExit(
        f"{path} does not contain all_trades. Re-run the backtest without "
        "--no-trades-json or use --export-csv and train from that CSV."
    )


def _safe_auc(y, prob):
    if len(set(y)) < 2:
        return None
    return {
        "roc_auc": round(float(roc_auc_score(y, prob)), 4),
        "average_precision": round(float(average_precision_score(y, prob)), 4),
        "brier_score": round(float(brier_score_loss(y, prob)), 4),
    }


def _calibrate_classifier(clf, x_val, y_val, method: str = "isotonic"):
    """Wrap a pre-fit classifier with isotonic/sigmoid calibration.

    Isotonic regression is preferred for large val sets (n > 1000) because it
    is non-parametric and can correct non-monotonic miscalibration. Sigmoid is
    better for small val sets. We auto-select based on size.

    Returns (calibrated_model, calibration_report_dict).
    """
    if len(y_val) < 50 or len(set(y_val)) < 2:
        return clf, {"status": "skipped_too_few_samples"}
    auto_method = "isotonic" if len(y_val) >= 1000 else "sigmoid"
    actual_method = method if method in ("isotonic", "sigmoid") else auto_method
    try:
        # TM-1: CalibratedClassifierCV(clf, cv=None) with a non-frozen estimator does 5-fold
        # CV internally — it re-trains clf on ~15% calibration slices, NOT on the full-train model.
        # The shipped "calibrated" model is a weak small-sample re-fit.
        # Fix: use FrozenEstimator (sklearn >= 1.6) so cv=None calibrates the pre-fitted model.
        try:
            from sklearn.frozen import FrozenEstimator
            _clf_to_calibrate = FrozenEstimator(clf)
        except ImportError:
            # Fallback for older sklearn — cv="prefit" is deprecated but calibrates in-place
            _clf_to_calibrate = clf  # type: ignore[assignment]
        cal = CalibratedClassifierCV(_clf_to_calibrate, method=actual_method, cv=None)
        cal.fit(x_val, y_val)
        raw_prob = clf.predict_proba(x_val)[:, 1]
        cal_prob = cal.predict_proba(x_val)[:, 1]
        # Build calibration curve for the report
        n_bins = min(10, max(5, len(y_val) // 50))
        try:
            # TM-5: "uniform" (equal-count) bucketing splits flat-probability mass arbitrarily,
            # corrupting the monotonicity check (a constant-0.6 predictor looks "well calibrated").
            # "quantile" bins by probability value edges — buckets have equal probability width,
            # not equal sample count, making monotonicity checks meaningful.
            frac_pos, mean_pred = calibration_curve(y_val, cal_prob, n_bins=n_bins, strategy="quantile")
            cal_curve = [{"mean_pred": round(float(mp), 4), "frac_pos": round(float(fp), 4)}
                         for mp, fp in zip(mean_pred, frac_pos)]
        except Exception:
            cal_curve = []
        report = {
            "method": actual_method,
            "brier_before": round(float(brier_score_loss(y_val, raw_prob)), 4),
            "brier_after": round(float(brier_score_loss(y_val, cal_prob)), 4),
            "calibration_curve": cal_curve,
        }
        return cal, report
    except Exception as exc:
        return clf, {"status": f"failed: {exc}"}


# Leaky forward-outcome column prefixes — must never appear in feature_names
_LEAKY_COLUMN_PREFIXES = ("h1_", "h2_", "h3_", "h4_", "h5_", "h7_", "h10_", "h14_", "h20_")
_LEAKY_COLUMN_SUFFIXES = ("_outcome", "_exit", "_exit_date", "_days", "_r_multiple",
                          "_target_hit", "_stopped_out", "_direction_correct",
                          "_bad_loss", "_strong_win")


def _check_feature_leakage(feature_names: list, hold: int) -> list:
    """Assert no forward-outcome features leaked into the design matrix.

    Returns a list of leaky feature names found (empty = clean).
    """
    leaky = []
    for f in feature_names:
        fl = f.lower()
        # Check h{hold}_return specifically
        if fl == f"h{hold}_return" or fl == f"h{hold}_mae" or fl == f"h{hold}_mfe":
            leaky.append(f)
            continue
        # Check any forward-return column
        for prefix in _LEAKY_COLUMN_PREFIXES:
            if fl.startswith(prefix):
                for suffix in _LEAKY_COLUMN_SUFFIXES + ("_return", "_mae", "_mfe", "_entry",
                                                          "_target", "_stop"):
                    if fl.endswith(suffix) or fl == prefix.rstrip("_"):
                        leaky.append(f)
                        break
    return leaky


def _threshold_search(test_df, win_prob, loss_prob, expected_return, hold: int,
                      thresholds=None, large_loss_max: float = 0.35) -> dict:
    """Search win-probability thresholds; return expectancy/n-trades at each level.

    Uses a simple expectancy × sqrt(n) quality score to flag the best threshold
    (maximises edge while keeping enough trades for statistical significance).
    """
    if thresholds is None:
        thresholds = [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65, 0.70]

    ret_col = f"h{hold}_return"
    if ret_col not in test_df.columns:
        return {}

    returns = pd.to_numeric(test_df[ret_col], errors="coerce").fillna(0.0).to_numpy()
    results = {}
    best_score = -9e9
    best_threshold = None

    for thr in thresholds:
        mask = win_prob >= thr
        if large_loss_max < 1.0 and loss_prob is not None:
            mask = mask & (loss_prob <= large_loss_max)
        selected = returns[mask]
        n = len(selected)
        if n < 5:
            results[str(thr)] = {"n": n, "status": "too_few"}
            continue
        wr = float((selected > 0).mean())
        avg_ret = float(selected.mean())
        pf_wins = selected[selected > 0].sum()
        pf_losses = abs(selected[selected < 0].sum())
        pf = round(pf_wins / pf_losses, 4) if pf_losses > 0 else 999.0
        score = avg_ret * (n ** 0.5)  # Kelly-weighted: edge × sqrt(sample size)
        results[str(thr)] = {
            "n": n,
            "win_rate": round(wr, 4),
            "avg_return_pct": round(avg_ret, 4),
            "profit_factor": pf,
            "quality_score": round(score, 6),
        }
        if score > best_score:
            best_score = score
            best_threshold = thr

    # TM-8: searching recommended_threshold on the test set is a latent holdout-tuning
    # landmine — the "recommended" threshold is already fitted to the evaluation data.
    # Label it diagnostic only; callers must NOT use this to select the production threshold.
    results["recommended_threshold"] = best_threshold
    results["recommended_threshold_WARNING"] = (
        "DIAGNOSTIC ONLY — computed on test set. Do not use for threshold selection. "
        "Use walk-forward ROC / expectancy curve instead."
    )
    return results


def _per_regime_diagnostics(test_df: pd.DataFrame, win_prob: np.ndarray,
                             y_win_test: np.ndarray) -> dict:
    """Compute per-regime performance metrics on the test set.

    Metrics per regime: n, win_rate, avg_return, max_drawdown, expectancy,
    roc_auc, brier_score, high_confidence_win_rate (prob >= 0.60).

    Used to identify which market environments the model handles best/worst.
    Per-regime ROC < 0.52 signals that regime-specific retraining may help.
    """
    from sklearn.metrics import roc_auc_score as _roc, brier_score_loss as _brier
    results = {}

    # Find a return column for return-based metrics
    _ret_col = None
    for rc in ("h3_return", "h5_return", "h10_return"):
        if rc in test_df.columns:
            _ret_col = rc
            break

    for regime_col in ("spy_regime", "vix_regime"):
        if regime_col not in test_df.columns:
            continue
        results[regime_col] = {}
        for rval in test_df[regime_col].unique():
            mask = (test_df[regime_col] == rval).to_numpy()
            n = int(mask.sum())
            if n < 20:
                results[regime_col][str(rval)] = {"n": n, "status": "too_few"}
                continue
            y_r = y_win_test[mask]
            p_r = win_prob[mask]
            wr = float(y_r.mean())
            pred_wr = float((p_r >= 0.50).mean())
            hc_mask = p_r >= 0.60
            hc_wr = float(y_r[hc_mask].mean()) if hc_mask.sum() >= 5 else None

            entry: dict = {
                "n": n,
                "actual_win_rate": round(wr, 4),
                "pred_win_rate_at_0.50": round(pred_wr, 4),
                "high_conf_win_rate_at_0.60": round(hc_wr, 4) if hc_wr is not None else None,
                "n_high_conf": int(hc_mask.sum()),
            }

            # Return-based metrics
            if _ret_col is not None:
                rets_r = pd.to_numeric(test_df.loc[mask, _ret_col], errors="coerce").fillna(0.0).to_numpy()
                entry["avg_return"] = round(float(rets_r.mean()), 5)
                entry["expectancy"] = round(float(rets_r.mean()), 5)
                # Max drawdown on cumulative return sequence
                if len(rets_r) > 1:
                    cum = np.cumprod(1.0 + rets_r)
                    rolling_max = np.maximum.accumulate(cum)
                    dd = (rolling_max - cum) / np.where(rolling_max > 0, rolling_max, 1.0)
                    entry["max_drawdown"] = round(float(dd.max()), 4)

            # ROC and Brier
            try:
                entry["roc_auc"] = round(float(_roc(y_r, p_r)), 4) if len(set(y_r)) > 1 else None
                entry["brier_score"] = round(float(_brier(y_r, p_r)), 4)
            except Exception:
                pass

            results[regime_col][str(rval)] = entry

    return results


def _confidence_bucket_analysis(test_df: pd.DataFrame, win_prob: np.ndarray,
                                 y_win_test: np.ndarray, n_buckets: int = 10) -> dict:
    """Analyse win rate and expectancy by probability decile.

    This surfaces whether the model's high-confidence predictions are actually
    more accurate (monotone calibration check). Expectancy by decile helps set
    a data-driven probability threshold for live trading.
    """
    ret_col = None
    for rc in ("h3_return", "h5_return", "h10_return"):
        if rc in test_df.columns:
            ret_col = rc
            break
    rets = pd.to_numeric(test_df[ret_col], errors="coerce").fillna(0.0).to_numpy() if ret_col else None

    bucket_size = max(1, len(win_prob) // n_buckets)
    sorted_idx = np.argsort(win_prob)
    buckets = []
    for b in range(n_buckets):
        start = b * bucket_size
        end = (b + 1) * bucket_size if b < n_buckets - 1 else len(sorted_idx)
        idx = sorted_idx[start:end]
        y_b = y_win_test[idx]
        p_b = win_prob[idx]
        r_b = rets[idx] if rets is not None else None
        entry = {
            "bucket": b + 1,
            "prob_range": [round(float(p_b.min()), 4), round(float(p_b.max()), 4)],
            "n": int(len(idx)),
            "actual_win_rate": round(float(y_b.mean()), 4),
            "mean_prob": round(float(p_b.mean()), 4),
        }
        if r_b is not None:
            wins = r_b[r_b > 0]
            losses = r_b[r_b < 0]
            entry["avg_return_pct"] = round(float(r_b.mean()), 5)
            entry["profit_factor"] = round(wins.sum() / max(abs(losses.sum()), 1e-8), 3)
        buckets.append(entry)
    return {"buckets": buckets, "n_buckets": n_buckets}


def train_models(args) -> dict:
    source = Path(args.input)
    rows = _load_rows(source)
    frame, numeric, categorical = _ml_prepare_frame(rows, args.hold)
    if len(frame) < args.min_rows:
        raise SystemExit(
            f"Only {len(frame)} usable rows found; need at least {args.min_rows}."
        )

    # Optionally filter to executed (rule-passing) rows only.
    # Use case: train model focused purely on within-setup quality discrimination.
    executed_only = getattr(args, "executed_only", False)
    if executed_only and "candidate_status" in frame.columns:
        n_before = len(frame)
        frame = frame[frame["candidate_status"] == "executed"].copy()
        print(f"  --executed-only: {len(frame):,} rows (was {n_before:,})")
        if len(frame) < args.min_rows:
            raise SystemExit(
                f"--executed-only: only {len(frame)} executed rows; need {args.min_rows}. "
                "Run backtest with a wider ticker universe."
            )

    if args.max_rows and args.max_rows > 0 and len(frame) > args.max_rows:
        frame = (
            frame.sort_values("_scan_dt")
            .sample(args.max_rows, random_state=args.seed)
            .sort_values("_scan_dt")
        )
    frame = frame.sort_values("_scan_dt").reset_index(drop=True)

    # ── Breakout mode: override features and win label ────────────────────────
    _training_mode = getattr(args, "mode", "pullback")
    if _training_mode == "breakout":
        # Override numeric features with breakout-specific set
        numeric = [f for f in ML_NUMERIC_FEATURES_BREAKOUT if f in frame.columns]
        if not numeric:
            raise SystemExit(
                "Breakout mode: no ML_NUMERIC_FEATURES_BREAKOUT columns found in input CSV. "
                "Run backtest with --score-mode breakout_v2 to generate breakout features."
            )
        # Override win label: use _breakout_win_label (h5_return > 1% + not stopped out)
        if "_breakout_win_label" in frame.columns:
            frame["_win_label"] = frame["_breakout_win_label"]
            print(f"  Breakout mode: _win_label ← _breakout_win_label  "
                  f"(win rate={frame['_win_label'].mean():.1%})")
        else:
            print("  Breakout mode: _breakout_win_label not found, using default _win_label")
        # Default hold=5 for breakouts if not explicitly set
        if not any(a == "--hold" for a in sys.argv):
            args.hold = 5
            print(f"  Breakout mode: hold period set to 5d (use --hold to override)")
        # Adjust output dir if still default
        if args.output_dir == "ml_models/latest":
            args.output_dir = "ml_models/breakout"
        print(f"  Breakout mode: {len(numeric)} features, output → {args.output_dir}")

    # Optional qlib feature columns — included only when present in the frame
    # (added by train_ml_from_stock_data.py --include-qlib-features).
    # Default OFF: absent columns produce no change to the feature set.
    _QLIB_COLS = ["qlib_mom_252_21", "qlib_vol_ratio", "qlib_atr_z", "qlib_close_rank"]
    _qlib_cols_used = [c for c in _QLIB_COLS if c in frame.columns]
    _qlib_cols_excluded = [c for c in _QLIB_COLS if c not in frame.columns]
    if _qlib_cols_used:
        numeric = numeric + [c for c in _qlib_cols_used if c not in numeric]
        print(f"  qlib features: {_qlib_cols_used} (included in numeric features)")
    if _qlib_cols_excluded:
        print(f"  qlib features: {_qlib_cols_excluded} not in frame — skipped "
              f"(run with --include-qlib-features to add them)")

    # FE-1: triple-barrier labeling override
    _label_mode = getattr(args, "label_mode", "fixed_horizon")
    if _label_mode == "triple_barrier":
        _outcome_col = f"h{args.hold}_outcome"
        if _outcome_col not in frame.columns:
            _outcome_col = "h3_outcome" if "h3_outcome" in frame.columns else None
        if _outcome_col is not None:
            try:
                from tradingagents.labeling.triple_barrier import compute_triple_barrier_labels
                _timeout_mode = getattr(args, "triple_barrier_timeout", "zero")
                _return_col = f"h{args.hold}_return" if f"h{args.hold}_return" in frame.columns else None
                _tb_labels = compute_triple_barrier_labels(
                    frame, outcome_col=_outcome_col, timeout_handling=_timeout_mode,
                    passthrough_return_col=_return_col,
                )
                _before = len(frame)
                if _timeout_mode == "drop":
                    frame = frame[~_tb_labels.isna()].copy()
                    _tb_labels = _tb_labels.dropna()
                frame["_win_label"] = _tb_labels.fillna(0).astype(int).values
                print(f"  Triple-barrier labels: {_timeout_mode} mode, "
                      f"{len(frame):,} rows (was {_before:,}), "
                      f"win rate={frame['_win_label'].mean():.1%}")
            except Exception as _tb_err:
                print(f"  Triple-barrier labeling failed ({_tb_err}), falling back to fixed_horizon")
        else:
            print(f"  Triple-barrier: outcome column not found, using fixed_horizon labels")

    # BT-2: slippage deduction from labels
    _slippage_bps = float(getattr(args, "label_slippage_bps", 0.0))
    if _slippage_bps != 0.0:
        _ret_col_slip = f"h{args.hold}_return"
        if _ret_col_slip in frame.columns:
            _slip_frac = _slippage_bps / 10000.0
            _adj_return = pd.to_numeric(frame[_ret_col_slip], errors="coerce") - _slip_frac
            frame["_win_label"] = (_adj_return > 0).astype(int)
            print(f"  Slippage {_slippage_bps:.1f}bps applied to labels, "
                  f"new win rate={frame['_win_label'].mean():.1%}")

    # Embargo >= forward-return horizon so boundary train rows can't leak
    # their hold-ahead label into the test year (honest bundle metrics).
    _embargo = int(np.ceil(int(args.hold) * 1.5)) + 1
    train_idx, test_idx, test_period = _ml_time_split(frame, embargo_days=_embargo)
    train_df = frame.loc[train_idx].copy()
    test_df = frame.loc[test_idx].copy()

    # ── Calibration split: carve off 15% of train set for fitting calibrators.
    # This is done AFTER the time-split so calibration data is always older
    # than the test period (no leakage in calibration either).
    calibrate = getattr(args, "calibrate", True)
    cal_df = pd.DataFrame()
    if calibrate and len(train_df) >= 500:
        cal_size = max(200, int(len(train_df) * 0.15))
        # Take the most-recent training rows for calibration (temporally safe)
        cal_df = train_df.tail(cal_size).copy()
        train_df = train_df.iloc[: len(train_df) - cal_size].copy()
        if len(train_df) < args.min_rows:
            # Not enough left for training — skip calibration
            train_df = pd.concat([train_df, cal_df]).sort_values("_scan_dt").reset_index(drop=True)
            cal_df = pd.DataFrame()
            calibrate = False

    # ── Sample weights: upweight executed (rule-passing) rows ─────────────────
    # Executed rows represent the actual trading decisions the model will gate.
    # Rejected rows are noise relative to the inference use case.
    # Scale factor > 1 focuses learning on setup quality discrimination without
    # discarding rejected rows entirely (which contain implicit market context).
    executed_weight = float(getattr(args, "executed_weight", 20.0))
    if executed_weight > 1.0 and not executed_only and "candidate_status" in train_df.columns:
        sample_weight_train = np.where(
            train_df["candidate_status"].values == "executed",
            executed_weight, 1.0
        ).astype(np.float64)
        n_exc = int((train_df["candidate_status"] == "executed").sum())
        n_rej = len(train_df) - n_exc
        print(f"  Sample weighting: {n_exc:,} executed rows × {executed_weight}× "
              f"+ {n_rej:,} rejected rows × 1×")
    else:
        sample_weight_train = None
        if executed_weight > 1.0:
            print("  Sample weighting: skipped (executed_only=True or no candidate_status column)")

    # ── Temporal decay: upweight recent signals to adapt to current market regime ──
    # Older signals may represent different market conditions; decay their weight so
    # the model focuses more on patterns that match the current environment.
    # decay=0.0 (default): uniform weights. decay=0.02: 24-month-old signals get ~0.62x weight.
    temporal_decay = float(getattr(args, "temporal_decay", 0.0))
    if temporal_decay > 0.0 and "_scan_dt" in train_df.columns:
        months_ago = (train_df["_scan_dt"].max() - train_df["_scan_dt"]).dt.days / 30.44
        decay_weights = np.exp(-temporal_decay * months_ago.fillna(0).values)
        if sample_weight_train is not None:
            sample_weight_train = sample_weight_train * decay_weights
        else:
            sample_weight_train = decay_weights.astype(np.float64)
        effective_n = decay_weights.sum()
        print(f"  Temporal decay: λ={temporal_decay} → effective_n={effective_n:.0f} (raw={len(train_df)})")

    # Also weight calibration set (if any)
    sample_weight_cal = None
    if (sample_weight_train is not None
            and len(cal_df) > 0
            and "candidate_status" in cal_df.columns):
        sample_weight_cal = np.where(
            cal_df["candidate_status"].values == "executed",
            executed_weight, 1.0
        ).astype(np.float64)
        if temporal_decay > 0.0 and "_scan_dt" in cal_df.columns:
            months_ago_cal = (cal_df["_scan_dt"].max() - cal_df["_scan_dt"]).dt.days / 30.44
            decay_cal = np.exp(-temporal_decay * months_ago_cal.fillna(0).values)
            sample_weight_cal = sample_weight_cal * decay_cal

    # ── Pre-training PSI pruning: drop features with severe distribution shift ──
    # Features that look different in test vs train are unreliable for live inference.
    # Pruning them before training prevents the model from learning spurious correlations
    # that only hold in the training regime (e.g. spy_ret60, sector_breadth in bull markets).
    _psi_pruned_features: list = []
    if getattr(args, "auto_prune_psi", True) and len(test_df) >= 30:
        try:
            sys.path.insert(0, str(ROOT))
            from tradingagents.portfolio.feature_monitor import FeatureMonitor
            _fm_pre = FeatureMonitor()
            _psi_cols = [c for c in numeric if c in train_df.columns and c in test_df.columns]
            _psi_pre = _fm_pre.compute_psi_report(train_df, test_df, _psi_cols)
            _psi_pruned_features = [f for f, d in _psi_pre.items() if d.get("status") == "fail"]
            if _psi_pruned_features:
                numeric = [f for f in numeric if f not in _psi_pruned_features]
                print(f"  PSI pre-pruning: dropped {len(_psi_pruned_features)} unstable features "
                      f"(PSI>0.25): {_psi_pruned_features[:8]}")
            else:
                print(f"  PSI pre-pruning: all features stable, no pruning needed")
        except Exception as _psi_pre_err:
            print(f"  PSI pre-pruning skipped: {_psi_pre_err}")

    x_train, feature_names = _ml_design_matrix(train_df, numeric, categorical)
    x_test, _ = _ml_design_matrix(test_df, numeric, categorical, feature_names)

    # keep_empty_features prevents all-NaN train columns from disappearing
    # and desynchronizing feature_names, model inputs, and importance reports.
    try:
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    except TypeError:  # sklearn < 1.2 fallback
        imputer = SimpleImputer(strategy="median")
    x_train_imp = imputer.fit_transform(x_train)
    x_test_imp = imputer.transform(x_test)
    if x_train_imp.shape[1] != len(feature_names):
        keep = getattr(imputer, "get_support", lambda: None)()
        if keep is not None and len(keep) == len(feature_names):
            feature_names = [f for f, k in zip(feature_names, keep) if k]
        else:
            feature_names = list(feature_names)[: x_train_imp.shape[1]]

    # ── Leakage guard: assert no forward-outcome columns entered feature set
    leaky = _check_feature_leakage(feature_names, args.hold)
    if leaky:
        raise SystemExit(
            f"LEAKAGE DETECTED: forward-outcome columns in feature set: {leaky}\n"
            "Remove them from ML_NUMERIC_FEATURES / ML_CATEGORICAL_FEATURES in backtest.py."
        )

    # LP-2: explicit label-column exclusion check — these are targets, never features
    _LABEL_COLS = {
        "_win_label", "_target_label", "_timeout_label", "_breakout_win_label",
        "_failed_breakout_label", "_big_move_label", "_large_loss_label",
        "_missed_winner_label", "_mfe", "_mae",
    }
    _leaky_labels = _LABEL_COLS & set(feature_names)
    if _leaky_labels:
        raise SystemExit(
            f"LABEL LEAKAGE: outcome-label columns in feature_names: {sorted(_leaky_labels)}\n"
            "These columns are training targets and must not be fed as features."
        )

    # Impute calibration set using same imputer (fit on train only)
    x_cal_imp = None
    if calibrate and len(cal_df) > 0:
        x_cal, _ = _ml_design_matrix(cal_df, numeric, categorical, feature_names)
        x_cal_imp = imputer.transform(x_cal)

    # ── Feature reference statistics for live drift detection ─────────────────
    # Save mean/std per numeric feature from the TRAINING set so paper_trade_today.py
    # can detect when live candidates have distributions shifted from training.
    feature_stats: dict = {}
    for feat in numeric:
        if feat in train_df.columns:
            vals = pd.to_numeric(train_df[feat], errors="coerce").dropna()
            if len(vals) > 0:
                feature_stats[feat] = {
                    "mean": float(vals.mean()),
                    "std": float(vals.std()),
                    "p10": float(vals.quantile(0.10)),
                    "p50": float(vals.quantile(0.50)),
                    "p90": float(vals.quantile(0.90)),
                    "n": int(len(vals)),
                }

    bundle = {
        "created_at": dt.datetime.now().isoformat(),
        "source": str(source),
        "hold": args.hold,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "feature_names": feature_names,
        "feature_stats": feature_stats,
        "imputer": imputer,
        "calibrated": calibrate,
        "thresholds": {
            "ml_probability_threshold": args.ml_probability_threshold,
            "ml_expected_return_min": args.ml_expected_return_min,
            "ml_large_loss_max": args.ml_large_loss_max,
        },
        "models": {},
    }

    y_win_train = train_df["_win_label"].astype(int).to_numpy()
    y_win_test = test_df["_win_label"].astype(int).to_numpy()

    # ── Label distribution diagnostics ───────────────────────────────────────
    # Records class balance and label validity before training.
    # Critical for catching: near-random labels, severe imbalance, degenerate splits.
    _ld_pos = int(y_win_train.sum()); _ld_neg = int(len(y_win_train) - _ld_pos)
    _ld_test_pos = int((test_df["_win_label"].astype(int)).sum())
    _ld_test_neg = int(len(test_df) - _ld_test_pos)
    _ret_col = f"h{args.hold}_return"
    _rets_tr = pd.to_numeric(train_df.get(_ret_col, pd.Series([], dtype=float)), errors="coerce").dropna()
    _rets_te = pd.to_numeric(test_df.get(_ret_col, pd.Series([], dtype=float)), errors="coerce").dropna()
    _executed_train = int((train_df.get("candidate_status", pd.Series()) == "executed").sum()) if "candidate_status" in train_df.columns else None
    label_distribution = {
        "train": {
            "n": int(len(train_df)),
            "win_label_pos": _ld_pos,
            "win_label_neg": _ld_neg,
            "win_rate": round(_ld_pos / max(len(y_win_train), 1), 4),
            "executed_rows": _executed_train,
            "avg_return": round(float(_rets_tr.mean()), 5) if len(_rets_tr) > 0 else None,
            "median_return": round(float(_rets_tr.median()), 5) if len(_rets_tr) > 0 else None,
        },
        "test": {
            "n": int(len(test_df)),
            "win_label_pos": _ld_test_pos,
            "win_label_neg": _ld_test_neg,
            "win_rate": round(_ld_test_pos / max(len(test_df), 1), 4),
            "avg_return": round(float(_rets_te.mean()), 5) if len(_rets_te) > 0 else None,
        },
        "large_loss_rate_train": round(float(train_df["_large_loss_label"].astype(int).mean()), 4)
            if "_large_loss_label" in train_df.columns else None,
    }

    report = {
        "settings": {
            "source": str(source),
            "hold": args.hold,
            "rows_used": int(len(frame)),
            "train_rows": int(len(train_df)),
            "cal_rows": int(len(cal_df)),
            "test_rows": int(len(test_df)),
            "test_period": test_period,
            "feature_count": int(len(feature_names)),
            "calibrated": calibrate,
            "ml_probability_threshold": args.ml_probability_threshold,
            "ml_expected_return_min": args.ml_expected_return_min,
            "ml_large_loss_max": args.ml_large_loss_max,
            "executed_weight": getattr(args, "executed_weight", 1.0),
            "executed_only": getattr(args, "executed_only", False),
            "psi_pruned_features": _psi_pruned_features,
            "psi_pruned_count": len(_psi_pruned_features),
        },
        "label_distribution": label_distribution,
        "models": {},
        "leakage_check": {"status": "clean", "leaky_features": []},
        "qlib_features": {
            "used": bool(_qlib_cols_used),
            "columns": _qlib_cols_used,
            "count": len(_qlib_cols_used),
            "feature_count_before_qlib": int(len(feature_names)) - len(_qlib_cols_used),
            "feature_count_after_qlib": int(len(feature_names)),
        },
    }

    print(f"\n  Label distribution — train: {_ld_pos:,}+ / {_ld_neg:,}- "
          f"({label_distribution['train']['win_rate']:.1%} win rate)"
          + (f" [{_executed_train:,} executed]" if _executed_train else ""))

    # ── Win/loss classifier — ensemble XGBoost + RandomForest when both available
    if len(set(y_win_train)) < 2:
        raise SystemExit("Win/loss labels contain one class only; cannot train classifier.")

    # Primary model (XGBoost if available, else RF)
    win_model = _make_clf(y_win_train, n_estimators=args.n_estimators,
                          max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                          seed=args.seed)
    win_model.fit(x_train_imp, y_win_train, sample_weight=sample_weight_train)

    # Diversity model: always RF for ensemble (uncorrelated with XGB)
    _rf_win = None
    _use_ensemble = _XGB_AVAILABLE and len(x_train_imp) >= 1000
    if _use_ensemble:
        _rf_win = RandomForestClassifier(
            n_estimators=300, max_depth=7, min_samples_leaf=max(20, args.min_samples_leaf),
            class_weight="balanced_subsample", random_state=args.seed + 1, n_jobs=-1,
        )
        _rf_win.fit(x_train_imp, y_win_train, sample_weight=sample_weight_train)

    # Calibrate each model independently
    if calibrate and x_cal_imp is not None:
        y_win_cal = cal_df["_win_label"].astype(int).to_numpy()
        win_model, win_cal_report = _calibrate_classifier(win_model, x_cal_imp, y_win_cal)
        if _rf_win is not None:
            _rf_win, _rf_cal_rpt = _calibrate_classifier(_rf_win, x_cal_imp, y_win_cal)
    else:
        win_cal_report = {"status": "skipped"}
        _rf_cal_rpt = {"status": "skipped"}

    # Ensemble prediction: weighted average (XGB 0.60 + RF 0.40)
    win_prob_primary = win_model.predict_proba(x_test_imp)[:, 1]
    if _rf_win is not None:
        win_prob_rf = _rf_win.predict_proba(x_test_imp)[:, 1]
        win_prob = 0.60 * win_prob_primary + 0.40 * win_prob_rf
    else:
        win_prob = win_prob_primary

    # Store both models; primary used for single-model inference path
    bundle["models"]["win_probability"] = win_model
    if _rf_win is not None:
        bundle["models"]["win_probability_rf"] = _rf_win
        bundle["ensemble_win_weights"] = {"xgb": 0.60, "rf": 0.40}

    roc_primary = None
    roc_rf = None
    roc_ensemble = None
    try:
        roc_primary = float(roc_auc_score(y_win_test, win_prob_primary))
        if _rf_win is not None:
            roc_rf = float(roc_auc_score(y_win_test, win_prob_rf))
            roc_ensemble = float(roc_auc_score(y_win_test, win_prob))
    except Exception:
        pass

    report["models"]["win_probability"] = {
        "metrics": {
            **_classification_metrics(y_win_test, win_prob),
            **(_safe_auc(y_win_test, win_prob) or {}),
        },
        "calibration": win_cal_report,
        "feature_importance": _feature_importance(win_model, feature_names, limit=40),
        "ensemble": {
            "enabled": _rf_win is not None,
            "roc_xgb": roc_primary,
            "roc_rf": roc_rf,
            "roc_ensemble": roc_ensemble,
            "rf_calibration": _rf_cal_rpt if _rf_win is not None else None,
        },
    }

    # ── Large-loss classifier
    y_loss_train = train_df["_large_loss_label"].astype(int).to_numpy()
    y_loss_test = test_df["_large_loss_label"].astype(int).to_numpy()
    if len(set(y_loss_train)) > 1:
        loss_model = _make_clf(y_loss_train, n_estimators=args.n_estimators,
                               max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                               seed=args.seed)
        loss_model.fit(x_train_imp, y_loss_train, sample_weight=sample_weight_train)

        if calibrate and x_cal_imp is not None:
            y_loss_cal = cal_df["_large_loss_label"].astype(int).to_numpy()
            loss_model, loss_cal_report = _calibrate_classifier(loss_model, x_cal_imp, y_loss_cal)
        else:
            loss_cal_report = {"status": "skipped"}

        loss_prob = loss_model.predict_proba(x_test_imp)[:, 1]
        bundle["models"]["large_loss_probability"] = loss_model
        report["models"]["large_loss_probability"] = {
            "metrics": {
                **_classification_metrics(y_loss_test, loss_prob),
                **(_safe_auc(y_loss_test, loss_prob) or {}),
            },
            "calibration": loss_cal_report,
            "feature_importance": _feature_importance(loss_model, feature_names, limit=40),
        }
    else:
        loss_prob = np.zeros(len(test_df))
        report["models"]["large_loss_probability"] = {"status": "single_class_labels"}

    # ── Expected return regressor
    # TM-10: ER regressor (R²≈0.012, gate disabled via ml_expected_return_min=-99) is still
    # trained and shipped every run, consuming compute and disk. Gate behind --train-er flag.
    # Default: skip training (saves ~30% of training wall-time with no predictive loss).
    ret_col = f"h{args.hold}_return"
    y_ret_train = pd.to_numeric(train_df[ret_col], errors="coerce")
    y_ret_test = pd.to_numeric(test_df[ret_col], errors="coerce")
    ret_mask_train = y_ret_train.notna()
    ret_mask_test = y_ret_test.notna()
    expected_return = np.zeros(len(test_df))
    train_er = getattr(args, "train_er", False)  # TM-10: default False
    if train_er and ret_mask_train.sum() >= args.min_rows // 2 and ret_mask_test.sum() >= 20:
        ret_model = _make_reg(n_estimators=args.n_estimators,
                              max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                              seed=args.seed)
        _sw_ret = sample_weight_train[ret_mask_train] if sample_weight_train is not None else None
        ret_model.fit(x_train_imp[ret_mask_train], y_ret_train[ret_mask_train],
                      sample_weight=_sw_ret)
        expected_return = ret_model.predict(x_test_imp)
        bundle["models"]["expected_return"] = ret_model
        report["models"]["expected_return"] = {
            "metrics": _regression_metrics(y_ret_test[ret_mask_test], expected_return[ret_mask_test]),
            "feature_importance": _feature_importance(ret_model, feature_names, limit=40),
        }
    else:
        report["models"]["expected_return"] = {"status": "skipped (use --train-er to enable; R²≈0.012, gate disabled)"}

    # ── Target/timeout classifiers
    for label, model_key, target_col in [
        ("target_before_stop_probability", "target_before_stop_probability", "_target_label"),
        ("timeout_probability", "timeout_probability", "_timeout_label"),
    ]:
        y_train = train_df[target_col].astype(int).to_numpy()
        y_test = test_df[target_col].astype(int).to_numpy()
        if len(set(y_train)) < 2:
            report["models"][label] = {"status": "single_class_labels"}
            continue
        model = _make_clf(y_train, n_estimators=args.n_estimators,
                          max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                          seed=args.seed)
        model.fit(x_train_imp, y_train, sample_weight=sample_weight_train)

        if calibrate and x_cal_imp is not None:
            y_aux_cal = cal_df[target_col].astype(int).to_numpy()
            model, aux_cal_report = _calibrate_classifier(model, x_cal_imp, y_aux_cal)
        else:
            aux_cal_report = {"status": "skipped"}

        prob = model.predict_proba(x_test_imp)[:, 1]
        bundle["models"][model_key] = model
        report["models"][label] = {
            "metrics": {
                **_classification_metrics(y_test, prob),
                **(_safe_auc(y_test, prob) or {}),
            },
            "calibration": aux_cal_report,
            "feature_importance": _feature_importance(model, feature_names, limit=40),
        }

    # ── Gate analysis with all strategies
    target_model = bundle["models"].get("target_before_stop_probability")
    timeout_model = bundle["models"].get("timeout_probability")
    target_prob = target_model.predict_proba(x_test_imp)[:, 1] if target_model else None
    timeout_prob = timeout_model.predict_proba(x_test_imp)[:, 1] if timeout_model else None
    report["gate_analysis"] = _build_gate_analysis(
        test_df,
        args.hold,
        win_prob,
        loss_prob,
        expected_return,
        target_prob=target_prob,
        timeout_prob=timeout_prob,
        ml_prob_threshold=args.ml_probability_threshold,
        ml_expected_return_min=args.ml_expected_return_min,
        ml_large_loss_max=args.ml_large_loss_max,
        diagnostics_limit=args.gate_diagnostics_limit,
    )

    # ── Threshold search: find best win-prob threshold by expectancy × sqrt(n)
    report["threshold_search"] = _threshold_search(
        test_df, win_prob, loss_prob, expected_return, args.hold,
        large_loss_max=args.ml_large_loss_max,
    )

    # ── Per-regime diagnostics ─────────────────────────────────────────────────
    try:
        report["regime_diagnostics"] = _per_regime_diagnostics(test_df, win_prob, y_win_test)
    except Exception as _rd_err:
        report["regime_diagnostics"] = {"error": str(_rd_err)}

    # ── Confidence bucket (decile) analysis ───────────────────────────────────
    try:
        report["confidence_buckets"] = _confidence_bucket_analysis(test_df, win_prob, y_win_test)
        # Quick summary: top decile win rate is key performance indicator
        buckets = report["confidence_buckets"].get("buckets", [])
        if buckets:
            top = buckets[-1]
            print(f"\n  Top decile: prob=[{top['prob_range'][0]:.3f},{top['prob_range'][1]:.3f}] "
                  f"n={top['n']} WR={top['actual_win_rate']:.3f} "
                  f"exp={top.get('avg_return_pct','?')}")
    except Exception as _cb_err:
        report["confidence_buckets"] = {"error": str(_cb_err)}

    # ── Walk-forward validation (inside training script for report completeness)
    # Uses the same purged expanding-window approach as backtest.py.
    # This is the most honest estimate of live performance — not used for selection.
    if getattr(args, "run_walk_forward", True) and not executed_only:
        try:
            print("\n  Running purged walk-forward validation...")
            oos_df, wf_win_prob, wf_loss_prob, wf_er = _ml_purged_walk_forward(
                frame, numeric, categorical, args.hold,
                min_train_rows=max(500, args.min_rows),
                step_days=21, max_folds=40,
            )
            if len(oos_df) > 0 and len(wf_win_prob) == len(oos_df):
                y_wf = oos_df["_win_label"].astype(int).to_numpy()
                try:
                    from sklearn.metrics import roc_auc_score as _roc
                    wf_roc = round(float(_roc(y_wf, wf_win_prob)), 4) if len(set(y_wf)) > 1 else None
                except Exception:
                    wf_roc = None
                wf_wr = round(float(y_wf.mean()), 4)
                wf_pred_wr = round(float((wf_win_prob >= 0.50).mean()), 4)
                wf_high_conf_mask = wf_win_prob >= args.ml_probability_threshold
                wf_hc_n = int(wf_high_conf_mask.sum())
                wf_hc_wr = round(float(y_wf[wf_high_conf_mask].mean()), 4) if wf_hc_n > 0 else None
                report["walk_forward"] = {
                    "n_oos_rows": int(len(oos_df)),
                    "roc_auc": wf_roc,
                    "actual_win_rate": wf_wr,
                    "predicted_win_rate_at_0.50": wf_pred_wr,
                    "high_conf_n": wf_hc_n,
                    "high_conf_win_rate": wf_hc_wr,
                    "threshold_used": args.ml_probability_threshold,
                }
                print(f"  Walk-forward: n_oos={len(oos_df):,} WF-ROC={wf_roc} "
                      f"WR@{args.ml_probability_threshold:.2f}={wf_hc_wr} n={wf_hc_n}")
            else:
                report["walk_forward"] = {"status": "insufficient_oos_data", "n_oos": len(oos_df)}
        except Exception as _wf_err:
            report["walk_forward"] = {"error": str(_wf_err)}
            print(f"  Walk-forward skipped: {_wf_err}")
    else:
        report["walk_forward"] = {"status": "skipped", "reason": "executed_only mode or disabled"}

    # ── WF-1: CPCV (optional) ─────────────────────────────────────────────────
    if getattr(args, "cpcv", False):
        try:
            from tradingagents.validation.cpcv import combinatorial_purged_cv
            from sklearn.ensemble import RandomForestClassifier as _RFC
            def _cpcv_train(X, y):
                return _RFC(n_estimators=50, max_depth=6, random_state=42).fit(X, y)
            def _cpcv_test(model, X):
                return model.predict_proba(X)[:, 1]
            _n_splits = getattr(args, "cpcv_splits", 5)
            _n_test_sp = getattr(args, "cpcv_test_splits", 2)
            _cpcv_result = combinatorial_purged_cv(
                frame[feature_names + ["_scan_dt", "_win_label"]].copy(),
                n_splits=_n_splits, n_test_splits=_n_test_sp,
                embargo_days=_embargo, train_fn=_cpcv_train, test_fn=_cpcv_test,
                feature_cols=feature_names, label_col="_win_label", fast_mode=True,
            )
            report["cpcv"] = _cpcv_result
            print(f"\n  CPCV: {_cpcv_result['n_paths']} paths | "
                  f"mean_Sharpe={_cpcv_result.get('mean_sharpe', 'n/a'):.3f} "
                  f"± {_cpcv_result.get('std_sharpe', 'n/a'):.3f}")
        except Exception as _cpcv_err:
            report["cpcv"] = {"error": str(_cpcv_err)}
            print(f"  CPCV skipped: {_cpcv_err}")

    # ── WF-2: Deflated Sharpe Ratio (optional) ────────────────────────────────
    if getattr(args, "compute_dsr", False):
        try:
            from tradingagents.validation.deflated_sharpe import deflated_sharpe_ratio
            _wf_roc = report.get("walk_forward", {}).get("roc_auc")
            if _wf_roc is not None:
                # Convert ROC to approximate Sharpe (2*ROC - 1 is a rough proxy)
                _wf_sharpe = max(0.0, 2.0 * float(_wf_roc) - 1.0)
                _n_trials = getattr(args, "dsr_n_trials", 50)
                _T = report.get("walk_forward", {}).get("n_oos_rows", 252)
                _dsr = deflated_sharpe_ratio(_wf_sharpe, n_trials=_n_trials, T=int(_T))
                report["deflated_sharpe"] = {
                    "wf_roc": _wf_roc, "approx_sharpe": _wf_sharpe,
                    "n_trials": _n_trials, "T": _T, "dsr": _dsr,
                    "likely_genuine": _dsr >= 0.5,
                }
                flag = "GENUINE" if _dsr >= 0.5 else "OVERFIT-RISK"
                print(f"\n  DSR={_dsr:.4f} ({flag}) | SR≈{_wf_sharpe:.3f} | trials={_n_trials}")
        except Exception as _dsr_err:
            report["deflated_sharpe"] = {"error": str(_dsr_err)}

    # ── FE-2: Noise feature test (optional) ───────────────────────────────────
    if getattr(args, "noise_feature_test", False):
        try:
            from tradingagents.validation.noise_feature_test import noise_feature_test
            from sklearn.ensemble import RandomForestClassifier as _RFC2
            def _nft_train(X, y):
                return _RFC2(n_estimators=50, max_depth=5, random_state=42).fit(X, y)
            _X_nft = train_df[feature_names].copy()
            _y_nft = train_df["_win_label"].astype(int)
            _nft_result = noise_feature_test(_X_nft, _y_nft, model_fn=_nft_train, n_noise=10, seed=42)
            report["noise_feature_test"] = _nft_result
            _n_below = len(_nft_result.get("features_below_noise", []))
            _n_above = len(_nft_result.get("features_above_noise", []))
            print(f"\n  Noise test: {_n_above} above noise, {_n_below} below noise "
                  f"(threshold={_nft_result.get('noise_threshold', 'n/a'):.6f})")
            if _n_below > 0:
                print(f"  ↳ Features below noise (review before next retrain): "
                      f"{_nft_result['features_below_noise'][:5]}")
        except Exception as _nft_err:
            report["noise_feature_test"] = {"error": str(_nft_err)}
            print(f"  Noise feature test skipped: {_nft_err}")

    # ── Feature PSI monitoring: train vs test distribution shift ──────────────
    try:
        sys.path.insert(0, str(ROOT))
        from tradingagents.portfolio.feature_monitor import FeatureMonitor
        _fm = FeatureMonitor()
        # Use only numeric features that appear in both splits
        _num_cols = [c for c in numeric if c in train_df.columns and c in test_df.columns]
        _psi_report = _fm.compute_psi_report(train_df, test_df, _num_cols)
        _psi_summary = _fm.summary(_psi_report)
        report["feature_psi"] = {
            "summary": _psi_summary,
            "passes_gate": _psi_summary["passes_gate"],
            "n_fail": _psi_summary["n_fail"],
            "n_watch": _psi_summary["n_watch"],
            "worst_features": _psi_summary["worst_features"],
        }
        if _psi_summary["n_fail"] > 0:
            bad = [f for f, d in _psi_report.items() if d.get("status") == "fail"]
            print(f"\n  ⚠ PSI WARNING: {len(bad)} features with PSI > 0.25: {bad[:5]}")
        else:
            print(f"\n  ✓ Feature PSI check: {_psi_summary['n_watch']} watching, 0 failing")
    except Exception as _psi_err:
        report["feature_psi"] = {"error": str(_psi_err)}

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model_bundle.joblib"
    report_path = output_dir / "training_report.json"
    joblib.dump(bundle, model_path)
    report["artifacts"] = {
        "model_bundle": str(model_path.resolve()),
        "training_report": str(report_path.resolve()),
    }
    try:
        from tradingagents.ml.training_report_schema import validate_training_report
        _schema_warnings = validate_training_report(report, strict=False)
        if _schema_warnings:
            for _w in _schema_warnings:
                print(f"  [SCHEMA WARNING] {_w}")
    except Exception as _sv_err:
        print(f"  [SCHEMA] validation skipped: {_sv_err}")
    report_path.write_text(json.dumps(report, indent=2, default=str))
    return report


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train TradingAgents ML gate models from backtest trades."
    )
    parser.add_argument("--input", required=True, help="Backtest JSON with all_trades or exported CSV.")
    parser.add_argument("--output-dir", default="ml_models/latest", help="Directory for model bundle/report.")
    parser.add_argument(
        "--mode", choices=["pullback", "breakout"], default="pullback",
        help="Training mode: 'pullback' (default, confirmed_pullback strategy) or "
             "'breakout' (breakout_v2 strategy — uses ML_NUMERIC_FEATURES_BREAKOUT and "
             "breakout-specific labels: _breakout_win_label, _big_move_label). "
             "Output dir default changes to ml_models/breakout/ when mode=breakout."
    )
    parser.add_argument("--hold", type=int, default=3, help="Hold period label to train on.")
    parser.add_argument("--max-rows", type=int, default=0, help="Maximum rows to train/evaluate (default: 0 = use all rows).")
    parser.add_argument("--min-rows", type=int, default=300, help="Minimum usable rows required.")
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-samples-leaf", type=int, default=30)
    parser.add_argument(
        "--executed-weight", type=float, default=20.0,
        help="Sample weight multiplier for executed (rule-passing) rows vs rejected rows. "
             "0 or 1.0 = no weighting. Default: 20 (executed rows count 20× in training)."
    )
    parser.add_argument(
        "--temporal-decay", type=float, default=0.0,
        help="Exponential decay rate for temporal sample weighting (0=off). "
             "λ=0.02: signals 24 months old get 0.62× weight; λ=0.03: 0.49× weight. "
             "Focuses model on recent market regime. Combine with executed-weight. "
             "Default 0.0 (uniform weights, backward compat)."
    )
    parser.add_argument(
        "--executed-only", action="store_true", default=False,
        help="Train exclusively on executed (rule-passing) rows. "
             "Requires at least --min-rows executed rows in input."
    )
    parser.add_argument(
        "--run-walk-forward", action=argparse.BooleanOptionalAction, default=True,
        help="Run purged walk-forward validation and include in report (default: on). "
             "Disable with --no-run-walk-forward to save time when iterating quickly."
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ml-probability-threshold", type=float, default=0.60)
    parser.add_argument("--ml-expected-return-min", type=float, default=-99.0)
    parser.add_argument("--ml-large-loss-max", type=float, default=1.0)
    parser.add_argument("--gate-diagnostics-limit", type=int, default=250)
    parser.add_argument(
        "--calibrate", action=argparse.BooleanOptionalAction, default=True,
        help="Apply isotonic/sigmoid probability calibration after training (default: on)."
    )
    parser.add_argument(
        "--train-er", action="store_true", default=False,
        help="TM-10: train the expected-return regressor (R²≈0.012, gate disabled). "
             "Skipped by default — no predictive value and costs ~30%% of wall-time."
    )
    # FE-1: triple-barrier labeling
    parser.add_argument(
        "--label-mode",
        choices=["fixed_horizon", "triple_barrier"],
        default="fixed_horizon",
        help="Label mode. 'fixed_horizon' uses outcome column directly. "
             "'triple_barrier' uses path-aware target/stop/timeout labels.",
    )
    parser.add_argument(
        "--triple-barrier-timeout",
        choices=["zero", "drop", "pass_through"],
        default="zero",
        help="How to handle TIMED_OUT rows in triple-barrier mode. Default: zero (label=0).",
    )
    # BT-2: slippage in labels
    parser.add_argument(
        "--label-slippage-bps",
        type=float,
        default=0.0,
        help="Round-trip slippage (basis points) to deduct from returns before "
             "computing win/loss labels. E.g., 10 = 10bps = 0.10%%. Default 0.",
    )
    # WF-1: CPCV validation
    parser.add_argument(
        "--cpcv", action="store_true", default=False,
        help="Run Combinatorial Purged Cross-Validation (CPCV) and include in report.",
    )
    parser.add_argument("--cpcv-splits", type=int, default=5)
    parser.add_argument("--cpcv-test-splits", type=int, default=2)
    # WF-2: Deflated Sharpe Ratio
    parser.add_argument(
        "--compute-dsr", action="store_true", default=False,
        help="Compute Deflated Sharpe Ratio (DSR) adjusting walk-forward SR for trial count.",
    )
    parser.add_argument("--dsr-n-trials", type=int, default=50,
                        help="Number of hyperparameter/model trials evaluated before this run.")
    # FE-2: noise feature test
    parser.add_argument(
        "--noise-feature-test", action="store_true", default=False,
        help="Inject Gaussian noise features and flag real features below noise threshold.",
    )
    # MS-1: GBDT ensemble member selection
    parser.add_argument(
        "--models",
        nargs="+",
        choices=["xgb", "lgbm", "catboost", "rf"],
        default=["xgb", "rf"],
        help="Model types to include in the soft-voting ensemble. Default: xgb rf.",
    )
    return parser.parse_args()


def main():
    report = train_models(parse_args())
    settings = report["settings"]
    win = report["models"]["win_probability"]["metrics"]
    thr_search = report.get("threshold_search", {})
    rec_thr = thr_search.get("recommended_threshold")

    print("\nTradingAgents ML training complete")
    print(f"  Rows used     : {settings['rows_used']:,}")
    print(f"  Train / cal / test : {settings['train_rows']:,} / {settings.get('cal_rows',0):,} / {settings['test_rows']:,}")
    print(f"  Test period   : {settings['test_period']}")
    print(f"  Calibrated    : {settings.get('calibrated', False)}")
    print(f"  Win AUC       : {win.get('roc_auc', 'n/a')}")
    print(f"  Win precision : {win.get('precision', 'n/a')}")
    print(f"  Win recall    : {win.get('recall', 'n/a')}")
    if rec_thr is not None:
        rec = thr_search.get(str(rec_thr), {})
        print(f"  Rec. threshold: {rec_thr} → wr={rec.get('win_rate','?')} exp={rec.get('avg_return_pct','?')}% n={rec.get('n','?')}")

    wf = report.get("walk_forward", {})
    if wf.get("roc_auc"):
        print(f"  Walk-fwd ROC  : {wf['roc_auc']} "
              f"(high-conf WR={wf.get('high_conf_win_rate','?')} n={wf.get('high_conf_n','?')})")
    elif wf.get("status"):
        print(f"  Walk-fwd      : {wf['status']}")

    regime = report.get("regime_diagnostics", {})
    spy_regimes = regime.get("spy_regime", {})
    if spy_regimes:
        print("  Regime ROCs   :", {r: spy_regimes[r].get("roc_auc") for r in spy_regimes if spy_regimes[r].get("roc_auc")})

    print(f"  Model bundle  : {report['artifacts']['model_bundle']}")
    print(f"  Report        : {report['artifacts']['training_report']}")

    leakage = report.get("leakage_check", {})
    if leakage.get("leaky_features"):
        print(f"\n  ⚠ LEAKAGE WARNING: {leakage['leaky_features']}")
    else:
        print("  Leakage check : clean")


if __name__ == "__main__":
    main()
