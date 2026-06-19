"""FINRA short-volume RISK overlay. It must NOT be additive to the buzz score
(heavy shorting can't rank a name up); it surfaces a per-signal risk level and
vetoes will_buy on extreme short pressure. Pure parser + classifier + cached map."""
import web.api.thematic_auto as t

_FILE = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260619|HEAVY|700000|0|1000000|CNMS
20260619|NORMAL|400000|0|1000000|CNMS
20260619|MILD|520000|0|1000000|CNMS
bad|row
20260619|ZEROVOL|10|0|0|CNMS
"""


def test_parse_finra_file():
    m = t._parse_finra_short_volume(_FILE)
    assert m["HEAVY"] == 0.7
    assert m["NORMAL"] == 0.4
    assert m["MILD"] == 0.52
    assert "ZEROVOL" not in m          # total volume 0 → skipped
    assert t._parse_finra_short_volume("") == {}
    assert t._parse_finra_short_volume("garbage no header") == {}


def test_short_pressure_levels():
    assert t._short_pressure_level(0.7) == "extreme"
    assert t._short_pressure_level(0.52) == "high"
    assert t._short_pressure_level(0.4) == "normal"
    assert t._short_pressure_level(None) == "unknown"
    assert t._short_pressure_level(float("nan")) == "unknown"


def test_overlay_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_SHORT_OVERLAY", raising=False)
    assert t._finra_short_map(fetch=lambda: _FILE) == {}


def test_overlay_cached_map(monkeypatch):
    monkeypatch.setenv("THEMATIC_SHORT_OVERLAY", "true")
    t._finra_short_cache.update({"day": "", "map": {}})
    m = t._finra_short_map(fetch=lambda: _FILE, day="2026-06-19")
    assert m["HEAVY"] == 0.7


def test_overlay_not_additive_to_score():
    # a short ratio is NOT fed into _merge_signals as a source — confirm there is
    # no 'short' / 'finra' contribution path in the merge breakdown.
    import inspect
    src = inspect.getsource(t._merge_signals)
    assert "short" not in src.lower().replace("short_", "")  # no short-volume _add
