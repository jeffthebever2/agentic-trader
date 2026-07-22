"""Tests for the 15 paper-only portfolio system.

Proves the spec's acceptance criteria: 15 isolated $10k portfolios, ML actually
gates entries, compliance (PDT/GFV/dupe/size) enforces and logs, and paper
portfolios can never reach live broker routes.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.portfolio.paper_configs import all_portfolios, get_portfolio, PAPER_PORTFOLIOS
from tradingagents.portfolio.paper_account import (
    PaperPortfolioAccount, PaperTrade, PaperPosition,
    assert_paper_only, LIVE_BROKER_ROUTES,
)
from tradingagents.portfolio.paper_compliance import (
    can_enter_trade, check_pdt_limit, check_duplicate_position,
    count_day_trades_last_5_business_days, DEFAULT_COMPLIANCE,
    PaperComplianceConfig,
)
from tradingagents.portfolio.paper_engine import (
    Candidate, run_portfolio, run_all, size_shares, resolve_levels, combine_candidates,
    sits_out_entries, SIT_OUT_DOWS, SKIP_DAY_MAX_HOLD,
)
from tradingagents.portfolio import paper_metrics

ET = ZoneInfo("US/Eastern")
T0 = datetime(2026, 7, 6, 10, 0, tzinfo=ET)


def _recent_ts() -> str:
    """Timestamp guaranteed inside the PDT lookback window.

    ``count_day_trades_last_5_business_days`` measures against the wall clock,
    so a frozen date silently ages out of its own window and the assertion
    flips from 3 to 0. Anything pinned to ``T0`` for a *rolling-window* test is
    a time bomb — use this instead."""
    return datetime.now(ET).isoformat()


@pytest.fixture
def base():
    return Path(tempfile.mkdtemp())


# ── Registry ─────────────────────────────────────────────────────────────────

def test_portfolio_registry_shape():
    # 30 core (15 single-tool + 15 multi-tool) + 1 thematic competitor = 31.
    assert len(PAPER_PORTFOLIOS) == 31
    multi = [p for p in PAPER_PORTFOLIOS if p.source_strategies]
    assert len(multi) == 15, "portfolios 16–30 must be multi-tool"
    thematic = [p for p in PAPER_PORTFOLIOS if p.source_strategy == "thematic"]
    assert len(thematic) == 1 and thematic[0].portfolio_id == "thematic_momentum"


def test_portfolios_unique_and_paper_only():
    ids = [p.portfolio_id for p in all_portfolios()]
    names = [p.name for p in all_portfolios()]
    assert len(set(ids)) == 31, "portfolio_ids must be unique"
    assert len(set(names)) == 31, "names must be unique"
    for p in all_portfolios():
        assert p.initial_cash == 10000.0
        assert p.paper_only is True


def test_configs_are_distinct_hypotheses():
    # No two identical accounts — full config signatures must differ.
    sigs = {(p.stop_mult, p.target_mult, p.max_hold_days, p.risk_per_trade_pct,
             p.ml_probability_threshold, p.source_strategy, p.trailing_stop_atr_mult,
             tuple(p.source_strategies or []), p.combine_mode) for p in all_portfolios()}
    assert len(sigs) == 31


def test_ml_named_portfolios_have_thresholds():
    # Every ML/AI/brain-sourced portfolio must carry a real ML threshold.
    for p in all_portfolios():
        if p.source_strategy in {"machine_learning", "ml_new", "combined", "pure_ai", "unified_brain"}:
            assert p.ml_probability_threshold is not None, f"{p.portfolio_id} missing ML threshold"


# ── Isolation ────────────────────────────────────────────────────────────────

def test_portfolios_are_isolated(base):
    a = PaperPortfolioAccount.load("breakout_phoenix", base)
    b = PaperPortfolioAccount.load("neural_falcon", base)
    run_portfolio(a, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    a.save(base)
    b.save(base)
    b2 = PaperPortfolioAccount.load("neural_falcon", base)
    assert b2.cash == 10000.0 and b2.open_position_count() == 0, "one portfolio's trade must not touch another"
    assert a.settled_cash < 10000.0


# ── ML gate ──────────────────────────────────────────────────────────────────

def test_ml_threshold_blocks_low_probability(base):
    acc = PaperPortfolioAccount.load("neural_falcon", base)  # threshold 0.60
    run_portfolio(acc, [Candidate("NVDA", 50, "machine_learning", ml_probability=0.50, stop=47, target=58)], {"NVDA": 50}, now=T0)
    assert acc.open_position_count() == 0
    assert any("ML_THRESHOLD_FAILED" in e.reason for e in acc.compliance_log)


def test_ml_threshold_allows_high_probability_and_stamps_provenance(base):
    acc = PaperPortfolioAccount.load("neural_falcon", base)  # threshold 0.60
    run_portfolio(acc, [Candidate("NVDA", 50, "machine_learning", ml_probability=0.65, stop=47, target=58)], {"NVDA": 50}, now=T0)
    assert acc.open_position_count() == 1
    pos = acc.positions[0]
    assert pos.used_ml is True
    assert pos.ml_probability == 0.65 and pos.ml_threshold == 0.60


def test_ml_missing_probability_is_skipped(base):
    acc = PaperPortfolioAccount.load("quantum_lynx", base)  # threshold 0.58
    run_portfolio(acc, [Candidate("TSLA", 200, "ml_new", ml_probability=None, stop=190, target=230)], {"TSLA": 200}, now=T0)
    assert acc.open_position_count() == 0


def test_unified_brain_provenance(base):
    acc = PaperPortfolioAccount.load("brainstorm_atlas", base)  # unified_brain, threshold 0.56
    run_portfolio(acc, [Candidate("META", 400, "unified_brain", ml_probability=0.60, stop=380, target=460)], {"META": 400}, now=T0)
    assert acc.open_position_count() == 1
    assert acc.positions[0].used_unified_brain is True


# ── Compliance ───────────────────────────────────────────────────────────────

def test_fidelity_default_does_not_block_on_old_pdt_count(base):
    acc = PaperPortfolioAccount.load("scalpel_cheetah", base)
    today = _recent_ts()
    for i in range(3):
        acc.trades.append(PaperTrade(
            portfolio_id=acc.portfolio_id, ticker=f"D{i}", side="BUY", shares=1,
            entry_price=10, entry_date=today, entry_timestamp=today,
            exit_price=11, exit_date=today, exit_timestamp=today, exit_reason="TARGET", realized_pnl=1.0))
    assert count_day_trades_last_5_business_days(acc) == 3
    ok, reason = can_enter_trade(acc, "AAPL", "BUY", 5, 100.0)
    assert ok, reason


def test_legacy_pdt_limit_blocks_fourth_day_trade_when_opted_in(base):
    acc = PaperPortfolioAccount.load("scalpel_cheetah", base)
    today = _recent_ts()
    for i in range(3):
        acc.trades.append(PaperTrade(
            portfolio_id=acc.portfolio_id, ticker=f"D{i}", side="BUY", shares=1,
            entry_price=10, entry_date=today, entry_timestamp=today,
            exit_price=11, exit_date=today, exit_timestamp=today, exit_reason="TARGET", realized_pnl=1.0))
    assert count_day_trades_last_5_business_days(acc) == 3
    legacy = PaperComplianceConfig(enforce_pdt_like_rule=True, broker_rule_profile="legacy_pdt")
    ok, reason = can_enter_trade(acc, "AAPL", "BUY", 5, 100.0, compliance=legacy)
    assert not ok and "PDT_LIMIT_REACHED" in reason


def test_duplicate_ticker_blocked_in_same_portfolio(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert acc.open_position_count() == 1
    # Same ticker again → duplicate skip
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert acc.open_position_count() == 1
    assert any("DUPLICATE_TICKER" in e.reason for e in acc.compliance_log)


def test_invalid_price_skipped(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 0.0, "algorithm")], {"AAPL": 0.0}, now=T0)
    assert acc.open_position_count() == 0
    assert any("INVALID_PRICE" in e.reason for e in acc.compliance_log)


def test_same_ticker_allowed_across_different_portfolios(base):
    a = PaperPortfolioAccount.load("breakout_phoenix", base)
    b = PaperPortfolioAccount.load("volcano_mantis", base)
    run_portfolio(a, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    run_portfolio(b, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert a.open_position_count() == 1 and b.open_position_count() == 1


# ── Paper-only safety (spec Part 10) ─────────────────────────────────────────

@pytest.mark.parametrize("route", sorted(LIVE_BROKER_ROUTES))
def test_paper_only_blocks_every_live_route(base, route):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    with pytest.raises(RuntimeError):
        assert_paper_only(acc, route)


def test_non_paper_config_raises(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    acc.config.paper_only = False
    with pytest.raises(RuntimeError):
        acc.assert_paper_only()


def test_engine_guards_paper_only(base, monkeypatch):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    acc.paper_only = False
    with pytest.raises(RuntimeError):
        run_portfolio(acc, [], {}, now=T0)


def test_engine_module_never_imports_live_broker():
    # The engine must not reference live broker execution modules.
    src = Path("tradingagents/portfolio/paper_engine.py").read_text()
    for banned in ("web.api.fidelity", "webull", "place_order", "_fidelity_thematic_trade"):
        assert banned not in src, f"engine references live-broker symbol: {banned}"


# ── Engine lifecycle ─────────────────────────────────────────────────────────

def test_target_exit_realizes_pnl_and_ror(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)  # risk 1% → $100
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert acc.positions[0].shares == 20  # $100 risk / $5 per-share
    run_portfolio(acc, [], {"AAPL": 111}, now=T0 + timedelta(days=1))
    assert acc.total_trades() == 1
    assert acc.trades[0].exit_reason == "TARGET"
    assert acc.trades[0].realized_pnl == pytest.approx(200.0)  # 20 * (110-100)
    assert acc.all_time_ror() == pytest.approx(2.0)


def test_stop_exit_fills_at_the_gapped_print_not_the_stop(base):
    """A stop cannot protect you from a gap, and the model must not pretend it can.

    Marks are sampled on a 15-minute loop and never overnight, so the engine used
    to book a clean fill at `pos.stop` even when the observed print was well below
    it — the best case, dressed up as the worst. That understated drawdown on
    exactly the trades that hurt, and the leaderboard built on these fills is what
    copytrade mirrors into real money.
    """
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    run_portfolio(acc, [], {"AAPL": 94}, now=T0 + timedelta(days=1))
    assert acc.trades[0].exit_reason == "STOP"
    assert acc.trades[0].exit_price == pytest.approx(94.0)   # 95 was never offered
    assert acc.trades[0].realized_pnl == pytest.approx(-120.0)  # 20 * (94-100)
    assert acc.all_time_ror() == pytest.approx(-1.2)


def test_stop_exit_fills_at_stop_when_price_does_not_gap(base):
    """No gap ⇒ the stop level is achievable and is still the fill."""
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    run_portfolio(acc, [], {"AAPL": 95}, now=T0 + timedelta(days=1))
    assert acc.trades[0].exit_reason == "STOP"
    assert acc.trades[0].exit_price == pytest.approx(95.0)
    assert acc.trades[0].realized_pnl == pytest.approx(-100.0)  # 20 * (95-100)


def test_max_hold_time_exit(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)  # max_hold 7d
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    # 8 days later, price sits between stop and target → time stop fires
    run_portfolio(acc, [], {"AAPL": 103}, now=T0 + timedelta(days=8))
    assert acc.trades[0].exit_reason == "MAX_HOLD"


def test_equity_conservation_on_open(base):
    # Opening a position must not change equity (cash ↓ = position value ↑).
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    eq0 = acc.current_equity()
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert acc.current_equity() == pytest.approx(eq0)


def test_settlement_rolls_next_day(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    run_portfolio(acc, [], {"AAPL": 111}, now=T0 + timedelta(days=1))  # close → unsettled
    assert acc.unsettled_cash > 0
    # Next day with no activity → unsettled rolls to settled
    run_portfolio(acc, [], {}, now=T0 + timedelta(days=2))
    assert acc.unsettled_cash == pytest.approx(0.0)
    assert acc.settled_cash == pytest.approx(acc.cash)


# ── Metrics ──────────────────────────────────────────────────────────────────

def test_metrics_win_rate_and_profit_factor(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    # one win, one loss
    acc.trades = [
        PaperTrade(portfolio_id="breakout_phoenix", ticker="W", side="BUY", shares=1, entry_price=10,
                   entry_date="2026-07-06", entry_timestamp="2026-07-06T10:00:00", exit_price=12,
                   exit_date="2026-07-07", exit_timestamp="2026-07-07T10:00:00", realized_pnl=2.0),
        PaperTrade(portfolio_id="breakout_phoenix", ticker="L", side="BUY", shares=1, entry_price=10,
                   entry_date="2026-07-06", entry_timestamp="2026-07-06T10:00:00", exit_price=9,
                   exit_date="2026-07-07", exit_timestamp="2026-07-07T10:00:00", realized_pnl=-1.0),
    ]
    assert paper_metrics.win_rate(acc) == pytest.approx(0.5)
    assert paper_metrics.profit_factor(acc) == pytest.approx(2.0)  # 2 win / 1 loss


def test_trailing_portfolio_rides_past_target_then_trails_out(base):
    # neural_falcon has trailing_stop_atr_mult=1.5 → let-winners-run.
    acc = PaperPortfolioAccount.load("neural_falcon", base)  # entry stop 95, target 110
    run_portfolio(acc, [Candidate("NVDA", 100, "machine_learning", ml_probability=0.70, stop=95, target=110)], {"NVDA": 100}, now=T0)
    assert acc.open_position_count() == 1
    # Price blows PAST the target — must NOT hard-exit at target; trail rides it.
    run_portfolio(acc, [], {"NVDA": 120}, now=T0 + timedelta(days=1))
    assert acc.open_position_count() == 1, "trailing portfolio should ride past target, not cap"
    trail = acc.positions[0].trailing_stop
    assert trail is not None and trail > 100  # trail ratcheted above breakeven
    # Pull back below the trail → exit locking a gain well above the fixed target.
    run_portfolio(acc, [], {"NVDA": trail - 1}, now=T0 + timedelta(days=2))
    assert acc.total_trades() == 1 and acc.trades[0].exit_reason == "TRAILING_STOP"
    assert acc.trades[0].realized_pnl > 0


def test_trailing_green_survivor_not_dumped_at_max_hold(base):
    # E-9: a green survivor on a trailing portfolio keeps riding past max_hold.
    acc = PaperPortfolioAccount.load("neural_falcon", base)  # max_hold 8d
    run_portfolio(acc, [Candidate("NVDA", 100, "machine_learning", ml_probability=0.70, stop=95, target=110)], {"NVDA": 100}, now=T0)
    # 9 days later, green but below target → classic time-stop would dump it.
    run_portfolio(acc, [], {"NVDA": 106}, now=T0 + timedelta(days=9))
    assert acc.open_position_count() == 1, "green survivor with trail protection must keep riding"


def test_trailing_red_survivor_still_force_closed_at_max_hold(base):
    # A loser at the time limit is still force-closed even on a trailing portfolio.
    acc = PaperPortfolioAccount.load("neural_falcon", base)
    run_portfolio(acc, [Candidate("NVDA", 100, "machine_learning", ml_probability=0.70, stop=95, target=110)], {"NVDA": 100}, now=T0)
    run_portfolio(acc, [], {"NVDA": 97}, now=T0 + timedelta(days=9))  # red, above stop, past max_hold
    assert acc.total_trades() == 1 and acc.trades[0].exit_reason == "MAX_HOLD"


def test_reset_restores_10k(base):
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    acc.save(base)
    fresh = PaperPortfolioAccount.reset("breakout_phoenix", base)
    assert fresh.cash == 10000.0 and fresh.open_position_count() == 0 and fresh.total_trades() == 0


def test_run_all_spans_all_and_persists(base):
    summaries = run_all(base, {"algorithm": [Candidate("MSFT", 200, "algorithm", stop=190, target=220, score=5)]},
                        {"MSFT": 200}, now=T0)
    assert len(summaries) == 31
    # at least the two single-source algorithm portfolios open MSFT (union multi-tool may too)
    opened = sum(s["opened"] for s in summaries)
    assert opened >= 2
    assert (base / "breakout_phoenix.json").exists()


# ── Multi-tool combination (portfolios 16–30) ────────────────────────────────

def _pool():
    return {
        "algorithm": [Candidate("AAPL", 100, "algorithm", stop=95, target=110, ml_probability=0.55, score=10),
                      Candidate("MSFT", 200, "algorithm", stop=190, target=220, ml_probability=0.50, score=8)],
        "machine_learning": [Candidate("AAPL", 100, "machine_learning", stop=96, target=112, ml_probability=0.64, score=12)],
    }


def test_consensus_requires_two_tools():
    merged = combine_candidates(_pool(), ["algorithm", "machine_learning"], "consensus_2")
    tickers = [c.ticker for c in merged]
    assert tickers == ["AAPL"]  # MSFT only in one bucket → dropped
    assert merged[0].sources == ["algorithm", "machine_learning"]
    assert merged[0].ml_probability == 0.64  # max across contributors
    assert merged[0].score == 22             # summed → agreement ranks higher


def test_intersection_requires_all_tools():
    merged = combine_candidates(_pool(), ["algorithm", "machine_learning"], "intersection")
    assert [c.ticker for c in merged] == ["AAPL"]


def test_union_takes_any_tool():
    merged = combine_candidates(_pool(), ["algorithm", "machine_learning"], "union")
    assert sorted(c.ticker for c in merged) == ["AAPL", "MSFT"]


def test_multitool_portfolio_opens_and_stamps_provenance(base):
    acc = PaperPortfolioAccount.load("consensus_breakout_ml", base)  # [algorithm, machine_learning] consensus_2
    # run via run_all so the combine happens
    run_all(base, _pool(), {"AAPL": 100, "MSFT": 200}, now=T0)
    acc = PaperPortfolioAccount.load("consensus_breakout_ml", base)
    assert acc.open_position_count() == 1
    pos = acc.positions[0]
    assert pos.ticker == "AAPL" and pos.used_ml is True
    assert pos.source_strategy == "algorithm+machine_learning"


def test_intersection_portfolio_needs_all_three(base):
    # triple_intersect = [algorithm, machine_learning, combined] intersection.
    # Only 2 of 3 present → nothing opens.
    run_all(base, _pool(), {"AAPL": 100, "MSFT": 200}, now=T0)
    acc = PaperPortfolioAccount.load("triple_intersect", base)
    assert acc.open_position_count() == 0


# ── Thematic competitor ───────────────────────────────────────────────────────

def test_thematic_portfolio_trades_its_own_bucket(base):
    # The thematic strategy competes head-to-head: a "thematic" candidate (its own
    # social-momentum pick, carrying its own stop/target) flows to thematic_momentum
    # and nowhere else.
    cand = Candidate("PLTR", 30.0, "thematic", stop=27.9, target=66.0, score=80.0)
    summaries = run_all(base, {"thematic": [cand]}, {"PLTR": 30.0}, now=T0, signal_bar_dow=1)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["thematic_momentum"]["opened"] >= 1
    acc = PaperPortfolioAccount.load("thematic_momentum", base)
    assert acc.open_position_count() == 1
    assert acc.positions[0].ticker == "PLTR" and acc.positions[0].source_strategy == "thematic"
    # No other portfolio draws from the thematic bucket.
    assert sum(s["opened"] for s in summaries) == 1


# ── Skip-day A/B (Mon/Thu sit-out is per-portfolio) ──────────────────────────

def test_skip_day_split_is_balanced():
    trade = [p for p in PAPER_PORTFOLIOS if p.trade_skip_days]
    skip = [p for p in PAPER_PORTFOLIOS if not p.trade_skip_days]
    # 15 core trade-cohort + thematic (also trades any day) = 16 trade, 15 skip.
    assert len(trade) == 16 and len(skip) == 15, "field must split 16/15 on trade_skip_days"
    # Both cohorts must span single- AND multi-tool so it isn't a single-vs-multi confound.
    assert any(p.source_strategies for p in trade) and any(not p.source_strategies for p in trade)
    assert any(p.source_strategies for p in skip) and any(not p.source_strategies for p in skip)
    # Both cohorts must contain algorithm and machine_learning archetypes.
    for cohort in (trade, skip):
        srcs = {p.source_strategy for p in cohort}
        assert "algorithm" in srcs and "machine_learning" in srcs
    # The thematic competitor trades any day (not breakout-signal-bar-gated).
    assert any(p.portfolio_id == "thematic_momentum" for p in trade)


def test_sits_out_entries_defers_to_config_by_default():
    skip_cfg = get_portfolio("breakout_phoenix")   # trade_skip_days False
    trade_cfg = get_portfolio("volcano_mantis")    # trade_skip_days True
    assert skip_cfg.trade_skip_days is False and trade_cfg.trade_skip_days is True
    # Default portfolio sits out Mon(0)/Thu(3), trades Tue(1)/Wed(2)/Fri(4).
    assert sits_out_entries(skip_cfg, 0) is True
    assert sits_out_entries(skip_cfg, 3) is True
    for dow in (1, 2, 4):
        assert sits_out_entries(skip_cfg, dow) is False
    # trade_skip_days portfolio never sits out a sit-out day.
    assert sits_out_entries(trade_cfg, 0) is False
    assert sits_out_entries(trade_cfg, 3) is False
    # Unknown weekday → never sit out.
    assert sits_out_entries(skip_cfg, None) is False


def test_sits_out_entries_global_force_overrides_config():
    skip_cfg = get_portfolio("breakout_phoenix")   # would sit out Monday
    trade_cfg = get_portfolio("volcano_mantis")    # would trade Monday
    # Force-skip Monday → even the trade_skip_days portfolio sits out.
    assert sits_out_entries(trade_cfg, 0, force_skip_monday=True) is True
    # Force-trade Monday → even the default portfolio trades.
    assert sits_out_entries(skip_cfg, 0, force_skip_monday=False) is False
    # Monday force must not leak into Thursday.
    assert sits_out_entries(skip_cfg, 3, force_skip_monday=False) is True
    assert sits_out_entries(trade_cfg, 3, force_skip_thursday=True) is True


def _algo_pool():
    return {"algorithm": [Candidate("AAPL", 100, "algorithm", stop=95, target=110)]}


def test_run_all_skip_day_gates_entries_per_portfolio(base):
    # Monday signal bar (dow=0): default algo portfolio sits out, trade_skip algo trades.
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0, signal_bar_dow=0)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["breakout_phoenix"]["sat_out"] is True
    assert by_id["breakout_phoenix"]["opened"] == 0
    assert by_id["volcano_mantis"]["sat_out"] is False
    assert by_id["volcano_mantis"]["opened"] >= 1


def test_run_all_non_skip_day_everyone_trades(base):
    # Tuesday signal bar (dow=1): both algo portfolios open, nobody sits out.
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0, signal_bar_dow=1)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["breakout_phoenix"]["sat_out"] is False
    assert by_id["breakout_phoenix"]["opened"] >= 1
    assert by_id["volcano_mantis"]["opened"] >= 1


def test_run_all_none_dow_never_sits_out(base):
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0, signal_bar_dow=None)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["breakout_phoenix"]["sat_out"] is False
    assert by_id["breakout_phoenix"]["opened"] >= 1


def test_run_all_force_skip_overrides_the_ab(base):
    # Force ALL to sit out Monday → the trade_skip_days portfolio opens nothing.
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0,
                        signal_bar_dow=0, force_skip_monday=True)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["volcano_mantis"]["sat_out"] is True
    assert by_id["volcano_mantis"]["opened"] == 0


def test_run_all_force_trade_overrides_the_ab(base):
    # Force ALL to trade Monday → the default sit-out portfolio opens.
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0,
                        signal_bar_dow=0, force_skip_monday=False)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["breakout_phoenix"]["sat_out"] is False
    assert by_id["breakout_phoenix"]["opened"] >= 1


def test_skip_day_still_runs_exits(base):
    # A portfolio that sits out entries on Monday must STILL manage/exit positions.
    acc = PaperPortfolioAccount.load("breakout_phoenix", base)
    run_portfolio(acc, [Candidate("AAPL", 100, "algorithm", stop=95, target=110)], {"AAPL": 100}, now=T0)
    assert acc.open_position_count() == 1
    acc.save(base)
    # Next Monday scan: entries gated (sits out), but AAPL hits its target → exits.
    summaries = run_all(base, _algo_pool(), {"AAPL": 111}, now=T0 + timedelta(days=1), signal_bar_dow=0)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["breakout_phoenix"]["sat_out"] is True
    assert by_id["breakout_phoenix"]["closed"] >= 1, "exits must run even on a sit-out day"
    reloaded = PaperPortfolioAccount.load("breakout_phoenix", base)
    assert reloaded.open_position_count() == 0


# ── Smart skip-day entry policy (fewer/higher-quality/smaller/shorter) ────────

def _fresh(pid: str) -> PaperPortfolioAccount:
    return PaperPortfolioAccount.load(pid, Path(tempfile.mkdtemp()))


def test_skip_day_ml_bump_raises_the_bar():
    # quantum_lynx: ml_new, base threshold 0.58, trades skip days.
    mid = Candidate("NVDA", 100, "ml_new", ml_probability=0.60, stop=90, target=130)  # R:R 3
    normal = _fresh("quantum_lynx")
    run_portfolio(normal, [mid], {"NVDA": 100}, now=T0, skip_day=False)
    assert normal.open_position_count() == 1, "0.60 >= base 0.58 opens on a normal day"
    skip = _fresh("quantum_lynx")
    run_portfolio(skip, [mid], {"NVDA": 100}, now=T0, skip_day=True)
    assert skip.open_position_count() == 0, "0.60 < skip-day bar 0.63 → rejected"
    strong = _fresh("quantum_lynx")
    run_portfolio(strong, [Candidate("NVDA", 100, "ml_new", ml_probability=0.65, stop=90, target=130)],
                  {"NVDA": 100}, now=T0, skip_day=True)
    assert strong.open_position_count() == 1, "0.65 >= 0.63 clears the raised bar"


def test_skip_day_min_rr_gate():
    # volcano_mantis: algorithm, no ML gate, trades skip days.
    low_rr = Candidate("AAA", 100, "algorithm", stop=95, target=104)   # R:R 4/5 = 0.8
    high_rr = Candidate("AAA", 100, "algorithm", stop=95, target=115)  # R:R 15/5 = 3.0
    # Normal day: the R:R gate does not apply → low R:R still opens.
    n = _fresh("volcano_mantis")
    run_portfolio(n, [low_rr], {"AAA": 100}, now=T0, skip_day=False)
    assert n.open_position_count() == 1
    # Skip day: low R:R rejected, strong R:R clears.
    s = _fresh("volcano_mantis")
    run_portfolio(s, [low_rr], {"AAA": 100}, now=T0, skip_day=True)
    assert s.open_position_count() == 0
    h = _fresh("volcano_mantis")
    run_portfolio(h, [high_rr], {"AAA": 100}, now=T0, skip_day=True)
    assert h.open_position_count() == 1


def test_skip_day_halves_size():
    cand = Candidate("AAA", 100, "algorithm", stop=95, target=130)  # R:R 6 clears the gate
    n = _fresh("volcano_mantis")
    run_portfolio(n, [cand], {"AAA": 100}, now=T0, skip_day=False)
    s = _fresh("volcano_mantis")
    run_portfolio(s, [cand], {"AAA": 100}, now=T0, skip_day=True)
    n_sh, s_sh = n.positions[0].shares, s.positions[0].shares
    assert s_sh < n_sh
    assert abs(s_sh - n_sh / 2) <= 1, f"skip-day size {s_sh} should be ~half of {n_sh}"


def test_skip_day_caps_hold():
    # titan_turtle: long_hold max_hold 30, ML thr 0.52, trades skip days.
    cand = Candidate("AAA", 100, "long_hold", ml_probability=0.60, stop=95, target=130)
    n = _fresh("titan_turtle")
    run_portfolio(n, [cand], {"AAA": 100}, now=T0, skip_day=False)
    assert n.positions[0].max_hold_days == 30
    s = _fresh("titan_turtle")
    run_portfolio(s, [cand], {"AAA": 100}, now=T0, skip_day=True)
    assert s.positions[0].max_hold_days == SKIP_DAY_MAX_HOLD


def test_run_all_marks_skip_day_policy(base):
    # Monday signal bar: a trade_skip portfolio runs the policy; a sitter does not.
    summaries = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0, signal_bar_dow=0)
    by_id = {s["portfolio_id"]: s for s in summaries}
    assert by_id["volcano_mantis"]["skip_day_policy"] is True
    assert by_id["breakout_phoenix"]["skip_day_policy"] is False
    # Normal day: nobody runs the policy.
    s2 = run_all(base, _algo_pool(), {"AAPL": 100}, now=T0, signal_bar_dow=1)
    assert all(s["skip_day_policy"] is False for s in s2)
