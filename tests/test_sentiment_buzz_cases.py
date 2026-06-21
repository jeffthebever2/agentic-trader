"""Sentiment classification for the spec's example phrases + selling-pressure
categories. Verifies bullish raises / bearish lowers / mixed is limited.
"""
from tradingagents.screening.tweet_intent import classify_intent as C
from tradingagents.screening import tweet_intent as ti


# ── the spec's worked examples ───────────────────────────────────────────────
def test_could_go_either_way_is_neutral():
    r = C("$NVDA could go either way from here")
    assert r.label == ti.UNCLEAR
    assert -0.1 <= r.sentiment <= 0.1
    assert not r.increase_buy and not r.reduce_buy


def test_good_company_high_valuation_is_slightly_bearish():
    r = C("$MSFT good company but valuation is too high here")
    assert r.sentiment < 0                # bearish lean
    assert r.sentiment >= -0.45           # but only SLIGHTLY (limited)
    assert not r.increase_buy


def test_holding_but_wouldnt_buy_is_bearish():
    r = C("$AAPL holding but wouldn't buy here")
    assert r.label == ti.SELL_SIGNAL
    assert r.reduce_buy and not r.increase_buy


def test_taking_profits_after_run_is_bearish():
    r = C("Taking profits on $SMCI after a huge run")
    assert r.label == ti.SELL_SIGNAL
    assert r.reduce_buy and r.sentiment < 0


def test_near_term_concerns_long_term_strong_is_mixed():
    r = C("$TSLA near-term concerns but strong long-term outlook")
    assert abs(r.sentiment) <= 0.35       # mixed → muted, near neutral
    assert not r.increase_buy and not r.reduce_buy


def test_strong_earnings_raising_guidance_is_bullish():
    r = C("$AMD strong earnings and raising guidance for next year")
    assert r.sentiment > 0
    assert not r.reduce_buy


# ── selling-pressure categories (each must reduce buy) ───────────────────────
def test_selling_pressure_categories_reduce_buy():
    bearish = [
        "$XYZ announced a secondary offering to raise capital",      # dilution
        "$XYZ missed earnings and cut guidance",                     # miss + lowered guidance
        "insider selling at $XYZ, the CFO sold a big block",         # insider selling
        "$XYZ trial failed, FDA issued a complete response letter",  # failed catalyst
        "$XYZ downgraded, analysts turning bearish",                 # downgrade / bearish consensus
        "$XYZ filing for bankruptcy, going concern doubt",           # distress
    ]
    for txt in bearish:
        r = C(txt)
        assert r.reduce_buy is True, f"expected reduce_buy for: {txt!r} (got {r.label})"
        assert r.sentiment < 0, f"expected negative sentiment for: {txt!r}"


def test_bullish_fundamentals_do_not_reduce_buy():
    for txt in ("$XYZ beat and raise quarter, record revenue",
                "$XYZ raised guidance after a blowout quarter"):
        r = C(txt)
        assert r.reduce_buy is False
        assert r.sentiment > 0


def test_bare_earnings_mention_stays_neutral_news():
    # must NOT be flipped bullish/bearish by the new fundamentals lexicon
    r = C("$AAPL reports earnings tomorrow after the close")
    assert r.label == ti.NEWS_ONLY
    assert not r.increase_buy and not r.reduce_buy
