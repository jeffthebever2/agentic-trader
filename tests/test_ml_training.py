import json
from types import SimpleNamespace

import pandas as pd
import pytest
import joblib

import backtest
from scripts import train_ml_models


@pytest.mark.unit
def test_train_models_writes_artifacts_into_report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(train_ml_models, "_XGB_AVAILABLE", False)
    rows = []
    for i in range(110):
        year = 2024 if i < 80 else 2025
        day = (i % 28) + 1
        ret = 0.04 if i % 2 == 0 else -0.04
        rows.append(
            {
                "ticker": f"T{i:03d}",
                "scan_date": f"{year}-01-{day:02d}",
                "year": year,
                "month": f"{year}-01",
                "score": 70 + (i % 30),
                "atr_pct": 0.03 + (i % 5) * 0.001,
                "vix_ts": None,
                "candidate_status": "executed",
                "day_of_week": i % 5,
                "h3_return": ret,
                "h3_outcome": "TARGET_HIT" if ret > 0 else "STOP_HIT",
                "h3_mae": abs(min(ret, 0)),
                "h3_mfe": max(ret, 0),
            }
        )
    source = tmp_path / "trades.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    report = train_ml_models.train_models(
        SimpleNamespace(
            input=str(source),
            output_dir=str(tmp_path / "model"),
            hold=3,
            max_rows=0,
            min_rows=20,
            n_estimators=3,
            max_depth=2,
            min_samples_leaf=2,
            seed=42,
            ml_probability_threshold=0.58,
            ml_expected_return_min=0.0,
            ml_large_loss_max=0.20,
            gate_diagnostics_limit=5,
        )
    )

    saved = json.loads((tmp_path / "model" / "training_report.json").read_text())
    assert saved["artifacts"] == report["artifacts"]
    assert saved["artifacts"]["model_bundle"].endswith("model_bundle.joblib")
    bundle = joblib.load(tmp_path / "model" / "model_bundle.joblib")
    assert "vix_ts" in bundle["feature_names"]
    assert bundle["models"]["win_probability"].n_features_in_ == len(bundle["feature_names"])


@pytest.mark.unit
def test_train_models_requires_qlib_columns_when_flag_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(train_ml_models, "_XGB_AVAILABLE", False)
    rows = []
    for i in range(40):
        rows.append(
            {
                "ticker": f"T{i:03d}",
                "scan_date": f"2024-01-{(i % 28) + 1:02d}",
                "year": 2024,
                "month": "2024-01",
                "score": 70 + (i % 10),
                "atr_pct": 0.03,
                "candidate_status": "executed",
                "h3_return": 0.03 if i % 2 == 0 else -0.03,
                "h3_outcome": "TARGET_HIT" if i % 2 == 0 else "STOP_HIT",
            }
        )
    source = tmp_path / "trades.csv"
    pd.DataFrame(rows).to_csv(source, index=False)

    with pytest.raises(SystemExit, match="no qlib_\\* columns"):
        train_ml_models.train_models(
            SimpleNamespace(
                input=str(source),
                output_dir=str(tmp_path / "model"),
                hold=3,
                max_rows=0,
                min_rows=20,
                n_estimators=3,
                max_depth=2,
                min_samples_leaf=2,
                seed=42,
                ml_probability_threshold=0.58,
                ml_expected_return_min=0.0,
                ml_large_loss_max=0.20,
                gate_diagnostics_limit=5,
                include_qlib_features=True,
            )
        )


@pytest.mark.unit
def test_ml_strategy_comparison_honors_expected_return_gate():
    frame = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "scan_date": "2025-01-02",
                "candidate_status": "executed",
                "score": 100,
                "entry": 10.0,
                "h3_return": 0.04,
                "h3_outcome": "TARGET_HIT",
                "h3_exit_date": "2025-01-06",
            },
            {
                "ticker": "BBB",
                "scan_date": "2025-01-03",
                "candidate_status": "executed",
                "score": 100,
                "entry": 10.0,
                "h3_return": -0.04,
                "h3_outcome": "STOP_HIT",
                "h3_exit_date": "2025-01-07",
            },
        ]
    )

    result = backtest._ml_strategy_comparison(
        frame,
        win_prob=[0.8, 0.8],
        loss_prob=[0.1, 0.1],
        expected_return=[0.03, -0.02],
        hold=3,
        ml_prob_threshold=0.6,
        ml_expected_return_min=0.0,
        ml_large_loss_max=0.2,
    )

    assert result["rule_only_strategy"]["trades"] == 2
    assert result["ml_filter_strategy"]["trades"] == 1
    assert result["ml_filter_strategy"]["win_rate"] == 1.0


@pytest.mark.unit
def test_backtest_defaults_use_honest_ml_walk_forward():
    args = SimpleNamespace(primary_hold=3, hold_periods=[3], grid_search=False, threshold=100)

    backtest.apply_arg_defaults(args)

    assert args.ml_walk_forward is True
    assert args.account_sizing_mode == "fixed"
