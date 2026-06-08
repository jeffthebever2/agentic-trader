"""Tests for training report schema validation — LOG-2."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.ml.training_report_schema import SchemaError, validate_training_report


def _valid_report() -> dict:
    return {
        "settings": {
            "source": "backtest.json",
            "hold": "2024-01-01",
            "rows_used": 5000,
            "train_rows": 3500,
            "cal_rows": 750,
            "test_rows": 750,
            "test_period": "2024-01-01_2024-03-31",
            "feature_count": 42,
            "calibrated": True,
            "ml_probability_threshold": 0.65,
            "ml_expected_return_min": 0.02,
            "ml_large_loss_max": 0.08,
            "executed_weight": 1.0,
            "executed_only": False,
            "psi_pruned_features": [],
            "psi_pruned_count": 0,
        },
        "label_distribution": {
            "train": {"n": 3500, "win_rate": 0.55},
            "test": {"n": 750, "win_rate": 0.53},
        },
        "models": {"win_probability": {"metrics": {"roc_auc": 0.62}}},
        "leakage_check": {"status": "clean", "leaky_features": []},
        "walk_forward": {"roc_auc": 0.5134, "n_oos_rows": 2800},
        "artifacts": {
            "model_bundle": "/ml_models/latest/model_bundle.joblib",
            "training_report": "/ml_models/latest/training_report.json",
        },
    }


class TestValidReport:
    def test_valid_report_no_warnings(self):
        warnings = validate_training_report(_valid_report())
        assert warnings == [], f"Unexpected warnings: {warnings}"

    def test_strict_valid_report_no_raise(self):
        validate_training_report(_valid_report(), strict=True)


class TestMissingTopLevel:
    def test_missing_settings_warns(self):
        r = _valid_report()
        del r["settings"]
        warns = validate_training_report(r)
        assert any("settings" in w for w in warns)

    def test_missing_label_distribution_warns(self):
        r = _valid_report()
        del r["label_distribution"]
        warns = validate_training_report(r)
        assert any("label_distribution" in w for w in warns)

    def test_missing_leakage_check_warns(self):
        r = _valid_report()
        del r["leakage_check"]
        warns = validate_training_report(r)
        assert any("leakage_check" in w for w in warns)

    def test_strict_missing_key_raises(self):
        r = _valid_report()
        del r["settings"]
        with pytest.raises(SchemaError):
            validate_training_report(r, strict=True)


class TestSettingsSubKeys:
    def test_missing_feature_count_warns(self):
        r = _valid_report()
        del r["settings"]["feature_count"]
        warns = validate_training_report(r)
        assert any("feature_count" in w for w in warns)

    def test_feature_count_zero_warns(self):
        r = _valid_report()
        r["settings"]["feature_count"] = 0
        warns = validate_training_report(r)
        assert any("feature_count" in w for w in warns)

    def test_rows_used_zero_warns(self):
        r = _valid_report()
        r["settings"]["rows_used"] = 0
        warns = validate_training_report(r)
        assert any("rows_used" in w for w in warns)


class TestLabelDistribution:
    def test_win_rate_out_of_range_warns(self):
        r = _valid_report()
        r["label_distribution"]["train"]["win_rate"] = 1.5
        warns = validate_training_report(r)
        assert any("win_rate" in w for w in warns)

    def test_missing_n_warns(self):
        r = _valid_report()
        del r["label_distribution"]["train"]["n"]
        warns = validate_training_report(r)
        assert any("label_distribution.train.n" in w for w in warns)


class TestWalkForward:
    def test_invalid_roc_auc_type_warns(self):
        r = _valid_report()
        r["walk_forward"]["roc_auc"] = "high"  # should be float
        warns = validate_training_report(r)
        assert any("roc_auc" in w for w in warns)

    def test_null_roc_auc_ok(self):
        r = _valid_report()
        r["walk_forward"]["roc_auc"] = None
        warns = validate_training_report(r)
        assert not any("roc_auc" in w for w in warns)

    def test_no_walk_forward_key_ok(self):
        r = _valid_report()
        del r["walk_forward"]
        warns = validate_training_report(r)
        assert warns == []


class TestEdgeCases:
    def test_not_a_dict_warns(self):
        warns = validate_training_report("not a dict")  # type: ignore
        assert len(warns) >= 1

    def test_empty_dict_warns(self):
        warns = validate_training_report({})
        assert len(warns) >= 4  # all 4 required top-level keys missing
