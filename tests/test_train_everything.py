import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.train_everything import (
    TrainingRun,
    _validate_ml_artifacts,
    _validate_qlib_forward_evidence,
    _validate_qlib_research_reports,
)
from scripts.retrain_weekly import _merge_qlib_features_into_csv


def _write_report(model_dir: Path, *, wf_roc=0.51, leakage=None, qlib_features=None):
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model_bundle.joblib").write_bytes(b"bundle")
    report = {
        "settings": {"rows_used": 500, "hold": 3, "feature_count": 2},
        "label_distribution": {"train": {"n": 300}, "test": {"n": 100}},
        "models": {"win_probability": {"metrics": {"roc_auc": 0.52}}},
        "leakage_check": leakage or {"status": "clean", "leaky_features": []},
        "walk_forward": {"roc_auc": wf_roc, "high_conf_win_rate": 0.61},
        "qlib_features": qlib_features or {},
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
        include_qlib_features=False,
        qlib_start="2020-01-01",
        qlib_end="2024-12-31",
        qlib_forward_days=5,
        qlib_max_tickers=50,
        qlib_max_tickers_ic=20,
        qlib_max_tickers_tournament=10,
        qlib_skip_ic=False,
        qlib_skip_tournament=False,
    )
    run = TrainingRun(args)

    cmd = run.command_retrain_weekly()

    assert "--output-dir" in cmd
    output_dir = cmd[cmd.index("--output-dir") + 1]
    assert "tmp/train_everything/unit_test/staging/latest" in output_dir
    assert output_dir != "ml_models/latest"
    assert "--compute-dsr" in cmd
    assert "--cpcv" in cmd


def test_default_run_ids_include_subsecond_entropy():
    args = SimpleNamespace(
        resume="",
        run_id="",
        profile="safe",
        dry_run=True,
        force_stage=False,
    )

    run_a = TrainingRun(args)
    run_b = TrainingRun(args)

    assert run_a.run_id != run_b.run_id
    assert len(run_a.run_id.split("_")) == 3


def test_train_everything_passes_qlib_flag_to_both_ml_training_paths():
    args = SimpleNamespace(
        resume="",
        run_id="unit_test_qlib",
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
        include_qlib_features=True,
        tickers="all_tickers.txt",
        stock_start="2019-01-01",
        stock_end="2024-12-31",
        stock_hold=3,
        target_mult=1.2,
        stop_mult=1.0,
        label_mode="triple_barrier",
        label_slippage_bps=10.0,
        rebuild_stock_dataset=False,
        max_tickers=None,
        qlib_start="2020-01-01",
        qlib_end="2024-12-31",
        qlib_forward_days=5,
        qlib_max_tickers=50,
        qlib_max_tickers_ic=20,
        qlib_max_tickers_tournament=10,
        qlib_skip_ic=False,
        qlib_skip_tournament=False,
    )
    run = TrainingRun(args)

    production_cmd = run.command_retrain_weekly()
    stock_cmd, _ = run.command_stock_universe()

    assert "--include-qlib-features" in production_cmd
    assert "--include-qlib-features" in stock_cmd


def test_qlib_research_command_writes_to_run_report_dir():
    args = SimpleNamespace(
        resume="",
        run_id="unit_test_qlib_research",
        profile="safe",
        dry_run=True,
        force_stage=False,
        tickers="all_tickers.txt",
        qlib_start="2020-01-01",
        qlib_end="2024-12-31",
        qlib_forward_days=5,
        qlib_max_tickers=50,
        qlib_max_tickers_ic=20,
        qlib_max_tickers_tournament=10,
        qlib_skip_ic=False,
        qlib_skip_tournament=False,
    )
    run = TrainingRun(args)

    cmd, report_dir = run.command_qlib_research()

    assert "scripts/qlib_research.py" in cmd
    assert "--report-dir" in cmd
    assert str(report_dir) == cmd[cmd.index("--report-dir") + 1]
    assert "tmp/train_everything/unit_test_qlib_research/qlib_reports" in str(report_dir)
    assert "--tickers-file" in cmd
    assert "--max-tickers-tournament" in cmd


def test_validate_qlib_research_reports_passes_research_only_report(tmp_path):
    report_dir = tmp_path / "qlib_reports"
    report_dir.mkdir()
    report = {
        "status": "OK",
        "qlib_version": "0.9.8",
        "leakage_safe": True,
        "production_ready": False,
        "factor_ic": {"aggregated": {"qlib_mom_63": {"mean_ic_across_tickers": 0.01}}},
        "tournament": {"best_model": "RandomForest", "best_wf_roc": 0.51},
    }
    (report_dir / "20260607_120000_qlib_research.json").write_text(json.dumps(report))

    result = _validate_qlib_research_reports(report_dir)

    assert result["status"] == "OK"
    assert result["leakage_safe"] is True
    assert result["production_ready"] is False
    assert result["factor_count"] == 1


def test_validate_qlib_research_reports_rejects_blocked(tmp_path):
    report_dir = tmp_path / "qlib_reports"
    report_dir.mkdir()
    report = {
        "status": "BLOCKED",
        "blocker": "qlib import failed",
        "leakage_safe": True,
        "production_ready": False,
    }
    (report_dir / "20260607_120000_qlib_research.json").write_text(json.dumps(report))

    with pytest.raises(RuntimeError, match="blocked"):
        _validate_qlib_research_reports(report_dir)


def _write_qlib_evidence(qlib_dir: Path, *, returns: list[float]) -> None:
    import datetime as dt
    from tradingagents.portfolio.prediction_ledger import PredictionLedger

    ledger = PredictionLedger(qlib_dir / "prediction_ledger.jsonl")
    event_rows = []
    for idx, ret in enumerate(returns):
        ticker = f"T{idx:03d}"
        entry_time = f"2026-06-{idx + 1:02d}T14:00:00"
        exit_time = f"2026-06-{idx + 2:02d}T14:00:00"
        ledger.log(ticker, "BUY", now=dt.datetime(2026, 6, 1, 14, 0) + dt.timedelta(days=idx))
        event_rows.append(json.dumps({
            "timestamp": entry_time,
            "type": "BUY",
            "ticker": ticker,
            "entry_time": entry_time,
            "entry_price": 100.0,
            "alpha_score": 0.9,
            "alpha_tier": "QLIB",
            "model_version": "qlib_factor_v1",
        }))
        event_rows.append(json.dumps({
            "timestamp": exit_time,
            "type": "SELL",
            "ticker": ticker,
            "entry_time": entry_time,
            "pnl_pct": ret,
            "exit_reason": "TAKE_PROFIT" if ret > 0 else "STOP_LOSS",
            "target_hit": ret > 0,
            "stop_hit": ret <= 0,
        }))
    (qlib_dir / "events.jsonl").write_text("\n".join(event_rows), encoding="utf-8")


def test_validate_qlib_forward_evidence_rejects_missing_ledger(tmp_path):
    with pytest.raises(RuntimeError, match="ledger missing"):
        _validate_qlib_forward_evidence(tmp_path / "qlib", min_grades=1)


def test_validate_qlib_forward_evidence_rejects_insufficient_grades(tmp_path):
    qlib_dir = tmp_path / "qlib"
    _write_qlib_evidence(qlib_dir, returns=[0.02])

    with pytest.raises(RuntimeError, match="requires"):
        _validate_qlib_forward_evidence(qlib_dir, min_grades=2)


def test_validate_qlib_forward_evidence_passes_with_thresholds(tmp_path):
    qlib_dir = tmp_path / "qlib"
    _write_qlib_evidence(qlib_dir, returns=[0.03, 0.02, -0.01, 0.04])

    result = _validate_qlib_forward_evidence(
        qlib_dir,
        min_grades=4,
        min_win_rate=0.50,
        min_avg_return=0.0,
    )

    assert result["n_grades"] == 4
    assert result["win_rate"] == pytest.approx(0.75)
    assert result["avg_return"] == pytest.approx(0.02)


def test_qlib_feature_model_promotion_requires_forward_evidence(tmp_path):
    staging = tmp_path / "staging"
    _write_report(staging, qlib_features={"requested": True, "used": True, "columns": ["qlib_mom_63"]})
    args = SimpleNamespace(
        resume="",
        run_id="unit_test_qlib_gate",
        profile="safe",
        dry_run=True,
        force_stage=False,
        min_roc=0.49,
        qlib_forward_evidence_warning_only=False,
        qlib_paper_dir=str(tmp_path / "missing_qlib"),
        qlib_min_forward_grades=1,
        qlib_min_forward_win_rate=0.50,
        qlib_min_forward_avg_return=0.0,
    )
    run = TrainingRun(args)

    with pytest.raises(RuntimeError, match="Qlib forward-evidence ledger missing"):
        run._validate_and_promote_ml(staging, tmp_path / "target")


def test_qlib_feature_model_promotion_accepts_forward_evidence(tmp_path):
    staging = tmp_path / "staging"
    _write_report(staging, qlib_features={"requested": True, "used": True, "columns": ["qlib_mom_63"]})
    qlib_dir = tmp_path / "qlib"
    _write_qlib_evidence(qlib_dir, returns=[0.03])
    args = SimpleNamespace(
        resume="",
        run_id="unit_test_qlib_gate_pass",
        profile="safe",
        dry_run=True,
        force_stage=False,
        min_roc=0.49,
        qlib_forward_evidence_warning_only=False,
        qlib_paper_dir=str(qlib_dir),
        qlib_min_forward_grades=1,
        qlib_min_forward_win_rate=0.50,
        qlib_min_forward_avg_return=0.0,
    )
    run = TrainingRun(args)

    result = run._validate_and_promote_ml(staging, tmp_path / "target")

    assert result["validation"]["qlib_forward_evidence"]["n_grades"] == 1


def test_retrain_qlib_enrichment_writes_features_and_summary(tmp_path, monkeypatch):
    csv_path = tmp_path / "trades.csv"
    csv_path.write_text(
        "ticker,scan_date,score,h3_return,h3_outcome\n"
        "AAPL,2021-02-01,70,0.01,TARGET_HIT\n"
        "AAPL,2021-03-01,71,-0.01,STOPPED_OUT\n"
        "MSFT,2021-02-01,72,0.02,TARGET_HIT\n"
        "MSFT,2021-03-01,73,-0.02,STOPPED_OUT\n"
    )

    import backtest
    import numpy as np
    import pandas as pd

    def fake_download_all(tickers, start, end, batch_size=50, threads=False):
        idx = pd.bdate_range("2019-01-01", "2021-03-15")
        data = {}
        for i, ticker in enumerate(tickers):
            base = 100.0 + i * 10
            close = pd.Series(base + np.arange(len(idx)) * 0.1, index=idx)
            data[ticker] = pd.DataFrame(
                {
                    "Open": close,
                    "High": close * 1.01,
                    "Low": close * 0.99,
                    "Close": close,
                    "Volume": 1_000_000,
                },
                index=idx,
            )
        return data

    monkeypatch.setattr(backtest, "download_all", fake_download_all)

    summary = _merge_qlib_features_into_csv(csv_path)

    enriched = pd.read_csv(csv_path)
    assert "qlib_mom_63" in enriched.columns
    assert enriched["qlib_mom_63"].notna().any()
    assert summary["lag_days"] == 1
    assert Path(summary["summary_path"]).exists()


# ── _check_report_gates schema enforcement ────────────────────────────────────

def _write_gate_report(model_dir: Path, *, wf_roc: float = 0.51, extra: dict | None = None) -> Path:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model_bundle.joblib").write_bytes(b"bundle")
    report = {
        "settings": {
            "source": "test",
            "hold": "2024-01-01",
            "rows_used": 500,
            "train_rows": 350,
            "test_rows": 100,
            "feature_count": 10,
            "calibrated": True,
        },
        "label_distribution": {
            "train": {"n": 350, "win_rate": 0.55},
            "test": {"n": 100, "win_rate": 0.52},
        },
        "models": {
            "win_probability": {
                "metrics": {"roc_auc": wf_roc},
                "calibration": {"brier_after": 0.22},
            }
        },
        "leakage_check": {"status": "clean", "leaky_features": []},
        "walk_forward": {"roc_auc": wf_roc},
    }
    if extra:
        report.update(extra)
    rp = model_dir / "training_report.json"
    rp.write_text(json.dumps(report))
    return rp


def test_check_report_gates_passes_valid_report(tmp_path):
    from scripts.retrain_weekly import _check_report_gates
    rp = _write_gate_report(tmp_path)
    passed, reason = _check_report_gates(rp, min_roc=0.49, max_brier=0.25)
    assert passed, f"Expected gate to pass, got: {reason}"


def test_check_report_gates_fails_low_roc(tmp_path):
    from scripts.retrain_weekly import _check_report_gates
    rp = _write_gate_report(tmp_path, wf_roc=0.47)
    passed, reason = _check_report_gates(rp, min_roc=0.49, max_brier=0.25)
    assert not passed
    assert "0.47" in reason


def test_check_report_gates_blocks_on_schema_failures(tmp_path):
    """Schema failures (missing required keys) must block deployment gate."""
    from scripts.retrain_weekly import _check_report_gates
    # Write a report missing required top-level keys
    rp = tmp_path / "training_report.json"
    rp.write_text(json.dumps({
        "walk_forward": {"roc_auc": 0.52},
        # Missing: settings, label_distribution, models, leakage_check
    }))
    passed, reason = _check_report_gates(rp, min_roc=0.49, max_brier=0.25)
    assert not passed, "Gate should fail when schema keys are missing"
    assert any(kw in reason.lower() for kw in ("missing", "settings", "models", "leakage")), \
        f"Expected schema failure in reason, got: {reason}"


def test_check_report_gates_schema_blocks_even_if_roc_passes(tmp_path):
    """High ROC should not override schema failures."""
    from scripts.retrain_weekly import _check_report_gates
    rp = tmp_path / "training_report.json"
    rp.write_text(json.dumps({
        "walk_forward": {"roc_auc": 0.75},
        # Missing settings, label_distribution, models, leakage_check
    }))
    passed, reason = _check_report_gates(rp, min_roc=0.49, max_brier=0.25)
    assert not passed, "High ROC should not bypass schema gate"
