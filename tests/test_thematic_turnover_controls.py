"""P0 turnover controls: signal TTL helpers + manual-scan min-interval throttle.

These guard against the wipe-and-rebuild flip-flop (pending signals now persist
across scans up to a TTL) and against burst manual re-scans that re-page the user.
"""
import asyncio
import datetime as dt
import time

import pytest

import web.api.thematic_auto as ta


# ── TTL / timestamp helpers ──────────────────────────────────────────────────
def test_signal_ts_epoch_from_iso():
    iso = dt.datetime(2026, 6, 19, 21, 0, 0).isoformat()
    got = ta._signal_ts_epoch({"ts": iso})
    assert abs(got - dt.datetime(2026, 6, 19, 21, 0, 0).timestamp()) < 1


def test_signal_ts_epoch_from_id_suffix():
    assert ta._signal_ts_epoch({"id": "TSM_1700000000"}) == 1700000000.0


def test_signal_ts_epoch_unknown():
    assert ta._signal_ts_epoch({}) == 0.0


def test_signal_ttl_hours_env(monkeypatch):
    monkeypatch.setenv("THEMATIC_SIGNAL_TTL_HOURS", "6")
    assert ta._signal_ttl_hours() == 6.0
    monkeypatch.setenv("THEMATIC_SIGNAL_TTL_HOURS", "bad")
    assert ta._signal_ttl_hours() == 24.0  # falls back to default


def test_min_scan_interval_env(monkeypatch):
    monkeypatch.setenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "45")
    assert ta._min_scan_interval_min() == 45.0
    monkeypatch.delenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", raising=False)
    assert ta._min_scan_interval_min() == 30.0


# ── trigger_scan throttle ────────────────────────────────────────────────────
class _FakeBG:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


def _no_status(monkeypatch, tmp_path):
    # Point STATUS_FILE at a nonexistent path so status == {} (not "running").
    monkeypatch.setattr(ta, "STATUS_FILE", tmp_path / "nope_status.json")


def test_trigger_scan_throttled_when_recent(monkeypatch, tmp_path):
    _no_status(monkeypatch, tmp_path)
    monkeypatch.setattr(ta, "_last_scan_done_epoch", lambda: time.time() - 60)  # 1 min ago
    monkeypatch.setenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "30")
    bg = _FakeBG()
    res = asyncio.run(ta.trigger_scan(bg, {"email": "u@x.com"}, force=False))
    assert res.get("skipped") is True
    assert res.get("status") == "throttled"
    assert bg.tasks == []  # no scan scheduled


def test_trigger_scan_force_bypasses_throttle(monkeypatch, tmp_path):
    _no_status(monkeypatch, tmp_path)
    monkeypatch.setattr(ta, "_last_scan_done_epoch", lambda: time.time() - 60)
    monkeypatch.setenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "30")
    bg = _FakeBG()
    res = asyncio.run(ta.trigger_scan(bg, {"email": "u@x.com"}, force=True))
    assert res.get("status") == "running"
    assert len(bg.tasks) == 1  # scan scheduled


def test_trigger_scan_runs_when_interval_elapsed(monkeypatch, tmp_path):
    _no_status(monkeypatch, tmp_path)
    monkeypatch.setattr(ta, "_last_scan_done_epoch", lambda: time.time() - 3600)  # 1h ago
    monkeypatch.setenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "30")
    bg = _FakeBG()
    res = asyncio.run(ta.trigger_scan(bg, {"email": "u@x.com"}, force=False))
    assert res.get("status") == "running"
    assert len(bg.tasks) == 1


def test_trigger_scan_disabled_interval_runs(monkeypatch, tmp_path):
    _no_status(monkeypatch, tmp_path)
    monkeypatch.setattr(ta, "_last_scan_done_epoch", lambda: time.time() - 1)
    monkeypatch.setenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "0")  # disabled
    bg = _FakeBG()
    res = asyncio.run(ta.trigger_scan(bg, {"email": "u@x.com"}, force=False))
    assert res.get("status") == "running"
    assert len(bg.tasks) == 1
