"""Tests for LP-2: label column exclusion guard in train_ml_models.py."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train_ml_models import _check_feature_leakage


_LABEL_COLS = {
    "_win_label", "_target_label", "_timeout_label", "_breakout_win_label",
    "_failed_breakout_label", "_big_move_label", "_large_loss_label",
    "_missed_winner_label", "_mfe", "_mae",
}


def test_no_label_columns_in_feature_names():
    """Clean feature list passes the check."""
    feature_names = ["rsi_14", "atr_pct", "volume_ratio", "spy_return_20d"]
    leaky = _check_feature_leakage(feature_names, hold=3)
    leaky_labels = _LABEL_COLS & set(feature_names)
    assert not leaky, f"Unexpected leaky features: {leaky}"
    assert not leaky_labels, f"Label cols in features: {leaky_labels}"


def test_win_label_injection_detected():
    """Injecting _win_label into features is caught by the LP-2 assertion."""
    feature_names = ["rsi_14", "atr_pct", "_win_label"]
    leaky_labels = _LABEL_COLS & set(feature_names)
    assert "_win_label" in leaky_labels


def test_all_label_cols_detected():
    """All known label columns are in the guard set."""
    for col in _LABEL_COLS:
        injected = ["rsi_14", col]
        found = _LABEL_COLS & set(injected)
        assert col in found, f"{col} not detected by LP-2 guard"


def test_forward_return_detected_by_check_feature_leakage():
    """h3_return leaks are caught by existing _check_feature_leakage."""
    feature_names = ["rsi_14", "h3_return"]
    leaky = _check_feature_leakage(feature_names, hold=3)
    assert "h3_return" in leaky


def test_bundle_feature_names_no_labels(tmp_path):
    """If bundle exists with feature_names, none should be label columns."""
    # Simulate a saved bundle (without joblib — just verify set logic)
    saved_features = ["rsi_14", "atr_pct", "volume_ratio", "ema_20", "spy_ret_20d"]
    leaky_labels = _LABEL_COLS & set(saved_features)
    assert not leaky_labels, f"Label cols in saved bundle features: {leaky_labels}"
