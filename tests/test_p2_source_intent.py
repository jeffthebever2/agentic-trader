"""P2 audit fixes (2026-07-05): news/cashtag sources routed through tweet_intent.

Before this batch:
- marketaux counted entities on API sentiment alone — never touched the lexicon,
  never recorded to _SOCIAL_INTENT (no bull/bear modulation, no avoid flag),
  despite having the highest merge multiplier (x3) and a _HIGH_TRUST_SOLO pass.
- The cashtag point-branches in brave / PR RSS / generic RSS / congress news
  added +3/+4 UNFILTERED even when the sibling sell-filter flagged the ticker:
  "\\$ACME announces public offering" blocked the +1 plain-text branch but still
  scored the +3 cashtag.

All paths here are lexicon-only (sync) — no AI, no network (fake clients).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import web.api.thematic_auto as ta  # noqa: E402

SELL_HEADLINE = "$ACME announces public offering of common stock"
BUY_HEADLINE = "Buying more $ZENQ breakout looks strong"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_scan_state():
    ta._reset_social_intent()
    yield
    ta._reset_social_intent()


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class _Client:
    """Fake httpx.AsyncClient returning the same canned response for every GET."""

    def __init__(self, resp: _Resp):
        self._resp = resp
        self.calls = 0

    async def get(self, url, **kw):
        self.calls += 1
        return self._resp


# ── marketaux ─────────────────────────────────────────────────────────────────

def _marketaux_resp(title: str, symbol: str, sent: float = 0.8) -> _Resp:
    return _Resp(json_data={"data": [{
        "title": title,
        "description": "",
        "entities": [{"symbol": symbol, "sentiment_score": sent}],
    }]})


def test_marketaux_sell_headline_blocked_and_recorded(monkeypatch):
    """Positive API sentiment on an offering headline must not pad buzz — and the
    sell intent must land in _SOCIAL_INTENT so merge modulation sees it."""
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "tok")
    client = _Client(_marketaux_resp(
        "ACME announces public offering of common stock", "ACME"))
    counts = _run(ta._marketaux_tickers(client))
    assert "ACME" not in counts
    rec = ta.net_social_buy_intent("ACME")
    assert rec.get("sell", 0) >= 1
    assert rec.get("sell_reason")


def test_marketaux_benign_headline_counted_and_recorded(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "tok")
    client = _Client(_marketaux_resp(
        "ZENQ wins massive new AI contract, shares surge", "ZENQ", sent=0.8))
    counts = _run(ta._marketaux_tickers(client))
    assert counts.get("ZENQ") == int(0.8 * 5) + 2
    # intent recorded → this name now participates in bull/bear modulation
    assert ta.net_social_buy_intent("ZENQ")


def test_marketaux_entity_without_text_still_counted(monkeypatch):
    """No title/description → no intent evidence → keep the entity (fail open on
    counting; blocking on absent evidence would silence the whole source)."""
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "tok")
    client = _Client(_Resp(json_data={"data": [{
        "entities": [{"symbol": "ZENQ", "sentiment_score": 0.5}],
    }]}))
    counts = _run(ta._marketaux_tickers(client))
    assert counts.get("ZENQ") == int(0.5 * 5) + 2


# ── PR RSS (_stocktwits_trending) + generic RSS cashtag branches ─────────────

def _rss_xml(headline: str) -> str:
    return f"<rss><channel><item><title>{headline}</title></item></channel></rss>"


def test_pr_rss_cashtag_blocked_on_sell_headline():
    client = _Client(_Resp(text=_rss_xml(SELL_HEADLINE)))
    counts = _run(ta._stocktwits_trending(client))
    assert "ACME" not in counts
    assert ta.net_social_buy_intent("ACME").get("sell", 0) >= 1


def test_pr_rss_cashtag_counted_on_benign_headline():
    client = _Client(_Resp(text=_rss_xml(BUY_HEADLINE)))
    counts = _run(ta._stocktwits_trending(client))
    assert counts.get("ZENQ", 0) >= 4  # cashtag branch still pays out


def test_generic_rss_cashtag_blocked_on_sell_headline():
    client = _Client(_Resp(text=_rss_xml(SELL_HEADLINE)))
    counts = _run(ta._rss_tickers(client))
    assert "ACME" not in counts


def test_generic_rss_cashtag_counted_on_benign_headline():
    client = _Client(_Resp(text=_rss_xml(BUY_HEADLINE)))
    counts = _run(ta._rss_tickers(client))
    assert counts.get("ZENQ", 0) >= 3


# ── Brave news cashtag branch ─────────────────────────────────────────────────

def _brave_resp(headline: str) -> _Resp:
    return _Resp(json_data={"results": [{"title": headline, "description": ""}]})


def test_brave_cashtag_blocked_on_sell_headline(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "key")
    monkeypatch.setattr(ta, "_BRAVE_USAGE_FILE", tmp_path / "brave_usage.json")
    client = _Client(_brave_resp(SELL_HEADLINE))
    counts = _run(ta._brave_tickers(client))
    assert "ACME" not in counts


def test_brave_cashtag_counted_on_benign_headline(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "key")
    monkeypatch.setattr(ta, "_BRAVE_USAGE_FILE", tmp_path / "brave_usage.json")
    client = _Client(_brave_resp(BUY_HEADLINE))
    counts = _run(ta._brave_tickers(client))
    assert counts.get("ZENQ", 0) >= 3


# ── congress-news helper: sell check without double-recording ────────────────

def test_lexicon_sells_norecord_flags_without_recording():
    sold = ta._lexicon_sells_norecord("ACME announces public offering of common stock")
    assert sold == {"ACME"}
    # must NOT record — the sibling _lexicon_counts call records this headline
    assert ta.net_social_buy_intent("ACME") == {}


def test_lexicon_sells_norecord_empty_on_benign():
    assert ta._lexicon_sells_norecord("ZENQ wins massive new AI contract") == set()
