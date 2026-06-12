"""YFinance implementation of MarketDataProvider — LOG-1."""
from __future__ import annotations

from typing import List, Optional

from .provider import MarketDataProvider, OHLCVBar


class YFinanceProvider(MarketDataProvider):
    """Fetch OHLCV data via yfinance.

    Requires: pip install yfinance
    Not suitable for high-frequency production use; use for backfill/training.
    """

    def get_bars(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> List[OHLCVBar]:
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:
            raise ImportError("yfinance not installed: pip install yfinance") from exc

        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        if df is None or df.empty:
            return []

        # yfinance returns MultiIndex columns when downloading single ticker with group_by
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.droplevel(1)

        bars: List[OHLCVBar] = []
        for idx, row in df.iterrows():
            date_str = str(idx)[:10]  # "YYYY-MM-DD"
            try:
                bars.append(OHLCVBar(
                    date=date_str,
                    open=float(row.get("Open", row.get("open", 0))),
                    high=float(row.get("High", row.get("high", 0))),
                    low=float(row.get("Low", row.get("low", 0))),
                    close=float(row.get("Close", row.get("close", 0))),
                    volume=int(row.get("Volume", row.get("volume", 0))),
                    ticker=ticker,
                ))
            except (TypeError, ValueError):
                continue

        return sorted(bars, key=lambda b: b.date)

    def get_latest_price(self, ticker: str) -> Optional[float]:
        try:
            import yfinance as yf  # type: ignore
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            return float(price) if price is not None else None
        except Exception:
            return None

    @property
    def source_name(self) -> str:
        return "yfinance"
