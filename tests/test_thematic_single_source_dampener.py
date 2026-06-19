"""Single-source confirmation dampener in _merge_signals. A name carried by one
ordinary source (a lone Reddit pump) is dampened so it can't clear the buy gate
without cross-confirmation; high-trust solo sources (insider, etc.) are exempt.
False-positive reduction."""
import asyncio

import web.api.thematic_auto as t


def _merge(**kwargs):
    async def _identity(tickers):
        return {x.upper() for x in tickers}

    ov, oh = t._validate_tickers, t._get_historical_scores
    t._validate_tickers = _identity
    t._get_historical_scores = lambda n_scans=5: {}
    try:
        return asyncio.run(t._merge_signals(**kwargs))
    finally:
        t._validate_tickers, t._get_historical_scores = ov, oh


def _score(ranked, tk):
    return dict(ranked).get(tk)


def test_lone_reddit_pump_dampened_below_gate():
    # 24 Reddit mentions × 2.0 = 48 (== MIN_SIGNAL_SCORE). Solo → ×0.7 = 33.6,
    # now safely under the buy gate without confirmation.
    ranked, bd = _merge(reddit={"SOLO": 24}, ddg={}, yahoo=[])
    assert _score(ranked, "SOLO") < t.MIN_SIGNAL_SCORE
    assert bd["SOLO"].get("single_source_dampener") == 0.7


def test_two_sources_not_dampened():
    ranked, bd = _merge(reddit={"DUO": 12}, ddg={}, yahoo=["DUO"])
    assert "single_source_dampener" not in bd["DUO"]
    # confirmed name keeps full strength (incl. multi-source bonus)
    assert _score(ranked, "DUO") >= 24


def test_high_trust_solo_exempt():
    # A lone insider cluster buy is genuine signal — not dampened.
    ranked, bd = _merge(reddit={}, ddg={}, yahoo=[], insider={"INS": 20})
    assert "single_source_dampener" not in bd.get("INS", {})
    assert _score(ranked, "INS") == 30.0  # 20 × 1.5, undampened
