"""Deflated Sharpe Ratio (DSR) — corrects for backtest selection bias.

Formula from Lopez de Prado (2014) "The Deflated Sharpe Ratio: Correcting
for Selection Bias, Backtest Overfitting and Non-Normality".
Implementation derived from ML4T ch8 (stefan-jansen/machine-learning-for-trading).
"""
from __future__ import annotations

import math

import scipy.stats as ss


# Euler-Mascheroni constant
_EMC = 0.5772156649


def expected_max_sharpe(n_trials: int, mean_sr: float = 0.0, std_sr: float = 1.0) -> float:
    """E[max SR] across n_trials independent strategies.

    Gives the expected maximum Sharpe ratio you'd see by chance when trying
    n_trials different strategy configs (hyperparams, feature sets, etc.).
    """
    if n_trials <= 1:
        return mean_sr
    z = (
        (1 - _EMC) * ss.norm.ppf(1 - 1.0 / n_trials)
        + _EMC * ss.norm.ppf(1 - 1.0 / (n_trials * math.e))
    )
    return mean_sr + std_sr * z


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    sr_std: float = 1.0,
    sr_skew: float = 0.0,
    sr_kurt: float = 3.0,
    n_obs: int = 252,
) -> float:
    """
    Probability that the true Sharpe ratio is positive after correcting for
    selection bias from testing n_trials strategies.

    Args:
        observed_sr: Best Sharpe ratio observed across all trials.
        n_trials:    Number of strategy configs / hyperparameter sets tried.
        sr_std:      Standard deviation of Sharpe ratios across trials (default 1.0).
        sr_skew:     Skewness of strategy returns (default 0 = normal).
        sr_kurt:     Excess kurtosis + 3 of strategy returns (default 3 = normal).
        n_obs:       Number of in-sample observations used to estimate SR (default 252).

    Returns:
        DSR in [0, 1]. DSR < 0.50 means the edge is likely spurious.
    """
    sr0 = expected_max_sharpe(n_trials, 0.0, sr_std)
    denom = math.sqrt(
        (1 - sr_skew * observed_sr + (sr_kurt - 1) / 4.0 * observed_sr ** 2)
        / (n_obs - 1)
    ) if n_obs > 1 else 1.0
    if denom <= 0:
        return 0.0
    z = (observed_sr - sr0) / denom
    return float(ss.norm.cdf(z))


def dsr_label(dsr: float) -> str:
    """Human-readable label for a DSR value."""
    if dsr >= 0.95:
        return "STRONG (DSR≥0.95)"
    if dsr >= 0.75:
        return "MODERATE (DSR≥0.75)"
    if dsr >= 0.50:
        return "WEAK (DSR≥0.50)"
    return "SPURIOUS (DSR<0.50)"
