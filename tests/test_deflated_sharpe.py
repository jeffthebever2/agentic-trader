"""Tests for tradingagents.backtesting.deflated_sharpe"""
import math
import pytest
from tradingagents.backtesting.deflated_sharpe import (
    expected_max_sharpe,
    deflated_sharpe_ratio,
    dsr_label,
)


# ── expected_max_sharpe ───────────────────────────────────────────────────────

def test_single_trial_returns_mean():
    assert expected_max_sharpe(1, mean_sr=0.5) == pytest.approx(0.5)


def test_more_trials_higher_expected_max():
    e10 = expected_max_sharpe(10)
    e100 = expected_max_sharpe(100)
    assert e100 > e10


def test_positive_mean_shifts_result():
    base = expected_max_sharpe(50, mean_sr=0.0)
    shifted = expected_max_sharpe(50, mean_sr=1.0)
    assert shifted == pytest.approx(base + 1.0)


# ── deflated_sharpe_ratio ─────────────────────────────────────────────────────

def test_dsr_in_unit_interval():
    dsr = deflated_sharpe_ratio(observed_sr=2.0, n_trials=50, n_obs=252)
    assert 0.0 <= dsr <= 1.0


def test_high_sr_single_trial_high_dsr():
    dsr = deflated_sharpe_ratio(observed_sr=3.0, n_trials=1, n_obs=252)
    assert dsr > 0.90


def test_many_trials_deflate_dsr():
    sr = 1.5
    dsr_few = deflated_sharpe_ratio(sr, n_trials=5, n_obs=252)
    dsr_many = deflated_sharpe_ratio(sr, n_trials=500, n_obs=252)
    assert dsr_few > dsr_many


def test_zero_observed_sr_low_dsr():
    dsr = deflated_sharpe_ratio(observed_sr=0.0, n_trials=50, n_obs=252)
    assert dsr < 0.5


def test_n_obs_one_returns_valid():
    dsr = deflated_sharpe_ratio(observed_sr=1.0, n_trials=10, n_obs=1)
    assert 0.0 <= dsr <= 1.0


# ── dsr_label ─────────────────────────────────────────────────────────────────

def test_label_strong():
    assert "STRONG" in dsr_label(0.96)


def test_label_moderate():
    assert "MODERATE" in dsr_label(0.80)


def test_label_weak():
    assert "WEAK" in dsr_label(0.60)


def test_label_spurious():
    assert "SPURIOUS" in dsr_label(0.30)
