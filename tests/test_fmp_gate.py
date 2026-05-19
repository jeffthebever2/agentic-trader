import json
from unittest.mock import MagicMock

import pytest

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.fmp import FMPGate, get_income_statement


def _cfg(tmp_path):
    return {
        "data_cache_dir": str(tmp_path),
        "fmp_enabled": True,
        "fmp_daily_limit": 3,
        "fmp_reserve_calls": 1,
        "alt_data_config_path": str(tmp_path / "missing.json"),
        "data_vendors": {},
        "tool_vendors": {},
    }


@pytest.mark.unit
def test_fmp_gate_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    gate = FMPGate(_cfg(tmp_path))
    ok, reason = gate.can_call()
    assert not ok
    assert "not configured" in reason


@pytest.mark.unit
def test_fmp_request_uses_cache_and_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    set_config(_cfg(tmp_path))

    response = MagicMock()
    response.json.return_value = [{"date": "2026-01-01", "revenue": 1}]
    response.raise_for_status.return_value = None
    monkeypatch.setattr("tradingagents.dataflows.fmp.requests.get", lambda *a, **k: response)

    first = get_income_statement("AAPL")
    second = get_income_statement("AAPL")

    assert "FMP income statement" in first
    assert first == second
    quota_files = list((tmp_path / "fmp").glob("quota-*.json"))
    assert quota_files
    assert json.loads(quota_files[0].read_text())["used"] == 1


@pytest.mark.unit
def test_fmp_gate_honors_reserve(tmp_path, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    gate = FMPGate(_cfg(tmp_path))
    gate.increment()
    gate.increment()
    ok, reason = gate.can_call()
    assert not ok
    assert "reserve" in reason
