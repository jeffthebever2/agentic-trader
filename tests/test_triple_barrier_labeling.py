"""Tests for triple-barrier labeling — FE-1."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.labeling.triple_barrier import compute_triple_barrier_labels, label_distribution


def _make_df(n_target=40, n_stop=30, n_timeout=30) -> pd.DataFrame:
    n = n_target + n_stop + n_timeout
    outcomes = ["TARGET_HIT"] * n_target + ["STOP_HIT"] * n_stop + ["TIMED_OUT"] * n_timeout
    # Shuffle reproducibly
    rng = np.random.default_rng(42)
    idx = rng.permutation(n)
    outcomes = [outcomes[i] for i in idx]
    returns = rng.normal(0.02, 0.03, n)  # random forward returns
    return pd.DataFrame({
        "outcome": outcomes,
        "h3_return": returns,
    })


def test_timeout_zero_labels_counts():
    """With timeout=zero: target→1, stop→0, timeout→0."""
    df = _make_df(40, 30, 30)
    labels = compute_triple_barrier_labels(df, outcome_col="outcome", timeout_handling="zero")

    assert labels.isna().sum() == 0, "No NaN expected with timeout=zero"
    assert (labels == 1).sum() == 40, f"Expected 40 wins, got {(labels==1).sum()}"
    assert (labels == 0).sum() == 60, f"Expected 60 losses, got {(labels==0).sum()}"


def test_timeout_drop_reduces_rows():
    """With timeout=drop: timeout rows become NaN."""
    df = _make_df(40, 30, 30)
    labels = compute_triple_barrier_labels(df, outcome_col="outcome", timeout_handling="drop")

    assert labels.isna().sum() == 30, f"Expected 30 NaN (timeouts), got {labels.isna().sum()}"
    assert (labels == 1).sum() == 40
    assert (labels == 0).sum() == 30


def test_timeout_pass_through_uses_return():
    """pass_through: timeout labels come from h3_return > threshold."""
    df = _make_df(40, 30, 30)
    labels = compute_triple_barrier_labels(
        df, outcome_col="outcome", timeout_handling="pass_through",
        passthrough_return_col="h3_return", passthrough_threshold=0.005,
    )

    assert labels.isna().sum() == 0
    assert (labels == 1).sum() >= 40, "At least target hits should be 1"


def test_fixed_horizon_backward_compat():
    """Computing labels with existing outcome column doesn't affect other columns."""
    df = _make_df(50, 30, 20)
    original_outcomes = df["outcome"].copy()
    labels = compute_triple_barrier_labels(df, timeout_handling="zero")

    # Original df unchanged
    pd.testing.assert_series_equal(df["outcome"], original_outcomes)
    assert len(labels) == len(df)


def test_label_distribution_keys():
    """label_distribution returns expected keys."""
    df = _make_df(40, 30, 30)
    labels = compute_triple_barrier_labels(df, timeout_handling="zero")
    dist = label_distribution(labels)

    assert "win_rate" in dist
    assert "target_pct" in dist
    assert "n_total" in dist
    assert dist["n_total"] == 100


def test_missing_outcome_column_raises():
    """Raises ValueError when outcome_col not in df."""
    df = pd.DataFrame({"other_col": ["a", "b"]})
    with pytest.raises(ValueError, match="not found"):
        compute_triple_barrier_labels(df, outcome_col="outcome")


def test_pass_through_missing_return_col_raises():
    """pass_through without return col raises ValueError."""
    df = _make_df(10, 10, 10)
    with pytest.raises(ValueError, match="pass_through"):
        compute_triple_barrier_labels(
            df, outcome_col="outcome", timeout_handling="pass_through",
            passthrough_return_col=None,
        )
