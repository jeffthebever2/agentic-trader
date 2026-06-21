"""Pure portfolio performance engine (tradingagents/portfolio/performance.py)."""
import json

import pytest

from tradingagents.portfolio import performance as perf
from tradingagents.portfolio.performance import Snapshot, Position, CashFlow


def _snap(date, total, cash=0.0, positions=None, ok=True, realized=0.0):
    return Snapshot(date=date, ts=0.0, total_value=total, cash=cash,
                    invested_value=total - cash, positions=positions or [],
                    ok=ok, realized_gl=realized)


# ── formula ───────────────────────────────────────────────────────────────────
def test_daily_pnl_formula():
    assert perf.daily_pnl(1000, 1100, 0, 0) == 100
    # deposit of 50 must NOT count as profit
    assert perf.daily_pnl(1000, 1100, 50, 0) == 50
    # withdrawal of 50 means real gain is higher than raw value delta
    assert perf.daily_pnl(1000, 1100, 0, 50) == 150


def test_daily_percent_guards_zero():
    assert perf.daily_percent(100, 1000) == 10.0
    assert perf.daily_percent(100, 0) == 0.0


# ── dedup / one-per-day ─────────────────────────────────────────────────────────
def test_merge_snapshot_one_per_day():
    snaps = [_snap("2026-06-01", 1000)]
    snaps = perf.merge_snapshot(snaps, _snap("2026-06-02", 1010))
    snaps = perf.merge_snapshot(snaps, _snap("2026-06-02", 1020))  # same day → replace
    assert len(snaps) == 2
    assert snaps[-1].total_value == 1020          # replaced, not duplicated
    assert snaps[0].total_value == 1000           # prior day preserved


# ── series: deposit not counted as profit ───────────────────────────────────────
def test_enrich_first_day_baseline_and_deposit_adjust():
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-02", 1600)]
    flows = [CashFlow("2026-06-02", "deposit", 500)]
    rows = perf.enrich_series(snaps, flows)
    assert rows[0]["daily_pnl"] is None and rows[0]["color"] == "gray"   # baseline
    # raw +600 but 500 was a deposit → real pnl 100
    assert rows[1]["daily_pnl"] == 100
    assert rows[1]["color"] == "green"
    assert rows[1]["deposits"] == 500


def test_color_red_on_loss():
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-02", 950)]
    rows = perf.enrich_series(snaps)
    assert rows[1]["daily_pnl"] == -50 and rows[1]["color"] == "red"


# ── metrics ─────────────────────────────────────────────────────────────────────
def test_metrics_full():
    snaps = [
        _snap("2026-06-01", 1000),
        _snap("2026-06-02", 1100),   # +100 (+10%)
        _snap("2026-06-03", 1045),   # -55 (-5%)
        _snap("2026-06-04", 1150),   # +105
    ]
    m = perf.compute_metrics(snaps, as_of="2026-06-04")
    assert m["has_data"] is True
    assert m["current_value"] == 1150
    assert m["today_pnl"] == 105
    assert m["trading_days"] == 3
    assert m["green_days"] == 2 and m["red_days"] == 1
    assert m["win_rate"] == pytest.approx(66.7, abs=0.1)
    assert m["best_day"]["pnl"] == 105
    assert m["worst_day"]["pnl"] == -55
    assert m["avg_green"] == pytest.approx((100 + 105) / 2, abs=0.01)
    assert m["avg_red"] == -55
    assert m["max_drawdown_pct"] < 0       # had a down day
    assert m["mtd_pct"] != 0


def test_metrics_empty():
    assert perf.compute_metrics([])["has_data"] is False


def test_ytd_mtd_split():
    snaps = [_snap("2025-12-31", 1000), _snap("2026-01-02", 1100), _snap("2026-06-02", 1210)]
    m = perf.compute_metrics(snaps, as_of="2026-06-02")
    # YTD counts only 2026 days; MTD only June
    assert m["ytd_pct"] != 0
    assert m["mtd_pct"] != 0


# ── attribution ─────────────────────────────────────────────────────────────────
def test_attribution_contribution_and_ranking():
    prev = _snap("2026-06-01", 1000, positions=[
        Position("AAA", unrealized_gl=10.0), Position("BBB", unrealized_gl=5.0)])
    curr = _snap("2026-06-02", 1030, positions=[
        Position("AAA", unrealized_gl=40.0),   # +30 contribution
        Position("BBB", unrealized_gl=-5.0)])   # -10 contribution
    a = perf.attribution(prev, curr)
    assert a["top_winners"][0]["symbol"] == "AAA" and a["top_winners"][0]["contribution"] == 30
    assert a["top_losers"][0]["symbol"] == "BBB" and a["top_losers"][0]["contribution"] == -10


def test_attribution_uses_realized_from_trades():
    prev = _snap("2026-06-01", 1000, positions=[])
    curr = _snap("2026-06-02", 1000, positions=[])
    trades = [{"ticker": "NVDA", "date": "2026-06-02", "realized_gl": 42.0}]
    a = perf.attribution(prev, curr, trades)
    assert a["top_winners"][0]["symbol"] == "NVDA"
    assert a["top_winners"][0]["contribution"] == 42.0
    assert len(a["trades"]) == 1


# ── validation ───────────────────────────────────────────────────────────────────
def test_validate_duplicate_and_apifail():
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-01", 1000),
             _snap("2026-06-02", 0, ok=False, )]
    issues = perf.validate(snaps)
    kinds = {i["type"] for i in issues}
    assert "duplicate_snapshot" in kinds
    assert "api_failure" in kinds


def test_validate_missing_business_days():
    # Mon 2026-06-01 then Thu 2026-06-04 → Tue/Wed missing
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-04", 1010)]
    issues = perf.validate(snaps)
    assert any(i["type"] == "missing_snapshot" for i in issues)


def test_validate_unusual_jump_flagged_unless_deposit():
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-02", 1500)]   # +50%
    assert any(i["type"] == "unusual_jump" for i in perf.validate(snaps))
    # same jump but recorded as a deposit → no unusual_jump
    flows = [CashFlow("2026-06-02", "deposit", 500)]
    issues = perf.validate(snaps, flows)
    assert not any(i["type"] == "unusual_jump" for i in issues)
    assert any(i["type"] == "cash_flow_recorded" for i in issues)


# ── export ───────────────────────────────────────────────────────────────────────
def test_export_csv_json():
    snaps = [_snap("2026-06-01", 1000), _snap("2026-06-02", 1100)]
    csv_txt = perf.to_csv(snaps)
    assert "date,ending_value" in csv_txt and "2026-06-02" in csv_txt
    rows = json.loads(perf.to_json(snaps))
    assert rows[1]["daily_pnl"] == 100


def test_snapshot_roundtrip():
    s = _snap("2026-06-01", 1000, cash=200, positions=[Position("NVDA", qty=5, unrealized_gl=12.0)])
    d = s.to_dict()
    s2 = Snapshot.from_dict(d)
    assert s2.date == "2026-06-01" and s2.total_value == 1000
    assert s2.positions[0].symbol == "NVDA" and s2.positions[0].unrealized_gl == 12.0
