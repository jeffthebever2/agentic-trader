"""Signal outcome tracking + adaptive source weights (tradingagents/screening/signal_outcomes.py).

Verifies the learning loop: scans record top tickers with price snapshots, later
scans fill forward returns, per-source hit-rates become clamped multipliers, and
a source only moves off 1.0 with enough evidence.
"""
import json
from datetime import datetime, timedelta

import pytest

from tradingagents.screening import signal_outcomes as so

CFG = so.OutcomeConfig()
T0 = datetime(2026, 6, 1, 12, 0, 0)


def _row(ticker="NVDA", ts=T0, price=100.0, sources=None, fwd=None, score=80.0):
    return {
        "ts": ts.isoformat(), "ticker": ticker, "score": score,
        "price": price, "sources": sources or {"reddit": 10.0},
        "fwd": fwd or {},
    }


# ── record_scan ───────────────────────────────────────────────────────────────
def test_record_scan_appends_top_tickers_with_price_and_sources():
    ranked = [("NVDA", 90.0), ("AMD", 70.0), ("XXXX", 50.0)]
    breakdown = {"NVDA": {"reddit": 20.0, "insider": 5.0}, "AMD": {"ddg": 8.0}}
    prices = {"NVDA": 100.0, "AMD": 50.0}   # XXXX has no price → skipped
    rows = so.record_scan([], ranked, breakdown, prices, T0)
    assert [r["ticker"] for r in rows] == ["NVDA", "AMD"]
    assert rows[0]["sources"] == {"reddit": 20.0, "insider": 5.0}
    assert rows[0]["price"] == 100.0


def test_record_scan_skips_open_duplicates():
    existing = [_row("NVDA")]  # 5d not yet evaluated → open
    rows = so.record_scan(existing, [("NVDA", 95.0)], {"NVDA": {"reddit": 5.0}},
                          {"NVDA": 101.0}, T0 + timedelta(hours=4))
    assert len(rows) == 1     # no duplicate observation while open


def test_record_scan_allows_new_row_after_evaluation():
    closed = [_row("NVDA", fwd={"1d": {"ret": 0.02}, "5d": {"ret": 0.05}})]
    rows = so.record_scan(closed, [("NVDA", 95.0)], {"NVDA": {"reddit": 5.0}},
                          {"NVDA": 101.0}, T0 + timedelta(days=10))
    assert len(rows) == 2


def test_record_scan_skips_no_sources_and_bad_price():
    rows = so.record_scan([], [("NVDA", 90.0), ("AMD", 80.0)],
                          {"AMD": {"ddg": 3.0}}, {"NVDA": 0.0, "AMD": 50.0}, T0)
    assert [r["ticker"] for r in rows] == ["AMD"]


# ── pending_tickers / update_forward_returns ─────────────────────────────────
def test_pending_only_after_horizon_elapsed():
    rows = [_row("NVDA")]
    assert so.pending_tickers(rows, T0 + timedelta(hours=2)) == set()
    assert so.pending_tickers(rows, T0 + timedelta(hours=24)) == {"NVDA"}


def test_update_fills_1d_then_5d():
    rows = [_row("NVDA", price=100.0)]
    n = so.update_forward_returns(rows, {"NVDA": 105.0}, T0 + timedelta(hours=24))
    assert n == 1
    assert rows[0]["fwd"]["1d"]["ret"] == pytest.approx(0.05)
    assert "5d" not in rows[0]["fwd"]
    n = so.update_forward_returns(rows, {"NVDA": 92.0}, T0 + timedelta(hours=130))
    assert n == 1
    assert rows[0]["fwd"]["5d"]["ret"] == pytest.approx(-0.08)
    # 1d already filled — not overwritten
    assert rows[0]["fwd"]["1d"]["ret"] == pytest.approx(0.05)


def test_update_skips_missing_price():
    rows = [_row("NVDA")]
    assert so.update_forward_returns(rows, {}, T0 + timedelta(days=2)) == 0


# ── trim ─────────────────────────────────────────────────────────────────────
def test_trim_drops_old_and_caps_count():
    old = _row("OLD", ts=T0 - timedelta(days=200))
    fresh = [_row(f"T{i}") for i in range(5)]
    cfg = so.OutcomeConfig(max_rows=3)
    kept = so.trim_rows([old] + fresh, T0 + timedelta(days=1), cfg)
    assert len(kept) == 3 and all(r["ticker"] != "OLD" for r in kept)


# ── source stats & weights ───────────────────────────────────────────────────
def _evaluated(ticker, ret, sources):
    return _row(ticker, sources=sources, fwd={"5d": {"ret": ret}})


def test_stats_attribute_proportionally():
    rows = [_evaluated("A", 0.10, {"reddit": 30.0, "ddg": 10.0})]
    stats = so.compute_source_stats(rows)
    assert stats["reddit"].n_eff == pytest.approx(0.75)
    assert stats["ddg"].n_eff == pytest.approx(0.25)
    assert stats["reddit"].hit_rate == 1.0


def test_weights_need_min_observations():
    rows = [_evaluated("A", 0.10, {"reddit": 10.0})]  # n_eff = 1 < min_weight_obs
    w = so.source_weights(rows)
    assert w["reddit"] == 1.0


def test_weights_reward_winning_source_and_punish_losing():
    rows = []
    for i in range(20):
        rows.append(_evaluated(f"W{i}", 0.10, {"winner": 10.0}))
        rows.append(_evaluated(f"L{i}", -0.10, {"loser": 10.0}))
    w = so.source_weights(rows)
    assert w["winner"] > 1.0 > w["loser"]
    assert w["winner"] <= CFG.hi and w["loser"] >= CFG.lo


def test_weights_clamped_at_bounds():
    rows = [_evaluated(f"W{i}", 0.50, {"hot": 10.0}) for i in range(200)]
    rows += [_evaluated(f"L{i}", -0.50, {"cold": 10.0}) for i in range(200)]
    w = so.source_weights(rows)
    assert w["hot"] == CFG.hi
    assert w["cold"] == CFG.lo


def test_weights_empty_without_evaluated_rows():
    rows = [_row("NVDA")]  # no fwd yet
    assert so.source_weights(rows) == {}


def test_trades_fold_in_with_higher_weight():
    # Weights are RELATIVE to the global baseline, so a reference source is
    # needed. Source A looks fine on scan rows; big losing REAL trades
    # (trade_weight× observations) should drag A below its scan-only weight.
    rows = [_evaluated(f"T{i}", 0.02, {"A": 10.0}) for i in range(8)]
    rows += [_evaluated(f"R{i}", 0.02 if i % 2 else -0.02, {"REF": 10.0}) for i in range(8)]
    w_no_trades = so.source_weights(rows)
    trades = [{"sources": {"A": 10.0}, "pnl_pct": -0.30}] * 3
    w_with = so.source_weights(rows, trades)
    assert w_with["A"] < w_no_trades["A"]


def test_row_outcome_prefers_5d():
    r = _row("X", fwd={"1d": {"ret": 0.5}, "5d": {"ret": -0.1}})
    assert so._row_outcome(r) == -0.1
    r1 = _row("X", fwd={"1d": {"ret": 0.5}})
    assert so._row_outcome(r1) == 0.5


# ── file IO round-trip ───────────────────────────────────────────────────────
def test_rows_roundtrip_and_corrupt_lines_skipped(tmp_path):
    p = tmp_path / "outcomes.jsonl"
    so.save_rows(p, [_row("NVDA"), _row("AMD")])
    p.write_text(p.read_text() + "not json\n")
    rows = so.load_rows(p)
    assert [r["ticker"] for r in rows] == ["NVDA", "AMD"]


def test_weights_roundtrip_and_bad_values_dropped(tmp_path):
    p = tmp_path / "weights.json"
    so.save_weights(p, {"reddit": 1.2, "ddg": 0.8}, T0)
    assert so.load_weights(p) == {"reddit": 1.2, "ddg": 0.8}
    p.write_text(json.dumps({"weights": {"reddit": 99.0, "ddg": "x", "ok": 1.1}}))
    assert so.load_weights(p) == {"ok": 1.1}


def test_load_missing_files(tmp_path):
    assert so.load_rows(tmp_path / "nope.jsonl") == []
    assert so.load_weights(tmp_path / "nope.json") == {}
