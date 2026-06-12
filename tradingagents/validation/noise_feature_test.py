"""Random Noise Feature Injection Test — FE-2.

Injects N random Gaussian columns into the feature matrix, trains the model,
computes permutation importance, and reports which real features rank below
the noise threshold.

Features below the noise threshold contribute no signal beyond random chance
and may be pruned. However, AUTO-REMOVAL is prohibited — flag only.
Human review is required before any feature removal.

Usage:
    from tradingagents.validation.noise_feature_test import noise_feature_test
    from sklearn.ensemble import RandomForestClassifier

    result = noise_feature_test(
        X=df[feature_cols],
        y=df["_win_label"],
        model_fn=lambda X, y: RandomForestClassifier(n_estimators=100).fit(X, y),
        n_noise=10,
        seed=42,
    )
    if result["features_below_noise"]:
        print(f"WARN: {len(result['features_below_noise'])} features below noise threshold")
        print(result["features_below_noise"])
"""

import warnings
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


def noise_feature_test(
    X: pd.DataFrame,
    y: pd.Series,
    model_fn: Callable[[np.ndarray, np.ndarray], Any],
    n_noise: int = 10,
    seed: int = 42,
    val_frac: float = 0.25,
    n_permutations: int = 5,
) -> Dict:
    """Inject random noise features and measure which real features rank below them.

    Parameters
    ----------
    X : pd.DataFrame
        Feature matrix (n_samples, n_features). No label columns.
    y : pd.Series
        Binary labels (0 or 1), aligned with X.
    model_fn : Callable
        fn(X_train: np.ndarray, y_train: np.ndarray) -> fitted_model.
        Model must have predict_proba(X) method.
    n_noise : int
        Number of random Gaussian columns to inject.
    seed : int
        Random seed for reproducibility.
    val_frac : float
        Fraction of data reserved for importance evaluation (not used for training).
    n_permutations : int
        Number of times to permute each feature for importance estimation.

    Returns
    -------
    dict with keys:
        noise_threshold : float — max importance of noise features (the bar to beat)
        noise_importances : list[float] — importance of each noise column
        features_below_noise : list[str] — real feature names ranking below threshold
        features_above_noise : list[str] — real feature names ranking above threshold
        all_importances : dict[str, float] — importance of every feature (real + noise)
        n_noise : int
        n_real : int
        warning : str or None — human-readable flag when features below noise found
    """
    rng = np.random.default_rng(seed)
    X = X.copy()
    y = y.astype(int)

    real_feature_names = list(X.columns)
    n_real = len(real_feature_names)

    # Inject noise columns
    noise_names = [f"__noise_{i}__" for i in range(n_noise)]
    noise_data = rng.standard_normal((len(X), n_noise))
    X_aug = X.copy()
    for i, name in enumerate(noise_names):
        X_aug[name] = noise_data[:, i]

    # Split into train / validation
    n_val = max(10, int(len(X_aug) * val_frac))
    n_train = len(X_aug) - n_val
    X_train = X_aug.iloc[:n_train].to_numpy(dtype=float)
    y_train = y.iloc[:n_train].to_numpy()
    X_val = X_aug.iloc[n_train:].to_numpy(dtype=float)
    y_val = y.iloc[n_train:].to_numpy()
    all_feature_names = list(X_aug.columns)

    if len(np.unique(y_train)) < 2:
        return {
            "noise_threshold": float("nan"),
            "noise_importances": [],
            "features_below_noise": [],
            "features_above_noise": real_feature_names,
            "all_importances": {},
            "n_noise": n_noise,
            "n_real": n_real,
            "warning": "Single class in training data — test skipped",
        }

    try:
        model = model_fn(X_train, y_train)
    except Exception as e:
        return {
            "noise_threshold": float("nan"),
            "noise_importances": [],
            "features_below_noise": [],
            "features_above_noise": real_feature_names,
            "all_importances": {},
            "n_noise": n_noise,
            "n_real": n_real,
            "warning": f"Model training failed: {e}",
        }

    # Compute baseline score
    try:
        if hasattr(model, "predict_proba"):
            baseline_preds = model.predict_proba(X_val)[:, 1]
        else:
            baseline_preds = model.predict(X_val).astype(float)

        from sklearn.metrics import roc_auc_score
        baseline_score = float(roc_auc_score(y_val, baseline_preds)) if len(np.unique(y_val)) > 1 else 0.5
    except Exception:
        baseline_score = 0.5

    # Permutation importance
    importances: Dict[str, List[float]] = {name: [] for name in all_feature_names}

    for feat_idx, feat_name in enumerate(all_feature_names):
        perm_drops: List[float] = []
        for _ in range(n_permutations):
            X_perm = X_val.copy()
            X_perm[:, feat_idx] = rng.permutation(X_perm[:, feat_idx])
            try:
                if hasattr(model, "predict_proba"):
                    perm_preds = model.predict_proba(X_perm)[:, 1]
                else:
                    perm_preds = model.predict(X_perm).astype(float)

                if len(np.unique(y_val)) > 1:
                    perm_score = float(roc_auc_score(y_val, perm_preds))
                else:
                    perm_score = 0.5
                perm_drops.append(baseline_score - perm_score)
            except Exception:
                perm_drops.append(0.0)
        importances[feat_name] = perm_drops

    mean_importances = {name: float(np.mean(v)) for name, v in importances.items()}

    noise_importances = [mean_importances[n] for n in noise_names]
    noise_threshold = float(np.max(noise_importances)) if noise_importances else 0.0

    real_importances = {name: mean_importances[name] for name in real_feature_names}
    features_below_noise = [
        name for name, imp in real_importances.items() if imp <= noise_threshold
    ]
    features_above_noise = [
        name for name, imp in real_importances.items() if imp > noise_threshold
    ]

    warning = None
    if features_below_noise:
        warning = (
            f"{len(features_below_noise)} real feature(s) rank at or below noise threshold "
            f"({noise_threshold:.6f}). HUMAN REVIEW required before any removal: "
            f"{features_below_noise[:10]}"
        )

    return {
        "noise_threshold": round(noise_threshold, 6),
        "noise_importances": [round(v, 6) for v in noise_importances],
        "features_below_noise": features_below_noise,
        "features_above_noise": features_above_noise,
        "all_importances": {k: round(v, 6) for k, v in sorted(mean_importances.items(), key=lambda x: -x[1])},
        "n_noise": n_noise,
        "n_real": n_real,
        "baseline_roc": round(baseline_score, 4),
        "warning": warning,
    }
