"""Scoring correctness: scan_memory (a ticker's own prior-scan history) must NOT
count as cross-confirmation for the single-source dampener. A name with one live
feed + scan_memory still has zero independent live confirmation → must be
dampened, not exempted."""
import asyncio

import web.api.thematic_auto as t


def _merge(historical=None, **kwargs):
    async def _identity(tickers):
        return {x.upper() for x in tickers}
    ov, oh = t._validate_tickers, t._get_historical_scores
    t._validate_tickers = _identity
    t._get_historical_scores = lambda n_scans=5: (historical or {})
    try:
        return asyncio.run(t._merge_signals(**kwargs))
    finally:
        t._validate_tickers, t._get_historical_scores = ov, oh


def test_one_live_source_plus_scan_memory_still_dampened():
    # SOLO has reddit today + strong history. Live sources = {reddit} only →
    # dampener must still apply (scan_memory is not confirmation).
    ranked, bd = _merge(historical={"SOLO": 25.0},
                        reddit={"SOLO": 20}, ddg={}, yahoo=[])
    assert bd["SOLO"].get("single_source_dampener") == 0.7


def test_two_live_sources_not_dampened_even_with_memory():
    ranked, bd = _merge(historical={"DUO": 25.0},
                        reddit={"DUO": 12}, ddg={"DUO": 12}, yahoo=[])
    assert "single_source_dampener" not in bd["DUO"]


def test_high_trust_solo_still_exempt_with_memory():
    ranked, bd = _merge(historical={"INS": 25.0},
                        reddit={}, ddg={}, yahoo=[], insider={"INS": 20})
    assert "single_source_dampener" not in bd.get("INS", {})
