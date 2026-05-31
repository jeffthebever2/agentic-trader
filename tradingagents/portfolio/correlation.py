"""Portfolio correlation checks to reduce hidden concentration risk."""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd
import yfinance as yf


class CorrelationAnalyzer:
    def __init__(self, threshold: float = 0.70, max_high_corr: int = 2):
        self.threshold = threshold
        self.max_high_corr = max_high_corr

    def get_correlation_matrix(self, tickers: List[str], period: str = "1y") -> pd.DataFrame:
        data = yf.download(tickers, period=period, progress=False, auto_adjust=False)
        if isinstance(data.columns, pd.MultiIndex):
            if "Adj Close" in data.columns.get_level_values(0):
                prices = data["Adj Close"]
            else:
                prices = data["Close"]
        else:
            prices = data[["Adj Close"]] if "Adj Close" in data else data[["Close"]]
            prices.columns = tickers[:1]
        return prices.pct_change(fill_method=None).dropna(how="all").corr()

    def check_concentration_risk(self, portfolio, new_ticker: str) -> Tuple[bool, str]:
        current_tickers = list(portfolio.positions.keys())
        if len(current_tickers) < 2:
            return True, "Not enough positions for correlation risk"

        new_ticker = new_ticker.upper()
        all_tickers = current_tickers + [new_ticker]
        try:
            corr_matrix = self.get_correlation_matrix(all_tickers)
            if new_ticker not in corr_matrix:
                return True, "Correlation data unavailable"
            new_corr = corr_matrix[new_ticker].drop(labels=[new_ticker], errors="ignore")
            high_corr = new_corr[new_corr > self.threshold]
            # Cycle 44 SR-10: block when the new name would be the (max_high_corr)-th
            # correlated position, not the (max_high_corr+1)-th. `>` previously allowed
            # a 3-way correlated cluster when max_high_corr=2.
            if len(high_corr) >= self.max_high_corr:
                names = ", ".join(high_corr.index.astype(str))
                return False, (
                    f"{new_ticker} is highly correlated with {len(high_corr)} "
                    f"positions ({names})"
                )
            return True, "OK to buy"
        except Exception as exc:
            return True, f"Correlation check unavailable: {exc}"
