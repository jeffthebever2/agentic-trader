"""_build_ai_pick_prompt encodes the picking discipline. These assertions lock
the accuracy rules into the prompt so the LLM and the deterministic sanitizer
pull the same way — anti-hallucination, concrete catalyst, lone-hype cap, and a
hard red-flag skip."""
import web.api.thematic_auto as t


def test_prompt_embeds_inputs():
    p = t._build_ai_pick_prompt("NVDA(120), AMD(80)", "NVDA hits record high")
    assert "NVDA(120), AMD(80)" in p
    assert "NVDA hits record high" in p


def test_prompt_has_discipline_rules():
    p = t._build_ai_pick_prompt("NVDA(120)", "news")
    # anti-hallucination
    assert "ONLY from the trending tickers" in p
    # concrete-catalyst requirement
    assert "CONCRETE catalyst" in p
    # single-source down-rank
    assert "SINGLE source" in p
    # red-flag hard skip
    assert "SKIP entirely" in p and "short-seller report" in p


def test_prompt_keeps_schema_and_theme_whitelist():
    p = t._build_ai_pick_prompt("NVDA(120)", "news")
    for field in ("conviction", "sentiment", "target_pct", "stop_pct", "crowd_view", "catalyst"):
        assert f'"{field}"' in p or f"{field}:" in p
    assert "ai_leaders" in p and "future_tech" in p


def test_prompt_is_pure_and_deterministic():
    a = t._build_ai_pick_prompt("X(1)", "n")
    b = t._build_ai_pick_prompt("X(1)", "n")
    assert a == b
