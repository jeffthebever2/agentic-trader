"""
QlibDataAdapter — bridges yfinance/pandas OHLCV data to qlib-compatible format.

Qlib expects data in a specific directory structure (qlib_data/) with binary
files per field per instrument.  For research purposes we work with in-memory
pandas DataFrames; this adapter handles the conversion both ways.

Usage::

    adapter = QlibDataAdapter()
    df = adapter.ohlcv_from_yfinance(["AAPL", "MSFT"], start="2020-01-01")
    # df has MultiIndex (datetime, instrument), columns: open close high low volume

    qlib_features = adapter.extract_alpha_features(df, ["AAPL"])
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import pandas as pd


class QlibDataAdapter:
    """Convert between yfinance OHLCV format and qlib-compatible DataFrames."""

    # Required columns in qlib canonical format
    REQUIRED_COLS = ("open", "close", "high", "low", "volume")

    def normalize_ohlcv(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Normalize a yfinance-style DataFrame to lowercase OHLCV columns.

        Accepts:
          - MultiIndex columns (field, ticker) from yf.download(multiple tickers)
          - Flat columns (Open, Close, High, Low, Volume) from single ticker
          - Already-normalized lowercase columns

        Returns DataFrame with lowercase columns: open, close, high, low, volume
        and DatetimeIndex.
        """
        if raw is None or raw.empty:
            return pd.DataFrame(columns=list(self.REQUIRED_COLS))

        df = raw.copy()

        # MultiIndex columns → stack to (date, ticker) MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=1)
            df.index.names = ["datetime", "instrument"]
            df.columns = [c.lower() for c in df.columns]
            return df[list(self.REQUIRED_COLS)].dropna(how="all")

        # Flat columns — map yfinance names to lowercase
        rename = {
            "Open": "open", "Close": "close", "High": "high",
            "Low": "low", "Volume": "volume",
            "Adj Close": "close",
        }
        df = df.rename(columns=rename)
        df.columns = [c.lower() for c in df.columns]
        available = [c for c in self.REQUIRED_COLS if c in df.columns]
        return df[available]

    def ohlcv_from_yfinance(
        self,
        tickers: List[str],
        start: str,
        end: Optional[str] = None,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch and normalize OHLCV data via yfinance.

        Returns DataFrame with MultiIndex (datetime, instrument) or flat
        DatetimeIndex for a single ticker.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance is required: pip install yfinance")

        end_str = end or dt.date.today().strftime("%Y-%m-%d")
        raw = yf.download(
            tickers, start=start, end=end_str,
            interval=interval, auto_adjust=True, progress=False,
        )
        return self.normalize_ohlcv(raw)

    def extract_alpha_features(
        self,
        df: pd.DataFrame,
        tickers: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Compute basic alpha factors from normalized OHLCV data.

        Returns dict: {ticker: DataFrame with columns [ret_1d, ret_5d, vol_20d, rsi_14]}
        """
        out: Dict[str, pd.DataFrame] = {}

        def _compute(prices: pd.Series) -> pd.DataFrame:
            ret1 = prices.pct_change(1).rename("ret_1d")
            ret5 = prices.pct_change(5).rename("ret_5d")
            vol20 = prices.pct_change(1).rolling(20).std().rename("vol_20d")
            delta = prices.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, float("nan"))
            rsi = (100 - 100 / (1 + rs)).rename("rsi_14")
            return pd.concat([ret1, ret5, vol20, rsi], axis=1)

        # MultiIndex (datetime, instrument)
        if isinstance(df.index, pd.MultiIndex) and "close" in df.columns:
            instruments = tickers or df.index.get_level_values(1).unique().tolist()
            for tkr in instruments:
                try:
                    close = df.xs(tkr, level=1)["close"].dropna()
                    if len(close) >= 20:
                        out[tkr] = _compute(close)
                except Exception:
                    pass
        # Flat DatetimeIndex with close column
        elif "close" in df.columns:
            key = tickers[0] if tickers else "ticker"
            close = df["close"].dropna()
            if len(close) >= 20:
                out[key] = _compute(close)

        return out
