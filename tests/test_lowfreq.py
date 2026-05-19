"""Correctness + leak-free guarantees for scripts/lowfreq.

Synthetic data only, CI-safe.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lowfreq as L  # noqa: E402


@pytest.mark.unit
def test_streak_matches_naive_reference():
    rng = np.random.default_rng(1)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    fast = L._streak(s).to_numpy()
    # naive O(n) reference
    sign = np.sign(s.diff().fillna(0).to_numpy())
    ref = np.zeros(len(sign))
    for i in range(1, len(sign)):
        if sign[i] == 0:
            ref[i] = 0
        elif sign[i] == sign[i - 1]:
            ref[i] = ref[i - 1] + sign[i]
        else:
            ref[i] = sign[i]
    assert np.array_equal(fast, ref)


@pytest.mark.unit
def test_connors_rsi_bounded():
    rng = np.random.default_rng(2)
    c = pd.Series(50 + np.cumsum(rng.normal(0, 0.5, 400)))
    cr = L.connors_rsi(c).dropna()
    assert cr.min() >= 0.0 and cr.max() <= 100.0


@pytest.mark.unit
def test_double_seven_is_leak_free_next_open_entry():
    # Build a series with a clean 7-day low on day 50 (uptrend > SMA200).
    idx = pd.bdate_range("2020-01-01", periods=300)
    base = np.linspace(50, 150, 300)
    base[50] = base[49] - 5.0          # a 7-day low at pos 50
    df = pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": np.full(300, 1e6)}, index=idx)
    trades = L._simulate_ticker("T", df, "double_seven", {"time_stop": 8})
    for tr in trades:
        # entry must be the NEXT bar's open, never the signal close
        p = idx.get_loc(tr["scan_date"])
        assert tr["entry"] == pytest.approx(float(df["Open"].iloc[p]))
        # exit cannot precede entry
        assert pd.Timestamp(tr["exit_date"]) >= pd.Timestamp(tr["scan_date"])
        assert tr["days"] >= 1


@pytest.mark.unit
def test_psr_and_deflated_sharpe_behaviour():
    good = np.full(60, 0.02) + np.random.default_rng(3).normal(0, 0.005, 60)
    flat = np.random.default_rng(4).normal(0, 0.02, 60)
    assert L.psr(good) > L.psr(flat)
    # DSR (vs expected max of N trials) must not exceed plain PSR(vs0)
    dsr = L.deflated_sharpe(good, [L.sharpe(good)] * 40)
    assert 0.0 <= dsr <= L.psr(good) + 1e-9


@pytest.mark.unit
def test_cpcv_returns_distribution():
    # deterministic winning trades spread over time -> several CPCV paths
    n = 120
    idx = pd.bdate_range("2019-01-02", periods=n, freq="7D")
    trades = pd.DataFrame({
        "ticker": [f"T{i%5}" for i in range(n)],
        "scan_date": idx, "score": 1.0,
        "ret": np.tile([0.05, -0.02], n // 2),
        "outcome": "EXIT", "days": 5, "win": True,
        "entry": 100.0, "mae": 0.02,
        "roll_half": 0.0005,
        "exit_date": idx + pd.Timedelta(days=7),
    })
    paths = L.cpcv_paths(trades, k=6, k_test=2)
    assert len(paths) >= 5
    assert all(np.isfinite(p) for p in paths)
