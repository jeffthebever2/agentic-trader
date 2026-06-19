"""_get_historical_scores must survive a corrupt/partial score-history file (the
jsonl is append-written and could have a torn last line) and never divide by zero
on all-zero history. Scan-memory is best-effort context — a bad line degrades it,
never crashes the scan."""
import json

import web.api.thematic_auto as t


def _write(tmp_path, monkeypatch, lines):
    f = tmp_path / "hist.jsonl"
    f.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(t, "SCORE_HISTORY_FILE", f)


def test_valid_history_yields_scaled_bonuses(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, [
        json.dumps({"ts": "1", "ranked": [["NVDA", 200], ["AMD", 100]]}),
        json.dumps({"ts": "2", "ranked": [["NVDA", 220], ["AMD", 80]]}),
    ])
    out = t._get_historical_scores(n_scans=5)
    assert out["NVDA"] == 30.0          # strongest → ~30pt cap
    assert 0 < out["AMD"] < 30.0


def test_corrupt_lines_skipped_not_fatal(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, [
        "this is not json",
        json.dumps({"ranked": [["NVDA", 150]]}),
        '{"ranked": [["AMD", "oops"]]}',      # non-numeric score → line skipped
        '{"truncated": ',                      # torn last line
    ])
    out = t._get_historical_scores(n_scans=10)
    # the one good line survives; bad lines ignored
    assert out.get("NVDA") == 30.0
    assert "AMD" not in out


def test_all_zero_history_no_divide_by_zero(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, [
        json.dumps({"ranked": [["NVDA", 0], ["AMD", 0]]}),
    ])
    assert t._get_historical_scores() == {}


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "SCORE_HISTORY_FILE", tmp_path / "nope.jsonl")
    assert t._get_historical_scores() == {}
