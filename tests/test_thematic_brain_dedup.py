"""The thematic scanner must never propose BUYING a name the Holdings-Brain
already holds/manages — else it buys what the brain is trimming (contradiction).
"""
import json

from web.api.thematic_auto import _brain_held_tickers


def _write(tmp_path, name, data):
    (tmp_path / name).write_text(json.dumps(data))


def test_held_from_proposals_and_store(tmp_path):
    _write(tmp_path, "holdings_brain_proposals_u@x.json",
           {"proposals": [{"ticker": "NVDA", "status": "pending"},
                          {"ticker": "iren", "status": "pending"}]})
    _write(tmp_path, "holdings_brain_u@x.json",
           {"ASTS": {"status": "managed"}, "HIMS": {"status": "adopted"},
            "OLD": {"status": "closed"}})
    held = _brain_held_tickers(tmp_path)
    assert held == {"NVDA", "IREN", "ASTS", "HIMS"}      # closed excluded


def test_empty_when_no_files(tmp_path):
    assert _brain_held_tickers(tmp_path) == set()


def test_corrupt_file_ignored(tmp_path):
    (tmp_path / "holdings_brain_bad.json").write_text("{not json")
    _write(tmp_path, "holdings_brain_proposals_u@x.json", {"proposals": [{"ticker": "TSLA"}]})
    assert _brain_held_tickers(tmp_path) == {"TSLA"}


# ── Unified 0-100 signal score (replaces conviction/10 + raw buzz) ──────────────
from web.api.thematic_auto import composite_score


def test_composite_score_range_and_backbone():
    assert composite_score(10, 0) == 75          # conviction backbone (c*7.5), no buzz
    assert 90 <= composite_score(10, 300) <= 100  # high conviction + strong buzz
    assert composite_score(1, 0) <= 15            # weak
    # buzz can nudge but never carry a weak thesis
    assert composite_score(3, 10000) < composite_score(8, 0)


def test_composite_score_monotonic_in_buzz():
    assert composite_score(7, 200) > composite_score(7, 0)
    assert composite_score(7, 50) >= composite_score(5, 50)


def test_composite_score_clamped():
    assert 0 <= composite_score(10, 10**9) <= 100
    assert 0 <= composite_score(0, 0) <= 100
