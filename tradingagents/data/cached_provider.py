"""Cache-backed Market Data Provider — LOG-1.

Wraps any MarketDataProvider with an OHLCVCache layer:
  - On get_bars: serve from cache if present, else fetch + store.
  - On get_latest_price: always delegates to upstream (price must be fresh).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .ohlcv_cache import OHLCVCache
from .provider import MarketDataProvider, OHLCVBar


class CachedProvider(MarketDataProvider):
    """Transparent caching wrapper around any MarketDataProvider.

    Parameters
    ----------
    upstream : MarketDataProvider
        The real data source to call on cache miss.
    cache : OHLCVCache or None
        Cache to use. If None, creates a default "ohlcv_cache.db" in cwd.
    force_refresh : bool
        If True, always fetch from upstream and overwrite cache.
    """

    def __init__(
        self,
        upstream: MarketDataProvider,
        cache: Optional[OHLCVCache] = None,
        force_refresh: bool = False,
    ):
        self._upstream = upstream
        self._cache = cache or OHLCVCache()
        self._force_refresh = force_refresh

    def get_bars(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> List[OHLCVBar]:
        if not self._force_refresh:
            cached = self._cache.get(ticker, start, end)
            if cached:
                return cached

        bars = self._upstream.get_bars(ticker, start, end, interval)
        if bars:
            self._cache.put(bars)
        return bars

    def get_latest_price(self, ticker: str) -> Optional[float]:
        return self._upstream.get_latest_price(ticker)

    def get_bars_bulk(
        self,
        tickers: List[str],
        start: str,
        end: str,
        interval: str = "1d",
    ) -> Dict[str, List[OHLCVBar]]:
        result: Dict[str, List[OHLCVBar]] = {}
        miss: List[str] = []

        if not self._force_refresh:
            for ticker in tickers:
                cached = self._cache.get(ticker, start, end)
                if cached:
                    result[ticker] = cached
                else:
                    miss.append(ticker)
        else:
            miss = list(tickers)

        if miss:
            fetched = self._upstream.get_bars_bulk(miss, start, end, interval)
            for ticker, bars in fetched.items():
                result[ticker] = bars
                if bars:
                    self._cache.put(bars)

        return result

    @property
    def source_name(self) -> str:
        return f"CachedProvider({self._upstream.source_name})"
