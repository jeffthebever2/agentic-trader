"""Tests for GBDT Ensemble and Experiment Tracker — MS-1, MS-2."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.ml.ensemble import SoftVotingEnsemble
from tradingagents.ml.experiment_tracker import ExperimentTracker


# ── SoftVotingEnsemble tests ──────────────────────────────────────────────────

def _mock_clf(proba):
    """Create a mock classifier that returns fixed probabilities."""
    clf = MagicMock()
    clf.predict_proba.return_value = np.array(proba)
    clf.classes_ = np.array([0, 1])
    return clf


def test_ensemble_predict_proba_shape():
    """Ensemble produces [n_samples, n_classes] output."""
    clf1 = _mock_clf([[0.3, 0.7], [0.6, 0.4], [0.4, 0.6]])
    clf2 = _mock_clf([[0.2, 0.8], [0.7, 0.3], [0.5, 0.5]])
    clf3 = _mock_clf([[0.4, 0.6], [0.5, 0.5], [0.3, 0.7]])

    ens = SoftVotingEnsemble([("a", clf1), ("b", clf2), ("c", clf3)])
    X = np.zeros((3, 5))
    proba = ens.predict_proba(X)

    assert proba.shape == (3, 2), f"Expected (3,2), got {proba.shape}"
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6), "Probabilities must sum to 1"
    assert (proba >= 0).all() and (proba <= 1).all(), "Probs must be in [0,1]"


def test_ensemble_averaging():
    """Verify soft vote is mean of member probabilities."""
    p1 = [[0.3, 0.7]]
    p2 = [[0.5, 0.5]]
    clf1 = _mock_clf(p1)
    clf2 = _mock_clf(p2)

    ens = SoftVotingEnsemble([("a", clf1), ("b", clf2)])
    proba = ens.predict_proba(np.zeros((1, 3)))

    expected = np.mean([[0.3, 0.7], [0.5, 0.5]], axis=0)
    np.testing.assert_allclose(proba[0], expected, atol=1e-6)


def test_ensemble_predict_returns_class_labels():
    """predict() returns class array, not probabilities."""
    clf1 = _mock_clf([[0.3, 0.7], [0.8, 0.2]])
    ens = SoftVotingEnsemble([("a", clf1)])
    X = np.zeros((2, 3))
    preds = ens.predict(X)

    assert preds.shape == (2,)
    assert set(preds.tolist()).issubset({0, 1})


def test_ensemble_backward_compat_predict_proba():
    """bundle['models']['win_probability'].predict_proba(X) returns valid output."""
    clf = _mock_clf([[0.4, 0.6], [0.7, 0.3]])
    ens = SoftVotingEnsemble([("xgb", clf)])

    # Simulate bundle access pattern
    bundle = {"models": {"win_probability": ens}}
    model = bundle["models"]["win_probability"]
    proba = model.predict_proba(np.zeros((2, 5)))

    assert proba.shape == (2, 2)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6)


def test_empty_estimators_raises():
    """Empty estimators list raises ValueError."""
    with pytest.raises(ValueError):
        SoftVotingEnsemble([])


# ── ExperimentTracker tests ───────────────────────────────────────────────────

def test_tracker_jsonl_fallback(tmp_path):
    """Without mlflow, writes to JSONL fallback."""
    log_path = tmp_path / "experiment_log.jsonl"
    tracker = ExperimentTracker(tracking_uri=None, fallback_path=str(log_path))
    assert tracker.backend == "jsonl"

    tracker.start_run("test_run")
    tracker.log_param("n_estimators", 100)
    tracker.log_metric("wf_roc", 0.5234)
    tracker.end_run()

    assert log_path.exists(), "JSONL log not written"
    line = json.loads(log_path.read_text().strip().split("\n")[-1])
    assert line["run_name"] == "test_run"
    assert line["params"]["n_estimators"] == 100
    assert abs(line["metrics"]["wf_roc"] - 0.5234) < 1e-6


def test_tracker_context_manager(tmp_path):
    """Context manager form calls end_run automatically."""
    log_path = tmp_path / "exp.jsonl"
    with ExperimentTracker(tracking_uri=None, fallback_path=str(log_path)) as tracker:
        tracker.start_run("ctx_run")
        tracker.log_metric("brier", 0.22)

    assert log_path.exists()


def test_tracker_log_metrics_bulk(tmp_path):
    """log_metrics writes multiple metrics at once."""
    log_path = tmp_path / "exp.jsonl"
    tracker = ExperimentTracker(tracking_uri=None, fallback_path=str(log_path))
    tracker.start_run("bulk")
    tracker.log_metrics({"a": 1.0, "b": 2.0, "c": 3.0})
    tracker.end_run()

    line = json.loads(log_path.read_text().strip())
    assert line["metrics"]["a"] == 1.0
    assert line["metrics"]["c"] == 3.0
