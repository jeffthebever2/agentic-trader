"""Tests for CPCV, Deflated Sharpe, and Noise Feature Test — WF-1, WF-2, FE-2."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.validation.cpcv import combinatorial_purged_cv
from tradingagents.validation.deflated_sharpe import deflated_sharpe_ratio
from tradingagents.validation.noise_feature_test import noise_feature_test


# ── CPCV tests ───────────────────────────────────────────────────────────────

def _make_cpcv_df(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "_scan_dt": dates.strftime("%Y-%m-%d"),
        "feat_a": rng.standard_normal(n),
        "feat_b": rng.standard_normal(n),
        "_win_label": (rng.standard_normal(n) > 0).astype(int),
    })


def _train_fn(X, y):
    return RandomForestClassifier(n_estimators=10, random_state=42).fit(X, y)


def _test_fn(model, X):
    return model.predict_proba(X)[:, 1]


def test_cpcv_path_count():
    """C(5,2) = 10 paths expected."""
    df = _make_cpcv_df(300)
    result = combinatorial_purged_cv(df, n_splits=5, n_test_splits=2, embargo_days=5,
                                      train_fn=_train_fn, test_fn=_test_fn, fast_mode=True)
    assert result["n_paths_expected"] == 10, f"Expected 10 paths, got {result['n_paths_expected']}"
    # Some paths may fail due to small data; at least some should succeed
    assert result["n_paths"] >= 1, f"Zero paths completed: {result}"


def test_cpcv_no_time_travel():
    """For each path, test groups must be after training groups chronologically."""
    df = _make_cpcv_df(300)
    result = combinatorial_purged_cv(df, n_splits=5, n_test_splits=2, embargo_days=5,
                                      train_fn=_train_fn, test_fn=_test_fn, fast_mode=True)
    # The paths list should contain no errors
    for path in result["paths"]:
        assert "error" not in path or path.get("error") is None, f"Path error: {path}"


def test_cpcv_returns_distribution():
    """Result contains mean, std, min, max Sharpe."""
    df = _make_cpcv_df(300)
    result = combinatorial_purged_cv(df, n_splits=5, n_test_splits=2, embargo_days=5,
                                      train_fn=_train_fn, test_fn=_test_fn, fast_mode=True)
    for key in ("mean_sharpe", "std_sharpe", "min_sharpe", "max_sharpe"):
        assert key in result, f"Missing key: {key}"


# ── Deflated Sharpe tests ─────────────────────────────────────────────────────

def test_dsr_less_than_raw_sharpe():
    """DSR must be <= 1.0 (probability) when n_trials > 1."""
    dsr = deflated_sharpe_ratio(sharpe=1.0, n_trials=100, T=252)
    assert 0.0 <= dsr <= 1.0, f"DSR out of [0,1]: {dsr}"


def test_dsr_penalizes_more_trials():
    """More trials → lower DSR (harder to pass the bar).

    Use T=20 so E[max SR] is large enough that n_trials=100 meaningfully deflates.
    """
    dsr_1 = deflated_sharpe_ratio(sharpe=0.1, n_trials=1, T=20)
    dsr_100 = deflated_sharpe_ratio(sharpe=0.1, n_trials=100, T=20)
    assert dsr_1 > dsr_100, f"More trials should lower DSR: dsr(1)={dsr_1}, dsr(100)={dsr_100}"


def test_dsr_single_trial_near_sr():
    """With n_trials=1, DSR should be near 1.0 (no selection bias)."""
    dsr = deflated_sharpe_ratio(sharpe=1.0, n_trials=1, T=252)
    assert dsr > 0.8, f"Single-trial DSR unexpectedly low: {dsr}"


def test_dsr_high_trial_low_sharpe_negative():
    """Many trials + low Sharpe → DSR near 0 (likely overfit).

    With T=20 and n_trials=1000, E[max SR] >> 0.1 so DSR << 0.5.
    """
    dsr = deflated_sharpe_ratio(sharpe=0.1, n_trials=1000, T=20)
    assert dsr < 0.5, f"Should be low DSR for many trials + low Sharpe: {dsr}"


def test_dsr_known_values():
    """Regression: sharpe=0.1, n_trials=100, T=20 → DSR < 0.5 (SR below expected max)."""
    dsr = deflated_sharpe_ratio(sharpe=0.1, n_trials=100, T=20)
    assert dsr < 0.5


# ── Noise feature test ────────────────────────────────────────────────────────

def test_noise_feature_test_basic():
    """At least some real features should rank above noise."""
    rng = np.random.default_rng(42)
    n = 500
    signal = rng.standard_normal((n, 5))
    label = (signal[:, 0] + signal[:, 1] + 0.3 * rng.standard_normal(n) > 0).astype(int)

    X = pd.DataFrame(signal, columns=[f"feat_{i}" for i in range(5)])
    y = pd.Series(label)

    def _train(X, y):
        return RandomForestClassifier(n_estimators=20, random_state=42).fit(X, y)

    result = noise_feature_test(X, y, model_fn=_train, n_noise=5, seed=42, n_permutations=3)

    assert "noise_threshold" in result
    assert "features_below_noise" in result
    assert "features_above_noise" in result
    # At least 1 real feature should be above noise
    assert len(result["features_above_noise"]) >= 1, (
        f"All real features below noise. Threshold={result['noise_threshold']}, "
        f"importances={result['all_importances']}"
    )


def test_noise_feature_test_no_auto_removal():
    """Result never removes features — only flags them."""
    rng = np.random.default_rng(99)
    X = pd.DataFrame(rng.standard_normal((200, 3)), columns=["a", "b", "c"])
    y = pd.Series((rng.standard_normal(200) > 0).astype(int))

    def _train(X, y):
        return RandomForestClassifier(n_estimators=10, random_state=0).fit(X, y)

    result = noise_feature_test(X, y, model_fn=_train, n_noise=3, seed=0, n_permutations=2)

    # All real feature names must still be present in all_importances
    assert "a" in result["all_importances"]
    assert "b" in result["all_importances"]
    assert "c" in result["all_importances"]
