"""Sentiment-weighted buzz (tradingagents/screening/buzz_score.py).

Verifies the spec: bullish raises buzz, bearish lowers it, neutral/mixed is limited,
volume alone does not pump, and thousands of bearish mentions != thousands bullish.
"""
import math

import pytest

from tradingagents.screening import buzz_score as bz
from tradingagents.screening import tweet_intent as ti

CFG = bz.DEFAULT


def _tally(bull_w=0.0, bear_w=0.0, neut_w=0.0, buy=0, sell=0, hold=0, watch=0, news=0, unclear=0):
    return dict(bull_w=bull_w, bear_w=bear_w, neut_w=neut_w, buy=buy, sell=sell,
                hold=hold, watch=watch, news=news, unclear=unclear)


# ── core behaviours ─────────────────────────────────────────────────────────
def test_bullish_raises_buzz():
    base = 30.0
    b = bz.compute_buzz(base, _tally(bull_w=3.0, buy=4))
    assert b.bull > 0 and b.bear == 0
    assert b.buzz > base                 # bullish conviction lifts buzz
    assert b.net_sentiment > 0.5


def test_bearish_lowers_buzz():
    base = 30.0
    b = bz.compute_buzz(base, _tally(bear_w=3.0, sell=4))
    assert b.bear > 0 and b.bull == 0
    assert b.buzz < base                 # bearish conviction cuts buzz
    assert b.net_sentiment < -0.5
    assert b.avoid is True


def test_neutral_has_limited_impact():
    base = 30.0
    b = bz.compute_buzz(base, _tally(neut_w=5.0, hold=3, news=4, unclear=3))
    # neutral does not move the score materially in either direction
    assert abs(b.buzz - base) < 1.0
    assert -0.2 < b.net_sentiment < 0.2


def test_volume_alone_does_not_pump():
    # Two names, identical bullish conviction, but one has 10x the neutral chatter.
    quiet = bz.compute_buzz(30.0, _tally(bull_w=2.0, buy=3, news=2))
    noisy = bz.compute_buzz(30.0, _tally(bull_w=2.0, buy=3, news=200, unclear=200))
    # the volume term is capped, so 400 extra neutral mentions barely move buzz
    assert noisy.volume <= CFG.vol_cap
    assert abs(noisy.buzz - quiet.buzz) < 1.0


def test_thousands_bearish_ne_thousands_bullish():
    bullish = bz.compute_buzz(40.0, _tally(bull_w=20.0, buy=2000))
    bearish = bz.compute_buzz(40.0, _tally(bear_w=20.0, sell=2000))
    assert bullish.buzz > bearish.buzz + 30      # wildly different, not the same buzz
    assert bullish.net_sentiment > 0.9
    assert bearish.net_sentiment < -0.9


def test_buzz_floored_at_zero():
    b = bz.compute_buzz(5.0, _tally(bear_w=50.0, sell=999))
    assert b.buzz == 0.0                          # heavy bear can't drive negative


def test_bull_bear_ratio_and_counts():
    b = bz.compute_buzz(30.0, _tally(bull_w=6.0, bear_w=2.0, buy=6, sell=2, hold=1))
    assert b.bull_bear_ratio == pytest.approx(3.0, rel=0.01)
    assert b.n_bull == 6 and b.n_bear == 2 and b.n_neutral == 1
    assert b.n_total == 9


def test_mixed_is_between_and_muted():
    # roughly balanced bull/bear → near-neutral, small net move
    b = bz.compute_buzz(30.0, _tally(bull_w=3.0, bear_w=3.0, buy=3, sell=3))
    assert -0.1 < b.net_sentiment < 0.1
    assert b.buzz < 30.0   # bear penalty slightly outweighs the lighter bull bonus


def test_buzz_varies_across_inputs():
    # distinct conviction profiles must produce distinct buzz (no clustering)
    profiles = [
        _tally(bull_w=8.0, buy=8), _tally(bull_w=2.0, buy=2),
        _tally(bear_w=4.0, sell=4), _tally(neut_w=4.0, news=4),
        _tally(bull_w=4.0, bear_w=1.0, buy=4, sell=1),
    ]
    buzzes = [round(bz.compute_buzz(30.0, p).buzz, 1) for p in profiles]
    assert len(set(buzzes)) == len(buzzes)        # all different


# ── count_weight (conviction-weighted volume) ───────────────────────────────
def test_count_weight_ordering():
    buy = ti.IntentResult("X", ti.BUY_SIGNAL, "bought", 0.7, 0.8, "", True, False)
    watch = ti.IntentResult("X", ti.WATCHLIST_ONLY, "watching", 0.1, 0.5, "", False, False)
    news = ti.IntentResult("X", ti.NEWS_ONLY, "news", 0.0, 0.5, "", False, False)
    unclear = ti.IntentResult("X", ti.UNCLEAR, "none", 0.0, 0.2, "", False, False)
    sell = ti.IntentResult("X", ti.SELL_SIGNAL, "sold", -0.7, 0.8, "", False, True)
    assert bz.count_weight(buy) > bz.count_weight(watch) > bz.count_weight(unclear)
    assert bz.count_weight(buy) >= bz.count_weight(news)
    assert bz.count_weight(sell) == 0.0           # sellers never pad volume


def test_contribution_signs():
    buy = ti.IntentResult("X", ti.BUY_SIGNAL, "bought", 0.7, 0.8, "", True, False)
    sell = ti.IntentResult("X", ti.SELL_SIGNAL, "sold", -0.7, 0.8, "", False, True)
    hold = ti.IntentResult("X", ti.HOLD_SIGNAL, "holding", 0.0, 0.55, "", False, False)
    assert bz.contribution(buy)[0] > 0 and bz.contribution(buy)[1] == 0
    assert bz.contribution(sell)[1] > 0 and bz.contribution(sell)[0] == 0
    assert bz.contribution(hold)[2] > 0           # neutral bucket


# ── blend_sentiment ─────────────────────────────────────────────────────────
def test_blend_bearish_crowd_drags_down():
    # LLM bullish but crowd strongly bearish → blended is pulled to the bearish side
    assert bz.blend_sentiment(0.6, -0.8) <= -0.3
    # both mild → average
    assert bz.blend_sentiment(0.4, 0.2) == pytest.approx(0.3, abs=0.01)
    # missing social → llm passes through
    assert bz.blend_sentiment(0.5, None) == 0.5
    assert bz.blend_sentiment(None, -0.4) == -0.4


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("BUZZ_K_BEAR", "20")
    cfg = bz.BuzzConfig.from_env()
    assert cfg.k_bear == 20.0
