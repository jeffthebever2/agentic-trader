"""P0 fixes from the 2026-07-05 full-system audit.

1. Trade-ticket account selection is fail-closed: selection or read-back failure
   aborts the order instead of falling through to the broker default account.
2. _save_signals never reverts a terminal signal status (approved/skipped/…)
   back to pending — a scan's stale in-memory snapshot could otherwise resurrect
   an already-approved signal and invite a double buy.
3. Model-age halt is demoted to a warning when the ML gate is in shadow mode —
   a model outside the decision path must not freeze rule-based entries.
4. Retrain deploy gate hard-blocks bundles without real walk-forward evidence.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

import web.api.fidelity as fid
import web.api.thematic_auto as ta
from tradingagents.portfolio.production_safety import ModelHealthChecker


# ── 1. _select_and_verify_account ────────────────────────────────────────────

class _FakeLocator:
    def __init__(self, page, text=""):
        self._page = page
        self._text = text

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        if self._page.click_raises:
            raise RuntimeError("dropdown gone")

    async def inner_text(self, timeout=None):
        if self._page.readback_raises:
            raise RuntimeError("no text")
        return self._page.readback_text


class _FakePage:
    def __init__(self, readback_text="", click_raises=False, readback_raises=False):
        self.readback_text = readback_text
        self.click_raises = click_raises
        self.readback_raises = readback_raises

    def locator(self, sel):
        return _FakeLocator(self, self.readback_text)


def _run(coro):
    return asyncio.run(coro)


def test_select_account_none_is_noop():
    _run(fid._select_and_verify_account(_FakePage(), None))


def test_select_account_click_failure_aborts():
    page = _FakePage(click_raises=True)
    with pytest.raises(HTTPException) as ei:
        _run(fid._select_and_verify_account(page, "123456789"))
    assert ei.value.status_code == 502


def test_select_account_readback_mismatch_aborts():
    # Ticket still shows a different account after the selection click.
    page = _FakePage(readback_text="Roth IRA (X987654321)")
    with pytest.raises(HTTPException) as ei:
        _run(fid._select_and_verify_account(page, "123456789"))
    assert ei.value.status_code == 502


def test_select_account_readback_unreadable_aborts():
    # Fail-closed: cannot verify → do not trade.
    page = _FakePage(readback_raises=True)
    with pytest.raises(HTTPException) as ei:
        _run(fid._select_and_verify_account(page, "123456789"))
    assert ei.value.status_code == 502


def test_select_account_full_number_match_ok():
    page = _FakePage(readback_text="Individual (123456789)")
    _run(fid._select_and_verify_account(page, "123456789"))


def test_select_account_last4_match_ok():
    page = _FakePage(readback_text="Individual ...6789")
    _run(fid._select_and_verify_account(page, "123456789"))


# ── 2. _save_signals terminal-status merge ───────────────────────────────────

def _patch_signals_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "SIGNALS_FILE", tmp_path / "thematic_signals.json")


def test_save_signals_keeps_terminal_status(monkeypatch, tmp_path):
    _patch_signals_file(monkeypatch, tmp_path)
    ta._save_signals({"signals": [{"id": "s1", "ticker": "NVDA", "status": "approved"}]})
    # Scan's stale snapshot still thinks s1 is pending.
    ta._save_signals({"signals": [{"id": "s1", "ticker": "NVDA", "status": "pending"}]})
    saved = json.loads((tmp_path / "thematic_signals.json").read_text())
    assert saved["signals"][0]["status"] == "approved"


def test_save_signals_pending_stays_pending(monkeypatch, tmp_path):
    _patch_signals_file(monkeypatch, tmp_path)
    ta._save_signals({"signals": [{"id": "s1", "status": "pending"}]})
    ta._save_signals({"signals": [{"id": "s1", "status": "pending", "score": 80}]})
    saved = json.loads((tmp_path / "thematic_signals.json").read_text())
    assert saved["signals"][0]["status"] == "pending"
    assert saved["signals"][0]["score"] == 80


def test_save_signals_terminal_update_wins(monkeypatch, tmp_path):
    # A writer moving pending → skipped must not be blocked by the merge.
    _patch_signals_file(monkeypatch, tmp_path)
    ta._save_signals({"signals": [{"id": "s1", "status": "pending"}]})
    ta._save_signals({"signals": [{"id": "s1", "status": "skipped"}]})
    saved = json.loads((tmp_path / "thematic_signals.json").read_text())
    assert saved["signals"][0]["status"] == "skipped"


# ── 3. shadow-mode model-age halt demotion ───────────────────────────────────

_OLD_BUNDLE = {"created_at": "2020-01-01T00:00:00", "numeric_features": ["a"]}


def test_model_age_halts_when_enforcing():
    res = ModelHealthChecker(max_age_days=45).check(bundle=_OLD_BUNDLE, ml_enforcing=True)
    assert any(r.startswith("model_too_old") for r in res["halt_reasons"])


def test_model_age_warns_only_in_shadow():
    res = ModelHealthChecker(max_age_days=45).check(bundle=_OLD_BUNDLE, ml_enforcing=False)
    assert not any(r.startswith("model_too_old") for r in res["halt_reasons"])
    assert any("WARN_model_too_old_shadow" in r for r in res["warn_reasons"])


# ── 4. retrain gate walk-forward hard block ──────────────────────────────────

def _gate(tmp_path, report):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "retrain_weekly", Path(__file__).resolve().parents[1] / "scripts" / "retrain_weekly.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "training_report.json"
    p.write_text(json.dumps(report))
    return mod._check_report_gates(p, min_roc=0.49, max_brier=0.30)


def _base_report(**wf):
    return {
        "models": {"win_probability": {
            "metrics": {"roc_auc": 0.55},
            "calibration": {"brier_after": 0.20},
        }},
        "settings": {
            "calibrated": True, "source": "test", "hold": 15,
            "rows_used": 1000, "train_rows": 800, "test_rows": 200,
            "feature_count": 40,
        },
        "label_distribution": {
            "train": {"n": 800, "win_rate": 0.45},
            "test": {"n": 200, "win_rate": 0.44},
        },
        "leakage_check": {"status": "clean"},
        "feature_psi": {"n_fail": 0},
        "walk_forward": wf,
    }


def test_gate_blocks_missing_walk_forward(tmp_path):
    ok, reason = _gate(tmp_path, _base_report(status="insufficient_oos_data", n_oos=0))
    assert not ok
    assert "walk_forward validation missing/failed" in reason


def test_gate_blocks_tiny_oos(tmp_path):
    ok, reason = _gate(tmp_path, _base_report(status="ok", roc_auc=0.55, n_oos=10))
    assert not ok
    assert "insufficient OOS rows" in reason


def test_gate_passes_real_walk_forward(tmp_path):
    ok, reason = _gate(tmp_path, _base_report(status="ok", roc_auc=0.55, n_oos=2000))
    assert ok, reason
