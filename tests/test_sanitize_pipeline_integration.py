"""Capstone: _sanitize_picks composes all the accuracy guards correctly on one
messy, realistic LLM response — hallucination drop, numeric clamp, theme fold,
weak-catalyst cap, red-flag veto, dedup, and free-text bounding."""
import web.api.thematic_auto as t

ALLOWED = {"NVDA", "AMD", "AVGO"}


def test_full_pipeline_on_messy_output():
    picks = [
        # hallucinated ticker (not in trending) → dropped
        {"ticker": "FAKE", "conviction": 10, "catalyst": "earnings"},
        # solid pick: concrete catalyst, valid → preserved, theme folded
        {"ticker": "NVDA", "conviction": 9, "sentiment": 0.8,
         "catalyst": "GTC keynote + Q4 earnings", "theme": "chips",
         "target_pct": 9999, "stop_pct": 0},
        # duplicate NVDA, lower conviction → dropped by dedup
        {"ticker": "nvda", "conviction": 5, "catalyst": "more GTC"},
        # red-flag pick → bearish-capped, low conviction
        {"ticker": "AMD", "conviction": 9, "sentiment": 0.7,
         "catalyst": "launch", "crowd_view": "reddit warns of an SEC investigation"},
        # weak/no catalyst → conviction capped at 6
        {"ticker": "AVGO", "conviction": 10, "catalyst": "momentum",
         "name": "X" * 500},
        # junk entries → ignored
        None, 42, {"no_ticker": 1},
    ]
    out = t._sanitize_picks(picks, ALLOWED)
    by = {p["ticker"]: p for p in out}

    assert set(by) == {"NVDA", "AMD", "AVGO"}      # FAKE dropped, dups folded
    assert "FAKE" not in by

    # NVDA: clamped numerics, folded theme, full conviction
    assert by["NVDA"]["conviction"] == 9
    assert by["NVDA"]["target_pct"] == 300 and by["NVDA"]["stop_pct"] == 5
    assert by["NVDA"]["theme"] == "ai_infrastructure"
    assert by["NVDA"]["catalyst"] == "GTC keynote + Q4 earnings"

    # AMD red-flag: deep-bearish + low conviction → never auto-tradeable
    assert by["AMD"]["red_flag"] is True
    assert by["AMD"]["sentiment"] <= -0.5 and by["AMD"]["conviction"] <= 4
    assert t.composite_score(by["AMD"]["conviction"], 100000, by["AMD"]["sentiment"]) <= 45

    # AVGO weak catalyst + huge name → capped conviction + bounded name
    assert by["AVGO"]["weak_catalyst"] is True and by["AVGO"]["conviction"] == 6
    assert len(by["AVGO"]["name"]) <= 80
