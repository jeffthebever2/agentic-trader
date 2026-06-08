"""Tests for tradingagents.portfolio.options_pricing"""
import math
import pytest
from tradingagents.portfolio.options_pricing import (
    bs_put_price,
    bs_call_price,
    implied_volatility,
    protective_put_annual_cost_pct,
    put_delta,
)


# ── bs_put_price ──────────────────────────────────────────────────────────────

def test_put_price_atm_positive():
    p = bs_put_price(S=100, K=100, T=0.25, r=0.045, sigma=0.20)
    assert p > 0


def test_put_itm_greater_than_otm():
    itm = bs_put_price(S=95, K=100, T=0.25, r=0.045, sigma=0.20)
    otm = bs_put_price(S=105, K=100, T=0.25, r=0.045, sigma=0.20)
    assert itm > otm


def test_put_at_expiry_intrinsic_only():
    p = bs_put_price(S=90, K=100, T=0.0, r=0.045, sigma=0.20)
    assert p == pytest.approx(10.0, abs=0.01)


def test_put_otm_at_expiry_zero():
    p = bs_put_price(S=110, K=100, T=0.0, r=0.045, sigma=0.20)
    assert p == pytest.approx(0.0, abs=0.01)


# ── bs_call_price ─────────────────────────────────────────────────────────────

def test_call_price_atm_positive():
    c = bs_call_price(S=100, K=100, T=0.25, r=0.045, sigma=0.20)
    assert c > 0


def test_put_call_parity():
    S, K, T, r, sigma = 100.0, 100.0, 0.25, 0.045, 0.20
    call = bs_call_price(S, K, T, r, sigma)
    put = bs_put_price(S, K, T, r, sigma)
    pv_k = K * math.exp(-r * T)
    # C - P = S - PV(K)
    assert (call - put) == pytest.approx(S - pv_k, abs=0.01)


# ── implied_volatility ────────────────────────────────────────────────────────

def test_iv_recovers_sigma():
    S, K, T, r, sigma = 100.0, 95.0, 0.25, 0.045, 0.22
    market_price = bs_put_price(S, K, T, r, sigma)
    iv = implied_volatility(market_price, S, K, T, r, option_type="put")
    assert iv == pytest.approx(sigma, abs=1e-4)


def test_iv_call_recovers_sigma():
    S, K, T, r, sigma = 100.0, 105.0, 0.25, 0.045, 0.18
    market_price = bs_call_price(S, K, T, r, sigma)
    iv = implied_volatility(market_price, S, K, T, r, option_type="call")
    assert iv == pytest.approx(sigma, abs=1e-4)


# ── protective_put_annual_cost_pct ────────────────────────────────────────────

def test_annual_cost_positive():
    cost = protective_put_annual_cost_pct(S=500, otm_pct=0.05, T_days=30, r=0.045, sigma=0.20)
    assert cost > 0


def test_annual_cost_increases_with_vol():
    low = protective_put_annual_cost_pct(S=500, sigma=0.15)
    high = protective_put_annual_cost_pct(S=500, sigma=0.35)
    assert high > low


def test_annual_cost_zero_at_expiry():
    cost = protective_put_annual_cost_pct(S=500, T_days=0, sigma=0.20)
    assert cost == 0.0


# ── put_delta ─────────────────────────────────────────────────────────────────

def test_put_delta_negative():
    d = put_delta(S=100, K=100, T=0.25, r=0.045, sigma=0.20)
    assert -1.0 <= d <= 0.0


def test_deep_itm_put_delta_near_minus_one():
    d = put_delta(S=50, K=100, T=0.25, r=0.045, sigma=0.10)
    assert d < -0.90
