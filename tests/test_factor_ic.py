"""Tests for tradingagents.qlib_integration.factor_ic"""
import math
import numpy as np
import pandas as pd
import pytest
from tradingagents.qlib_integration.factor_ic import (
    compute_ic,
    ic_series,
    icir,
    factor_summary,
)


def _make_cross_section(n_stocks: int = 20, rng=None):
    rng = rng or np.random.default_rng(42)
    factor = pd.Series(rng.standard_normal(n_stocks),
                       index=[f"T{i:02d}" for i in range(n_stocks)])
    returns = factor * 0.1 + rng.standard_normal(n_stocks) * 0.05
    return factor, pd.Series(returns, index=factor.index)


# ── compute_ic ────────────────────────────────────────────────────────────────

def test_perfect_factor_ic_is_one():
    factor = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    fwd = factor.copy()
    assert compute_ic(factor, fwd) == pytest.approx(1.0, abs=1e-6)


def test_inverse_factor_ic_is_neg_one():
    factor = pd.Series(range(10), dtype=float)
    fwd = factor[::-1].reset_index(drop=True)
    assert compute_ic(factor, fwd) == pytest.approx(-1.0, abs=1e-6)


def test_too_few_observations_returns_nan():
    f = pd.Series([1.0, 2.0, 3.0])
    r = pd.Series([1.0, 2.0, 3.0])
    assert math.isnan(compute_ic(f, r))


def test_correlated_factor_positive_ic():
    f, r = _make_cross_section()
    ic = compute_ic(f, r)
    assert ic > 0.3  # strong synthetic correlation


# ── ic_series ─────────────────────────────────────────────────────────────────

def _make_panel(n_dates: int = 30, n_stocks: int = 20, rng=None):
    rng = rng or np.random.default_rng(0)
    dates = pd.date_range("2026-01-01", periods=n_dates)
    tickers = [f"T{i:02d}" for i in range(n_stocks)]
    factor = pd.DataFrame(rng.standard_normal((n_dates, n_stocks)), index=dates, columns=tickers)
    returns = factor * 0.05 + rng.standard_normal((n_dates, n_stocks)) * 0.02
    return factor, pd.DataFrame(returns, index=dates, columns=tickers)


def test_ic_series_returns_series():
    f, r = _make_panel()
    ics = ic_series(f, r, forward_days=5)
    assert isinstance(ics, pd.Series)
    assert len(ics) > 0


def test_ic_series_values_bounded():
    f, r = _make_panel()
    ics = ic_series(f, r, forward_days=5).dropna()
    assert (ics >= -1.0).all() and (ics <= 1.0).all()


# ── icir ─────────────────────────────────────────────────────────────────────

def test_icir_nan_for_too_few():
    ics = pd.Series([0.1, 0.2, 0.3])
    assert math.isnan(icir(ics, min_obs=20))


def test_icir_positive_for_good_factor():
    ics = pd.Series([0.15] * 30)
    result = icir(ics, min_obs=20)
    # constant series has std→0 so icir blows up — ensure it's inf or nan but not negative
    assert result is None or math.isnan(result) or result > 0


def test_icir_finite_for_noisy_factor():
    rng = np.random.default_rng(1)
    ics = pd.Series(rng.normal(0.05, 0.10, 50))
    result = icir(ics)
    assert math.isfinite(result)
    assert result > 0


# ── factor_summary ────────────────────────────────────────────────────────────

def test_factor_summary_keys():
    f, r = _make_panel(n_dates=40)
    s = factor_summary(f, r, forward_days=5)
    for key in ("ic_mean", "ic_std", "icir", "ic_positive_rate", "ic_abs_mean", "n_obs"):
        assert key in s


def test_factor_summary_n_obs_positive():
    f, r = _make_panel(n_dates=40)
    s = factor_summary(f, r, forward_days=5)
    assert s["n_obs"] > 0
