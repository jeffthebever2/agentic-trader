"""Backtest stored paper decisions against historical prices."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import yfinance as yf

from tradingagents.agents.utils.memory import TradingMemoryLog


class BacktestEngine:
    def __init__(self, memory_log: TradingMemoryLog | None = None):
        self.memory_log = memory_log or TradingMemoryLog()

    def backtest_strategy(self, tickers: List[str], start_date: str = None, end_date: str = None) -> Dict:
        decisions = self.memory_log._read_json_db(self.memory_log._decisions_path)
        results = {"trades": [], "total_pnl": 0.0, "win_count": 0, "loss_count": 0}
        for ticker in tickers:
            for dec in decisions.get(ticker.upper(), []):
                if dec.get("recommendation", "").lower() not in ("buy", "overweight"):
                    continue
                entry_date = datetime.fromisoformat(dec["analysis_date"])
                price_data = yf.download(
                    ticker,
                    start=entry_date.strftime("%Y-%m-%d"),
                    end=(entry_date + timedelta(days=30)).strftime("%Y-%m-%d"),
                    progress=False,
                    auto_adjust=True,
                )["Close"].dropna()
                if len(price_data) < 2:
                    continue
                entry_price = float(price_data.iloc[0])
                exit_price = float(price_data.iloc[-1])
                pnl = (exit_price - entry_price) / entry_price * 100
                trade = {
                    "ticker": ticker,
                    "entry_date": entry_date.isoformat(),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "is_win": pnl > 0,
                }
                results["trades"].append(trade)
                results["total_pnl"] += pnl
                results["win_count" if pnl > 0 else "loss_count"] += 1

        pnls = [t["pnl"] for t in results["trades"]]
        if pnls:
            results["win_rate"] = results["win_count"] / len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            results["avg_win"] = sum(wins) / max(len(wins), 1)
            results["avg_loss"] = sum(losses) / max(len(losses), 1)
            gross_loss = abs(sum(losses))
            results["profit_factor"] = sum(wins) / gross_loss if gross_loss else 0.0
            results["sharpe_ratio"] = float(np.mean(pnls) / np.std(pnls)) if np.std(pnls) > 0 else 0.0
            results["max_drawdown"] = float(min(0, min(np.cumsum(pnls))))
        return results
