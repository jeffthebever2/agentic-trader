"""Performance metrics for paper portfolios.

Pure, deterministic calculations from an account's trades + equity snapshots.
Primary metric is all-time ROR; win rate is a minor supporting metric.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from tradingagents.portfolio.paper_account import PaperPortfolioAccount


@dataclass
class PortfolioMetricsResult:
    """Full metric set for one portfolio."""

    portfolio_id: str
    name: str

    # Cash / equity
    current_equity: float
    initial_cash: float
    cash: float
    settled_cash: float
    unsettled_cash: float
    realized_pnl: float
    unrealized_pnl: float

    # Returns (ROR = primary)
    all_time_ror: float
    daily_ror: float
    weekly_ror: float
    monthly_ror: float

    # Risk-adjusted / quality (secondary)
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float

    # Activity
    total_trades: int
    open_positions: int
    avg_hold_days: float
    day_trades_last_5: int
    compliance_skips: int

    # Minor
    win_rate: float


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _equity_series(account: PaperPortfolioAccount) -> list[tuple[datetime, float]]:
    """Ordered (timestamp, equity) from snapshots. Falls back to a flat line at initial cash."""
    series: list[tuple[datetime, float]] = []
    for snap in account.equity_snapshots:
        ts = _parse_dt(snap.timestamp)
        if ts is not None:
            series.append((ts, float(snap.equity)))
    series.sort(key=lambda x: x[0])
    return series


def max_drawdown(account: PaperPortfolioAccount) -> float:
    """Largest peak-to-trough decline in equity, as a positive percentage."""
    series = _equity_series(account)
    if len(series) < 2:
        return 0.0
    peak = series[0][1]
    max_dd = 0.0
    for _, eq in series:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 4)


def _daily_returns(account: PaperPortfolioAccount) -> list[float]:
    """Per-snapshot fractional returns of the equity curve."""
    series = _equity_series(account)
    rets: list[float] = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        cur = series[i][1]
        if prev > 0:
            rets.append((cur - prev) / prev)
    return rets


def sharpe_ratio(account: PaperPortfolioAccount, periods_per_year: int = 252) -> float:
    """Annualized Sharpe from snapshot returns (risk-free = 0). 0.0 if insufficient data."""
    rets = _daily_returns(account)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return round((mean / std) * math.sqrt(periods_per_year), 4)


def profit_factor(account: PaperPortfolioAccount) -> float:
    """Gross wins / gross losses from closed trades. inf-capped at 999 when no losses."""
    gross_win = sum(t.realized_pnl for t in account.trades if t.exit_date and t.realized_pnl > 0)
    gross_loss = -sum(t.realized_pnl for t in account.trades if t.exit_date and t.realized_pnl < 0)
    if gross_loss == 0:
        return round(gross_win, 4) if gross_win == 0 else 999.0
    return round(gross_win / gross_loss, 4)


def win_rate(account: PaperPortfolioAccount) -> float:
    """Fraction of closed trades that were profitable (0.0–1.0). Minor metric."""
    closed = [t for t in account.trades if t.exit_date is not None]
    if not closed:
        return 0.0
    wins = sum(1 for t in closed if t.realized_pnl > 0)
    return round(wins / len(closed), 4)


def avg_hold_days(account: PaperPortfolioAccount) -> float:
    """Mean holding period (days) across closed trades."""
    holds: list[float] = []
    for t in account.trades:
        entry = _parse_dt(t.entry_date) or _parse_dt(t.entry_timestamp)
        exit_ = _parse_dt(t.exit_date) or _parse_dt(t.exit_timestamp)
        if entry and exit_:
            holds.append((exit_ - entry).total_seconds() / 86400.0)
    if not holds:
        return 0.0
    return round(sum(holds) / len(holds), 2)


def _ror_over_window(account: PaperPortfolioAccount, days: int) -> float:
    """ROR of equity over the trailing `days` window, vs the earliest snapshot in it."""
    series = _equity_series(account)
    if len(series) < 2:
        return 0.0
    now = series[-1][0]
    cutoff = now - timedelta(days=days)
    window = [(ts, eq) for ts, eq in series if ts >= cutoff]
    if len(window) < 2:
        # Not enough points in window; compare current to the earliest available.
        base = series[0][1]
    else:
        base = window[0][1]
    cur = series[-1][1]
    if base <= 0:
        return 0.0
    return round((cur - base) / base * 100.0, 4)


def daily_ror(account: PaperPortfolioAccount) -> float:
    return _ror_over_window(account, 1)


def weekly_ror(account: PaperPortfolioAccount) -> float:
    return _ror_over_window(account, 7)


def monthly_ror(account: PaperPortfolioAccount) -> float:
    return _ror_over_window(account, 30)


def day_trades_last_5(account: PaperPortfolioAccount) -> int:
    """Count round-trip same-day trades in the last 5 business days."""
    from tradingagents.portfolio.paper_compliance import count_day_trades_last_5_business_days
    return count_day_trades_last_5_business_days(account)


def compute_metrics(account: PaperPortfolioAccount) -> PortfolioMetricsResult:
    """Compute the full metric set for one account."""
    return PortfolioMetricsResult(
        portfolio_id=account.portfolio_id,
        name=account.name,
        current_equity=round(account.current_equity(), 2),
        initial_cash=account.initial_cash,
        cash=round(account.cash, 2),
        settled_cash=round(account.settled_cash, 2),
        unsettled_cash=round(account.unsettled_cash, 2),
        realized_pnl=round(account.realized_pnl(), 2),
        unrealized_pnl=round(account.unrealized_pnl(), 2),
        all_time_ror=round(account.all_time_ror(), 4),
        daily_ror=daily_ror(account),
        weekly_ror=weekly_ror(account),
        monthly_ror=monthly_ror(account),
        max_drawdown=max_drawdown(account),
        sharpe_ratio=sharpe_ratio(account),
        profit_factor=profit_factor(account),
        total_trades=account.total_trades(),
        open_positions=account.open_position_count(),
        avg_hold_days=avg_hold_days(account),
        day_trades_last_5=day_trades_last_5(account),
        compliance_skips=len(account.compliance_log),
        win_rate=win_rate(account),
    )


def leaderboard_sort_key(m: PortfolioMetricsResult) -> tuple:
    """Default leaderboard ordering (descending): all-time ROR, then Sharpe, then
    lower drawdown, then profit factor, then equity. Use as reverse=True sort key."""
    return (
        m.all_time_ror,
        m.sharpe_ratio,
        -m.max_drawdown,      # lower drawdown ranks higher
        m.profit_factor,
        m.current_equity,
    )
