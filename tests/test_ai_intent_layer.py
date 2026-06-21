"""Free-AI intent layer: reconcile safety + batch parse + reddit AI-resolve path.

The AI (Cloudflare/OpenRouter free models) only RESOLVES posts the lexicon read
weakly, and a SELL from either source still blocks the buy.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta
from tradingagents.screening import tweet_intent as ti
from tradingagents.screening.tweet_intent import (
    BUY_SIGNAL, HOLD_SIGNAL, SELL_SIGNAL, UNCLEAR, IntentResult,
    classify_intent, reconcile_intents,
)


# ── reconcile safety ───────────────────────────────────────────────────────────
def _ai(label, conf=0.75):
    return IntentResult("X", label, "ai", 0.6 if label == BUY_SIGNAL else -0.6 if label == SELL_SIGNAL else 0.0,
                        conf, "ai reason", label == BUY_SIGNAL, label == SELL_SIGNAL)


def test_reconcile_none_ai_returns_lexicon():
    lex = classify_intent("bought $NVDA")
    assert reconcile_intents(lex, None) is lex


def test_ai_rescues_unclear_to_buy():
    lex = classify_intent("$NVDA 🚀🚀")            # UNCLEAR (no verb)
    assert lex.label == UNCLEAR
    out = reconcile_intents(lex, _ai(BUY_SIGNAL))
    assert out.label == BUY_SIGNAL and out.increase_buy and not out.reduce_buy


def test_ai_cannot_override_explicit_sell():
    lex = classify_intent("selling $NVDA, taking profits")   # conf ~0.8, explicit
    assert lex.confidence >= 0.7
    out = reconcile_intents(lex, _ai(BUY_SIGNAL, conf=0.99))
    assert out.label == SELL_SIGNAL and out.reduce_buy is True


def test_iffy_lexicon_sell_defers_to_ai():
    # A borderline lexicon SELL (a soft "warning", conf < 0.7) was sent to AI
    # because it was iffy → the AI verdict wins instead of the shaky lexicon call.
    lex = classify_intent("$NVDA looks a bit extended here")   # sentiment-warning, ~0.55
    assert lex.reduce_buy and lex.confidence < 0.7
    out = reconcile_intents(lex, _ai(BUY_SIGNAL))
    assert out.label == BUY_SIGNAL and out.reduce_buy is False  # AI decided


def test_ai_sell_blocks_even_if_lexicon_unclear():
    lex = classify_intent("$NVDA hmm")             # UNCLEAR
    out = reconcile_intents(lex, _ai(SELL_SIGNAL))
    assert out.label == SELL_SIGNAL and out.reduce_buy is True and not out.increase_buy


def test_ai_hold_does_not_increase_buy():
    lex = classify_intent("$NVDA thoughts?")
    out = reconcile_intents(lex, _ai(HOLD_SIGNAL))
    assert out.increase_buy is False and out.reduce_buy is False


# ── batch JSON parse ─────────────────────────────────────────────────────────
def test_parse_intent_json_maps_indices():
    items = [("NVDA", "x"), ("TSLA", "y"), ("AMD", "z")]
    content = '```json\n[{"i":0,"label":"BUY_SIGNAL","reason":"adding"},' \
              '{"i":1,"label":"SELL_SIGNAL","reason":"trimming"},' \
              '{"i":2,"label":"bogus","reason":"x"}]\n```'
    out = ta._parse_intent_json(content, items)
    assert out[0].label == BUY_SIGNAL and out[0].increase_buy
    assert out[1].label == SELL_SIGNAL and out[1].reduce_buy
    assert 2 not in out                            # invalid label dropped


def test_parse_intent_json_garbage_returns_empty():
    assert ta._parse_intent_json("not json at all", [("NVDA", "x")]) == {}


# ── reddit AI-resolve path (mocked AI, no network) ──────────────────────────────
class _Resp:
    def __init__(self, posts): self.status_code = 200; self._p = posts
    def json(self): return {"data": {"children": [{"data": p} for p in self._p]}}


class _Client:
    def __init__(self, posts): self._p = posts
    async def get(self, url, headers=None, timeout=None): return _Resp(self._p)


def test_reddit_ai_resolves_unclear(monkeypatch):
    monkeypatch.setattr(ta, "SUBREDDITS", ["stocks"])
    monkeypatch.setenv("THEMATIC_AI_INTENT", "true")
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    # lexicon reads this as UNCLEAR (no action verb) → goes to AI
    posts = [{"title": "$WILD 🚀 to the moon next week", "selftext": ""}]

    async def fake_ai(items):
        # AI says it's actually a sell/warning
        return {0: ti.IntentResult(items[0][0], SELL_SIGNAL, "ai", -0.6, 0.8,
                                   "pump warning", False, True)}
    monkeypatch.setattr(ta, "_ai_classify_intents", fake_ai)

    ta._reset_social_intent()
    counts = asyncio.run(ta._reddit_tickers(_Client(posts)))
    assert counts.get("WILD", 0) == 0                         # AI sell → no buzz
    assert ta.net_social_buy_intent("WILD").get("sell", 0) == 1


# ── Cloudflare neuron budget guard (free 10k/day cap) ───────────────────────────
def test_intent_model_is_cheap_by_default(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_INTENT_MODEL", raising=False)
    # NOT the 70B pick model — must be a cheap model for the high-volume classifier.
    assert "70b" not in ta._cf_intent_model().lower()


def test_estimate_neurons_70b_costlier_than_3b():
    b70 = ta._estimate_neurons("@cf/meta/llama-3.3-70b-instruct-fp8-fast", 3680, 1200)
    b3 = ta._estimate_neurons("@cf/meta/llama-3.2-3b-instruct", 3680, 1200)
    assert b70 > b3 * 5            # ~6x in practice


def test_neuron_usage_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_NEURON_USAGE_FILE", tmp_path / "neu.json")
    assert ta._neuron_usage_today() == 0.0
    ta._add_neuron_usage(50.0)
    ta._add_neuron_usage(25.0)
    assert ta._neuron_usage_today() == 75.0


def test_budget_guard_skips_cf_when_over(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_NEURON_USAGE_FILE", tmp_path / "neu.json")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "x")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "y")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("CF_DAILY_NEURON_BUDGET", "100")
    ta._add_neuron_usage(200.0)     # already over budget

    async def _boom_post(*a, **k):
        raise AssertionError("CF must not be called when over neuron budget")
    # if CF were called it'd hit httpx; instead the guard returns before any call
    out = asyncio.run(ta._ai_classify_intents([("NVDA", "bought, adding")]))
    assert out == {}                # no CF call, no OpenRouter key → empty (lexicon stands)


# ── OpenRouter daily-call budget (free ~1000/day) ───────────────────────────────
def test_openrouter_call_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_OR_USAGE_FILE", tmp_path / "or.json")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_DAILY_CALL_BUDGET", "2")
    assert ta._openrouter_call_ok() is True
    ta._record_openrouter_call()
    ta._record_openrouter_call()
    assert ta._openrouter_calls_today() == 2
    assert ta._openrouter_call_ok() is False     # at cap


def test_openrouter_no_key_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_OR_USAGE_FILE", tmp_path / "or.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ta._openrouter_call_ok() is False


def test_intent_skips_openrouter_when_over_budget(monkeypatch, tmp_path):
    # CF unavailable + OR over budget → returns {} (lexicon stands), no network.
    monkeypatch.setattr(ta, "_OR_USAGE_FILE", tmp_path / "or.json")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_DAILY_CALL_BUDGET", "1")
    ta._record_openrouter_call()                 # already at cap
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)
    out = asyncio.run(ta._ai_classify_intents([("NVDA", "bought, adding")]))
    assert out == {}


# ── OpenRouter multi-model fallback (free models often 429) ─────────────────────
def test_openrouter_intent_models_order(monkeypatch):
    monkeypatch.delenv("OPENROUTER_INTENT_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_INTENT_MODEL", raising=False)
    models = ta._openrouter_intent_models()
    assert models and all(":free" in m for m in models)      # all free
    assert "70b" not in models[0]                            # 70b not first (it 429s)
    monkeypatch.setenv("OPENROUTER_INTENT_MODEL", "x/custom:free")
    assert ta._openrouter_intent_models()[0] == "x/custom:free"
    monkeypatch.setenv("OPENROUTER_INTENT_MODELS", "a:free,b:free")
    monkeypatch.delenv("OPENROUTER_INTENT_MODEL", raising=False)
    assert ta._openrouter_intent_models() == ["a:free", "b:free"]


def test_openrouter_429_falls_through_to_next_model(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "_OR_USAGE_FILE", tmp_path / "or.json")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_DAILY_CALL_BUDGET", "10")
    monkeypatch.setenv("OPENROUTER_INTENT_MODELS", "model-a:free,model-b:free")
    monkeypatch.setattr(ta, "_ai_intent_enabled", lambda: True)

    seen = []

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code; self._p = payload
        def json(self): return self._p

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            model = json["model"]; seen.append(model)
            if model == "model-a:free":
                return _Resp(429)                            # first model overloaded
            return _Resp(200, {"choices": [{"message": {"content":
                '[{"i":0,"label":"BUY_SIGNAL","reason":"adding"}]'}}]})
    monkeypatch.setattr(ta.httpx, "AsyncClient", _FakeClient)

    out = asyncio.run(ta._ai_classify_intents([("NVDA", "bought, adding")]))
    assert seen == ["model-a:free", "model-b:free"]          # skipped 429 → next
    assert out[0].label == BUY_SIGNAL


# ── free-models-only enforcement (never spend the $10 credit) ───────────────────
def test_free_only_filters_paid():
    mixed = ["openai/gpt-4o-mini", "google/gemma-4-31b-it:free", "anthropic/claude-x", "x:free"]
    assert ta._free_only(mixed) == ["google/gemma-4-31b-it:free", "x:free"]


def test_intent_models_drop_paid_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_INTENT_MODELS", "openai/gpt-4o-mini, google/gemma-4-31b-it:free")
    monkeypatch.delenv("OPENROUTER_INTENT_MODEL", raising=False)
    out = ta._openrouter_intent_models()
    assert all(m.endswith(":free") for m in out)
    assert "openai/gpt-4o-mini" not in out


def test_intent_models_all_paid_falls_back_to_free(monkeypatch):
    monkeypatch.setenv("OPENROUTER_INTENT_MODELS", "openai/gpt-4o-mini,anthropic/claude-x")
    monkeypatch.delenv("OPENROUTER_INTENT_MODEL", raising=False)
    out = ta._openrouter_intent_models()
    assert out and all(m.endswith(":free") for m in out)   # never a paid model


def test_all_default_models_are_free():
    assert all(m.endswith(":free") for m in ta._OR_INTENT_MODELS_DEFAULT)


def test_reddit_ai_disabled_uses_lexicon(monkeypatch):
    monkeypatch.setattr(ta, "SUBREDDITS", ["stocks"])
    monkeypatch.setenv("THEMATIC_AI_INTENT", "false")
    called = {"n": 0}

    async def boom(items):
        called["n"] += 1
        return {}
    monkeypatch.setattr(ta, "_ai_classify_intents", boom)
    posts = [{"title": "bought $AMD, adding on dips", "selftext": ""}]
    ta._reset_social_intent()
    counts = asyncio.run(ta._reddit_tickers(_Client(posts)))
    assert counts.get("AMD", 0) == 1 and called["n"] == 0     # AI never called
