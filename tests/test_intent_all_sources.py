"""Intent now spans RSS-tweets + news, not just Reddit. Shared resolver + the
lexicon helpers must keep sellers/warnings from padding buzz on every source.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta
from tradingagents.screening import tweet_intent as ti


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("THEMATIC_AI_INTENT", "false")   # lexicon-deterministic
    ta._reset_social_intent()
    yield
    ta._reset_social_intent()


# ── shared resolver ─────────────────────────────────────────────────────────
def test_resolve_intent_rows_weights_and_blocks():
    rows = [
        {"ticker": "AAPL", "text": "loading $AAPL, started a position", "lex": None, "weight": 5},
        {"ticker": "TSLA", "text": "trimming $TSLA, took profits", "lex": None, "weight": 5},
    ]
    # lex None → resolver computes it; AI off
    for r in rows:
        r["lex"] = ti.classify_intent(r["text"], ticker=r["ticker"])
    counts = asyncio.run(ta._resolve_intent_rows(rows, use_ai=False))
    assert counts.get("AAPL") == 5          # buyer counted at its weight
    assert "TSLA" not in counts             # seller blocked
    assert ta.net_social_buy_intent("TSLA").get("sell", 0) == 1


def test_lexicon_counts_news():
    # A plain news headline (NEWS_ONLY) counts at a REDUCED weight — attention, not
    # conviction, so raw volume can't pump buzz; a warning headline doesn't count.
    nvda = ta._lexicon_counts("$NVDA reports earnings tomorrow").get("NVDA")
    assert 0 < nvda < 1                       # news counts, but down-weighted vs a buy
    ta._reset_social_intent()
    assert "MSTR" not in ta._lexicon_counts("$MSTR overextended here, analysts warn to avoid")


def test_lexicon_counts_weight():
    # source weight scales the (conviction-weighted) news contribution linearly
    one = ta._lexicon_counts("$AMD announces new chip", 1).get("AMD")
    ta._reset_social_intent()
    two = ta._lexicon_counts("$AMD announces new chip", 2).get("AMD")
    assert 0 < one < 1
    assert two == pytest.approx(2 * one)


def test_lexicon_sell_tickers():
    sells = ta._lexicon_sell_tickers("$XYZ is a sell, taking profits and exiting")
    assert "XYZ" in sells


# ── RSS-tweet source is now intent-aware ───────────────────────────────────────
class _Resp:
    def __init__(self, xml): self.status_code = 200; self.text = xml
class _Client:
    def __init__(self, xml): self._xml = xml
    async def get(self, url, headers=None, timeout=None): return _Resp(self._xml)


def _feed(*items):
    body = "".join(f"<item><title>{t}</title><description>{d}</description></item>" for t, d in items)
    return f"<rss><channel>{body}</channel></rss>"


def test_trusted_twitter_intent_aware(monkeypatch):
    monkeypatch.setattr(ta, "TRUSTED_TWITTER_FEEDS", ["http://x/feed.xml"])
    xml = _feed(
        ("Loading up on $ASTS here, starting a position", ""),
        ("Trimming $NVDA, taking profits into this rip", ""),
    )
    counts = asyncio.run(ta._trusted_twitter_tickers(_Client(xml)))
    assert counts.get("ASTS", 0) >= 5          # cashtag buyer at high weight
    assert "NVDA" not in counts                # seller blocked from buzz
    assert ta.net_social_buy_intent("NVDA").get("sell", 0) == 1


def test_trusted_twitter_buyer_cashtag_weight(monkeypatch):
    monkeypatch.setattr(ta, "TRUSTED_TWITTER_FEEDS", ["http://x/feed.xml"])
    counts = asyncio.run(ta._trusted_twitter_tickers(_Client(_feed(("bought $MU, adding", "")))))
    assert counts.get("MU", 0) >= 5
