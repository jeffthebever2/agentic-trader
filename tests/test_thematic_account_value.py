"""_thematic_account_value feeds adaptive position sizing. Garbage/non-finite
values in the paper state must not crash it — it fails to 0 (caller falls back to
the flat base size), and always returns a finite, non-negative number."""
import json
import math

import web.api.thematic_auto as t


def _state(tmp_path, monkeypatch, data):
    f = tmp_path / "paper.json"
    f.write_text(json.dumps(data))
    import web.api.thematic_portfolio as tp
    monkeypatch.setattr(tp, "PAPER_STATE_FILE", f)
    return f


def test_normal_value(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch, {
        "cash": 5000,
        "positions": {"NVDA": {"entry_price": 100, "shares": 10}},
    })
    assert t._thematic_account_value("u@x.com") == 6000.0  # 5000 + 100*10


def test_garbage_values_do_not_crash(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch, {
        "cash": "oops",
        "positions": {"X": {"entry_price": "n/a", "shares": None}},
    })
    out = t._thematic_account_value("u@x.com")
    assert math.isfinite(out) and out >= 0.0  # garbage → 0 contributions


def test_nan_values_fail_to_finite(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch, {"cash": float("nan"),
                                   "positions": {"X": {"entry_price": float("inf"), "shares": 5}}})
    out = t._thematic_account_value("u@x.com")
    assert math.isfinite(out) and out >= 0.0


def test_missing_file_returns_zero(tmp_path, monkeypatch):
    import web.api.thematic_portfolio as tp
    monkeypatch.setattr(tp, "PAPER_STATE_FILE", tmp_path / "nope.json")
    assert t._thematic_account_value("u@x.com") == 0.0
