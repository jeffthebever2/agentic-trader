"""Scan-source resilience (web/api/thematic_auto.py).

The AI intent call must never take a whole source down with it (trusted_twitter
timed out 12 scans straight because the LLM call outlived the per-source
budget), and reddit must fall back to RSS when the JSON API 403s.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta
from tradingagents.screening import tweet_intent as ti


def _rows(text="loading up on $NVDA, buying more", ticker="NVDA", weight=5):
    lex = ti.classify_intent(text, ticker=ticker)
    return [{"ticker": ticker, "text": text, "lex": lex, "weight": weight}]


def test_resolve_intent_survives_ai_timeout(monkeypatch):
    """A hung AI call is abandoned at the budget; lexicon counts still return."""
    monkeypatch.setenv("THEMATIC_SOURCE_TIMEOUT", "10")   # ai budget = 5s floor
    monkeypatch.setenv("THEMATIC_AI_INTENT", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")  # _ai_intent_enabled → True
    monkeypatch.setenv("THEMATIC_AI_INTENT_THRESHOLD", "1.0")  # force AI routing

    async def _hang(items):
        await asyncio.sleep(999)

    monkeypatch.setattr(ta, "_ai_classify_intents", _hang)
    _orig_wait_for = asyncio.wait_for
    monkeypatch.setattr(ta.asyncio, "wait_for",
                        lambda coro, timeout: _orig_wait_for(coro, timeout=0.05))

    counts = asyncio.run(ta._resolve_intent_rows(_rows(), use_ai=True))
    assert counts.get("NVDA", 0) > 0     # lexicon read survived the AI timeout


def test_resolve_intent_no_ai_path(monkeypatch):
    monkeypatch.setenv("THEMATIC_AI_INTENT", "false")
    counts = asyncio.run(ta._resolve_intent_rows(_rows(), use_ai=True))
    assert counts.get("NVDA", 0) > 0


class _Resp:
    def __init__(self, status_code, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


class _Client:
    """Stub httpx client: JSON endpoints 403, RSS endpoints 200."""
    def __init__(self, rss_text):
        self.rss_text = rss_text
        self.calls = []

    async def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith(".rss") or "/.rss" in url:
            return _Resp(200, text=self.rss_text)
        return _Resp(403)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>YOLO update: buying more $NVDA calls</title>
    <content type="html">&lt;p&gt;loading up on NVDA, this is the bottom&lt;/p&gt;</content>
  </entry>
</feed>
"""


def test_reddit_falls_back_to_rss_on_403(monkeypatch):
    monkeypatch.setenv("THEMATIC_AI_INTENT", "false")   # lexicon-only, no network
    client = _Client(RSS)
    counts = asyncio.run(ta._reddit_tickers(client))
    assert counts.get("NVDA", 0) > 0
    assert any(".rss" in u for u in client.calls)
