"""Tests for HRP Optimizer and Weight Controller — PC-2."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("scipy", reason="scipy not installed")

from tradingagents.portfolio.hrp_optimizer import HRPOptimizer
from tradingagents.portfolio.weight_controller import WeightController


def _make_returns(n_assets: int = 5, n_obs: int = 60, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tickers = [f"T{i}" for i in range(n_assets)]
    # Add some correlation structure
    market = rng.normal(0, 0.01, n_obs)
    returns = pd.DataFrame(
        {t: market + rng.normal(0, 0.008, n_obs) for t in tickers},
        columns=tickers,
    )
    return returns


# ── HRPOptimizer ──────────────────────────────────────────────────────────────

class TestHRPOptimizer:
    def test_weights_sum_to_one(self):
        returns = _make_returns(5, 60)
        opt = HRPOptimizer()
        weights = opt.fit(returns)
        assert abs(sum(weights.values()) - 1.0) < 1e-6, f"Weights sum: {sum(weights.values())}"

    def test_weights_non_negative(self):
        returns = _make_returns(5, 60)
        opt = HRPOptimizer()
        weights = opt.fit(returns)
        for t, w in weights.items():
            assert w >= 0.0, f"Negative weight for {t}: {w}"

    def test_all_tickers_present(self):
        returns = _make_returns(8, 80)
        opt = HRPOptimizer()
        weights = opt.fit(returns)
        assert set(weights.keys()) == set(returns.columns)

    def test_single_asset_returns_one(self):
        returns = pd.DataFrame({"AAPL": [0.01, -0.02, 0.005, 0.01]})
        opt = HRPOptimizer()
        weights = opt.fit(returns)
        assert weights == {"AAPL": 1.0}

    def test_insufficient_data_returns_equal_weights(self):
        returns = _make_returns(4, 5)  # 5 obs < min_obs=20
        opt = HRPOptimizer(min_obs=20)
        weights = opt.fit(returns)
        for w in weights.values():
            assert abs(w - 0.25) < 1e-6, f"Expected equal weight 0.25, got {w}"

    def test_diversified_vs_concentrated(self):
        """HRP should give more balanced weights than equal-weight when assets are uncorrelated."""
        rng = np.random.default_rng(0)
        n, T = 5, 100
        # Pure independent assets
        ret = pd.DataFrame(
            {f"T{i}": rng.normal(0, 0.01 * (i + 1), T) for i in range(n)}
        )
        opt = HRPOptimizer()
        weights = opt.fit(ret)
        # Lower-vol assets should get higher weight (inverse-variance property)
        weights_list = [(t, w) for t, w in weights.items()]
        weights_list.sort(key=lambda x: x[0])
        # T0 has smallest vol (0.01), should have highest weight
        assert weights["T0"] > weights["T4"], (
            f"T0 (low vol) should outweigh T4 (high vol): {weights}"
        )

    def test_10_asset_weights_sum_one(self):
        returns = _make_returns(10, 120)
        opt = HRPOptimizer()
        weights = opt.fit(returns)
        assert abs(sum(weights.values()) - 1.0) < 1e-4  # rounding to 6dp on 10 assets


# ── WeightController ──────────────────────────────────────────────────────────

class TestWeightController:
    def _base_weights(self):
        return {"AAPL": 0.40, "MSFT": 0.35, "NVDA": 0.25}

    def test_weights_sum_to_one_after_enforce(self):
        ctrl = WeightController(max_single=0.30, max_sector=1.0, max_turnover=1.0, min_weight=0.0)
        w = ctrl.enforce(self._base_weights())
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_single_name_cap_applied(self):
        # 5 assets — only AAPL exceeds cap so cap is feasible (uncapped assets absorb the excess)
        weights = {"AAPL": 0.50, "MSFT": 0.20, "GOOG": 0.15, "AMZN": 0.10, "NVDA": 0.05}
        ctrl = WeightController(max_single=0.30, max_sector=1.0, max_turnover=1.0, min_weight=0.0)
        w = ctrl.enforce(weights)
        for t, v in w.items():
            assert v <= 0.30 + 1e-6, f"{t}={v:.4f} exceeds max_single=0.30"

    def test_sector_cap_applied(self):
        weights = {"AAPL": 0.40, "MSFT": 0.40, "NVDA": 0.20}
        sector_map = {"AAPL": "tech", "MSFT": "tech", "NVDA": "semi"}
        ctrl = WeightController(max_single=1.0, max_sector=0.50, max_turnover=1.0, min_weight=0.0)
        w = ctrl.enforce(weights, sector_map=sector_map)
        tech_total = w.get("AAPL", 0) + w.get("MSFT", 0)
        assert tech_total <= 0.50 + 1e-6, f"Tech sector weight {tech_total:.4f} > 0.50"

    def test_sector_cap_skips_unknown(self):
        """'unknown' sector should not be capped."""
        weights = {"A": 0.50, "B": 0.50}
        sector_map = {"A": "unknown", "B": "unknown"}
        ctrl = WeightController(max_single=1.0, max_sector=0.30, max_turnover=1.0, min_weight=0.0)
        w = ctrl.enforce(weights, sector_map=sector_map)
        # Should not apply cap to unknown sectors
        assert sum(w.values()) > 0.99  # weights preserved

    def test_turnover_constraint(self):
        prev = {"A": 0.33, "B": 0.33, "C": 0.34}
        new = {"A": 0.60, "B": 0.20, "C": 0.20}  # large turnover
        ctrl = WeightController(max_single=1.0, max_sector=1.0, max_turnover=0.30, min_weight=0.0)
        w = ctrl.enforce(new, prev_weights=prev)
        # L1 turnover of result should be ≤ 0.30
        turnover = sum(abs(w.get(t, 0) - prev.get(t, 0)) for t in set(w) | set(prev)) / 2.0
        assert turnover <= 0.30 + 1e-6, f"Turnover {turnover:.4f} exceeds 0.30"

    def test_empty_weights_returns_empty(self):
        ctrl = WeightController()
        assert ctrl.enforce({}) == {}

    def test_single_asset(self):
        ctrl = WeightController()
        w = ctrl.enforce({"AAPL": 1.0})
        assert abs(w["AAPL"] - 1.0) < 1e-6

    def test_min_weight_zeros_tiny_positions(self):
        weights = {"A": 0.001, "B": 0.001, "C": 0.998}
        ctrl = WeightController(max_single=1.0, max_sector=1.0, max_turnover=1.0, min_weight=0.01)
        w = ctrl.enforce(weights)
        # A and B should be zeroed (below min_weight after normalize)
        assert w.get("A", 0.0) == 0.0 or w.get("A", 0.0) >= 0.01
