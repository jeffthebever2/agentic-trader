"""Tests for the pure per-ticker enrichment dossier (thematic revamp Stage 1).

Fixtures use the REAL FMP /stable/ + Finnhub response shapes verified live 2026-07-06.
"""
from __future__ import annotations

import datetime as dt

from tradingagents.screening.dossier import (
    parse_earnings_calendar, parse_shares_float, parse_grades_consensus,
    parse_finnhub_metric, parse_most_actives, build_dossier, earnings_gate,
    TickerDossier, LOW_FLOAT_SHARES,
)

TODAY = dt.date(2026, 7, 6)


# ── Parsers (real schemas) ────────────────────────────────────────────────────

def test_parse_earnings_calendar_picks_next_future_print():
    rows = [
        {"symbol": "AAPL", "date": "2026-06-30", "epsEstimated": 1.4},   # past → ignore
        {"symbol": "AAPL", "date": "2026-07-30", "epsEstimated": 1.5, "revenueEstimated": 9e10},
        {"symbol": "AAPL", "date": "2026-10-30", "epsEstimated": 1.6},   # later → not nearest
        {"symbol": "NKE", "date": "2026-07-08", "epsEstimated": 0.7},
    ]
    out = parse_earnings_calendar(rows, today=TODAY)
    assert out["AAPL"]["date"] == "2026-07-30" and out["AAPL"]["days_to"] == 24
    assert out["AAPL"]["eps_est"] == 1.5 and out["AAPL"]["rev_est"] == 9e10
    assert out["NKE"]["days_to"] == 2


def test_parse_shares_float():
    obj = {"symbol": "AAPL", "freeFloat": 99.83, "floatShares": 14662534368, "outstandingShares": 14687356000}
    out = parse_shares_float(obj)
    assert out["float_pct"] == 99.83 and out["float_shares"] == 14662534368
    assert parse_shares_float([obj]) == out  # accepts 1-item list


def test_parse_grades_consensus_skew():
    obj = {"symbol": "AAPL", "strongBuy": 1, "buy": 69, "hold": 34, "sell": 7, "strongSell": 0, "consensus": "Buy"}
    out = parse_grades_consensus(obj)
    assert out["consensus"] == "Buy" and out["analyst_count"] == 111
    # (1*2 + 69 - 7 - 0*2) / (2*111) = 64/222 ≈ 0.288
    assert out["skew"] == 0.288
    # all strong sell → skew -1
    assert parse_grades_consensus({"strongSell": 5})["skew"] == -1.0


def test_parse_finnhub_metric():
    obj = {"metric": {"beta": 1.0946, "52WeekHigh": 317.4, "52WeekLow": 201.5}, "symbol": "AAPL"}
    out = parse_finnhub_metric(obj)
    assert out["beta"] == 1.0946 and out["hi_52w"] == 317.4 and out["lo_52w"] == 201.5


def test_parse_most_actives():
    rows = [{"symbol": "SOXS", "changesPercentage": -7.53}, {"symbol": "NVDA", "changesPercentage": 4.1}]
    out = parse_most_actives(rows)
    assert out["SOXS"] == -7.53 and out["NVDA"] == 4.1


def test_parsers_tolerate_garbage():
    assert parse_earnings_calendar(None) == {}
    assert parse_shares_float("nope") == {}
    assert parse_grades_consensus([]).get("skew") is None  # structured, None fields, no crash
    assert parse_finnhub_metric({}).get("beta") is None
    assert parse_finnhub_metric("nope") == {}              # non-dict → empty
    assert parse_most_actives(None) == {}


# ── Assembly + derived signals ────────────────────────────────────────────────

def test_build_dossier_low_float_and_52w():
    d = build_dossier(
        "GME",
        earnings=parse_earnings_calendar([{"symbol": "GME", "date": "2026-07-15"}], today=TODAY).get("GME"),
        float_data=parse_shares_float({"symbol": "GME", "floatShares": 30_000_000, "freeFloat": 40.0}),
        grades=parse_grades_consensus({"strongBuy": 0, "buy": 1, "hold": 2, "sell": 3, "strongSell": 1}),
        metric=parse_finnhub_metric({"metric": {"beta": 1.8, "52WeekHigh": 100.0, "52WeekLow": 20.0}}),
        mover_chg=12.5, price=80.0,
    )
    assert d.low_float is True                      # 30M < 50M
    assert d.days_to_earnings == 9
    assert d.analyst_skew is not None and d.analyst_skew < 0
    assert d.is_mover is True and d.mover_chg_pct == 12.5
    assert d.pct_from_52w_high == -20.0             # (80-100)/100
    assert d.beta == 1.8


def test_build_dossier_high_float_not_flagged():
    d = build_dossier("AAPL", float_data={"float_shares": 14_000_000_000})
    assert d.low_float is False


# ── Earnings gate (block <=2d, half-size <=5d) ────────────────────────────────

def test_earnings_gate_blackout():
    allowed, factor, reason = earnings_gate(1)
    assert allowed is False and factor == 0.0 and "blackout" in reason


def test_earnings_gate_halfsize():
    allowed, factor, reason = earnings_gate(4)
    assert allowed is True and factor == 0.5 and "half-size" in reason


def test_earnings_gate_clear():
    assert earnings_gate(10) == (True, 1.0, "")
    assert earnings_gate(None) == (True, 1.0, "")
    assert earnings_gate(-3) == (True, 1.0, "")  # past print
    # boundary: exactly block_days → blocked; exactly halfsize_days → half
    assert earnings_gate(2)[0] is False
    assert earnings_gate(5)[1] == 0.5
