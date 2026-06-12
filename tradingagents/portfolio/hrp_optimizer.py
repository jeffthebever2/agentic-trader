"""Hierarchical Risk Parity (HRP) Portfolio Optimizer — PC-2.

Implements Lopez de Prado (2016) HRP algorithm:
  1. Correlation-distance matrix: d_ij = sqrt((1 - rho_ij) / 2)
  2. Hierarchical clustering: scipy.cluster.hierarchy.linkage (single-linkage)
  3. Quasi-diagonalization: sort assets by dendrogram leaf order
  4. Recursive bisection: allocate inverse-variance weights within each cluster

HRP avoids Markowitz matrix inversion instability and works with many
correlated assets (e.g., 20-50 equities in a daily equity portfolio).

Usage:
    from tradingagents.portfolio.hrp_optimizer import HRPOptimizer

    optimizer = HRPOptimizer()
    weights = optimizer.fit(returns_df)   # returns_df: (n_days, n_assets)
    # weights: dict[ticker -> float], sums to 1.0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, List, Optional


class HRPOptimizer:
    """Hierarchical Risk Parity weights from historical returns.

    Parameters
    ----------
    min_obs : int
        Minimum number of observations required to compute weights.
    cov_shrink : float
        Ledoit-Wolf-style shrinkage applied to the diagonal before clustering.
        0.0 = no shrinkage. Typical: 0.1 for small samples.
    """

    def __init__(self, min_obs: int = 20, cov_shrink: float = 0.0):
        self.min_obs = min_obs
        self.cov_shrink = cov_shrink

    def fit(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        """Compute HRP weights from a returns DataFrame.

        Parameters
        ----------
        returns_df : pd.DataFrame
            Shape (n_days, n_assets). Columns are ticker symbols.
            Returns should be simple (arithmetic), not log-returns.

        Returns
        -------
        dict[str, float]
            Asset → weight mapping. Weights sum to 1.0.
            Returns equal weights if insufficient data or scipy unavailable.
        """
        tickers = list(returns_df.columns)
        n = len(tickers)
        if n == 1:
            return {tickers[0]: 1.0}

        if len(returns_df) < self.min_obs:
            w = 1.0 / n
            return {t: round(w, 6) for t in tickers}

        try:
            from scipy.cluster.hierarchy import linkage, leaves_list  # type: ignore
        except ImportError:
            w = 1.0 / n
            return {t: round(w, 6) for t in tickers}

        # ── Covariance + correlation matrix ───────────────────────────────────
        cov = returns_df.cov().values.astype(np.float64)
        if self.cov_shrink > 0.0:
            # Simple shrinkage toward diagonal (structured)
            target = np.diag(np.diag(cov))
            cov = (1.0 - self.cov_shrink) * cov + self.cov_shrink * target

        std = np.sqrt(np.maximum(np.diag(cov), 1e-12))
        corr = cov / np.outer(std, std)
        np.fill_diagonal(corr, 1.0)
        corr = np.clip(corr, -1.0, 1.0)

        # ── Distance matrix (correlation-based) ───────────────────────────────
        dist = np.sqrt(np.maximum((1.0 - corr) / 2.0, 0.0))

        # ── Hierarchical clustering on upper triangle ─────────────────────────
        condensed = dist[np.triu_indices(n, k=1)]
        link = linkage(condensed, method="single")
        sorted_idx = list(leaves_list(link))

        # ── Recursive bisection ───────────────────────────────────────────────
        weights_arr = np.ones(n, dtype=np.float64)
        self._recursive_bisect(sorted_idx, weights_arr, cov)

        weights_arr = weights_arr / weights_arr.sum()

        return {tickers[i]: round(float(weights_arr[i]), 6) for i in range(n)}

    def _recursive_bisect(
        self,
        sorted_idx: List[int],
        weights: np.ndarray,
        cov: np.ndarray,
    ) -> None:
        """Recursively bisect the cluster list and assign inverse-variance weights."""
        if len(sorted_idx) <= 1:
            return

        mid = len(sorted_idx) // 2
        left = sorted_idx[:mid]
        right = sorted_idx[mid:]

        var_left = self._cluster_variance(left, cov)
        var_right = self._cluster_variance(right, cov)

        # Inverse-variance split
        total_var = var_left + var_right
        if total_var <= 0.0:
            alpha_left = 0.5
        else:
            alpha_left = var_right / total_var

        weights[left] *= alpha_left
        weights[right] *= (1.0 - alpha_left)

        self._recursive_bisect(left, weights, cov)
        self._recursive_bisect(right, weights, cov)

    @staticmethod
    def _cluster_variance(idx: List[int], cov: np.ndarray) -> float:
        """Compute inverse-variance weighted portfolio variance of a cluster."""
        if not idx:
            return 0.0
        sub_cov = cov[np.ix_(idx, idx)]
        diag_var = np.diag(sub_cov)
        # Inverse-variance weights within cluster
        with np.errstate(divide="ignore", invalid="ignore"):
            inv_var = np.where(diag_var > 0, 1.0 / diag_var, 0.0)
        total_inv = inv_var.sum()
        if total_inv <= 0.0:
            return 0.0
        w = inv_var / total_inv
        return float(w @ sub_cov @ w)
