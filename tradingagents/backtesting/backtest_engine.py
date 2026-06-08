"""Backtest stored paper decisions against historical prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import yfinance as yf

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.backtesting.tearsheet import compute_tearsheet


@dataclass
class FillModel:
    """Realistic fill cost model applied to every entry and exit.

    Defaults approximate a retail broker (e.g., Alpaca):
      - 5 bps round-trip commission
      - 2 bps market impact / slippage per side
      - 2.5 bps half-spread cost per side
    """
    commission_pct: float = 0.0005    # 5 bps per side
    slippage_pct: float = 0.0002      # 2 bps market impact
    spread_half_bps: float = 2.5      # half-spread, in bps

    def fill_price(self, signal_price: float, direction: int) -> float:
        """Return realistic fill price. direction: +1=buy, -1=sell."""
        cost = signal_price * (self.slippage_pct + self.spread_half_bps / 10_000)
        return signal_price + direction * cost

    def commission(self, notional: float) -> float:
        return abs(notional) * self.commission_pct


class BacktestEngine:
    def __init__(
        self,
        memory_log: TradingMemoryLog | None = None,
        fill_model: FillModel | None = None,
    ):
        self.memory_log = memory_log or TradingMemoryLog()
        self.fill_model = fill_model or FillModel()

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
                raw_entry = float(price_data.iloc[0])
                raw_exit = float(price_data.iloc[-1])
                entry_price = self.fill_model.fill_price(raw_entry, +1)
                exit_price = self.fill_model.fill_price(raw_exit, -1)
                commission_total = (
                    self.fill_model.commission(entry_price)
                    + self.fill_model.commission(exit_price)
                )
                pnl = (exit_price - entry_price - commission_total) / entry_price * 100
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

        pnls_pct = [t["pnl"] / 100.0 for t in results["trades"]]
        if pnls_pct:
            ts = compute_tearsheet(pnls_pct)
            results.update({
                "win_rate": ts["win_rate"],
                "avg_win": ts["avg_win_pct"],
                "avg_loss": ts["avg_loss_pct"],
                "profit_factor": ts["profit_factor"],
                "expectancy_pct": ts["expectancy_pct"],
                "sqn": ts["sqn"],
                "sharpe_ratio": ts["sharpe"],
                "sortino_ratio": ts["sortino"],
                "calmar_ratio": ts["calmar"],
                "cagr_pct": ts["cagr_pct"],
                "max_drawdown": ts["max_drawdown_pct"],
                "kelly_criterion": ts["kelly_criterion"],
                "total_pnl": sum(t["pnl"] for t in results["trades"]),
            })
        return results
