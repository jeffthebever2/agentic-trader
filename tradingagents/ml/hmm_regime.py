"""HMM Regime Feature Layer — RD-1.

Fits a Gaussian HMM on market return/volatility data and outputs per-row
probabilistic regime state features. Intended as feature engineering, not
a hard trading signal.

Usage:
    from tradingagents.ml.hmm_regime import HMMRegimeFeatures

    hmm = HMMRegimeFeatures(n_components=3, seed=42)
    hmm.fit(spy_returns_series)                        # pd.Series of log-returns
    features = hmm.transform(spy_returns_series)       # pd.DataFrame of probs + state
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


class HMMRegimeFeatures:
    """Gaussian HMM wrapper that produces regime probability features.

    Parameters
    ----------
    n_components : int
        Number of hidden states (regimes). Typically 2-4.
    seed : int
        Random seed for reproducibility.
    n_iter : int
        Max EM iterations for hmmlearn.
    """

    def __init__(self, n_components: int = 3, seed: int = 42, n_iter: int = 100):
        self.n_components = n_components
        self.seed = seed
        self.n_iter = n_iter
        self._model = None
        self._is_fitted = False
        self._state_labels: Optional[list] = None

    def fit(self, returns: pd.Series) -> "HMMRegimeFeatures":
        """Fit Gaussian HMM on a series of log-returns.

        Parameters
        ----------
        returns : pd.Series
            Log-return series (e.g., np.log(close/close.shift(1)).dropna()).
        """
        try:
            from hmmlearn import hmm as _hmmlearn  # type: ignore
        except ImportError as exc:
            raise ImportError("hmmlearn not installed: pip install hmmlearn") from exc

        _X = self._to_feature_matrix(returns)
        model = _hmmlearn.GaussianHMM(
            n_components=self.n_components,
            covariance_type="full",
            n_iter=self.n_iter,
            random_state=self.seed,
        )
        model.fit(_X)
        self._model = model
        self._is_fitted = True
        # Label regimes by mean return (ascending: bearish → bullish)
        means = model.means_.flatten()
        self._state_labels = [
            f"regime_{i}" for i in np.argsort(means)
        ]
        return self

    def transform(self, returns: pd.Series, filtered: bool = True) -> pd.DataFrame:
        """Generate regime probability features for a return series.

        Parameters
        ----------
        returns : pd.Series
            Log-return series.
        filtered : bool
            True (default): FILTERED probabilities P(state_t | obs_1..t) via the
            forward pass only — point-in-time, safe as ML training features.
            False: SMOOTHED probabilities P(state_t | obs_1..T) via
            forward-backward. Each row then uses FUTURE observations — a
            lookahead leak if used as features; only valid for offline
            historical regime analysis.

        Returns
        -------
        pd.DataFrame
            Columns: hmm_prob_0..N + hmm_state (argmax).
        """
        if not self._is_fitted or self._model is None:
            raise RuntimeError("Call fit() before transform()")

        _X = self._to_feature_matrix(returns)
        probs = None
        loglik = None
        if filtered:
            probs, loglik = self._filtered_posteriors(_X)
        if probs is None:
            # smoothed (forward-backward) — lookahead within the window
            log_prob, probs = self._model.score_samples(_X)
            # per-row average of the sequence log-likelihood (scalar spread evenly)
            loglik = np.full(len(probs), log_prob / max(len(probs), 1))

        result = pd.DataFrame(
            probs,
            index=returns.index,
            columns=[f"hmm_prob_{i}" for i in range(self.n_components)],
        )
        result["hmm_state"] = probs.argmax(axis=1)
        result["hmm_log_likelihood"] = loglik
        return result

    def _filtered_posteriors(self, X: np.ndarray):
        """Forward-only (filtered) state probabilities P(state_t | obs_1..t).

        Returns (probs, per_row_loglik) where per_row_loglik[t] is the
        predictive log-likelihood increment log P(obs_t | obs_1..t-1) —
        point-in-time, unlike the old scalar whole-sequence broadcast.
        Uses hmmlearn internals; returns (None, None) if the private API is
        unavailable so the caller can fall back to smoothed posteriors.
        """
        try:
            from scipy.special import logsumexp
            model = self._model
            framelogprob = model._compute_log_likelihood(X)
            out = model._do_forward_pass(framelogprob)
            fwdlattice = out[1] if isinstance(out, tuple) else out
            norm = logsumexp(fwdlattice, axis=1, keepdims=True)
            probs = np.exp(fwdlattice - norm)
            cum_ll = norm.ravel()
            loglik = np.diff(cum_ll, prepend=0.0)
            return probs, loglik
        except Exception:
            return None, None

    def predict_state(self, returns: pd.Series) -> np.ndarray:
        """Return most likely state sequence via Viterbi."""
        if not self._is_fitted or self._model is None:
            raise RuntimeError("Call fit() before predict_state()")
        _X = self._to_feature_matrix(returns)
        return self._model.predict(_X)

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @staticmethod
    def _to_feature_matrix(returns: pd.Series) -> np.ndarray:
        """Convert return series to (n, 1) float64 array for hmmlearn."""
        arr = np.array(returns, dtype=np.float64).reshape(-1, 1)
        # Replace NaN/inf with 0 (HMM expects clean data)
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        return arr
