"""Tweet/social intent classifier — separate buy/sell/hold/news BEFORE scoring.

Core guarantee: a positive-sounding mention is NOT a buy when the author is
selling, trimming, taking profits, exiting, or warning. Only BUY_SIGNAL raises
buy conviction; SELL_SIGNAL flags reduce/block.
"""
import pytest

from tradingagents.screening.tweet_intent import (
    BUY_SIGNAL, HOLD_SIGNAL, NEWS_ONLY, SELL_SIGNAL, UNCLEAR, WATCHLIST_ONLY,
    aggregate_for_tickers, classify_intent,
)


@pytest.mark.parametrize("text,label", [
    ("Just bought $NVDA, adding more on dips", BUY_SIGNAL),
    ("Started a position in $PLTR, going long", BUY_SIGNAL),
    ("Backing up the truck on $AMD here", BUY_SIGNAL),
    ("buying the dip on $SOFI", BUY_SIGNAL),
    ("Selling my $NVDA here, taking profits", SELL_SIGNAL),
    ("Trimming $TSLA into this rip", SELL_SIGNAL),
    ("Exited $COIN, reducing exposure", SELL_SIGNAL),
    ("$MSTR looks overextended here, be careful", SELL_SIGNAL),
    ("No longer like $RIVN, warning everyone", SELL_SIGNAL),
    ("Still holding $AMD, not selling", HOLD_SIGNAL),
    ("Not selling my $TSLA", HOLD_SIGNAL),
    ("$TSLA on my watchlist, waiting for confirmation", WATCHLIST_ONLY),
    ("Keeping an eye on $HOOD if it breaks out", WATCHLIST_ONLY),
    ("$AAPL reports earnings tomorrow after close", NEWS_ONLY),
    ("$XYZ announces a new partnership", NEWS_ONLY),
    ("$SOFI", UNCLEAR),
])
def test_labels(text, label):
    assert classify_intent(text).label == label


def test_mixed_tweet_action_beats_sentiment():
    # The spec's exact example: bullish words but the ACTION is selling → SELL.
    r = classify_intent("I love $XYZ long term but I'm trimming here")
    assert r.label == SELL_SIGNAL
    assert r.reduce_buy and not r.increase_buy
    assert "mixed" in r.reason.lower()


def test_only_buy_increases_score():
    assert classify_intent("bought $NVDA").increase_buy is True
    for t in ("sold $NVDA", "holding $NVDA", "$NVDA on watch", "$NVDA earnings out", "$NVDA"):
        assert classify_intent(t).increase_buy is False


def test_sell_flags_reduce_block():
    for t in ("selling $NVDA", "trimming $NVDA", "taking profits on $NVDA",
              "exiting $NVDA", "$NVDA overextended, be careful"):
        r = classify_intent(t)
        assert r.label == SELL_SIGNAL and r.reduce_buy is True


def test_negated_sell_is_hold_not_sell():
    for t in ("not selling $AMD", "won't trim $AMD", "no plans to sell $AMD"):
        r = classify_intent(t)
        assert r.label != SELL_SIGNAL
        assert r.reduce_buy is False


def test_output_schema():
    d = classify_intent("bought $NVDA, adding").to_dict()
    assert set(d) == {"ticker", "label", "action", "sentiment", "confidence",
                      "reason", "increase_buy", "reduce_buy"}
    assert -1.0 <= d["sentiment"] <= 1.0
    assert 0.0 <= d["confidence"] <= 1.0


def test_sentiment_direction():
    assert classify_intent("bought $NVDA, adding").sentiment > 0
    assert classify_intent("selling $NVDA, taking profits").sentiment < 0


def test_multi_ticker_split():
    out = aggregate_for_tickers("buying $AAPL aggressively, dumping $TSLA into this pump",
                                ["AAPL", "TSLA"])
    assert out["AAPL"].label == BUY_SIGNAL and out["AAPL"].increase_buy
    assert out["TSLA"].label == SELL_SIGNAL and out["TSLA"].reduce_buy


def test_multi_ticker_independent_actions():
    out = aggregate_for_tickers("long $NVDA, took profits on $SMCI", ["NVDA", "SMCI"])
    assert out["NVDA"].label == BUY_SIGNAL
    assert out["SMCI"].label == SELL_SIGNAL


def test_single_ticker_uses_whole_text_mixed_rule():
    # one ticker, bullish clause holds the mention, but action elsewhere is sell
    out = aggregate_for_tickers("I love $XYZ long term but trimming today", ["XYZ"])
    assert out["XYZ"].label == SELL_SIGNAL


def test_empty_text():
    r = classify_intent("")
    assert r.label == UNCLEAR and r.confidence == 0.0


# ── real-world cases surfaced by the live RSS test ──────────────────────────────
def test_sold_out_capacity_is_not_a_sell():
    # "sold out" = demand/capacity (bullish), NOT the author exiting.
    for t in ("$MU sold out capacity, pricing power strong",
              "chips are selling out everywhere", "GPUs flying off the shelves"):
        assert classify_intent(t).label != SELL_SIGNAL


def test_sold_out_of_position_is_a_sell():
    for t in ("I sold out of $NVDA completely", "sold out my entire $TSLA position"):
        assert classify_intent(t).label == SELL_SIGNAL


def test_long_list_tweet_demoted_to_watchlist():
    # A many-ticker value-chain/thesis list is a watchlist, not N buy calls.
    txt = ("PHOTONICS VALUE CHAIN: $COHR $CIEN $LITE $AAOI $POET $GLW $FN $MRVL "
           "$NVDA $AVGO $ANET $KEYS are the names")
    tickers = ["COHR", "CIEN", "LITE", "AAOI", "POET", "GLW", "FN", "MRVL", "NVDA", "AVGO", "ANET", "KEYS"]
    out = aggregate_for_tickers(txt, tickers)
    assert all(r.label == WATCHLIST_ONLY for r in out.values())
    assert all(not r.increase_buy for r in out.values())


def test_long_bearish_list_still_warns():
    txt = ("These are all topping and overextended, I'm warning you: "
           "$AAA $BBB $CCC $DDD $EEE $FFF $GGG")
    out = aggregate_for_tickers(txt, ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"])
    assert all(r.label == SELL_SIGNAL and r.reduce_buy for r in out.values())
