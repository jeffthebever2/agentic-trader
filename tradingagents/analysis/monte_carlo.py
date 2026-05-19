"""Monte Carlo trade probability simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np
import yfinance as yf


class MonteCarloSimulator:
    def estimate_trade_probability(
        self,
        ticker: str,
        entry: float,
        stop: float,
        target: float,
        days_ahead: int = 5,
        simulations: int = 10000,
        seed: int = 42,
    ) -> Dict:
        returns = yf.download(ticker, period="3mo", progress=False, auto_adjust=True)["Close"].pct_change().dropna()
        volatility = float(returns.std() * (252 ** 0.5)) if len(returns) else 0.20
        rng = np.random.default_rng(seed)
        daily_vol = volatility / (252 ** 0.5)
        terminal = []
        for _ in range(simulations):
            price = entry
            for _day in range(days_ahead):
                price *= 1 + rng.normal(0, daily_vol)
                if price <= stop or price >= target:
                    break
            terminal.append(price)
        arr = np.array(terminal)
        wins = arr >= target
        losses = arr <= stop
        return {
            "probability_of_win": float(wins.mean()),
            "probability_of_loss": float(losses.mean()),
            "probability_of_breakeven": float(1 - wins.mean() - losses.mean()),
            "median_price": float(np.median(arr)),
            "percentile_10": float(np.percentile(arr, 10)),
            "percentile_90": float(np.percentile(arr, 90)),
        }
