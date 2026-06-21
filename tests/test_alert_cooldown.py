"""Per-ticker alert cooldown — the core anti-spam primitive for HIL alerts.

Suppresses a repeat alert for the same (scope, ticker) within ALERT_COOLDOWN_HOURS
unless the action kind changed or the score moved by >= ALERT_RESCORE_DELTA.
"""
import importlib

import pytest

import web.alert_cooldown as ac


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_FILE", tmp_path / "ac.json")
    monkeypatch.setenv("ALERT_COOLDOWN_HOURS", "12")
    monkeypatch.setenv("ALERT_RESCORE_DELTA", "8")
    yield


def test_first_alert_allowed_then_suppressed():
    assert ac.should_alert("thematic:u", "TSM", score=98, kind="BUY") is True
    ac.record_alert("thematic:u", "TSM", score=98, kind="BUY")
    assert ac.should_alert("thematic:u", "TSM", score=98, kind="BUY") is False


def test_score_move_realerts():
    ac.record_alert("thematic:u", "TSM", score=90, kind="BUY")
    assert ac.should_alert("thematic:u", "TSM", score=90 + 8, kind="BUY") is True   # >= delta
    assert ac.should_alert("thematic:u", "TSM", score=90 + 7, kind="BUY") is False  # < delta


def test_kind_change_realerts():
    ac.record_alert("brain:u", "NVDA", score=60, kind="TRIM")
    assert ac.should_alert("brain:u", "NVDA", score=60, kind="TRIM") is False
    assert ac.should_alert("brain:u", "NVDA", score=60, kind="EXIT") is True


def test_scopes_and_tickers_isolated():
    ac.record_alert("thematic:u", "TSM", score=98, kind="BUY")
    assert ac.should_alert("thematic:other", "TSM", score=98, kind="BUY") is True
    assert ac.should_alert("brain:u", "TSM", score=98, kind="BUY") is True
    assert ac.should_alert("thematic:u", "MU", score=98, kind="BUY") is True


def test_cooldown_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("ALERT_COOLDOWN_HOURS", "0")
    ac.record_alert("thematic:u", "TSM", score=98, kind="BUY")
    assert ac.should_alert("thematic:u", "TSM", score=98, kind="BUY") is True


def test_cooldown_elapses(monkeypatch):
    import time as _t
    ac.record_alert("thematic:u", "TSM", score=98, kind="BUY")
    # Force the stored timestamp to be older than the cooldown window.
    d = ac._load()
    d["THEMATIC:U:TSM"]["ts"] = _t.time() - 13 * 3600
    ac._save(d)
    assert ac.should_alert("thematic:u", "TSM", score=98, kind="BUY") is True


def test_record_prunes_week_old(monkeypatch):
    import time as _t
    ac.record_alert("thematic:u", "OLD", score=50, kind="BUY")
    d = ac._load()
    d["THEMATIC:U:OLD"]["ts"] = _t.time() - 8 * 86400  # > 7d
    ac._save(d)
    ac.record_alert("thematic:u", "NEW", score=50, kind="BUY")  # triggers prune
    assert "THEMATIC:U:OLD" not in ac._load()
    assert "THEMATIC:U:NEW" in ac._load()


def test_persists_across_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "_FILE", tmp_path / "persist.json")
    ac.record_alert("thematic:u", "TSM", score=98, kind="BUY")
    importlib.reload(ac)
    monkeypatch.setattr(ac, "_FILE", tmp_path / "persist.json")
    monkeypatch.setenv("ALERT_COOLDOWN_HOURS", "12")
    assert ac.should_alert("thematic:u", "TSM", score=98, kind="BUY") is False
