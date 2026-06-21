"""Per-source scan health → /status. Catches the 'scanner silently dead' failure:
a feed that errors/times out repeatedly, or went stale after working, is flagged
DEAD; a feed that's merely empty (quiet day) is NOT.
"""
import asyncio
import time

import pytest

import web.api.thematic_auto as ta


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_SOURCE_HEALTH_FILE", tmp_path / "sh.json")


def test_ok_source_healthy():
    ta._record_source_health([{"NVDA": 3, "AMD": 1}], ["reddit"])
    s = ta._source_health_summary()["sources"]["reddit"]
    assert s["status"] == "ok" and s["last_count"] == 2 and s["dead"] is False


def test_error_source_dead_after_threshold():
    for _ in range(ta._SOURCE_DEAD_FAILS):
        ta._record_source_health([RuntimeError("boom")], ["brave"])
    s = ta._source_health_summary()["sources"]["brave"]
    assert s["status"] == "error" and s["consecutive_failures"] >= ta._SOURCE_DEAD_FAILS
    assert s["dead"] is True and s.get("last_error") == "boom"
    assert "brave" in ta._source_health_summary()["dead_sources"]


def test_timeout_tracked():
    ta._record_source_health([asyncio.TimeoutError()], ["ddg"])
    assert ta._source_health_summary()["sources"]["ddg"]["status"] == "timeout"


def test_empty_is_not_dead():
    # a source that returns empty (but never errors) is NOT dead — just no-data
    for _ in range(5):
        ta._record_source_health([{}], ["insider"])
    s = ta._source_health_summary()["sources"]["insider"]
    assert s["status"] == "empty" and s["dead"] is False


def test_recovery_resets_failures():
    ta._record_source_health([RuntimeError("x")], ["finviz"])
    ta._record_source_health([RuntimeError("x")], ["finviz"])
    ta._record_source_health([{"NVDA": 1}], ["finviz"])      # recovers
    s = ta._source_health_summary()["sources"]["finviz"]
    assert s["status"] == "ok" and s["consecutive_failures"] == 0 and s["dead"] is False


def test_went_stale_after_working_is_dead(monkeypatch):
    ta._record_source_health([{"NVDA": 1}], ["rss_news"])
    # backdate last_success beyond the stale window
    data = ta._source_health()
    data["rss_news"]["last_success"] = time.time() - (ta._SOURCE_STALE_HOURS + 1) * 3600
    ta._SOURCE_HEALTH_FILE.write_text(__import__("json").dumps(data))
    s = ta._source_health_summary()["sources"]["rss_news"]
    assert s["dead"] is True


def test_summary_counts():
    ta._record_source_health([{"A": 1}, {}, RuntimeError("e")], ["s_ok", "s_empty", "s_err"])
    for _ in range(ta._SOURCE_DEAD_FAILS - 1):
        ta._record_source_health([{"A": 1}, {}, RuntimeError("e")], ["s_ok", "s_empty", "s_err"])
    summ = ta._source_health_summary()
    assert summ["total_sources"] == 3
    assert "s_err" in summ["dead_sources"] and "s_ok" not in summ["dead_sources"]
