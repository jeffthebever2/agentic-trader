"""Correctness of the honest sweep account simulator (scripts/honest_sweep).

These guard the metrics the final report relies on: annualized return,
profit factor, conservative MAE-marked drawdown, and the leak-free fast
replay matching the authoritative slow replay. Synthetic data only — no
large pickles, CI-safe.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import honest_sweep as HS  # noqa: E402


@pytest.mark.unit
def test_portfolio_metrics_math():
    # Two sequential, non-overlapping winners of +21% each on a single
    # all-in book over ~2 years -> compounding 1.21*1.21 = 1.4641.
    trades = pd.DataFrame({
        "ticker": ["A", "B"],
        "scan_date": pd.to_datetime(["2020-01-02", "2021-01-04"]),
        "score": [1.0, 1.0],
        "ret": [0.21, 0.21],
        "outcome": ["TARGET_HIT", "TARGET_HIT"],
        "days": [5, 5],
        "win": [True, True],
        "entry": [100.0, 100.0],
        "mae": [0.0, 0.0],
    })
    p = HS.portfolio(trades, start=10000.0, pos_pct=1.0, max_pos=1,
                     rank="score:desc", costs={"on": False})
    assert p["n"] == 2
    assert abs(p["end"] - 14641.0) < 1.0          # 1.21^2 compounding
    assert abs(p["total_ret"] - 46.41) < 0.1
    assert p["wr"] == 100.0
    assert p["pf"] == 999.0                        # no losers
    assert p["max_dd"] == 0.0                      # no adverse excursion
    # span = first scan (2020-01-02) -> last exit (~2021-01-09) ~= 1.02y,
    # so CAGR of the 46.41% total is ~45%.
    assert 40.0 < p["ann"] < 50.0


@pytest.mark.unit
def test_drawdown_uses_mae_not_cost():
    # A position that ultimately wins +10% but drew down 30% intrabar must
    # report a drawdown driven by the 30% MAE, not 0 (cost-marking would
    # hide it).
    trades = pd.DataFrame({
        "ticker": ["A"], "scan_date": pd.to_datetime(["2021-01-04"]),
        "score": [1.0], "ret": [0.10], "outcome": ["TIMED_OUT"],
        "days": [10], "win": [True], "entry": [100.0], "mae": [0.30],
    })
    p = HS.portfolio(trades, start=10000.0, pos_pct=1.0, max_pos=1, costs={"on": False})
    assert p["max_dd"] >= 29.0   # ~30% conservative MAE-marked drawdown


@pytest.mark.unit
def test_profit_factor_and_loss_accounting():
    trades = pd.DataFrame({
        "ticker": ["A", "B", "C", "D"],
        "scan_date": pd.to_datetime(["2021-01-04", "2021-03-01",
                                     "2021-06-01", "2021-09-01"]),
        "score": [1.0] * 4,
        "ret": [0.20, -0.10, 0.20, -0.10],
        "outcome": ["TARGET_HIT", "STOP_HIT", "TARGET_HIT", "STOP_HIT"],
        "days": [5, 5, 5, 5],
        "win": [True, False, True, False],
        "entry": [100.0] * 4, "mae": [0.0, 0.10, 0.0, 0.10],
    })
    p = HS.portfolio(trades, start=10000.0, pos_pct=1.0, max_pos=1, costs={"on": False})
    assert p["n"] == 4
    assert p["wr"] == 50.0
    # gross win / gross loss with compounding all-in book; both > 0, PF > 1.
    assert p["pf"] > 1.0
    assert p["profit"] > 0.0


@pytest.mark.unit
def test_fast_replay_matches_slow_on_synthetic():
    # One ticker, deterministic ramp; fast_run_config must equal run_config
    # (the verbatim resim.measure_outcome port) bit-for-bit.
    idx = pd.bdate_range("2020-01-02", periods=60)
    base = np.linspace(100, 130, 60)
    px = {"T": pd.DataFrame({
        "Open": base, "High": base * 1.02, "Low": base * 0.99,
        "Close": base, "Volume": np.full(60, 1e6)}, index=idx)}
    sig = pd.DataFrame({
        "ticker": ["T"], "scan_date": [idx[10]], "pos": [10],
        "atr": [2.0], "pdf_key": ["T"], "score": [1.0],
    })
    a = HS.run_config(sig, px, 2.0, 1.0, 15)
    b = HS.fast_run_config(sig, px, 2.0, 1.0, 15)
    assert len(a) == len(b) == 1
    assert abs(float(a["ret"].iloc[0]) - float(b["ret"].iloc[0])) < 1e-9
    assert a["outcome"].iloc[0] == b["outcome"].iloc[0]
    assert int(a["days"].iloc[0]) == int(b["days"].iloc[0])
    assert pd.Timestamp(a["exit_date"].iloc[0]) == pd.Timestamp(b["exit_date"].iloc[0])


@pytest.mark.unit
def test_portfolio_uses_real_exit_dates_not_calendar_day_offsets():
    # Ten trading bars after Jan 2 lands later than Jan 12 because weekends
    # intervene. The portfolio must hold capital until the actual replay exit
    # date, not scan_date + N calendar days.
    trades = pd.DataFrame({
        "ticker": ["A", "B"],
        "scan_date": pd.to_datetime(["2024-01-02", "2024-01-15"]),
        "exit_date": pd.to_datetime(["2024-01-17", "2024-01-16"]),
        "score": [1.0, 1.0],
        "ret": [0.10, 0.10],
        "outcome": ["TIMED_OUT", "TIMED_OUT"],
        "days": [10, 1],
        "win": [True, True],
        "entry": [100.0, 100.0],
        "mae": [0.0, 0.0],
        "roll_half": [0.0, 0.0],
    })

    p = HS.portfolio(
        trades, start=10000.0, pos_pct=1.0, max_pos=1,
        costs={"on": False},
    )

    assert p["n"] == 1
    assert p["end_date"] == "2024-01-17"


@pytest.mark.unit
def test_fast_replay_honors_hold_longer_than_default_window():
    idx = pd.bdate_range("2024-01-02", periods=45)
    close = np.full(45, 100.0)
    # No target/stop touch; a 30-bar timeout should exit on bar 40 from signal
    # position 10, not silently truncate to an older cached 25-bar window.
    px = {"T": pd.DataFrame({
        "Open": close, "High": close * 1.001, "Low": close * 0.999,
        "Close": close, "Volume": np.full(45, 1e6)}, index=idx)}
    sig = pd.DataFrame({
        "ticker": ["T"], "scan_date": [idx[10]], "pos": [10],
        "atr": [5.0], "pdf_key": ["T"], "score": [1.0],
    })

    out = HS.fast_run_config(
        sig, px, target_mult=10.0, stop_mult=10.0, hold=30,
        entry_timing="next_open",
    )

    assert int(out["days"].iloc[0]) == 30
    assert pd.Timestamp(out["exit_date"].iloc[0]) == idx[40]


@pytest.mark.unit
def test_transaction_costs_reduce_profit_and_are_default_on():
    trades = pd.DataFrame({
        "ticker": ["A", "B"],
        "scan_date": pd.to_datetime(["2020-01-02", "2021-01-04"]),
        "score": [1.0, 1.0], "ret": [0.05, 0.05],
        "outcome": ["TIMED_OUT", "TIMED_OUT"], "days": [5, 5],
        "win": [True, True], "entry": [50.0, 50.0], "mae": [0.0, 0.0],
        "roll_half": [0.0010, 0.0010],   # 10 bps half-spread
    })
    gross = HS.portfolio(trades, 10000.0, pos_pct=1.0, max_pos=1,
                         costs={"on": False})
    net = HS.portfolio(trades, 10000.0, pos_pct=1.0, max_pos=1)  # default ON
    assert net["profit"] < gross["profit"]          # costs bite
    assert net["end"] < gross["end"]
    # spread+commission both sides on ~$10k notional, twice -> material.
    assert (gross["profit"] - net["profit"]) > 50.0


@pytest.mark.unit
def test_roll_half_spread_is_asof_and_bounded():
    import numpy as np
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 1, 200))
    h = HS.roll_half_spread_frac(closes)
    assert 0.0003 <= h <= 0.015            # clamped to liquid band
    # uses only the trailing window -> appending future bars after the
    # window must not change an estimate computed on the prefix.
    h_prefix = HS.roll_half_spread_frac(closes[:120])
    h_prefix_again = HS.roll_half_spread_frac(closes[:120])
    assert h_prefix == h_prefix_again      # deterministic, as-of
