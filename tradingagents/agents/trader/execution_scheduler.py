"""Paper execution scheduling helpers."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import yfinance as yf


class ExecutionScheduler:
    def get_optimal_entry_time(self, ticker: str, trade_type: str) -> str:
        try:
            intraday = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
            if intraday.empty:
                return "MONITOR (intraday data unavailable)"
            current_price = float(intraday["Close"].iloc[-1])
            vwap = calculate_vwap(intraday)
        except Exception as exc:
            return f"MONITOR (execution data unavailable: {exc})"

        trade_type = trade_type.upper()
        if trade_type == "BUY":
            return "EXECUTE_NOW (price below VWAP)" if current_price < vwap * 0.995 else "WAIT_FOR_DIP (price above VWAP)"
        if trade_type == "SELL":
            return "EXECUTE_NOW (price above VWAP)" if current_price > vwap * 1.005 else "WAIT_FOR_RALLY (price below VWAP)"
        return "MONITOR"

    def get_execution_time_window(self) -> str:
        hour = datetime.now().hour
        if 9 <= hour < 10:
            return "EXCELLENT (morning open)"
        if 15 <= hour < 16:
            return "EXCELLENT (market close)"
        if 10 <= hour < 15:
            return "ACCEPTABLE (mid-day)"
        return "AVOID (pre/post-market)"


def calculate_vwap(data: pd.DataFrame) -> float:
    typical = (data["High"] + data["Low"] + data["Close"]) / 3
    volume = data["Volume"].replace(0, pd.NA).ffill().fillna(1)
    return float((typical * volume).sum() / volume.sum())
