"""Red-flag veto in _sanitize_picks: a pick whose own crowd_view/catalyst/thesis
cites a hard negative catalyst (fraud, SEC probe, halt, delisting, short report,
dilution...) is forced deep-bearish + low conviction so it can never auto-trade
— even if the LLM returned a bullish sentiment. Cuts pump/disaster false
positives. bear_case is NOT scanned (always negative by design)."""
import web.api.thematic_auto as t

ALLOWED = {"NVDA", "SCAM", "DLST"}


def test_red_flag_in_crowdview_forces_bearish():
    out = t._sanitize_picks([{
        "ticker": "SCAM", "conviction": 9, "sentiment": 0.8,
        "crowd_view": "Reddit hyping it but warns of an SEC investigation and possible delisting",
        "catalyst": "earnings", "thesis": "momentum",
    }], ALLOWED)
    p = out[0]
    assert p["red_flag"] is True
    assert p["sentiment"] <= -0.5
    assert p["conviction"] <= 4
    # composite_score must hard-cap such a name below the auto-trade gate
    assert t.composite_score(p["conviction"], 100000, p["sentiment"]) <= 45


def test_clean_pick_not_flagged():
    out = t._sanitize_picks([{
        "ticker": "NVDA", "conviction": 9, "sentiment": 0.7,
        "crowd_view": "Crowd calling a breakout on strong datacenter demand",
        "catalyst": "GTC keynote", "thesis": "AI compute leader",
    }], ALLOWED)
    p = out[0]
    assert "red_flag" not in p
    assert p["sentiment"] == 0.7 and p["conviction"] == 9


def test_bear_case_negativity_does_not_trigger():
    # bear_case naturally lists downside ("fraud risk if...") — must NOT veto.
    out = t._sanitize_picks([{
        "ticker": "NVDA", "conviction": 8, "sentiment": 0.6,
        "crowd_view": "Bullish, strong momentum",
        "catalyst": "product launch",
        "bear_case": "Could crash on an SEC investigation or accounting fraud",
    }], ALLOWED)
    p = out[0]
    assert "red_flag" not in p
    assert p["sentiment"] == 0.6


def test_halt_and_dilution_terms_trigger():
    for txt in ("Shares were halted today", "company announced a dilutive offering priced below market"):
        out = t._sanitize_picks([{"ticker": "DLST", "conviction": 7, "sentiment": 0.5, "crowd_view": txt}], ALLOWED)
        assert out[0].get("red_flag") is True
