"""Combinatorial Purged Cross-Validation (CPCV) — WF-1.

Generates C(n_splits, n_test_splits) out-of-sample paths, each with purging
and embargo applied. Returns a distribution of OOS metrics rather than a single
Sharpe estimate, enabling overfit detection through path spread.

Reference: Lopez de Prado (2018) "Advances in Financial Machine Learning",
Chapter 12: Cross-Validation in Finance.

Usage:
    from tradingagents.validation.cpcv import combinatorial_purged_cv
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier

    result = combinatorial_purged_cv(
        df=df,
        n_splits=8,
        n_test_splits=2,
        embargo_days=10,
        train_fn=lambda X, y: RandomForestClassifier(n_estimators=100).fit(X, y),
        test_fn=lambda model, X: model.predict_proba(X)[:, 1],
    )
    print(f"CPCV Sharpe: {result['mean_sharpe']:.3f} ± {result['std_sharpe']:.3f} "
          f"over {result['n_paths']} paths")
"""

import math
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


def combinatorial_purged_cv(
    df: pd.DataFrame,
    n_splits: int,
    n_test_splits: int,
    embargo_days: int,
    train_fn: Callable[[np.ndarray, np.ndarray], Any],
    test_fn: Callable[[Any, np.ndarray], np.ndarray],
    label_col: str = "_win_label",
    date_col: str = "_scan_dt",
    feature_cols: Optional[List[str]] = None,
    return_col: Optional[str] = None,
    fast_mode: bool = False,
) -> Dict[str, Any]:
    """Run Combinatorial Purged Cross-Validation.

    Parameters
    ----------
    df : pd.DataFrame
        Feature + label DataFrame sorted by time (ascending).
    n_splits : int
        Number of sequential time groups to split df into.
    n_test_splits : int
        Number of groups used as test in each combination. C(n_splits, n_test_splits) paths.
    embargo_days : int
        Calendar days to drop from training before each test group (leakage prevention).
    train_fn : Callable
        fn(X_train: np.ndarray, y_train: np.ndarray) -> fitted_model.
    test_fn : Callable
        fn(model, X_test: np.ndarray) -> predicted_probabilities (np.ndarray, shape [n]).
    label_col : str
        Binary label column name.
    date_col : str
        Date/datetime column for ordering and purging.
    feature_cols : list[str] or None
        Feature columns to pass to train_fn / test_fn. If None, uses all non-label
        non-date numeric columns.
    return_col : str or None
        Column to use for return-based Sharpe calculation. If None, uses label win/loss.
    fast_mode : bool
        If True, limits to n_splits=5 for faster CI runs.

    Returns
    -------
    dict with keys:
        n_paths : int — number of OOS paths evaluated (= C(n_splits, n_test_splits))
        mean_sharpe : float — mean Sharpe across paths
        std_sharpe : float — std Sharpe across paths
        min_sharpe : float — minimum Sharpe
        max_sharpe : float — maximum Sharpe
        paths : list[dict] — per-path metrics
        mean_roc : float — mean OOS ROC-AUC
        std_roc : float — std OOS ROC-AUC
    """
    if fast_mode:
        n_splits = min(n_splits, 5)
        n_test_splits = min(n_test_splits, 2)

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col).reset_index(drop=True)

    if len(df) < n_splits * 10:
        return {
            "n_paths": 0, "mean_sharpe": float("nan"), "std_sharpe": float("nan"),
            "min_sharpe": float("nan"), "max_sharpe": float("nan"),
            "paths": [], "mean_roc": float("nan"), "std_roc": float("nan"),
            "error": "Insufficient data for CPCV",
        }

    if feature_cols is None:
        exclude = {label_col, date_col, "ticker", "scan_date", "year", "month"}
        feature_cols = [
            c for c in df.columns
            if c not in exclude and pd.api.types.is_numeric_dtype(df[c])
        ]

    # Split df into n_splits sequential groups
    group_size = len(df) // n_splits
    groups: List[pd.Index] = []
    for i in range(n_splits):
        start = i * group_size
        end = (i + 1) * group_size if i < n_splits - 1 else len(df)
        groups.append(df.index[start:end])

    n_paths_expected = math.comb(n_splits, n_test_splits)
    paths: List[Dict] = []
    sharpes: List[float] = []
    rocs: List[float] = []

    for test_group_idxs in combinations(range(n_splits), n_test_splits):
        test_group_idxs_set = set(test_group_idxs)
        train_group_idxs = [i for i in range(n_splits) if i not in test_group_idxs_set]

        # Test rows = union of selected test groups
        test_idx_list = []
        for g in test_group_idxs:
            test_idx_list.extend(groups[g].tolist())
        test_idx = pd.Index(test_idx_list)

        if len(test_idx) == 0:
            continue

        test_dates = df.loc[test_idx, date_col]
        test_start = test_dates.min()

        # Training rows = union of non-test groups with embargo applied
        # Embargo: drop training rows within embargo_days of each test group's start
        train_mask = pd.Series(False, index=df.index)
        for g in train_group_idxs:
            train_mask[groups[g]] = True

        # Apply embargo: remove training rows within embargo_days before any test group start
        for g in test_group_idxs:
            group_start = df.loc[groups[g], date_col].min()
            cutoff = group_start - pd.Timedelta(days=embargo_days)
            # Remove training rows too close to this test group
            train_mask = train_mask & ~(
                (df[date_col] > cutoff) & (df[date_col] < group_start)
            )

        # Remove any training rows that overlap with test period (purging)
        train_mask = train_mask & ~(df.index.isin(test_idx))
        train_idx = df.index[train_mask]

        if len(train_idx) < 30 or len(test_idx) < 5:
            continue

        train_df = df.loc[train_idx]
        test_df_path = df.loc[test_idx]

        # Build feature matrices
        X_train = train_df[feature_cols].fillna(0.0).to_numpy()
        X_test = test_df_path[feature_cols].fillna(0.0).to_numpy()

        if label_col not in train_df.columns:
            continue
        y_train = train_df[label_col].astype(int).to_numpy()

        if len(np.unique(y_train)) < 2:
            continue

        try:
            model = train_fn(X_train, y_train)
            preds = test_fn(model, X_test)
            y_test = test_df_path[label_col].astype(int).to_numpy()

            # Compute ROC-AUC
            roc = float("nan")
            if len(np.unique(y_test)) > 1:
                try:
                    from sklearn.metrics import roc_auc_score
                    roc = float(roc_auc_score(y_test, preds))
                except Exception:
                    pass

            # Compute Sharpe from predictions (binary signal strategy)
            if return_col and return_col in test_df_path.columns:
                rets = test_df_path[return_col].to_numpy()
                signals = (preds >= 0.5).astype(float)
                path_rets = signals * rets
            else:
                # Use label as return proxy: win=+1, loss=-1
                path_rets = np.where(y_test == 1, preds, -preds)

            if len(path_rets) > 1 and path_rets.std() > 0:
                sharpe = float(path_rets.mean() / path_rets.std() * math.sqrt(252))
            else:
                sharpe = 0.0

            path_info = {
                "test_groups": list(test_group_idxs),
                "n_train": int(len(train_idx)),
                "n_test": int(len(test_idx)),
                "sharpe": round(sharpe, 4),
                "roc": round(roc, 4) if not math.isnan(roc) else None,
            }
            paths.append(path_info)
            sharpes.append(sharpe)
            if not math.isnan(roc):
                rocs.append(roc)

        except Exception as e:
            paths.append({"test_groups": list(test_group_idxs), "error": str(e)})

    if not sharpes:
        return {
            "n_paths": 0, "mean_sharpe": float("nan"), "std_sharpe": float("nan"),
            "min_sharpe": float("nan"), "max_sharpe": float("nan"),
            "paths": paths, "mean_roc": float("nan"), "std_roc": float("nan"),
            "error": "No valid paths completed",
        }

    return {
        "n_paths": len(sharpes),
        "n_paths_expected": n_paths_expected,
        "mean_sharpe": round(float(np.mean(sharpes)), 4),
        "std_sharpe": round(float(np.std(sharpes)), 4),
        "min_sharpe": round(float(np.min(sharpes)), 4),
        "max_sharpe": round(float(np.max(sharpes)), 4),
        "mean_roc": round(float(np.mean(rocs)), 4) if rocs else float("nan"),
        "std_roc": round(float(np.std(rocs)), 4) if rocs else float("nan"),
        "paths": paths,
    }
