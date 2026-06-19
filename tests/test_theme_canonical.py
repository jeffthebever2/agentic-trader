"""Theme folding: LLM theme variants map to a canonical THEMES_MAP key (so a
mis-labelled theme keeps its real sector, which drives the per-theme
concentration cap) instead of collapsing to 'future_tech'."""
import web.api.thematic_auto as t


def test_canonical_passthrough():
    for th in t._VALID_THEMES:
        assert t._canonical_theme(th) == th


def test_aliases_map_to_canonical():
    assert t._canonical_theme("AI") == "ai_leaders"
    assert t._canonical_theme("semiconductors") == "ai_infrastructure"
    assert t._canonical_theme("Chips") == "ai_infrastructure"
    assert t._canonical_theme("nuclear") == "nuclear_energy"
    assert t._canonical_theme("rare earth") == "critical_minerals"   # space → underscore
    assert t._canonical_theme("defense") == "space_defense"


def test_unknown_and_empty_to_future_tech():
    assert t._canonical_theme("blockchain_moon") == "future_tech"
    assert t._canonical_theme("") == "future_tech"
    assert t._canonical_theme(None) == "future_tech"


def test_all_alias_targets_are_valid():
    for canonical in t._THEME_ALIASES.values():
        assert canonical in t._VALID_THEMES


def test_sanitize_applies_canonical_theme():
    out = t._sanitize_picks(
        [{"ticker": "NVDA", "conviction": 8, "catalyst": "GTC", "theme": "chips"}],
        {"NVDA"},
    )
    assert out[0]["theme"] == "ai_infrastructure"
