import asyncio
import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts.paper_trade_today import (
    Candidate,
    PaperAccount,
    STRATEGY_LABELS,
    create_strategy_accounts,
    scan_account_once,
)
from web.api import paper


def _write_state(path, *, cash=10000.0, starting_cash=10000.0, realized_pnl=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "starting_cash": starting_cash,
                "cash": cash,
                "realized_pnl": realized_pnl,
                "positions": {},
                "trades": [{"ticker": "AAPL", "pnl": realized_pnl}] if realized_pnl else [],
            }
        ),
        encoding="utf-8",
    )


def _runtime_args(**overrides):
    values = {
        "bear_regime_size_factor": 0.5,
        "neutral_regime_size_factor": 0.75,
        "daily_loss_limit_pct": 0.0,
        "partial_profit_pct": 0.5,
        "partial_profit_fraction": 0.5,
        "trailing_stop_atr_mult": 0.0,
        "time_decay_scans": 0,
        "defensive_trim_buffer_pct": 35.0,
        "defensive_trim_fraction": 0.5,
        "early_stop_buffer_pct": 15.0,
        "long_hold_days": 20,
        "max_portfolio_drawdown": 0.0,
        "breadth_threshold": 0.4,
        "sector_max_positions": 0,
        "min_risk_reward": 0.0,
        "scale_in_min_probability": 0.55,
        "scale_in_trigger_atr": 0.5,
        "scale_in_add_pct": 5.0,
        "position_cap_pct": 20.0,
        "position_cap_min_pct": 5.0,
        "position_high_confidence_threshold": 0.8,
        "risk_per_trade_pct": 0.0,
        "ml_probability_threshold": 0.58,
        "commission": 0.0,
        "max_positions": 5,
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
        "max_entry_extension_atr": 0.7,
        "hold_overnight": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _candidate(ticker="TEST", *, entry=100.0, target=110.0, stop=90.0, ml_probability=0.6):
    return Candidate(
        ticker=ticker,
        signal_date="2026-05-12",
        score=100.0,
        entry=entry,
        target=target,
        stop=stop,
        signal_close=entry,
        atr=5.0,
        ml_probability=ml_probability,
        expected_return=0.02,
        large_loss_probability=0.1,
        rule_pass=True,
        ml_pass=True,
        gate_status="pass",
    )


def _paper_account(tmp_path):
    return PaperAccount(
        tmp_path / "algorithm" / "state.json",
        tmp_path / "algorithm" / "events.jsonl",
        starting_cash=10000.0,
        commission=0.0,
        reset=True,
        strategy="algorithm",
    )


@pytest.mark.unit
def test_create_strategy_accounts_carries_forward_previous_day(tmp_path):
    previous = tmp_path / "20260508"
    today = tmp_path / "20260511"
    for index, strategy in enumerate(STRATEGY_LABELS):
        _write_state(
            previous / strategy / "state.json",
            cash=12345.67 + index,
            realized_pnl=2345.67 + index,
        )

    accounts = create_strategy_accounts(today, starting_cash=10000.0, commission=0.0, reset=False)

    for index, strategy in enumerate(STRATEGY_LABELS):
        assert accounts[strategy].cash == pytest.approx(12345.67 + index)
        assert accounts[strategy].realized_pnl == pytest.approx(2345.67 + index)
        assert len(accounts[strategy].trades) == 1
        saved = json.loads((today / strategy / "state.json").read_text(encoding="utf-8"))
        assert saved["cash"] == pytest.approx(12345.67 + index)


@pytest.mark.unit
def test_create_strategy_accounts_reset_ignores_previous_day(tmp_path):
    previous = tmp_path / "20260508"
    today = tmp_path / "20260511"
    for strategy in STRATEGY_LABELS:
        _write_state(previous / strategy / "state.json", cash=12345.67, realized_pnl=2345.67)

    accounts = create_strategy_accounts(today, starting_cash=5000.0, commission=0.0, reset=True)

    for strategy in STRATEGY_LABELS:
        assert accounts[strategy].cash == pytest.approx(5000.0)
        assert accounts[strategy].realized_pnl == 0.0
        assert accounts[strategy].trades == []


@pytest.mark.unit
def test_latest_data_dir_ignores_empty_today_folder(tmp_path, monkeypatch):
    previous = tmp_path / "20260508"
    empty_today = tmp_path / "20260510"
    empty_today.mkdir(parents=True)
    _write_state(previous / "algorithm" / "state.json")

    monkeypatch.setattr(paper, "_last_output_base", tmp_path)
    monkeypatch.setattr(paper, "_ny_today", lambda: __import__("datetime").date(2026, 5, 10))

    assert paper._latest_data_dir() == previous


@pytest.mark.unit
def test_paper_status_uses_fallback_for_missing_strategy_state(tmp_path, monkeypatch):
    data_dir = tmp_path / "20260508"
    for strategy in ("algorithm", "machine_learning", "combined"):
        _write_state(data_dir / strategy / "state.json", cash=10000.0)

    monkeypatch.setattr(paper, "_last_output_base", tmp_path)
    monkeypatch.setattr(paper, "_last_log_path", None)
    monkeypatch.setattr(paper, "_ny_today", lambda: __import__("datetime").date(2026, 5, 10))

    status = asyncio.run(paper.paper_status())
    summaries = {account["strategy"]: account["summary"] for account in status["accounts"]}

    assert summaries["pure_ai"]["cash"] == pytest.approx(10000.0)
    assert summaries["pure_ai"]["total_value"] == pytest.approx(10000.0)
    assert summaries["pure_ai"]["not_started"] is True


@pytest.mark.unit
def test_paper_start_refuses_weekend_without_creating_empty_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "DEFAULT_OUTPUT_BASE", tmp_path)
    monkeypatch.setattr(paper, "_last_output_base", tmp_path)
    monkeypatch.setattr(paper, "_ny_today", lambda: __import__("datetime").date(2026, 5, 10))
    monkeypatch.setattr(paper, "_paper_runner_processes", lambda: [])

    result = asyncio.run(paper.start_paper_runner(paper.PaperStartRequest()))

    assert result["success"] is False
    assert "not a regular weekday" in result["error"]
    assert not (tmp_path / "20260510").exists()


@pytest.mark.unit
def test_autostart_defaults_to_disabled_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "AUTOSTART_CONFIG_PATH", tmp_path / "paper_autostart.json")

    config = asyncio.run(paper.get_autostart())

    assert config["enabled"] is False
    assert config["tickers"] == "all_tickers.txt"
    assert config["scan_interval_minutes"] == 15


@pytest.mark.unit
def test_autostart_save_merges_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "paper_autostart.json"
    monkeypatch.setattr(paper, "AUTOSTART_CONFIG_PATH", config_path)

    result = asyncio.run(paper.set_autostart({"enabled": True, "max_tickers": 25}))

    assert result["config"]["enabled"] is True
    assert result["config"]["max_tickers"] == 25
    assert result["config"]["starting_cash"] == 10000
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["include_pure_ai"] is True


@pytest.mark.unit
def test_paper_api_and_web_defaults_stay_strict():
    req = paper.PaperStartRequest()
    assert req.position_cap_pct == pytest.approx(25.0)
    assert req.position_cap_min_pct == pytest.approx(10.0)
    assert req.min_risk_reward == pytest.approx(1.3)
    assert req.ml_probability_threshold == pytest.approx(0.72)
    assert req.ml_large_loss_max == pytest.approx(0.20)
    assert req.ml_expected_return_min == pytest.approx(0.0)
    assert req.target_mult == pytest.approx(1.5)
    assert req.stop_mult == pytest.approx(1.0)

    for key, expected in {
        "position_cap_pct": 25.0,
        "position_cap_min_pct": 10.0,
        "min_risk_reward": 1.3,
        "ml_probability_threshold": 0.72,
        "ml_large_loss_max": 0.20,
        "ml_expected_return_min": 0.0,
        "target_mult": 1.5,
        "stop_mult": 1.0,
    }.items():
        assert paper.DEFAULT_AUTOSTART_CONFIG[key] == pytest.approx(expected)

    html = (paper.ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert "paperNumber('paper-position-cap', 25)" in html
    assert "paperNumber('paper-position-cap-min', 10)" in html
    assert "paperNumber('paper-min-rr', 1.3)" in html
    assert "paperNumber('paper-ml-prob-threshold', 0.72)" in html
    assert "paperNumber('paper-ml-loss-max', 0.20)" in html
    assert "paperNumber('paper-ml-ret-min', 0.0)" in html
    assert "paperNumber('paper-target-mult', 1.5)" in html
    assert "paperNumber('paper-stop-mult', 1.0)" in html


@pytest.mark.unit
def test_target_exit_takes_priority_over_partial_profit(tmp_path):
    now = dt.datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    account = _paper_account(tmp_path)
    account.buy(_candidate(), price=100.0, shares=10, now=now)

    result = scan_account_once(
        account,
        "algorithm",
        [],
        {"TEST": 111.0},
        _runtime_args(partial_profit_pct=0.5),
        now,
        now.replace(hour=16, minute=0),
        market_breadth=0.8,
        spy_regime="bull",
    )
    account.close()

    assert result["sold"] == 1
    assert account.positions == {}
    assert account.trades[-1]["exit_reason"] == "TARGET"
    assert account.trades[-1].get("partial") is None


@pytest.mark.unit
def test_early_stop_exit_takes_priority_over_defensive_trim(tmp_path):
    now = dt.datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    account = _paper_account(tmp_path)
    account.buy(_candidate(stop=90.0, target=110.0), price=100.0, shares=10, now=now)

    result = scan_account_once(
        account,
        "algorithm",
        [],
        {"TEST": 91.0},
        _runtime_args(defensive_trim_buffer_pct=35.0, early_stop_buffer_pct=15.0),
        now,
        now.replace(hour=16, minute=0),
        market_breadth=0.8,
        spy_regime="bull",
    )
    account.close()

    assert result["sold"] == 1
    assert account.positions == {}
    assert account.trades[-1]["exit_reason"] == "EARLY_STOP_EXIT"
    assert account.trades[-1].get("partial") is None


@pytest.mark.unit
def test_scale_in_requires_real_ml_probability(tmp_path):
    now = dt.datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    account = _paper_account(tmp_path)
    account.buy(_candidate(ml_probability=0.6), price=100.0, shares=10, now=now)

    scan_account_once(
        account,
        "algorithm",
        [_candidate(entry=100.0, target=120.0, stop=95.0, ml_probability=None)],
        {"TEST": 103.0},
        _runtime_args(),
        now,
        now.replace(hour=16, minute=0),
        market_breadth=0.8,
        spy_regime="bull",
    )
    account.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "algorithm" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert account.positions["TEST"].shares == 10
    assert not any(event.get("type") == "SCALE_IN" for event in events)


@pytest.mark.unit
def test_hold_overnight_keeps_position_near_close(tmp_path):
    now = dt.datetime(2026, 5, 13, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    account = _paper_account(tmp_path)
    account.buy(_candidate(), price=100.0, shares=10, now=now.replace(hour=12, minute=0))

    result = scan_account_once(
        account,
        "algorithm",
        [],
        {"TEST": 101.0},
        _runtime_args(hold_overnight=True),
        now,
        now.replace(hour=16, minute=0),
        market_breadth=0.8,
        spy_regime="bull",
    )
    account.close()

    assert result["sold"] == 0
    assert "TEST" in account.positions
    assert account.trades == []


@pytest.mark.unit
def test_no_hold_overnight_restores_eod_flatten(tmp_path):
    now = dt.datetime(2026, 5, 13, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    account = _paper_account(tmp_path)
    account.buy(_candidate(), price=100.0, shares=10, now=now.replace(hour=12, minute=0))

    result = scan_account_once(
        account,
        "algorithm",
        [],
        {"TEST": 101.0},
        _runtime_args(hold_overnight=False),
        now,
        now.replace(hour=16, minute=0),
        market_breadth=0.8,
        spy_regime="bull",
    )
    account.close()

    assert result["sold"] == 1
    assert account.positions == {}
    assert account.trades[-1]["exit_reason"] == "EOD_FLATTEN"
