"""_sanitize_picks is the deterministic safety floor on the LLM's thematic picks:
drop hallucinated/off-list tickers and clamp every numeric field back to its
documented range. A bad pick here would seed a false-positive trade."""
import web.api.thematic_auto as t

ALLOWED = {"NVDA", "AMD", "AVGO"}


def test_drops_hallucinated_ticker():
    picks = [
        {"ticker": "NVDA", "conviction": 8},
        {"ticker": "FAKE", "conviction": 9},   # not in trending → dropped
        {"ticker": "ZZZZ", "conviction": 10},  # not in trending → dropped
    ]
    out = t._sanitize_picks(picks, ALLOWED)
    assert [p["ticker"] for p in out] == ["NVDA"]


def test_normalizes_ticker_then_matches():
    # cashtag / lowercase still matches the allowed set after normalization
    out = t._sanitize_picks([{"ticker": "$nvda", "conviction": 7}], ALLOWED)
    assert len(out) == 1 and out[0]["ticker"] == "NVDA"


def test_clamps_numeric_ranges():
    out = t._sanitize_picks([{
        "ticker": "AMD",
        "conviction": 99,      # → 10
        "sentiment": 5.0,      # → 1.0
        "target_pct": 9999,    # → 300
        "stop_pct": 0,         # → 5
        "hold_days": 1000,     # → 30
    }], ALLOWED)
    p = out[0]
    assert p["conviction"] == 10
    assert p["sentiment"] == 1.0
    assert p["target_pct"] == 300
    assert p["stop_pct"] == 5
    assert p["hold_days"] == 30


def test_garbage_numbers_get_defaults():
    out = t._sanitize_picks([{
        "ticker": "AVGO",
        "conviction": "n/a",
        "sentiment": None,
        "target_pct": float("nan"),
        "stop_pct": float("inf"),
        "hold_days": "soon",
    }], ALLOWED)
    p = out[0]
    assert p["conviction"] == 5 and p["sentiment"] == 0.0
    assert p["target_pct"] == 60 and p["stop_pct"] == 10 and p["hold_days"] == 10


def test_unknown_theme_coerced():
    out = t._sanitize_picks([{"ticker": "NVDA", "theme": "crypto_moonshot"}], ALLOWED)
    assert out[0]["theme"] == "future_tech"
    out2 = t._sanitize_picks([{"ticker": "NVDA", "theme": "ai_leaders"}], ALLOWED)
    assert out2[0]["theme"] == "ai_leaders"


def test_non_list_and_non_dict_safe():
    assert t._sanitize_picks(None, ALLOWED) == []
    assert t._sanitize_picks("oops", ALLOWED) == []
    assert t._sanitize_picks([None, 5, "x", {"no_ticker": 1}], ALLOWED) == []


# ── De-duplication (added run 7) ────────────────────────────────────────────
def test_duplicate_ticker_deduped_keep_highest_conviction():
    picks = [
        {"ticker": "NVDA", "conviction": 6, "thesis": "first"},
        {"ticker": "nvda", "conviction": 9, "thesis": "second"},   # same name, higher conv
        {"ticker": "AMD", "conviction": 7},
    ]
    out = t._sanitize_picks(picks, {"NVDA", "AMD"})
    by = {p["ticker"]: p for p in out}
    assert len(out) == 2                       # NVDA collapsed to one
    assert by["NVDA"]["conviction"] == 9       # highest-conviction instance kept
    assert by["NVDA"]["thesis"] == "second"


def test_dedup_tie_keeps_first():
    picks = [
        {"ticker": "AMD", "conviction": 7, "thesis": "a"},
        {"ticker": "AMD", "conviction": 7, "thesis": "b"},
    ]
    out = t._sanitize_picks(picks, {"AMD"})
    assert len(out) == 1 and out[0]["thesis"] == "a"
