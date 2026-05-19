import json
from datetime import datetime

import pytest

from tradingagents.portfolio import DrawdownMonitor, PortfolioState, PositionSizer


def _config(tmp_path):
    return {
        "portfolio_state_path": str(tmp_path / "positions.json"),
        "trade_log_path": str(tmp_path / "trades.jsonl"),
        "paper_decision_log_path": str(tmp_path / "paper.jsonl"),
        "starting_cash": 100000,
        "max_positions": 2,
        "max_position_size": 0.05,
        "max_sector_exposure": 0.30,
        "max_daily_loss": -0.05,
        "max_monthly_loss": -0.15,
    }


@pytest.mark.unit
def test_portfolio_buy_sell_and_duplicate_block(tmp_path):
    prices = {"AAPL": 100.0, "MSFT": 200.0}
    sectors = {"AAPL": "Technology", "MSFT": "Technology"}
    portfolio = PortfolioState(
        _config(tmp_path),
        price_lookup=lambda ticker: prices[ticker],
        sector_lookup=lambda ticker: sectors[ticker],
    )

    ok, reason = portfolio.can_buy("AAPL", 10)
    assert ok, reason

    portfolio.execute_buy("AAPL", 10, 100.0, "test thesis")
    assert portfolio.cash == 99000
    assert "AAPL" in portfolio.positions

    ok, reason = portfolio.can_buy("AAPL", 1)
    assert not ok
    assert "Already long" in reason

    portfolio.execute_sell("AAPL", "TEST_EXIT")
    assert "AAPL" not in portfolio.positions
    assert portfolio.cash == 100000
    assert (tmp_path / "trades.jsonl").exists()


@pytest.mark.unit
def test_position_size_and_sector_limits(tmp_path):
    portfolio = PortfolioState(
        _config(tmp_path),
        price_lookup=lambda ticker: 100.0,
        sector_lookup=lambda ticker: "Technology",
    )
    ok, reason = portfolio.can_buy("NVDA", 100)
    assert not ok
    assert "max" in reason


@pytest.mark.unit
def test_drawdown_monitor_blocks_after_daily_loss(tmp_path):
    cfg = _config(tmp_path)
    trade = {
        "ticker": "AAPL",
        "exit_date": datetime.now().isoformat(),
        "pnl_pct": -0.06,
    }
    with open(cfg["trade_log_path"], "w", encoding="utf-8") as f:
        f.write(json.dumps(trade) + "\n")

    ok, reason = DrawdownMonitor(cfg).should_keep_trading()
    assert not ok
    assert "STOP TRADING" in reason


@pytest.mark.unit
def test_drawdown_monitor_accepts_exit_time_and_skips_bad_rows(tmp_path):
    cfg = _config(tmp_path)
    trade = {
        "ticker": "AAPL",
        "exit_time": datetime.now().isoformat(),
        "pnl_pct": -0.06,
    }
    with open(cfg["trade_log_path"], "w", encoding="utf-8") as f:
        f.write("not-json\n")
        f.write(json.dumps({"ticker": "MSFT", "pnl_pct": -0.99}) + "\n")
        f.write(json.dumps(trade) + "\n")

    ok, reason = DrawdownMonitor(cfg).should_keep_trading()
    assert not ok
    assert "STOP TRADING" in reason


@pytest.mark.unit
def test_position_sizer_rejects_bad_risk_reward():
    shares, reason = PositionSizer().calculate_position_size(
        "AAPL",
        {"confidence": 0.8, "entry_target": 100, "stop_loss": 95, "take_profit": 102},
        portfolio_value=100000,
    )
    assert shares == 0
    assert "Risk/reward" in reason
