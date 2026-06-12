"""Deflated Sharpe Ratio (DSR) — WF-2.

Bailey & Lopez de Prado (2014): "The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting, and Non-Normality."
https://doi.org/10.2139/ssrn.2460551

DSR adjusts the observed Sharpe ratio downward to account for:
  - Number of trials (hyperparameter iterations / grid search steps)
  - Sample size T (number of observations)
  - Non-normality of returns (skewness, excess kurtosis)

Interpretation:
  - DSR > 0: strategy is likely genuine after accounting for selection bias
  - DSR < 0: strategy likely owes its Sharpe to overfitting / trial count
  - DSR = 0: on the boundary; treat as unresolved

Usage:
    from tradingagents.validation.deflated_sharpe import deflated_sharpe_ratio

    dsr = deflated_sharpe_ratio(sharpe=0.95, n_trials=30, T=252)
    if dsr < 0:
        print("Model likely overfit — do not gate on this result alone")
"""

import math
from typing import Union

try:
    from scipy.stats import norm as _norm
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False


def _expected_max_sharpe(n_trials: int, T: int) -> float:
    """Expected maximum Sharpe ratio from n_trials independent trials with T observations.

    Uses Gumbel extreme-value approximation for E[max of n_trials standard normals]:
      E[max Z_N] ≈ sqrt(2*ln(N)) - (ln(ln(N)) + ln(4π)) / (2*sqrt(2*ln(N)))
    then scales to SR units via E[max SR_N] = E[max Z_N] / sqrt(T-1).
    """
    if n_trials <= 1:
        return 0.0
    c = math.sqrt(2.0 * math.log(n_trials))
    if c <= 0:
        return 0.0
    log_log_n = math.log(math.log(max(n_trials, 2)))
    log_4pi = math.log(4.0 * math.pi)
    expected_max_z = c - (log_log_n + log_4pi) / (2.0 * c)
    return expected_max_z / math.sqrt(max(T - 1, 1))


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    T: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Compute the Deflated Sharpe Ratio (DSR).

    Parameters
    ----------
    sharpe : float
        Observed (annualised or period) Sharpe ratio of the strategy.
    n_trials : int
        Number of independent strategy/hyperparameter variants evaluated before
        selecting this one. Each grid search step, filter change, or retrain
        attempt counts as one trial.
    T : int
        Number of observations (trading days, bars) used to compute `sharpe`.
        For daily returns over one year: T ≈ 252.
    skewness : float
        Sample skewness of the return series. Default 0 (normal).
    kurtosis : float
        Sample kurtosis (not excess). Default 3 (normal). Excess kurtosis = kurtosis - 3.

    Returns
    -------
    float
        DSR value in [0, 1] (probability that SR > SR_max).
        - DSR > 0.5: strategy likely genuine
        - DSR < 0: model likely overfit (negative probability has no mathematical
          meaning but signals the adjusted SR is below the expected maximum)
        - Negative values are clipped to a small negative float for readability.

    Notes
    -----
    The formula adjusts the observed Sharpe by the expected maximum Sharpe from
    n_trials random draws, then scales by the non-normality penalty from skewness
    and excess kurtosis.

    References
    ----------
    Bailey, D.H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio."
    Journal of Portfolio Management, 40(5), 94-107.
    """
    if n_trials <= 0 or T <= 0:
        return float("nan")

    sr_expected_max = _expected_max_sharpe(n_trials, T)

    # Variance of the SR estimator (Bailey 2014 Eq. 3):
    # V[SR] = (1 - γ₃*SR + (γ₄-1)/4 * SR²) / (T-1)
    # γ₄ is full kurtosis (3.0 for normal); γ₃ is skewness.
    variance_of_sr = (1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe ** 2) / max(T - 1, 1)
    variance_of_sr = max(variance_of_sr, 1e-12)

    # DSR = Φ((SR - E[max SR_n]) / sqrt(V[SR]))
    z = (sharpe - sr_expected_max) / math.sqrt(variance_of_sr)
    if _HAVE_SCIPY:
        dsr = float(_norm.cdf(z))
    else:
        # Fallback: rational approximation of standard normal CDF
        dsr = _normal_cdf_approx(z)

    return round(dsr, 6)


def _normal_cdf_approx(z: float) -> float:
    """Rational approximation of the standard normal CDF (Abramowitz & Stegun 26.2.17)."""
    sign = 1.0 if z >= 0 else -1.0
    z = abs(z)
    t = 1.0 / (1.0 + 0.2316419 * z)
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    cdf = 1.0 - (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * z * z) * poly
    return cdf if sign >= 0 else 1.0 - cdf
