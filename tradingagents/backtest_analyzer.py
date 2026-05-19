"""Enhanced backtesting utilities with risk metrics."""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
import logging

from tradingagents.logging_config import get_logger

logger = get_logger(__name__)


class BacktestAnalyzer:
    """Enhanced backtesting with comprehensive risk and performance metrics."""

    def __init__(self, returns: pd.Series, benchmark_returns: Optional[pd.Series] = None):
        """Initialize with returns series.

        Args:
            returns: Series of strategy returns (daily)
            benchmark_returns: Optional benchmark returns (e.g., SPY)
        """
        self.returns = returns
        self.benchmark_returns = benchmark_returns
        self._validate_data()

    def _validate_data(self) -> None:
        """Validate input data."""
        if len(self.returns) == 0:
            raise ValueError("Returns series cannot be empty")

        if self.returns.isnull().any():
            logger.warning("Found NaN values in returns, filling with 0")
            self.returns = self.returns.fillna(0)

    def calculate_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Sharpe ratio (annualized).

        Args:
            risk_free_rate: Annual risk-free rate (default 2%)

        Returns:
            Annualized Sharpe ratio
        """
        daily_rf = risk_free_rate / 252  # Daily risk-free rate
        excess_returns = self.returns - daily_rf

        if excess_returns.std() == 0:
            return 0.0

        return np.sqrt(252) * excess_returns.mean() / excess_returns.std()

    def calculate_sortino_ratio(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Sortino ratio (downside deviation only).

        Args:
            risk_free_rate: Annual risk-free rate

        Returns:
            Annualized Sortino ratio
        """
        daily_rf = risk_free_rate / 252
        excess_returns = self.returns - daily_rf

        # Downside deviation (only negative returns)
        downside_returns = excess_returns[excess_returns < 0]
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0

        return np.sqrt(252) * excess_returns.mean() / downside_returns.std()

    def calculate_max_drawdown(self) -> Tuple[float, pd.Timestamp, pd.Timestamp]:
        """Calculate maximum drawdown.

        Returns:
            Tuple of (max_drawdown, peak_date, trough_date)
        """
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        max_dd = drawdown.min()
        trough_idx = drawdown.idxmin()
        peak_idx = running_max.loc[:trough_idx].idxmax()

        return abs(max_dd), peak_idx, trough_idx

    def calculate_calmar_ratio(self, risk_free_rate: float = 0.02) -> float:
        """Calculate Calmar ratio (return / max drawdown).

        Args:
            risk_free_rate: Annual risk-free rate

        Returns:
            Calmar ratio
        """
        if len(self.returns) < 252:  # Need at least a year
            return 0.0

        # Annualized return
        total_return = (1 + self.returns).prod()
        years = len(self.returns) / 252
        annualized_return = total_return ** (1 / years) - 1

        max_dd, _, _ = self.calculate_max_drawdown()

        if max_dd == 0:
            return 0.0

        return (annualized_return - risk_free_rate) / max_dd

    def calculate_alpha_beta(self) -> Tuple[float, float]:
        """Calculate alpha and beta vs benchmark.

        Returns:
            Tuple of (alpha, beta) if benchmark available, else (0, 1)
        """
        if self.benchmark_returns is None or len(self.benchmark_returns) != len(self.returns):
            return 0.0, 1.0

        # Align the series
        common_index = self.returns.index.intersection(self.benchmark_returns.index)
        if len(common_index) < 30:  # Need minimum data
            return 0.0, 1.0

        strategy = self.returns.loc[common_index]
        benchmark = self.benchmark_returns.loc[common_index]

        # Calculate beta (covariance / variance)
        covariance = np.cov(strategy, benchmark)[0, 1]
        benchmark_var = np.var(benchmark)

        if benchmark_var == 0:
            return 0.0, 1.0

        beta = covariance / benchmark_var

        # Calculate alpha (excess return not explained by beta)
        strategy_mean = strategy.mean()
        benchmark_mean = benchmark.mean()
        alpha = strategy_mean - beta * benchmark_mean

        # Annualize
        alpha *= 252

        return alpha, beta

    def calculate_win_rate(self, threshold: float = 0.0) -> float:
        """Calculate win rate (percentage of positive returns).

        Args:
            threshold: Minimum return to count as a win

        Returns:
            Win rate as decimal (0.0 to 1.0)
        """
        wins = (self.returns > threshold).sum()
        total = len(self.returns)
        return wins / total if total > 0 else 0.0

    def calculate_profit_factor(self) -> float:
        """Calculate profit factor (gross profit / gross loss).

        Returns:
            Profit factor (>1 means profitable)
        """
        gross_profit = self.returns[self.returns > 0].sum()
        gross_loss = abs(self.returns[self.returns < 0].sum())

        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0

        return gross_profit / gross_loss

    def get_comprehensive_metrics(self, risk_free_rate: float = 0.02) -> Dict[str, float]:
        """Get all risk and performance metrics.

        Args:
            risk_free_rate: Annual risk-free rate

        Returns:
            Dictionary of all calculated metrics
        """
        # Basic metrics
        total_return = (1 + self.returns).prod() - 1
        annualized_return = (1 + total_return) ** (252 / len(self.returns)) - 1
        volatility = self.returns.std() * np.sqrt(252)
        win_rate = self.calculate_win_rate()

        # Risk metrics
        sharpe = self.calculate_sharpe_ratio(risk_free_rate)
        sortino = self.calculate_sortino_ratio(risk_free_rate)
        max_dd, peak_date, trough_date = self.calculate_max_drawdown()
        calmar = self.calculate_calmar_ratio(risk_free_rate)

        # Benchmark metrics
        alpha, beta = self.calculate_alpha_beta()

        # Additional metrics
        profit_factor = self.calculate_profit_factor()
        avg_win = self.returns[self.returns > 0].mean()
        avg_loss = self.returns[self.returns < 0].mean()

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "calmar_ratio": calmar,
            "alpha": alpha,
            "beta": beta,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_win": avg_win if not np.isnan(avg_win) else 0.0,
            "avg_loss": avg_loss if not np.isnan(avg_loss) else 0.0,
            "total_trades": len(self.returns),
            "peak_date": str(peak_date) if peak_date else None,
            "trough_date": str(trough_date) if trough_date else None,
        }


def analyze_strategy_performance(
    strategy_returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    risk_free_rate: float = 0.02
) -> Dict[str, float]:
    """Convenience function to analyze strategy performance.

    Args:
        strategy_returns: Daily returns series
        benchmark_returns: Optional benchmark returns
        risk_free_rate: Annual risk-free rate

    Returns:
        Dictionary of performance metrics
    """
    analyzer = BacktestAnalyzer(strategy_returns, benchmark_returns)
    return analyzer.get_comprehensive_metrics(risk_free_rate)