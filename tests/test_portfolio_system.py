"""Tests for the 15-portfolio competition system.

Covers:
  - Registry integrity (all source_strategies valid, no duplicate names)
  - PortfolioConfig.filter_candidates() — ML threshold gate
  - PortfolioConfig.as_param_dict() — only non-None params included
  - Comparison engine: stats, ranking, equity curve
  - Leaderboard ordering (active > no_data, sorted by return)
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

KNOWN_SOURCE_STRATEGIES = {"algorithm", "machine_learning", "ml_new", "combined", "long_hold", "pure_ai"}


def _make_candidate(ml_probability: float = 0.55, rr_ratio: float = 1.2) -> SimpleNamespace:
    c = SimpleNamespace()
    c.ml_probability = ml_probability
    c.risk_reward_ratio = rr_ratio
    c.ticker = "AAPL"
    return c


def _write_portfolio_state(port_dir: Path, starting_cash: float = 10000.0,
                            cash: float = 10500.0, realized_pnl: float = 500.0,
                            positions: dict | None = None) -> None:
    port_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "starting_cash": starting_cash,
        "cash": cash,
        "realized_pnl": realized_pnl,
        "positions": positions or {},
        "trades": [],
    }
    (port_dir / "state.json").write_text(json.dumps(state))


def _write_sell_event(port_dir: Path, pnl: float, entry_price: float = 10.0,
                      shares: int = 100) -> None:
    events_path = port_dir / "events.jsonl"
    event = {
        "type": "SELL",
        "pnl": pnl,
        "entry_price": entry_price,
        "shares": shares,
        "timestamp": "2026-06-01T10:00:00",
        "entry_time": "2026-05-28T10:00:00",
    }
    with events_path.open("a") as f:
        f.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------

class TestPortfolioRegistry:
    def test_registry_loads(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        assert len(PORTFOLIO_REGISTRY) == 15

    def test_all_names_unique(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        names = [p.name for p in PORTFOLIO_REGISTRY]
        assert len(names) == len(set(names)), "Duplicate portfolio names in registry"

    def test_all_source_strategies_valid(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        invalid = [(p.name, p.source_strategy) for p in PORTFOLIO_REGISTRY
                   if p.source_strategy not in KNOWN_SOURCE_STRATEGIES]
        assert not invalid, f"Portfolios with unknown source_strategy: {invalid}"

    def test_all_groups_valid(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        valid_groups = {"signal", "risk", "hold", "filter"}
        invalid = [(p.name, p.group) for p in PORTFOLIO_REGISTRY if p.group not in valid_groups]
        assert not invalid, f"Portfolios with unknown group: {invalid}"

    def test_all_required_fields_present(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        for p in PORTFOLIO_REGISTRY:
            assert p.name, f"Portfolio missing name"
            assert p.label, f"Portfolio {p.name} missing label"
            assert p.description, f"Portfolio {p.name} missing description"
            assert p.emoji, f"Portfolio {p.name} missing emoji"

    def test_get_portfolio_returns_correct(self):
        from tradingagents.portfolios.registry import get_portfolio
        p = get_portfolio("algo_standard")
        assert p.name == "algo_standard"
        assert p.source_strategy == "algorithm"

    def test_get_portfolio_raises_for_unknown(self):
        from tradingagents.portfolios.registry import get_portfolio
        with pytest.raises(KeyError):
            get_portfolio("nonexistent_portfolio_xyz")

    def test_list_portfolios_returns_all_names(self):
        from tradingagents.portfolios.registry import list_portfolios, PORTFOLIO_REGISTRY
        names = list_portfolios()
        assert len(names) == len(PORTFOLIO_REGISTRY)
        assert all(isinstance(n, str) for n in names)

    def test_ml_threshold_portfolios_have_source_combined(self):
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        threshold_ports = [p for p in PORTFOLIO_REGISTRY if p.ml_probability_threshold]
        for p in threshold_ports:
            assert p.source_strategy in ("combined", "algorithm", "ml_new"), \
                f"{p.name}: ml_probability_threshold set but source_strategy is '{p.source_strategy}'"

    def test_high_rr_portfolio_exists(self):
        from tradingagents.portfolios.registry import get_portfolio
        p = get_portfolio("high_rr_only")
        assert p.min_risk_reward is not None
        assert p.min_risk_reward >= 1.0


# ---------------------------------------------------------------------------
# PortfolioConfig methods
# ---------------------------------------------------------------------------

class TestPortfolioConfig:
    def test_as_param_dict_excludes_none(self):
        from tradingagents.portfolios.config import PortfolioConfig
        p = PortfolioConfig(name="test", label="T", description="D",
                            source_strategy="algorithm",
                            stop_mult=1.5, target_mult=None)
        d = p.as_param_dict()
        assert "stop_mult" in d
        assert "target_mult" not in d  # None excluded

    def test_as_param_dict_always_includes_source_strategy(self):
        from tradingagents.portfolios.config import PortfolioConfig
        p = PortfolioConfig(name="test", label="T", description="D",
                            source_strategy="combined")
        d = p.as_param_dict()
        assert d["source_strategy"] == "combined"

    def test_filter_candidates_no_threshold_returns_all(self):
        from tradingagents.portfolios.config import PortfolioConfig
        p = PortfolioConfig(name="test", label="T", description="D")
        candidates = [_make_candidate(0.45), _make_candidate(0.60), _make_candidate(0.30)]
        result = p.filter_candidates(candidates)
        assert result == candidates

    def test_filter_candidates_ml_threshold_filters_correctly(self):
        from tradingagents.portfolios.config import PortfolioConfig
        p = PortfolioConfig(name="test", label="T", description="D",
                            ml_probability_threshold=0.55)
        candidates = [
            _make_candidate(0.45),   # below → filtered out
            _make_candidate(0.60),   # above → kept
            _make_candidate(0.55),   # at threshold → kept
        ]
        result = p.filter_candidates(candidates)
        assert len(result) == 2
        assert all(c.ml_probability >= 0.55 for c in result)

    def test_filter_candidates_zero_threshold_returns_all(self):
        from tradingagents.portfolios.config import PortfolioConfig
        p = PortfolioConfig(name="test", label="T", description="D",
                            ml_probability_threshold=0.0)
        candidates = [_make_candidate(0.10), _make_candidate(0.90)]
        result = p.filter_candidates(candidates)
        assert result == candidates


# ---------------------------------------------------------------------------
# Source strategy routing (simulating scan loop logic)
# ---------------------------------------------------------------------------

class TestSourceStrategyRouting:
    def test_candidates_routed_to_correct_portfolio(self):
        """Portfolios with different source_strategies should get different candidate sets."""
        candidates_by_strategy = {
            "algorithm": [_make_candidate(0.6), _make_candidate(0.7)],
            "combined":  [_make_candidate(0.65)],
            "ml_new":    [_make_candidate(0.72)],
        }

        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY

        for portfolio in PORTFOLIO_REGISTRY:
            src = portfolio.source_strategy
            expected = candidates_by_strategy.get(src, [])
            # Apply ML threshold (same logic as scan loop)
            if portfolio.ml_probability_threshold:
                expected = [c for c in expected
                            if (getattr(c, "ml_probability", None) or 0.0) >= portfolio.ml_probability_threshold]
            # Should not crash and result should be a subset of the source bucket
            full_bucket = candidates_by_strategy.get(src, [])
            assert all(c in full_bucket for c in expected), \
                f"Portfolio {portfolio.name} got candidate from wrong bucket"

    def test_unknown_source_strategy_returns_empty(self):
        candidates_by_strategy = {"algorithm": [_make_candidate()]}
        result = candidates_by_strategy.get("nonexistent", [])
        assert result == []

    def test_ml_threshold_filter_in_scan_loop(self):
        """Replicate exact scan-loop ML filter logic."""
        raw_candidates = [_make_candidate(0.50), _make_candidate(0.58), _make_candidate(0.63)]
        ml_thresh = 0.57
        filtered = [c for c in raw_candidates
                    if (getattr(c, "ml_probability", None) or 0.0) >= ml_thresh]
        assert len(filtered) == 2
        assert all(c.ml_probability >= ml_thresh for c in filtered)

    def test_min_rr_filter_in_scan_loop(self):
        """Replicate exact scan-loop min_risk_reward filter logic."""
        raw_candidates = [
            _make_candidate(rr_ratio=1.0),
            _make_candidate(rr_ratio=1.5),
            _make_candidate(rr_ratio=2.0),
        ]
        min_rr = 1.5
        filtered = [c for c in raw_candidates
                    if (getattr(c, "risk_reward_ratio", None) or 0.0) >= min_rr]
        assert len(filtered) == 2
        assert all(c.risk_reward_ratio >= min_rr for c in filtered)

    def test_combined_ml_and_rr_filters(self):
        """ML threshold + min_rr applied in sequence."""
        raw = [
            SimpleNamespace(ml_probability=0.45, risk_reward_ratio=2.0, ticker="A"),  # fails ML
            SimpleNamespace(ml_probability=0.60, risk_reward_ratio=1.0, ticker="B"),  # fails RR
            SimpleNamespace(ml_probability=0.60, risk_reward_ratio=1.5, ticker="C"),  # passes both
        ]
        after_ml = [c for c in raw if (getattr(c, "ml_probability", None) or 0.0) >= 0.55]
        after_rr = [c for c in after_ml if (getattr(c, "risk_reward_ratio", None) or 0.0) >= 1.5]
        assert len(after_rr) == 1
        assert after_rr[0].ticker == "C"


# ---------------------------------------------------------------------------
# Comparison engine
# ---------------------------------------------------------------------------

class TestComparisonEngine:
    def test_compute_stats_no_data(self):
        from tradingagents.portfolios.comparison import compute_portfolio_stats
        from tradingagents.portfolios.registry import get_portfolio
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p = get_portfolio("algo_standard")
            stats = compute_portfolio_stats(p, base)
            assert stats["status"] == "no_data"
            assert stats["trade_count"] == 0
            assert stats["total_return_pct"] == 0.0
            assert stats["config"] is not None

    def test_compute_stats_with_data(self):
        from tradingagents.portfolios.comparison import compute_portfolio_stats
        from tradingagents.portfolios.registry import get_portfolio
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p = get_portfolio("algo_standard")
            port_dir = base / p.name
            _write_portfolio_state(port_dir, starting_cash=10000, cash=9800, realized_pnl=200)
            # Two wins, one loss
            _write_sell_event(port_dir, pnl=300, entry_price=10, shares=100)
            _write_sell_event(port_dir, pnl=200, entry_price=10, shares=100)
            _write_sell_event(port_dir, pnl=-100, entry_price=10, shares=100)

            stats = compute_portfolio_stats(p, base)
            assert stats["status"] == "active"
            assert stats["trade_count"] == 3
            assert stats["win_rate"] == pytest.approx(2/3, rel=0.01)
            assert stats["profit_factor"] == pytest.approx(500/100, rel=0.01)
            assert stats["config"] is not None
            assert stats["source_strategy"] == "algorithm"

    def test_max_drawdown_calculated(self):
        from tradingagents.portfolios.comparison import _max_drawdown
        curve = [10000, 10500, 10200, 9800, 10100]
        dd = _max_drawdown(curve)
        expected = (10500 - 9800) / 10500
        assert abs(dd - expected) < 0.001

    def test_max_drawdown_monotone_increase(self):
        from tradingagents.portfolios.comparison import _max_drawdown
        curve = [10000, 10100, 10200, 10300]
        assert _max_drawdown(curve) == 0.0

    def test_sharpe_with_constant_returns(self):
        from tradingagents.portfolios.comparison import _sharpe
        # constant returns → std = 0 → None
        assert _sharpe([0.01, 0.01, 0.01, 0.01]) is None

    def test_sharpe_with_varied_returns(self):
        from tradingagents.portfolios.comparison import _sharpe
        returns = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.008, 0.012]
        result = _sharpe(returns)
        assert result is not None
        assert isinstance(result, float)

    def test_leaderboard_ranking_active_before_no_data(self):
        from tradingagents.portfolios.comparison import rank_portfolios
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Only write state for one portfolio
            first = PORTFOLIO_REGISTRY[0]
            port_dir = base / first.name
            _write_portfolio_state(port_dir, cash=11000, realized_pnl=1000)

            ranked = rank_portfolios(base)
            # Active portfolio should be ranked first
            assert ranked[0]["name"] == first.name
            assert ranked[0]["status"] == "active"
            # All no_data portfolios follow
            assert all(r["status"] == "no_data" for r in ranked[1:])

    def test_leaderboard_ranks_by_return_descending(self):
        from tradingagents.portfolios.comparison import rank_portfolios
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p1, p2 = PORTFOLIO_REGISTRY[0], PORTFOLIO_REGISTRY[1]
            _write_portfolio_state(base / p1.name, cash=11000, realized_pnl=1000)  # +10%
            _write_portfolio_state(base / p2.name, cash=10500, realized_pnl=500)   # +5%

            ranked = rank_portfolios(base)
            active = [r for r in ranked if r["status"] == "active"]
            assert active[0]["name"] == p1.name
            assert active[1]["name"] == p2.name
            assert active[0]["total_return_pct"] > active[1]["total_return_pct"]

    def test_leaderboard_summary_fields(self):
        from tradingagents.portfolios.comparison import leaderboard_summary
        from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            summary = leaderboard_summary(base)
            assert "portfolios" in summary
            assert "as_of" in summary
            assert "portfolio_count" in summary
            assert summary["portfolio_count"] == len(PORTFOLIO_REGISTRY)
            assert "groups" in summary

    def test_config_included_in_stats(self):
        from tradingagents.portfolios.comparison import compute_portfolio_stats
        from tradingagents.portfolios.registry import get_portfolio
        with tempfile.TemporaryDirectory() as tmp:
            p = get_portfolio("algo_conservative")
            stats = compute_portfolio_stats(p, Path(tmp))
            assert "config" in stats
            assert stats["config"].get("stop_mult") == 0.8
            assert stats["config"].get("risk_per_trade_pct") == 0.5
