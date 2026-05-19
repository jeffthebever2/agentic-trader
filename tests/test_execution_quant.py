import pandas as pd
import pytest

from tradingagents.agents.trader.execution_scheduler import calculate_vwap
from tradingagents.analysis.monte_carlo import MonteCarloSimulator


@pytest.mark.unit
def test_calculate_vwap():
    data = pd.DataFrame({
        "High": [11, 12],
        "Low": [9, 10],
        "Close": [10, 11],
        "Volume": [100, 200],
    })
    assert round(calculate_vwap(data), 2) == 10.67


@pytest.mark.unit
def test_monte_carlo_deterministic(monkeypatch):
    prices = pd.DataFrame({"Close": [100, 101, 99, 102, 100, 103]})
    monkeypatch.setattr("tradingagents.analysis.monte_carlo.yf.download", lambda *a, **k: prices)

    result = MonteCarloSimulator().estimate_trade_probability(
        "AAPL", entry=100, stop=95, target=110, simulations=1000, seed=7
    )

    assert 0 <= result["probability_of_win"] <= 1
    assert 0 <= result["probability_of_loss"] <= 1
    assert result["median_price"] > 0
