"""Tests for scripts/model_readiness_report.py"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from model_readiness_report import (
    build_report,
    Check,
    ReadinessReport,
    HARD_WF_ROC_MIN,
    SOFT_WF_ROC_MIN,
    _check_bundle_exists,
    _check_report_exists,
    _check_bundle_age,
    _check_wf_roc_hard,
    _check_wf_roc_soft,
    _check_highconf_wr,
    _check_gate_wr,
    MODEL_MAX_AGE_DAYS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_report(wf_roc=0.52, hc_n=5, hc_wr=0.65, gate_wr=0.75) -> dict:
    return {
        "walk_forward": {
            "roc_auc": wf_roc,
            "high_conf_n": hc_n,
            "high_conf_win_rate": hc_wr,
        },
        "gate_analysis": {
            "strategy_comparison": {
                "rule_plus_ml": {"win_rate": gate_wr},
            }
        },
    }


# ── _check_bundle_exists ─────────────────────────────────────────────────────

def test_bundle_exists_pass(tmp_path):
    p = tmp_path / "model.joblib"
    p.touch()
    c = _check_bundle_exists(p)
    assert c.passed and c.hard


def test_bundle_exists_fail(tmp_path):
    c = _check_bundle_exists(tmp_path / "missing.joblib")
    assert not c.passed and c.hard


# ── _check_bundle_age ────────────────────────────────────────────────────────

def test_bundle_age_fresh(tmp_path):
    p = tmp_path / "model.joblib"
    p.touch()
    c = _check_bundle_age(p)
    assert c.passed


def test_bundle_age_missing(tmp_path):
    c = _check_bundle_age(tmp_path / "gone.joblib")
    assert not c.passed and c.hard


# ── _check_wf_roc_hard ────────────────────────────────────────────────────────

def test_wf_roc_hard_pass():
    c = _check_wf_roc_hard({"walk_forward": {"roc_auc": HARD_WF_ROC_MIN + 0.01}})
    assert c.passed and c.hard


def test_wf_roc_hard_fail():
    c = _check_wf_roc_hard({"walk_forward": {"roc_auc": HARD_WF_ROC_MIN - 0.01}})
    assert not c.passed and c.hard


def test_wf_roc_hard_missing_report():
    c = _check_wf_roc_hard(None)
    assert not c.passed and c.hard


def test_wf_roc_hard_missing_key():
    c = _check_wf_roc_hard({"walk_forward": {}})
    assert not c.passed


# ── _check_wf_roc_soft ────────────────────────────────────────────────────────

def test_wf_roc_soft_pass():
    c = _check_wf_roc_soft({"walk_forward": {"roc_auc": SOFT_WF_ROC_MIN + 0.01}})
    assert c.passed and not c.hard


def test_wf_roc_soft_fail():
    c = _check_wf_roc_soft({"walk_forward": {"roc_auc": SOFT_WF_ROC_MIN - 0.01}})
    assert not c.passed and not c.hard


# ── _check_highconf_wr ────────────────────────────────────────────────────────

def test_highconf_wr_skipped_when_few_trades():
    c = _check_highconf_wr({"walk_forward": {"high_conf_n": 5, "high_conf_win_rate": 0.3}})
    assert c.passed  # skipped, not a failure


def test_highconf_wr_pass():
    c = _check_highconf_wr({"walk_forward": {"high_conf_n": 20, "high_conf_win_rate": 0.70}})
    assert c.passed


def test_highconf_wr_fail():
    c = _check_highconf_wr({"walk_forward": {"high_conf_n": 20, "high_conf_win_rate": 0.45}})
    assert not c.passed


# ── build_report ─────────────────────────────────────────────────────────────

def test_build_report_ready(tmp_path):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report(wf_roc=0.53, hc_n=5, gate_wr=0.75)))
    paper_dir = tmp_path / "paper_accounts"
    paper_dir.mkdir()
    rpt = build_report(bundle, rpt_path, paper_dir)
    assert rpt.verdict == "READY"


def test_build_report_not_ready_missing_bundle(tmp_path):
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report()))
    rpt = build_report(tmp_path / "missing.joblib", rpt_path, tmp_path)
    assert rpt.verdict == "NOT_READY"


def test_build_report_not_ready_low_roc(tmp_path):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report(wf_roc=0.48)))
    rpt = build_report(bundle, rpt_path, tmp_path)
    assert rpt.verdict == "NOT_READY"


def test_build_report_degraded_soft_fail(tmp_path):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report(wf_roc=0.505, hc_n=20, hc_wr=0.45)))
    rpt = build_report(bundle, rpt_path, tmp_path)
    assert rpt.verdict == "DEGRADED"


def test_build_report_has_all_check_ids(tmp_path):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report()))
    rpt = build_report(bundle, rpt_path, tmp_path)
    ids = {c.id for c in rpt.checks}
    assert {"H1", "H2", "H3", "H4", "S1", "S2", "S3", "S4"} <= ids


def test_build_report_to_dict_is_json_serializable(tmp_path):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(_make_report()))
    rpt = build_report(bundle, rpt_path, tmp_path)
    json.dumps(rpt.to_dict())  # must not raise


# ── CLI smoke test ────────────────────────────────────────────────────────────

def test_cli_runs_without_crash():
    result = subprocess.run(
        [sys.executable, "scripts/model_readiness_report.py", "--json"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode in (0, 1)
    data = json.loads(result.stdout)
    assert "verdict" in data
    assert data["verdict"] in ("READY", "DEGRADED", "NOT_READY")
