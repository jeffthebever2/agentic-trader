"""GBDT Soft-Voting Ensemble — MS-1.

Wraps multiple fitted classifiers and averages their predicted probabilities
(soft voting). Exposes the same sklearn interface as a single classifier so
it can be dropped into CalibratedClassifierCV or any sklearn pipeline.

Backward compat: bundle["models"]["win_probability"].predict_proba(X) continues
to work whether the underlying model is a single classifier or SoftVotingEnsemble.

Usage:
    from tradingagents.ml.ensemble import SoftVotingEnsemble

    ens = SoftVotingEnsemble([
        ("xgb", xgb_model),
        ("lgbm", lgbm_model),
        ("catboost", cat_model),
    ])
    probs = ens.predict_proba(X)  # shape [n_samples, 2]
"""
import numpy as np


class SoftVotingEnsemble:
    """Average the predict_proba outputs of multiple fitted classifiers.

    Parameters
    ----------
    estimators : list of (name, fitted_classifier) tuples
        Each classifier must implement predict_proba(X) → ndarray [n, 2].
    """

    def __init__(self, estimators: list):
        if not estimators:
            raise ValueError("estimators must be a non-empty list of (name, clf) pairs.")
        self.estimators = estimators
        # sklearn convention: set classes_ from first estimator
        first_clf = estimators[0][1]
        if hasattr(first_clf, "classes_"):
            self.classes_ = first_clf.classes_
        else:
            self.classes_ = np.array([0, 1])

    def predict_proba(self, X) -> np.ndarray:
        """Average predicted probabilities across all member classifiers.

        Returns
        -------
        np.ndarray, shape [n_samples, n_classes]
        """
        if not isinstance(X, np.ndarray):
            try:
                X = np.asarray(X, dtype=float)
            except Exception:
                pass

        all_probs = []
        for name, clf in self.estimators:
            try:
                proba = clf.predict_proba(X)
                if proba.shape[1] != len(self.classes_):
                    continue
                all_probs.append(proba)
            except Exception:
                pass

        if not all_probs:
            # Fallback: uniform distribution
            n = X.shape[0] if hasattr(X, "shape") else len(X)
            return np.full((n, len(self.classes_)), 1.0 / len(self.classes_))

        avg_proba = np.mean(all_probs, axis=0)
        # Renormalize in case floating-point drift
        row_sums = avg_proba.sum(axis=1, keepdims=True)
        avg_proba = avg_proba / np.maximum(row_sums, 1e-12)
        return avg_proba

    def predict(self, X) -> np.ndarray:
        """Return argmax class from averaged probabilities."""
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]

    def __repr__(self) -> str:
        names = [name for name, _ in self.estimators]
        return f"SoftVotingEnsemble(estimators={names})"
