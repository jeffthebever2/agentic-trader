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
    _ml_time_split,
    _regression_metrics,
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


def train_models(args) -> dict:
    source = Path(args.input)
    rows = _load_rows(source)
    frame, numeric, categorical = _ml_prepare_frame(rows, args.hold)
    if len(frame) < args.min_rows:
        raise SystemExit(
            f"Only {len(frame)} usable rows found; need at least {args.min_rows}."
        )

    if args.max_rows and args.max_rows > 0 and len(frame) > args.max_rows:
        frame = (
            frame.sort_values("_scan_dt")
            .sample(args.max_rows, random_state=args.seed)
            .sort_values("_scan_dt")
        )
    frame = frame.sort_values("_scan_dt").reset_index(drop=True)

    # Embargo >= forward-return horizon so boundary train rows can't leak
    # their hold-ahead label into the test year (honest bundle metrics).
    _embargo = int(np.ceil(int(args.hold) * 1.5)) + 1
    train_idx, test_idx, test_period = _ml_time_split(frame, embargo_days=_embargo)
    train_df = frame.loc[train_idx].copy()
    test_df = frame.loc[test_idx].copy()

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

    bundle = {
        "created_at": dt.datetime.now().isoformat(),
        "source": str(source),
        "hold": args.hold,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "feature_names": feature_names,
        "imputer": imputer,
        "thresholds": {
            "ml_probability_threshold": args.ml_probability_threshold,
            "ml_expected_return_min": args.ml_expected_return_min,
            "ml_large_loss_max": args.ml_large_loss_max,
        },
        "models": {},
    }

    report = {
        "settings": {
            "source": str(source),
            "hold": args.hold,
            "rows_used": int(len(frame)),
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "test_period": test_period,
            "feature_count": int(len(feature_names)),
            "ml_probability_threshold": args.ml_probability_threshold,
            "ml_expected_return_min": args.ml_expected_return_min,
            "ml_large_loss_max": args.ml_large_loss_max,
        },
        "models": {},
    }

    y_win_train = train_df["_win_label"].astype(int).to_numpy()
    y_win_test = test_df["_win_label"].astype(int).to_numpy()
    if len(set(y_win_train)) < 2:
        raise SystemExit("Win/loss labels contain one class only; cannot train classifier.")

    win_model = _make_clf(y_win_train, n_estimators=args.n_estimators,
                          max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                          seed=args.seed)
    win_model.fit(x_train_imp, y_win_train)
    win_prob = win_model.predict_proba(x_test_imp)[:, 1]
    bundle["models"]["win_probability"] = win_model
    report["models"]["win_probability"] = {
        "metrics": {
            **_classification_metrics(y_win_test, win_prob),
            **(_safe_auc(y_win_test, win_prob) or {}),
        },
        "feature_importance": _feature_importance(win_model, feature_names, limit=40),
    }

    y_loss_train = train_df["_large_loss_label"].astype(int).to_numpy()
    y_loss_test = test_df["_large_loss_label"].astype(int).to_numpy()
    if len(set(y_loss_train)) > 1:
        loss_model = _make_clf(y_loss_train, n_estimators=args.n_estimators,
                               max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                               seed=args.seed)
        loss_model.fit(x_train_imp, y_loss_train)
        loss_prob = loss_model.predict_proba(x_test_imp)[:, 1]
        bundle["models"]["large_loss_probability"] = loss_model
        report["models"]["large_loss_probability"] = {
            "metrics": {
                **_classification_metrics(y_loss_test, loss_prob),
                **(_safe_auc(y_loss_test, loss_prob) or {}),
            },
            "feature_importance": _feature_importance(loss_model, feature_names, limit=40),
        }
    else:
        loss_prob = np.zeros(len(test_df))
        report["models"]["large_loss_probability"] = {"status": "single_class_labels"}

    ret_col = f"h{args.hold}_return"
    y_ret_train = pd.to_numeric(train_df[ret_col], errors="coerce")
    y_ret_test = pd.to_numeric(test_df[ret_col], errors="coerce")
    ret_mask_train = y_ret_train.notna()
    ret_mask_test = y_ret_test.notna()
    expected_return = np.zeros(len(test_df))
    if ret_mask_train.sum() >= args.min_rows // 2 and ret_mask_test.sum() >= 20:
        ret_model = _make_reg(n_estimators=args.n_estimators,
                              max_depth=args.max_depth, min_samples_leaf=args.min_samples_leaf,
                              seed=args.seed)
        ret_model.fit(x_train_imp[ret_mask_train], y_ret_train[ret_mask_train])
        expected_return = ret_model.predict(x_test_imp)
        bundle["models"]["expected_return"] = ret_model
        report["models"]["expected_return"] = {
            "metrics": _regression_metrics(y_ret_test[ret_mask_test], expected_return[ret_mask_test]),
            "feature_importance": _feature_importance(ret_model, feature_names, limit=40),
        }

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
        model.fit(x_train_imp, y_train)
        prob = model.predict_proba(x_test_imp)[:, 1]
        bundle["models"][model_key] = model
        report["models"][label] = {
            "metrics": {
                **_classification_metrics(y_test, prob),
                **(_safe_auc(y_test, prob) or {}),
            },
            "feature_importance": _feature_importance(model, feature_names, limit=40),
        }

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

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model_bundle.joblib"
    report_path = output_dir / "training_report.json"
    joblib.dump(bundle, model_path)
    report["artifacts"] = {
        "model_bundle": str(model_path.resolve()),
        "training_report": str(report_path.resolve()),
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    return report


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train TradingAgents ML gate models from backtest trades."
    )
    parser.add_argument("--input", required=True, help="Backtest JSON with all_trades or exported CSV.")
    parser.add_argument("--output-dir", default="ml_models/latest", help="Directory for model bundle/report.")
    parser.add_argument("--hold", type=int, default=3, help="Hold period label to train on.")
    parser.add_argument("--max-rows", type=int, default=0, help="Maximum rows to train/evaluate (default: 0 = use all rows).")
    parser.add_argument("--min-rows", type=int, default=300, help="Minimum usable rows required.")
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-samples-leaf", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ml-probability-threshold", type=float, default=0.60)
    parser.add_argument("--ml-expected-return-min", type=float, default=-99.0)
    parser.add_argument("--ml-large-loss-max", type=float, default=1.0)
    parser.add_argument("--gate-diagnostics-limit", type=int, default=250)
    return parser.parse_args()


def main():
    report = train_models(parse_args())
    settings = report["settings"]
    win = report["models"]["win_probability"]["metrics"]
    print("\nTradingAgents ML training complete")
    print(f"  Rows used     : {settings['rows_used']:,}")
    print(f"  Train / test  : {settings['train_rows']:,} / {settings['test_rows']:,}")
    print(f"  Test period   : {settings['test_period']}")
    print(f"  Win AUC       : {win.get('roc_auc', 'n/a')}")
    print(f"  Win precision : {win.get('precision', 'n/a')}")
    print(f"  Win recall    : {win.get('recall', 'n/a')}")
    print(f"  Model bundle  : {report['artifacts']['model_bundle']}")
    print(f"  Report        : {report['artifacts']['training_report']}")


if __name__ == "__main__":
    main()
