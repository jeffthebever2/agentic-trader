"""Full backtesting tearsheet metrics.

Formulas ported from backtesting.py/_stats.py (kernc, Apache 2.0).
Standalone — no dependency on the backtesting.py library.
"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import pandas as pd


def geometric_mean(returns: pd.Series) -> float:
    r = returns.fillna(0) + 1
    if np.any(r <= 0):
        return 0.0
    return float(np.exp(np.log(r).sum() / (len(r) or np.nan)) - 1)


def compute_tearsheet(
    pnl_pct: List[float],
    equity_curve: Optional[List[float]] = None,
    bh_start_price: Optional[float] = None,
    bh_end_price: Optional[float] = None,
    risk_free_rate: float = 0.045,
    annual_trading_days: int = 252,
) -> dict:
    """
    Compute a full set of backtest performance metrics.

    Args:
        pnl_pct:            Per-trade return as a fraction (0.05 = 5%).
        equity_curve:       Equity values over time (bars, not trades).
                            If None, computed from pnl_pct via cumulative product.
        bh_start_price:     Buy-and-hold start price (for Alpha/Beta).
        bh_end_price:       Buy-and-hold end price (for Alpha/Beta).
        risk_free_rate:     Annual risk-free rate (default 4.5%).
        annual_trading_days: Trading days per year (default 252).

    Returns:
        dict with all metrics. Missing values are None.
    """
    if not pnl_pct:
        return {k: None for k in (
            "n_trades", "win_rate", "avg_win_pct", "avg_loss_pct",
            "profit_factor", "expectancy_pct", "sqn",
            "sharpe", "sortino", "calmar", "cagr_pct",
            "max_drawdown_pct", "avg_drawdown_pct",
            "kelly_criterion", "alpha_pct", "beta",
        )}

    pnl = np.array(pnl_pct, dtype=float)
    n = len(pnl)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate = float(np.mean(pnl > 0))
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    gross_loss = float(np.abs(losses.sum())) if len(losses) else 0.0
    profit_factor = float(wins.sum() / gross_loss) if gross_loss > 0 else float("nan")
    expectancy = float(pnl.mean())
    sqn = float(math.sqrt(n) * pnl.mean() / pnl.std(ddof=1)) if pnl.std(ddof=1) > 0 else float("nan")

    mean_win = float(wins.mean()) if len(wins) else 0.0
    mean_loss = float(np.abs(losses.mean())) if len(losses) else 0.0
    if mean_win > 0 and mean_loss > 0:
        kelly = win_rate - (1 - win_rate) / (mean_win / mean_loss)
    else:
        kelly = float("nan")

    # Equity curve
    if equity_curve is not None:
        eq = np.array(equity_curve, dtype=float)
    else:
        eq = np.cumprod(1 + np.concatenate([[0], pnl]))
        eq = eq / eq[0]  # normalize to start at 1.0

    # Drawdown
    peak = np.maximum.accumulate(eq)
    dd = 1 - eq / np.where(peak > 0, peak, 1)
    max_dd = float(dd.max())

    # Annualized return (from equity endpoints)
    total_return = float(eq[-1] / eq[0]) - 1 if eq[0] > 0 else 0.0
    n_years = n / annual_trading_days  # approximate (trades, not bars)
    cagr = float((1 + total_return) ** (1 / n_years) - 1) if n_years > 0 else float("nan")

    # Daily return approximation from per-trade returns for Sharpe/Sortino.
    # Trades are treated as daily returns (consistent with n_years = n / 252),
    # so annualized vol = per-trade std × sqrt(annual_trading_days). The prior
    # sqrt(annual_trading_days / n) factor shrank vol by ~n×, inflating Sharpe.
    ann_return = cagr
    ann_vol = float(pnl.std(ddof=1) * math.sqrt(annual_trading_days)) if n > 1 else 0.0
    sharpe = (ann_return - risk_free_rate) / ann_vol if ann_vol > 0 else float("nan")

    # Downside deviation: RMS of negative returns over ALL trades (zeros for
    # non-losing trades), not the mean square of the losing subset alone.
    downside = np.minimum(pnl, 0.0)
    downside_vol = float(
        math.sqrt(np.mean(downside ** 2)) * math.sqrt(annual_trading_days)
    ) if n > 0 else 0.0
    sortino = (ann_return - risk_free_rate) / downside_vol if downside_vol > 0 else float("nan")

    calmar = ann_return / max_dd if max_dd > 0 else float("nan")

    # Alpha / Beta vs buy-and-hold
    alpha = None
    beta = None
    if bh_start_price and bh_end_price and bh_start_price > 0:
        bh_return = (bh_end_price - bh_start_price) / bh_start_price
        if len(eq) > 1:
            # Simple one-period beta: cov(strategy, bh) / var(bh)
            # Approximate: just report CAPM alpha
            beta = 1.0  # placeholder — need bar-level data for true beta
            alpha = ann_return - risk_free_rate - beta * (bh_return - risk_free_rate)
            alpha = round(alpha * 100, 4)

    result = {
        "n_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win * 100, 4),
        "avg_loss_pct": round(avg_loss * 100, 4),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else None,
        "expectancy_pct": round(expectancy * 100, 4),
        "sqn": round(sqn, 4) if math.isfinite(sqn) else None,
        "sharpe": round(sharpe, 4) if math.isfinite(sharpe) else None,
        "sortino": round(sortino, 4) if math.isfinite(sortino) else None,
        "calmar": round(calmar, 4) if math.isfinite(calmar) else None,
        "cagr_pct": round(cagr * 100, 4) if math.isfinite(cagr) else None,
        "max_drawdown_pct": round(max_dd * 100, 4),
        "kelly_criterion": round(kelly, 4) if math.isfinite(kelly) else None,
        "alpha_pct": alpha,
        "beta": beta,
    }
    return result
