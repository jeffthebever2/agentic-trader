"""Tests for the pure X (Twitter) v2 cashtag sentiment core (thematic revamp)."""
from __future__ import annotations

from tradingagents.screening.x_sentiment import (
    build_cashtag_queries, parse_tweets, score_buzz, TickerBuzz,
)


# ── Query builder ─────────────────────────────────────────────────────────────

def test_query_builds_one_batched_or_group():
    qs = build_cashtag_queries(["nvda", "pltr", "tsla"])
    assert qs == ["($NVDA OR $PLTR OR $TSLA) lang:en -is:retweet"]


def test_query_dedups_and_filters_nonalpha():
    qs = build_cashtag_queries(["NVDA", "nvda", "BRK.B", "123", ""])
    assert qs == ["($NVDA) lang:en -is:retweet"]


def test_query_chunks_over_length_cap():
    # 60 distinct ALPHA cashtags (digits would be filtered by the alpha guard).
    tks = [chr(65 + i // 26 % 26) + chr(65 + i % 26) + "X" for i in range(60)]
    qs = build_cashtag_queries(tks, query_max=120)
    assert len(qs) > 1                                  # split into multiple queries
    assert all(len(q) <= 120 for q in qs)               # each under the cap
    assert all(q.startswith("(") and "lang:en" in q for q in qs)


def test_query_empty():
    assert build_cashtag_queries([]) == []


# ── Tweet parsing ─────────────────────────────────────────────────────────────

def _tw(text, likes=0, rts=0, impr=0):
    return {"text": text, "public_metrics": {"like_count": likes, "retweet_count": rts,
                                              "quote_count": 0, "reply_count": 0,
                                              "impression_count": impr}}


def test_parse_tweets_counts_and_weights():
    tweets = [
        _tw("$NVDA to the moon 🚀", likes=100, rts=50, impr=10000),
        _tw("bearish on $NVDA and $AMD here", likes=5, impr=200),
        _tw("$TSLA not in the set... $NVDA yes", likes=1),
    ]
    out = parse_tweets(tweets, ["NVDA", "AMD"])
    assert set(out) == {"NVDA", "AMD"}         # TSLA ignored (not requested)
    assert out["NVDA"].mentions == 3
    assert out["NVDA"].engagement == 156.0     # tw1 (100+50) + tw2 (5) + tw3 (1 like)
    assert out["NVDA"].impressions == 10200.0
    assert out["AMD"].mentions == 1


def test_parse_tweets_multi_ticker_post_counts_each_once():
    out = parse_tweets([_tw("$NVDA $NVDA $AMD spam", likes=10)], ["NVDA", "AMD"])
    assert out["NVDA"].mentions == 1 and out["AMD"].mentions == 1  # deduped within a post


def test_parse_tweets_garbage_safe():
    assert parse_tweets(None, ["NVDA"]) == {}
    assert parse_tweets(["not a dict", 5], ["NVDA"]) == {}


def test_parse_tweets_caps_texts():
    tweets = [_tw("$NVDA " + str(i)) for i in range(20)]
    out = parse_tweets(tweets, ["NVDA"], max_texts_per_ticker=3)
    assert out["NVDA"].mentions == 20 and len(out["NVDA"].texts) == 3


# ── Scoring ───────────────────────────────────────────────────────────────────

def test_score_buzz_attention_only():
    b = TickerBuzz("NVDA", mentions=4, engagement=3000)
    s = score_buzz(b)  # no intent_fn
    assert s["mentions"] == 4 and s["attention"] == 7.0 and s["bull"] == 0 and s["net_intent"] is None


def test_score_buzz_with_intent():
    b = TickerBuzz("NVDA", mentions=3, texts=["buy the dip", "dumping this", "loading up"])

    def intent(t):
        t = t.lower()
        if "buy" in t or "loading" in t:
            return "BUY"
        if "dump" in t:
            return "SELL"
        return "HOLD"

    s = score_buzz(b, intent_fn=intent)
    assert s["bull"] == 2 and s["bear"] == 1
    assert s["net_intent"] == round((2 - 1) / 3, 3)


def test_score_buzz_intent_errors_ignored():
    b = TickerBuzz("NVDA", mentions=1, texts=["x"])

    def boom(t):
        raise ValueError("bad")

    s = score_buzz(b, intent_fn=boom)  # must not raise
    assert s["bull"] == 0 and s["bear"] == 0
