"""The six free-AI features (#1–#6) + shared helper. AI is mocked — no network.

All features must (a) work when the AI returns a good reply, and (b) degrade
safely (no crash, conservative default) when the AI is off / fails.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta
import web.api.holdings_brain as hb


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    # default: AI unavailable unless a test opts in
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: False)


def _ai_returns(monkeypatch, text):
    """Make both async + sync shared completions return a canned string + enable AI."""
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)

    async def _async(system, prompt, *, prefer="smart", max_tokens=600):
        return text
    monkeypatch.setattr(ta, "_ai_complete", _async)
    monkeypatch.setattr(ta, "_ai_complete_sync", lambda system, prompt, *, prefer="smart", max_tokens=600: text)


# ── shared helper ───────────────────────────────────────────────────────────
def test_extract_json_variants():
    assert ta._extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert ta._extract_json('noise [1,2,3] tail') == [1, 2, 3]
    assert ta._extract_json("nothing") is None
    assert ta._extract_json(None) is None


# ── #1 brain llm_fn (free only) ───────────────────────────────────────────────
def test_brain_llm_fn_disabled(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_LLM", "false")
    assert hb._make_llm_fn() is None


def test_brain_llm_fn_none_without_free_model(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_LLM", "true")
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: False)
    assert hb._make_llm_fn() is None


def test_brain_llm_fn_uses_free_complete(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_LLM", "true")
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    monkeypatch.setattr(ta, "_ai_complete_sync",
                        lambda system, prompt, *, prefer="smart", max_tokens=400: '{"action":"HOLD"}')
    fn = hb._make_llm_fn()
    assert callable(fn) and fn("assess NVDA") == '{"action":"HOLD"}'


# ── #2 ticker validation ──────────────────────────────────────────────────────
def test_ticker_validation_fail_open(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_TICKER_VALID_FILE", tmp_path / "tv.json")
    monkeypatch.setattr(ta, "_ticker_valid_cache", None)
    # AI off → keep everything (fail-open)
    out = asyncio.run(ta._ai_validate_tickers(["NVDA", "OLDER", "SPEND"]))
    assert out == {"NVDA", "OLDER", "SPEND"}


def test_ticker_validation_drops_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_TICKER_VALID_FILE", tmp_path / "tv.json")
    monkeypatch.setattr(ta, "_ticker_valid_cache", None)
    _ai_returns(monkeypatch, '{"NVDA": true, "OLDER": false, "SPEND": false}')
    out = asyncio.run(ta._ai_validate_tickers(["NVDA", "OLDER", "SPEND"]))
    assert out == {"NVDA"}
    # cached now → second call needs no AI
    monkeypatch.setattr(ta, "_ai_complete", None)
    out2 = asyncio.run(ta._ai_validate_tickers(["NVDA", "OLDER"]))
    assert out2 == {"NVDA"}


# ── #3 catalyst materiality ────────────────────────────────────────────────────
def test_catalyst_materiality_parse(monkeypatch):
    _ai_returns(monkeypatch, '[{"i":0,"catalyst_quality":"strong","why_now":"earnings beat"},'
                             '{"i":1,"catalyst_quality":"none","why_now":"just hype"}]')
    sigs = [{"ticker": "NVDA"}, {"ticker": "HYPE"}]
    out = asyncio.run(ta._ai_catalyst_materiality(sigs))
    assert out["NVDA"]["catalyst_quality"] == "strong"
    assert out["HYPE"]["catalyst_quality"] == "none"


def test_catalyst_materiality_off(monkeypatch):
    assert asyncio.run(ta._ai_catalyst_materiality([{"ticker": "X"}])) == {}


# ── #4 exit news check ─────────────────────────────────────────────────────────
def test_exit_check_conservative_when_off(monkeypatch):
    bad, why = asyncio.run(ta._ai_exit_news_check("NVDA"))
    assert bad is True                      # AI off → exit proceeds


def test_exit_check_rescues_on_no_bad_news(monkeypatch):
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    monkeypatch.setenv("THEMATIC_AI_EXIT_CHECK", "true")

    async def _heads(t, n=6): return ["NVDA dips on profit-taking", "NVDA quiet day"]
    monkeypatch.setattr(ta, "_fetch_ticker_headlines", _heads)

    async def _c(system, prompt, *, prefer="smart", max_tokens=600):
        return '{"bad_news": false, "reason": "just attention fade"}'
    monkeypatch.setattr(ta, "_ai_complete", _c)
    bad, why = asyncio.run(ta._ai_exit_news_check("NVDA"))
    assert bad is False                     # AI clears it → hold


def test_exit_check_confirms_on_bad_news(monkeypatch):
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    monkeypatch.setenv("THEMATIC_AI_EXIT_CHECK", "true")

    async def _heads(t, n=6): return ["NVDA announces $5B dilutive offering"]
    monkeypatch.setattr(ta, "_fetch_ticker_headlines", _heads)

    async def _c(system, prompt, *, prefer="smart", max_tokens=600):
        return '{"bad_news": true, "reason": "dilution"}'
    monkeypatch.setattr(ta, "_ai_complete", _c)
    bad, _ = asyncio.run(ta._ai_exit_news_check("NVDA"))
    assert bad is True


# ── #5 sector gap-fill ─────────────────────────────────────────────────────────
def test_sector_fill_off(monkeypatch):
    assert ta._ai_sector_fill("ASTS") == ""


def test_sector_fill_parse(monkeypatch):
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    monkeypatch.setattr(ta, "_ai_complete_sync",
                        lambda s, p, *, prefer="cheap", max_tokens=20: "Technology")
    assert ta._ai_sector_fill("ASTS") == "Technology"
    monkeypatch.setattr(ta, "_ai_complete_sync",
                        lambda s, p, *, prefer="cheap", max_tokens=20: "Banana")
    assert ta._ai_sector_fill("XYZ") == ""        # not a real sector → ""


# ── #6 red-flag deepening ───────────────────────────────────────────────────────
def test_red_flag_only_flags_true(monkeypatch):
    _ai_returns(monkeypatch, '[{"i":0,"red_flag":false,"risk":""},'
                             '{"i":1,"red_flag":true,"risk":"dilution: $500M ATM"}]')
    sigs = [{"ticker": "GOOD"}, {"ticker": "DILUTE"}]
    out = asyncio.run(ta._ai_red_flag_check(sigs))
    assert "GOOD" not in out
    assert out["DILUTE"]["red_flag"] is True and "dilution" in out["DILUTE"]["risk"]


def test_red_flag_off(monkeypatch):
    assert asyncio.run(ta._ai_red_flag_check([{"ticker": "X"}])) == {}
