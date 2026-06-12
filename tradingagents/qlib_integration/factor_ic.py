"""Alpha factor IC / ICIR pipeline.

Computes the Information Coefficient (Spearman rank correlation between a
factor and forward returns) and the IC Information Ratio (ICIR = mean/std).

Inspired by ML4T chapter 4 (stefan-jansen/machine-learning-for-trading) and
the Alphalens evaluation framework.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import pandas as pd
import scipy.stats as ss


def compute_ic(factor: pd.Series, forward_returns: pd.Series) -> float:
    """Cross-sectional Spearman IC between factor values and forward returns.

    Args:
        factor:          Factor values indexed by ticker (cross-sectional slice).
        forward_returns: Realized forward returns indexed by ticker.

    Returns:
        Spearman correlation in [-1, 1], or nan if < 10 observations.
    """
    aligned = pd.concat([factor, forward_returns], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    ic, _ = ss.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(ic)


def ic_series(
    factor_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    forward_days: int = 5,
    min_stocks: int = 10,
) -> pd.Series:
    """Rolling daily IC time series.

    Args:
        factor_df:    DataFrame[date × ticker] of factor values.
        returns_df:   DataFrame[date × ticker] of daily returns.
        forward_days: Hold period for forward return calculation.
        min_stocks:   Minimum stocks per date (skip if fewer).

    Returns:
        pd.Series indexed by date with IC values.
    """
    fwd = returns_df.rolling(forward_days).sum().shift(-forward_days)
    ics = {}
    for date in factor_df.index:
        if date not in fwd.index:
            continue
        f = factor_df.loc[date].dropna()
        r = fwd.loc[date].dropna()
        both = f.index.intersection(r.index)
        if len(both) < min_stocks:
            continue
        ic_val, _ = ss.spearmanr(f[both].values, r[both].values)
        ics[date] = float(ic_val)
    return pd.Series(ics, name=f"IC_{forward_days}d")


def icir(ic_s: pd.Series, min_obs: int = 20) -> float:
    """IC Information Ratio = mean(IC) / std(IC).

    Analogous to Sharpe ratio for alpha factors. Values > 0.5 are considered
    meaningful; > 1.0 is strong.

    Args:
        ic_s:    IC time series from ic_series().
        min_obs: Minimum observations required (returns nan if fewer).
    """
    clean = ic_s.dropna()
    if len(clean) < min_obs:
        return float("nan")
    std = float(clean.std(ddof=1))
    if std <= 0:
        return float("nan")
    return float(clean.mean() / std)


def factor_summary(
    factor_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    forward_days: int = 5,
) -> Dict[str, float]:
    """Compute full IC summary for a factor DataFrame.

    Returns:
        dict with: ic_mean, ic_std, icir, ic_positive_rate, ic_abs_mean
    """
    ics = ic_series(factor_df, returns_df, forward_days)
    clean = ics.dropna()
    if clean.empty:
        return {
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "icir": float("nan"),
            "ic_positive_rate": float("nan"),
            "ic_abs_mean": float("nan"),
            "n_obs": 0,
        }
    return {
        "ic_mean": round(float(clean.mean()), 6),
        "ic_std": round(float(clean.std(ddof=1)), 6),
        "icir": round(icir(ics), 4),
        "ic_positive_rate": round(float((clean > 0).mean()), 4),
        "ic_abs_mean": round(float(clean.abs().mean()), 6),
        "n_obs": len(clean),
    }
