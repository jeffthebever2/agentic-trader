"""Honesty guarantees for backtest._simulate_account.

Covers the two integrity fixes:
  - fixed sizing mode is look-ahead free (size = position_cap_pct of current
    equity; appending trades that occur AFTER an early trade must not change
    that early trade's fill);
  - reported max_drawdown is the conservative MAE-marked drawdown, never
    smaller than the legacy cost-marked drawdown.
"""
import pandas as pd
import pytest

import backtest


def _trades():
    data = [
        ("AAA", "2024-01-02", 100.0, 10.0, -0.20, "2024-01-09", 10.4, 0.25),
        ("BBB", "2024-01-03", 100.0, 10.0, 0.30, "2024-01-10", 13.0, 0.05),
        ("CCC", "2024-02-01", 100.0, 10.0, -0.15, "2024-02-08", 8.5, 0.18),
        ("DDD", "2024-03-01", 100.0, 10.0, 0.10, "2024-03-08", 11.0, 0.06),
        ("EEE", "2024-04-01", 100.0, 10.0, 0.05, "2024-04-08", 10.5, 0.03),
        ("FFF", "2024-05-01", 100.0, 10.0, -0.10, "2024-05-08", 9.0, 0.12),
    ]
    return pd.DataFrame([
        {"ticker": t, "scan_date": d, "score": s, "entry": e,
         "h5_return": r, "h5_exit_date": xd, "h5_exit": xp, "h5_mae": mae,
         "spy_regime": "bull"}
        for (t, d, s, e, r, xd, xp, mae) in data
    ])


@pytest.mark.unit
def test_fixed_sizing_is_lookahead_free():
    df = _trades()
    hs = {"half_kelly_pct": 5.0}
    # Run on the full set, and on a truncated set that drops the LAST trade.
    full = backtest._simulate_account(df, 5, hs, account_size=10000.0,
                                      position_cap_pct=20.0,
                                      sizing_mode="fixed")
    trunc = backtest._simulate_account(df.iloc[:-1], 5, hs,
                                       account_size=10000.0,
                                       position_cap_pct=20.0,
                                       sizing_mode="fixed")
    # Settings reflect the look-ahead-free contract.
    assert full["settings"]["sizing_mode"] == "fixed"
    assert full["settings"]["sizing_lookahead_free"] is True
    assert full["settings"]["effective_position_pct"] == 20.0
    # Every trade that closed in BOTH runs must be byte-identical: a later
    # trade cannot retroactively change an earlier fill under fixed sizing.
    fk = {(t["ticker"], t["entry_date"]): t for t in full["closed_trades"]}
    for t in trunc["closed_trades"]:
        key = (t["ticker"], t["entry_date"])
        assert key in fk
        assert fk[key]["shares"] == t["shares"]
        assert fk[key]["pnl"] == t["pnl"]


@pytest.mark.unit
def test_max_drawdown_is_conservative_not_understated():
    df = _trades()
    hs = {"half_kelly_pct": 5.0}
    sim = backtest._simulate_account(df, 5, hs, account_size=10000.0,
                                     position_cap_pct=20.0,
                                     sizing_mode="fixed")
    s = sim["summary"]
    # MAE-marked drawdown is reported and is >= the legacy cost-marked one.
    assert "max_drawdown_cost_marked" in s
    assert s["max_drawdown"] >= s["max_drawdown_cost_marked"]
    # With genuine adverse excursions present, it must be strictly positive.
    assert s["max_drawdown"] > 0.0


@pytest.mark.unit
def test_fixed_differs_from_kelly_static_when_kelly_below_cap():
    df = _trades()
    hs = {"half_kelly_pct": 2.0}  # well below 20% cap
    fixed = backtest._simulate_account(df, 5, hs, account_size=10000.0,
                                       position_cap_pct=20.0,
                                       sizing_mode="fixed")
    kelly = backtest._simulate_account(df, 5, hs, account_size=10000.0,
                                       position_cap_pct=20.0,
                                       sizing_mode="kelly_static")
    assert fixed["settings"]["effective_position_pct"] == 20.0
    assert kelly["settings"]["effective_position_pct"] == 2.0
    assert fixed["summary"]["final_value"] != kelly["summary"]["final_value"]
