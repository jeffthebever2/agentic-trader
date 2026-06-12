"""Standalone Black-Scholes options pricing.

No external dependencies beyond numpy/scipy.
Adapted from FinancePy analytic core (domokane/FinancePy, LGPL-3.0).
Used for protective put cost estimation in daily_audit.py.
"""
from __future__ import annotations

import math

import scipy.stats as ss


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European put price via Black-Scholes.

    Args:
        S: current spot price
        K: strike price
        T: time to expiry in years
        r: risk-free rate (e.g. 0.045)
        sigma: annualized implied volatility (e.g. 0.20)
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * ss.norm.cdf(-d2) - S * ss.norm.cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European call price via Black-Scholes."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * ss.norm.cdf(d1) - K * math.exp(-r * T) * ss.norm.cdf(d2)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "put",
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Newton-Raphson IV solver for European put or call.

    Returns implied volatility (annualized), or NaN if solver fails.
    """
    pricer = bs_put_price if option_type == "put" else bs_call_price
    sigma = 0.25
    for _ in range(max_iter):
        price = pricer(S, K, T, r, sigma)
        # vega identical for put and call
        if T <= 0 or sigma <= 0:
            return float("nan")
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        vega = S * math.sqrt(T) * ss.norm.pdf(d1)
        diff = price - market_price
        if abs(diff) < tol:
            break
        if vega < 1e-10:
            return float("nan")
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 5.0))
    return float(sigma)


def protective_put_annual_cost_pct(
    S: float,
    otm_pct: float = 0.05,
    T_days: int = 30,
    r: float = 0.045,
    sigma: float = 0.20,
) -> float:
    """
    Annual cost of an OTM protective put as % of position value.

    Args:
        S:        current spot price
        otm_pct:  how far OTM (0.05 = 5% below spot)
        T_days:   days to expiry
        r:        risk-free rate
        sigma:    annualized volatility (e.g. VIX/100)

    Returns:
        Annualized cost rate as a decimal (0.03 = 3%/yr).
    """
    K = S * (1 - otm_pct)
    T = T_days / 365.0
    put = bs_put_price(S, K, T, r, sigma)
    if T <= 0:
        return 0.0
    return put / S / T  # annualized


def put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put delta (negative, range [-1, 0])."""
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return float(ss.norm.cdf(d1) - 1)
