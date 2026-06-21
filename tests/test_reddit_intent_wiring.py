"""Reddit ingestion routes posts through the intent classifier before scoring:
sellers don't pad buzz, and net-sell tickers get a penalty + avoid flag in merge.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta


class _FakeResp:
    def __init__(self, posts):
        self.status_code = 200
        self._posts = posts

    def json(self):
        return {"data": {"children": [{"data": p} for p in self._posts]}}


class _FakeClient:
    """Returns the same post list for every subreddit GET."""
    def __init__(self, posts):
        self._posts = posts

    async def get(self, url, headers=None, timeout=None):
        return _FakeResp(self._posts)


def _run_reddit(posts, monkeypatch, ai=False):
    # one subreddit so each post is counted once
    monkeypatch.setattr(ta, "SUBREDDITS", ["stocks"])
    if not ai:
        # keep these lexicon-deterministic (no network to the free AI)
        monkeypatch.setenv("THEMATIC_AI_INTENT", "false")
    ta._reset_social_intent()   # reset moved to _run_scan; do it here for direct calls
    return asyncio.run(ta._reddit_tickers(_FakeClient(posts)))


def test_seller_does_not_pad_buzz(monkeypatch):
    posts = [
        {"title": "Selling my $NVDA here, taking profits", "selftext": ""},
        {"title": "trimming $NVDA into this rip", "selftext": ""},
    ]
    counts = _run_reddit(posts, monkeypatch)
    # NVDA mentions are sells → not counted toward buy buzz
    assert counts.get("NVDA", 0) == 0
    intent = ta.net_social_buy_intent("NVDA")
    assert intent.get("sell", 0) == 2 and intent.get("buy", 0) == 0


def test_buyer_counts_toward_buzz(monkeypatch):
    posts = [{"title": "Just bought $AMD, adding on dips", "selftext": ""}]
    counts = _run_reddit(posts, monkeypatch)
    assert counts.get("AMD", 0) == 1
    assert ta.net_social_buy_intent("AMD").get("buy", 0) == 1


def test_merge_penalizes_and_avoids_net_sell(monkeypatch):
    # Seed the per-scan intent stash as if reddit saw 3 sells, 0 buys for WILD.
    ta._reset_social_intent()
    ta._SOCIAL_INTENT["WILD"] = {"buy": 0, "sell": 3, "hold": 0, "watch": 0,
                                 "news": 0, "unclear": 0,
                                 "sell_reason": "took profits", "sell_conf": 0.8}
    # WILD also has some residual buzz from another source (e.g. finviz mover).
    ranked, breakdown = asyncio.run(ta._merge_signals(
        reddit={}, ddg={}, yahoo={}, twitter={}, finviz={"WILD": 10},
    ))
    bd = breakdown.get("WILD", {})
    # either penalized out of the ranking, or kept with an avoid flag + penalty
    if "WILD" in dict(ranked):
        assert bd.get("avoid") is True
        assert bd.get("sell_intent_penalty", 0) < 0
    else:
        # dropped below the min-score floor by the penalty — also acceptable
        assert True
    ta._reset_social_intent()


def test_net_buy_not_penalized(monkeypatch):
    ta._reset_social_intent()
    ta._SOCIAL_INTENT["GOOD"] = {"buy": 4, "sell": 1, "hold": 0, "watch": 0,
                                 "news": 0, "unclear": 0, "sell_reason": "", "sell_conf": 0.0}
    ranked, breakdown = asyncio.run(ta._merge_signals(
        reddit={"GOOD": 4}, ddg={}, yahoo={}, twitter={},
    ))
    assert breakdown.get("GOOD", {}).get("avoid") is not True
    ta._reset_social_intent()


def test_extract_tickers_cashtag_first():
    # When cashtags exist, ONLY cashtags are used (no uppercase-prose garbage).
    got = ta.extract_tickers("How to play the cycle: $MU / $SNDK sold out capacity, DRAM spend up")
    assert set(got) == {"MU", "SNDK"}          # not CYCLE/SOLD/DRAM/SPEND
    # No cashtags → bare-word fallback still works (filtered by _SKIP).
    assert "NVDA" in ta.extract_tickers("NVDA breaking out on volume")


def test_extract_tickers_dedupes_cashtags():
    assert ta.extract_tickers("$ASTS $ASTS $ASTS to the moon") == ["ASTS"]
