"""Tests for scripts/daily_audit.py"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from daily_audit import (
    run_audit,
    AuditReport,
    Finding,
    PASS,
    WARN,
    FAIL,
    _audit_change_control,
    _audit_prediction_ledger,
)


def _make_bundle_and_report(tmp_path, wf_roc=0.52, hc_n=5):
    bundle = tmp_path / "model.joblib"
    bundle.touch()
    report = {
        "walk_forward": {"roc_auc": wf_roc, "high_conf_n": hc_n, "high_conf_win_rate": 0.65},
        "gate_analysis": {"strategy_comparison": {"rule_plus_ml": {"win_rate": 0.80}}},
    }
    rpt_path = tmp_path / "training_report.json"
    rpt_path.write_text(json.dumps(report))
    return bundle, rpt_path


# ── AuditReport ───────────────────────────────────────────────────────────────

def test_audit_report_pass_when_all_ok(tmp_path):
    import datetime as dt
    from tradingagents.portfolio.prediction_ledger import PredictionLedger
    bundle, rpt_path = _make_bundle_and_report(tmp_path)
    paper_dir = tmp_path / "paper_accounts"
    paper_dir.mkdir()
    # Create ledger so LEDGER_MISSING doesn't fire
    PredictionLedger(paper_dir / "prediction_ledger.jsonl").log(
        "AAPL", "BUY", now=dt.datetime(2026, 6, 6, 14, 0)
    )
    cc_path = tmp_path / "cc.jsonl"
    rpt = run_audit(bundle, rpt_path, paper_dir, cc_path)
    assert rpt.overall == PASS


def test_audit_report_warn_on_model_degraded(tmp_path):
    bundle, rpt_path = _make_bundle_and_report(tmp_path, wf_roc=0.52, hc_n=20)
    # Overwrite with a report that fails the high_conf gate
    report = {
        "walk_forward": {"roc_auc": 0.52, "high_conf_n": 20, "high_conf_win_rate": 0.40},
        "gate_analysis": {"strategy_comparison": {"rule_plus_ml": {"win_rate": 0.80}}},
    }
    rpt_path.write_text(json.dumps(report))
    paper_dir = tmp_path / "paper_accounts"
    paper_dir.mkdir()
    cc_path = tmp_path / "cc.jsonl"
    rpt = run_audit(bundle, rpt_path, paper_dir, cc_path)
    assert rpt.overall in (WARN, FAIL)


def test_audit_report_fail_on_missing_bundle(tmp_path):
    _, rpt_path = _make_bundle_and_report(tmp_path)
    paper_dir = tmp_path / "paper_accounts"
    paper_dir.mkdir()
    cc_path = tmp_path / "cc.jsonl"
    rpt = run_audit(tmp_path / "missing.joblib", rpt_path, paper_dir, cc_path)
    assert rpt.overall == FAIL


def test_audit_report_has_findings(tmp_path):
    bundle, rpt_path = _make_bundle_and_report(tmp_path)
    rpt = run_audit(bundle, rpt_path, tmp_path, tmp_path / "cc.jsonl")
    assert len(rpt.findings) > 0


def test_audit_report_to_dict_json_safe(tmp_path):
    bundle, rpt_path = _make_bundle_and_report(tmp_path)
    rpt = run_audit(bundle, rpt_path, tmp_path, tmp_path / "cc.jsonl")
    json.dumps(rpt.to_dict())  # must not raise


# ── _audit_change_control ─────────────────────────────────────────────────────

def test_cc_audit_clear(tmp_path):
    cc_path = tmp_path / "cc.jsonl"
    findings = _audit_change_control(cc_path)
    assert any(f.id == "CC_CLEAR" and f.severity == PASS for f in findings)


def test_cc_audit_pending(tmp_path):
    from tradingagents.portfolio.change_control import ChangeControl
    import datetime as dt
    cc = ChangeControl(tmp_path / "cc.jsonl")
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "test",
                now=dt.datetime(2026, 6, 6, 12, 0))
    findings = _audit_change_control(tmp_path / "cc.jsonl")
    assert any(f.id == "CC_PENDING" and f.severity == WARN for f in findings)


# ── _audit_prediction_ledger ──────────────────────────────────────────────────

def test_ledger_missing_warns(tmp_path):
    findings = _audit_prediction_ledger(tmp_path / "nope")
    assert any(f.id == "LEDGER_MISSING" and f.severity == WARN for f in findings)


def test_ledger_present_passes(tmp_path):
    import datetime as dt
    from tradingagents.portfolio.prediction_ledger import PredictionLedger
    ledger = PredictionLedger(tmp_path / "prediction_ledger.jsonl")
    ledger.log("AAPL", "BUY", now=dt.datetime(2026, 6, 6, 14, 0))
    findings = _audit_prediction_ledger(tmp_path)
    assert any(f.id == "LEDGER_OK" and f.severity == PASS for f in findings)


# ── CLI smoke test ────────────────────────────────────────────────────────────

def test_cli_runs_json(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/daily_audit.py", "--json"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode in (0, 1, 2)
    data = json.loads(result.stdout)
    assert "overall" in data
    assert data["overall"] in (PASS, WARN, FAIL)
    assert "findings" in data


def test_cli_strict_exits_nonzero_on_fail(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/daily_audit.py",
         "--bundle", "ml_models/NONEXISTENT/model.joblib",
         "--report", "ml_models/NONEXISTENT/report.json",
         "--strict"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode != 0
