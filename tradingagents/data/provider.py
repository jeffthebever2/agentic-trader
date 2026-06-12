"""Abstract Market Data Provider — LOG-1.

All concrete providers (yfinance, Polygon, Fidelity, etc.) implement this
interface so the rest of the codebase can swap sources without code changes.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class OHLCVBar:
    """Single OHLCV bar."""
    date: str          # ISO format YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: int
    ticker: str

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "ticker": self.ticker,
        }


class MarketDataProvider(abc.ABC):
    """Abstract base class for all market data sources.

    Implementations must be thread-safe for concurrent ticker lookups.
    All dates are ISO strings ("YYYY-MM-DD"). All prices are USD floats.
    """

    @abc.abstractmethod
    def get_bars(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> List[OHLCVBar]:
        """Fetch OHLCV bars for one ticker.

        Parameters
        ----------
        ticker : str
            Symbol, e.g. "AAPL".
        start, end : str
            Inclusive date range ("YYYY-MM-DD").
        interval : str
            Bar interval. Providers need only support "1d" (daily).

        Returns
        -------
        List[OHLCVBar]
            Sorted ascending by date. May be empty if no data found.
        """

    @abc.abstractmethod
    def get_latest_price(self, ticker: str) -> Optional[float]:
        """Return the most recent close price for ticker, or None on failure."""

    def get_bars_bulk(
        self,
        tickers: List[str],
        start: str,
        end: str,
        interval: str = "1d",
    ) -> Dict[str, List[OHLCVBar]]:
        """Fetch bars for multiple tickers. Default: sequential loop.

        Override for parallel/batch efficiency.
        """
        result: Dict[str, List[OHLCVBar]] = {}
        for ticker in tickers:
            try:
                result[ticker] = self.get_bars(ticker, start, end, interval)
            except Exception:
                result[ticker] = []
        return result

    @property
    def source_name(self) -> str:
        """Human-readable name for this provider (for logging)."""
        return self.__class__.__name__
