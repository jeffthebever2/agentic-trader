"""The multi-source confirmation bonus must come from QUALITY feeds, not from two
screener/mover feeds agreeing. Two junk sources are not a confirmed thesis."""
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


def test_two_quality_sources_earn_bonus():
    _, bd = _merge(reddit={"AAA": 8}, ddg={"AAA": 8}, yahoo=[])
    assert bd["AAA"].get("multi_source_bonus", 0) > 0


def test_two_low_signal_sources_no_bonus():
    # finviz + yahoo_movers are screener feeds (not quality) — agreeing earns no
    # confirmation bonus. (Score >= 80 keeps it past the quality gate for the test.)
    _, bd = _merge(reddit={}, ddg={}, yahoo=[],
                   finviz={"JUNK": 30}, yahoo_movers={"JUNK": 30})
    assert "multi_source_bonus" not in bd.get("JUNK", {})


def test_one_quality_one_junk_no_bonus():
    # reddit (quality) + finviz (junk) → only 1 quality source → no confirmation.
    _, bd = _merge(reddit={"MIX": 25}, ddg={}, yahoo=[], finviz={"MIX": 10})
    assert "multi_source_bonus" not in bd.get("MIX", {})
