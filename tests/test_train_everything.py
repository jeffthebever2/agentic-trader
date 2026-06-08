import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.train_everything import TrainingRun, _validate_ml_artifacts


def _write_report(model_dir: Path, *, wf_roc=0.51, leakage=None):
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model_bundle.joblib").write_bytes(b"bundle")
    report = {
        "settings": {"rows_used": 500, "hold": 3, "feature_count": 2},
        "label_distribution": {"train": {"n": 300}, "test": {"n": 100}},
        "models": {"win_probability": {"metrics": {"roc_auc": 0.52}}},
        "leakage_check": leakage or {"status": "clean", "leaky_features": []},
        "walk_forward": {"roc_auc": wf_roc, "high_conf_win_rate": 0.61},
    }
    (model_dir / "training_report.json").write_text(json.dumps(report))


def test_validate_ml_artifacts_passes_clean_report(tmp_path):
    model_dir = tmp_path / "model"
    _write_report(model_dir)

    result = _validate_ml_artifacts(model_dir)

    assert result["wf_roc"] == 0.51
    assert result["rows_used"] == 500


def test_validate_ml_artifacts_rejects_leakage(tmp_path):
    model_dir = tmp_path / "model"
    _write_report(model_dir, leakage={"status": "failed", "leaky_features": ["h3_return"]})

    with pytest.raises(RuntimeError, match="leakage"):
        _validate_ml_artifacts(model_dir)


def test_validate_ml_artifacts_rejects_low_walk_forward_roc(tmp_path):
    model_dir = tmp_path / "model"
    _write_report(model_dir, wf_roc=0.48)

    with pytest.raises(RuntimeError, match="walk-forward ROC"):
        _validate_ml_artifacts(model_dir, min_wf_roc=0.49)


def test_production_retrain_command_uses_staging_output():
    args = SimpleNamespace(
        resume="",
        run_id="unit_test",
        profile="safe",
        dry_run=True,
        force_stage=False,
        months=84,
        production_hold=10,
        min_roc=0.49,
        max_brier=0.25,
        account_commission=1.0,
        account_slippage_bps=5.0,
        dsr_n_trials=50,
        cpcv=True,
        cpcv_splits=5,
        cpcv_test_splits=2,
        noise_feature_test=True,
        production_resume_csv="",
        skip_holdout=True,
    )
    run = TrainingRun(args)

    cmd = run.command_retrain_weekly()

    assert "--output-dir" in cmd
    output_dir = cmd[cmd.index("--output-dir") + 1]
    assert "tmp/train_everything/unit_test/staging/latest" in output_dir
    assert output_dir != "ml_models/latest"
    assert "--compute-dsr" in cmd
    assert "--cpcv" in cmd
