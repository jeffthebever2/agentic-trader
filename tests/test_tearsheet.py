"""Tests for tradingagents.backtesting.tearsheet"""
import math
import pytest
from tradingagents.backtesting.tearsheet import compute_tearsheet


# ── empty input ───────────────────────────────────────────────────────────────

def test_empty_returns_nones():
    r = compute_tearsheet([])
    assert r["n_trades"] is None
    assert r["sharpe"] is None


# ── basic metrics ─────────────────────────────────────────────────────────────

def test_n_trades_correct():
    pnl = [0.01, -0.02, 0.03, 0.01, -0.005]
    r = compute_tearsheet(pnl)
    assert r["n_trades"] == 5


def test_win_rate():
    pnl = [0.01, 0.02, -0.03, 0.01, 0.02]  # 4 wins, 1 loss
    r = compute_tearsheet(pnl)
    assert r["win_rate"] == pytest.approx(0.80)


def test_all_wins_profit_factor_none():
    pnl = [0.01, 0.02, 0.03]
    r = compute_tearsheet(pnl)
    # no losses → profit_factor is None (division by zero avoided)
    assert r["profit_factor"] is None or r["profit_factor"] > 100


def test_all_losses_win_rate_zero():
    pnl = [-0.01, -0.02, -0.03]
    r = compute_tearsheet(pnl)
    assert r["win_rate"] == pytest.approx(0.0)


def test_max_drawdown_non_negative():
    pnl = [0.05, -0.10, 0.03, -0.08]
    r = compute_tearsheet(pnl)
    assert r["max_drawdown_pct"] >= 0


def test_expectancy_matches_mean():
    pnl = [0.01, -0.02, 0.03]
    r = compute_tearsheet(pnl)
    expected = sum(pnl) / len(pnl) * 100
    assert r["expectancy_pct"] == pytest.approx(expected, rel=1e-4)


def test_kelly_is_finite_for_mixed_trades():
    pnl = [0.05, -0.02, 0.04, -0.01, 0.03, -0.02, 0.06, -0.01]
    r = compute_tearsheet(pnl)
    k = r["kelly_criterion"]
    assert k is not None
    assert math.isfinite(k)


def test_sharpe_present_for_mixed_trades():
    pnl = [0.05, -0.02, 0.04, -0.01, 0.03] * 4
    r = compute_tearsheet(pnl)
    assert r["sharpe"] is not None


def test_sqn_present():
    pnl = [0.05, -0.02, 0.04, -0.01, 0.03] * 4
    r = compute_tearsheet(pnl)
    assert r["sqn"] is not None


def test_profit_factor_correct():
    pnl = [0.10, 0.10, -0.05]  # wins=0.20, losses=0.05 → PF=4
    r = compute_tearsheet(pnl)
    assert r["profit_factor"] == pytest.approx(4.0, rel=1e-3)


def test_cagr_present():
    pnl = [0.01] * 252
    r = compute_tearsheet(pnl)
    assert r["cagr_pct"] is not None
