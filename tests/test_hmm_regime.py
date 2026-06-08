"""Tests for HMM Regime Feature Layer — RD-1."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("hmmlearn", reason="hmmlearn not installed")

from tradingagents.ml.hmm_regime import HMMRegimeFeatures


def _make_returns(n: int = 300, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.Series(rng.normal(0.0003, 0.012, n), index=dates, name="log_return")


class TestHMMRegimeFeatures:
    def test_fit_sets_is_fitted(self):
        hmm = HMMRegimeFeatures(n_components=2, seed=0)
        assert not hmm.is_fitted
        hmm.fit(_make_returns())
        assert hmm.is_fitted

    def test_transform_output_shape(self):
        returns = _make_returns(200)
        hmm = HMMRegimeFeatures(n_components=3, seed=0)
        hmm.fit(returns)
        features = hmm.transform(returns)

        # n_components prob cols + hmm_state + hmm_log_likelihood
        assert features.shape == (len(returns), 3 + 2), f"Unexpected shape: {features.shape}"

    def test_prob_columns_sum_to_one(self):
        returns = _make_returns(200)
        hmm = HMMRegimeFeatures(n_components=3, seed=0)
        hmm.fit(returns)
        features = hmm.transform(returns)

        prob_cols = [f"hmm_prob_{i}" for i in range(3)]
        row_sums = features[prob_cols].sum(axis=1)
        np.testing.assert_allclose(row_sums.values, 1.0, atol=1e-6,
                                   err_msg="Regime probabilities don't sum to 1")

    def test_state_column_valid_range(self):
        returns = _make_returns(200)
        hmm = HMMRegimeFeatures(n_components=3, seed=0)
        hmm.fit(returns)
        features = hmm.transform(returns)

        states = features["hmm_state"].values
        assert states.min() >= 0
        assert states.max() <= 2  # 3 components → states 0,1,2

    def test_transform_before_fit_raises(self):
        hmm = HMMRegimeFeatures(n_components=2, seed=0)
        with pytest.raises(RuntimeError, match="fit"):
            hmm.transform(_make_returns())

    def test_predict_state_length(self):
        returns = _make_returns(150)
        hmm = HMMRegimeFeatures(n_components=2, seed=0)
        hmm.fit(returns)
        states = hmm.predict_state(returns)
        assert len(states) == len(returns)

    def test_index_preserved_in_transform(self):
        returns = _make_returns(100)
        hmm = HMMRegimeFeatures(n_components=2, seed=0)
        hmm.fit(returns)
        features = hmm.transform(returns)
        assert list(features.index) == list(returns.index)

    def test_feature_columns_present(self):
        returns = _make_returns(150)
        hmm = HMMRegimeFeatures(n_components=2, seed=0)
        hmm.fit(returns)
        features = hmm.transform(returns)
        for col in ("hmm_prob_0", "hmm_prob_1", "hmm_state", "hmm_log_likelihood"):
            assert col in features.columns, f"Missing column: {col}"
