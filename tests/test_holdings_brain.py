"""Tests for tradingagents.portfolio.holdings_brain.

Covers the deterministic rule engine (the real-money safety floor), broker-row
normalization, broker-vs-store reconciliation, the fast stop guard, LLM clamping,
and the compliance contract the brain depends on.
"""
import datetime as dt

import pytest

from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT, validate_live_order
from tradingagents.portfolio import holdings_brain as hb
from tradingagents.portfolio.holdings_brain import (
    Action,
    Holding,
    assess_holding,
    assess_portfolio,
    check_stops,
    load_store,
    normalize_holdings,
    reconcile,
    save_store,
)

CAP = float(MAX_POSITION_PCT_OF_ACCOUNT)


def _holding(**kw) -> Holding:
    base = dict(
        ticker="AAPL", shares=10, avg_cost=100.0, last=105.0,
        market_value=1050.0, pct_of_account=5.0, unrealized_pct=5.0,
    )
    base.update(kw)
    return Holding(**base)


def _plan(**kw) -> dict:
    base = dict(status="managed", stop=90.0, target=130.0, conviction=6, trail_high=105.0)
    base.update(kw)
    return base


# ── normalize_holdings ──────────────────────────────────────────────────────────
def test_normalize_fidelity_row_parses_dollar_strings():
    raw = [{
        "symbol": "AAPL", "qty": "10", "cost_per_share": "$100.00",
        "last_price": "$120.00", "market_value": "$1,200.00",
        "pct_of_account": "12.5", "total_gain_pct": "+20.0%", "description": "APPLE INC",
    }]
    [h] = normalize_holdings(raw, "fidelity")
    assert h.ticker == "AAPL"
    assert h.shares == 10
    assert h.avg_cost == 100.0
    assert h.last == 120.0
    assert h.market_value == 1200.0
    assert h.pct_of_account == 12.5
    assert h.unrealized_pct == 20.0
    assert h.broker == "fidelity"


def test_normalize_fidelity_derives_cost_when_missing():
    raw = [{
        "symbol": "MSFT", "qty": "4", "cost_per_share": "",
        "last_price": "$125.00", "total_gain_pct": "+25.0%",
    }]
    [h] = normalize_holdings(raw, "fidelity")
    assert h.avg_cost == pytest.approx(100.0, abs=0.01)


def test_normalize_webull_row():
    raw = [{
        "symbol": "NVDA", "qty": 5, "cost_price": 100.0, "last_price": 150.0,
        "market_value": 750.0, "unrealized_pnl_pct": 50.0,
    }]
    [h] = normalize_holdings(raw, "webull")
    assert h.ticker == "NVDA"
    assert h.avg_cost == 100.0
    assert h.last == 150.0
    assert h.unrealized_pct == 50.0


def test_normalize_skips_zero_shares_and_bad_tickers():
    raw = [
        {"symbol": "CASH", "qty": ""},                 # no shares
        {"symbol": "TOOLONG", "qty": "1", "last_price": "$5"},  # >5 chars
        {"symbol": "GOOD", "qty": "2", "last_price": "$10"},
    ]
    out = normalize_holdings(raw, "fidelity")
    assert [h.ticker for h in out] == ["GOOD"]


# ── reconcile ─────────────────────────────────────────────────────────────────
def test_reconcile_classifies_adopt_tracked_closed():
    holdings = [_holding(ticker="AAPL"), _holding(ticker="NVDA")]
    store = {
        "AAPL": _plan(),                       # tracked
        "TSLA": _plan(),                       # closed (sold outside system)
        "OLD": {"status": "closed"},           # inactive, ignored
    }
    res = reconcile(holdings, store)
    assert [h.ticker for h in res.tracked] == ["AAPL"]
    assert [h.ticker for h in res.to_adopt] == ["NVDA"]
    assert res.closed == ["TSLA"]


# ── rule engine ─────────────────────────────────────────────────────────────────
def test_assess_no_plan_proposes_adopt_with_stop_target():
    a = assess_holding(_holding(), plan=None, ctx={})
    assert a.kind == hb.ACTION_ADOPT
    assert a.stop is not None and a.stop < a.target


def test_assess_crash_regime_forces_exit():
    a = assess_holding(_holding(), _plan(), ctx={"regime": {"no_trade": True, "crash_risk_score": 0.9}})
    assert a.kind == hb.ACTION_EXIT
    assert a.fraction == 1.0
    assert "regime_crash_risk" in a.risk_flags


def test_assess_below_stop_exits():
    a = assess_holding(_holding(last=85.0), _plan(stop=90.0), ctx={})
    assert a.kind == hb.ACTION_EXIT
    assert "below_managed_stop" in a.risk_flags


def test_assess_over_concentration_trims():
    h = _holding(pct_of_account=CAP + 8.0, last=110.0)
    a = assess_holding(h, _plan(), ctx={})
    assert a.kind == hb.ACTION_TRIM
    assert 0 < a.fraction <= hb.MAX_TRIM_FRACTION


def test_assess_target_reached_trims_partial():
    a = assess_holding(_holding(last=140.0), _plan(target=130.0), ctx={})
    assert a.kind == hb.ACTION_TRIM
    assert a.fraction == pytest.approx(0.33)


def test_assess_winner_raises_trailing_stop():
    # up 20%, modest size, target not reached → SET_STOP ratchet
    h = _holding(avg_cost=100.0, last=120.0, unrealized_pct=20.0, pct_of_account=5.0)
    a = assess_holding(h, _plan(stop=100.0, target=200.0), ctx={})
    assert a.kind == hb.ACTION_SET_STOP
    assert a.stop > 100.0


def test_assess_high_conviction_adds_when_room():
    h = _holding(avg_cost=100.0, last=103.0, unrealized_pct=3.0, pct_of_account=4.0)
    a = assess_holding(h, _plan(conviction=9, stop=95.0, target=150.0),
                       ctx={"regime": {"regime": "bull"}})
    assert a.kind == hb.ACTION_ADD
    assert a.fraction > 0


def test_assess_default_is_hold():
    h = _holding(avg_cost=100.0, last=104.0, unrealized_pct=4.0, pct_of_account=5.0)
    a = assess_holding(h, _plan(conviction=6, stop=90.0, target=140.0), ctx={})
    assert a.kind == hb.ACTION_HOLD


# ── LLM augmentation + clamping ────────────────────────────────────────────────
def test_llm_can_refine_but_trim_fraction_is_clamped():
    h = _holding(avg_cost=100.0, last=104.0, unrealized_pct=4.0, pct_of_account=5.0)
    llm = lambda _p: '{"action":"TRIM","fraction":0.9,"conviction":7,"reason":"de-risk"}'
    a = assess_holding(h, _plan(), ctx={}, llm_fn=llm)
    assert a.kind == hb.ACTION_TRIM
    assert a.fraction == hb.MAX_TRIM_FRACTION   # 0.9 clamped to 0.5
    assert a.source == "llm+rule"


def test_llm_cannot_soften_crash_exit():
    llm = lambda _p: '{"action":"HOLD","reason":"ride it out"}'
    a = assess_holding(_holding(), _plan(), ctx={"regime": {"no_trade": True, "crash_risk_score": 0.9}}, llm_fn=llm)
    assert a.kind == hb.ACTION_EXIT


def test_llm_bad_json_falls_back_to_rule():
    llm = lambda _p: "the model rambled and returned no json"
    a = assess_holding(_holding(), _plan(conviction=6), ctx={}, llm_fn=llm)
    assert a.kind == hb.ACTION_HOLD
    assert a.source == "rule"


def test_llm_exception_falls_back_to_rule():
    def boom(_p):
        raise RuntimeError("api down")
    a = assess_holding(_holding(), _plan(conviction=6), ctx={}, llm_fn=boom)
    assert a.kind == hb.ACTION_HOLD


# ── check_stops ─────────────────────────────────────────────────────────────────
def test_check_stops_detects_hits_and_ignores_unmanaged():
    holdings = [
        _holding(ticker="AAPL", last=85.0),
        _holding(ticker="NVDA", last=150.0),
        _holding(ticker="MSFT", last=10.0),   # no plan → ignored
    ]
    store = {
        "AAPL": _plan(stop=90.0, target=200.0),
        "NVDA": _plan(stop=100.0, target=140.0),
    }
    breaches = {b.ticker: b.reason for b in check_stops(holdings, store)}
    assert breaches == {"AAPL": "stop_hit", "NVDA": "target_hit"}


def test_check_stops_prefers_live_quote_over_last():
    holdings = [_holding(ticker="AAPL", last=120.0)]
    store = {"AAPL": _plan(stop=110.0, target=300.0)}
    # last price (120) is above stop, but the live quote (105) breaches it
    breaches = check_stops(holdings, store, quotes={"AAPL": 105.0})
    assert len(breaches) == 1 and breaches[0].reason == "stop_hit"


# ── portfolio posture ───────────────────────────────────────────────────────────
def test_assess_portfolio_flags_concentration_and_regime():
    holdings = [_holding(ticker="AAPL", pct_of_account=CAP + 5)]
    rep = assess_portfolio(holdings, ctx={"regime": {"no_trade": True, "crash_risk_score": 0.9}})
    assert rep["posture"] == "reduce_risk"
    assert any("regime" in f for f in rep["risk_flags"])
    assert any("concentration" in f for f in rep["risk_flags"])


# ── store persistence ───────────────────────────────────────────────────────────
def test_store_roundtrip(tmp_path):
    store = {"AAPL": _plan(thesis="ai leader")}
    save_store("user@example.com", store, base_dir=tmp_path)
    loaded = load_store("user@example.com", base_dir=tmp_path)
    assert loaded["AAPL"]["thesis"] == "ai leader"


def test_load_store_missing_returns_empty(tmp_path):
    assert load_store("nobody@example.com", base_dir=tmp_path) == {}


def test_adopt_plan_shape():
    a = Action("AAPL", hb.ACTION_ADOPT, stop=95.0, target=130.0, conviction=7)
    plan = hb.adopt_plan(a, _holding(), theme="ai_leaders")
    assert plan["status"] == "managed"
    assert plan["stop"] == 95.0 and plan["theme"] == "ai_leaders"


# ── compliance contract the brain relies on ─────────────────────────────────────
def test_compliance_blocks_market_orders():
    d = validate_live_order(
        {"action": "buy", "order_type": "market", "quantity": 1,
         "symbol": "AAPL", "limit_price": 10, "execute": False}
    )
    assert not d.allowed


def test_compliance_allows_limit_preview():
    d = validate_live_order(
        {"action": "sell", "order_type": "limit", "quantity": 1,
         "symbol": "AAPL", "limit_price": 10, "execute": False}
    )
    assert d.allowed


def test_compliance_blocks_oversize_order():
    d = validate_live_order(
        {"action": "buy", "order_type": "limit", "quantity": 1,
         "symbol": "AAPL", "limit_price": 10_000_000, "execute": False}
    )
    assert not d.allowed
