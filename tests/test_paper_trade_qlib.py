import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import paper_trade_qlib
from tradingagents.portfolio.prediction_grader import PredictionGrader


def _price_frame(start: float, end: float, periods: int = 320) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=periods)
    close = pd.Series(np.linspace(start, end, periods), index=index)
    return pd.DataFrame(
        {
            "Close": close,
            "High": close * 1.01,
            "Low": close * 0.99,
        }
    )


@pytest.mark.unit
def test_compute_latest_signals_uses_qlib_features_and_ranks_cross_section():
    cache = {
        "AAPL": _price_frame(80.0, 160.0),
        "MSFT": _price_frame(120.0, 100.0),
    }

    signals = paper_trade_qlib.compute_latest_signals(cache)

    assert signals
    assert signals[0].ticker == "AAPL"
    assert signals[0].score > signals[-1].score
    assert "qlib_mom_63" in signals[0].features
    assert signals[0].as_of == "2025-03-24"


@pytest.mark.unit
def test_run_paper_cycle_writes_isolated_paper_state(tmp_path):
    signal = paper_trade_qlib.QlibPaperSignal(
        ticker="AAPL",
        score=95.0,
        price=100.0,
        as_of="2026-06-05",
        features={"qlib_mom_63": 0.2},
        thesis="unit test qlib signal",
    )

    result = paper_trade_qlib.run_paper_cycle(
        [signal],
        output_dir=tmp_path,
        starting_cash=10_000.0,
        max_positions=3,
        position_pct=0.10,
        min_score=50.0,
        reset=True,
    )

    account_dir = tmp_path / paper_trade_qlib.ACCOUNT_NAME
    state = json.loads((account_dir / "state.json").read_text(encoding="utf-8"))
    summary = json.loads((account_dir / "summary.json").read_text(encoding="utf-8"))
    decisions = (account_dir / "paper_decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ledger_rows = [
        json.loads(line)
        for line in (account_dir / "prediction_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert result["paper_only"] is True
    assert result["bought"] == 1
    assert set(state["positions"]) == {"AAPL"}
    assert summary["strategy"] == paper_trade_qlib.ACCOUNT_NAME
    assert summary["paper_only"] is True
    assert state["cash"] == pytest.approx(9_000.0)
    assert decisions
    assert json.loads(decisions[-1])["decision"] == "BUY"
    assert ledger_rows[0]["decision"] == "BUY"
    assert ledger_rows[0]["strategy"] == paper_trade_qlib.ACCOUNT_NAME
    assert ledger_rows[0]["qlib_features"] == {"qlib_mom_63": 0.2}


@pytest.mark.unit
def test_run_paper_cycle_dry_run_does_not_create_position(tmp_path):
    signal = paper_trade_qlib.QlibPaperSignal(
        ticker="NVDA",
        score=90.0,
        price=50.0,
        as_of="2026-06-05",
        features={"qlib_mom_63": 0.3},
        thesis="dry run signal",
    )

    result = paper_trade_qlib.run_paper_cycle(
        [signal],
        output_dir=tmp_path,
        starting_cash=5_000.0,
        max_positions=2,
        position_pct=0.10,
        min_score=50.0,
        reset=True,
        dry_run=True,
    )

    account_dir = tmp_path / paper_trade_qlib.ACCOUNT_NAME
    state_path = account_dir / "state.json"

    assert result["dry_run"] is True
    assert result["bought"] == 1
    assert result["positions"] == []
    assert not state_path.exists()
    assert (account_dir / "summary.json").exists()


@pytest.mark.unit
def test_qlib_closed_paper_trade_is_gradeable_from_events_jsonl(tmp_path):
    first = paper_trade_qlib.QlibPaperSignal(
        ticker="AAPL",
        score=95.0,
        price=100.0,
        as_of="2026-06-05",
        features={"qlib_mom_63": 0.2},
        thesis="entry signal",
    )
    second = paper_trade_qlib.QlibPaperSignal(
        ticker="AAPL",
        score=96.0,
        price=111.0,
        as_of="2026-06-06",
        features={"qlib_mom_63": 0.25},
        thesis="target hit signal",
    )

    paper_trade_qlib.run_paper_cycle(
        [first],
        output_dir=tmp_path,
        starting_cash=10_000.0,
        max_positions=3,
        position_pct=0.10,
        min_score=50.0,
        reset=True,
    )
    paper_trade_qlib.run_paper_cycle(
        [second],
        output_dir=tmp_path,
        starting_cash=10_000.0,
        max_positions=3,
        position_pct=0.10,
        min_score=50.0,
    )

    grader = PredictionGrader(tmp_path / paper_trade_qlib.ACCOUNT_NAME)
    grades = grader.grade_all(fetch_benchmarks=False)

    assert len(grades) == 1
    assert grades[0].ticker == "AAPL"
    assert grades[0].target_hit is True
    assert grades[0].actual_return == pytest.approx(0.11)
    assert grades[0].model_version == "qlib_factor_v1"


@pytest.mark.unit
def test_qlib_runner_exports_paper_only_contract():
    assert paper_trade_qlib.PAPER_ONLY is True
    source = Path(paper_trade_qlib.__file__).read_text(encoding="utf-8").lower()
    assert "fidelity" not in source
    assert "webull" not in source
    assert "broker" not in source
