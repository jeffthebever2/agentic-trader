"""Dual-class / renamed ticker folding in _norm_ticker. GOOG/GOOGL (Alphabet),
FB/META etc. refer to one company — folding them prevents a split signal (neither
confirms) and stops two proposals for the same business."""
import asyncio

import web.api.thematic_auto as t


def test_aliases_fold_to_primary():
    assert t._norm_ticker("GOOG") == "GOOGL"
    assert t._norm_ticker("goog") == "GOOGL"
    assert t._norm_ticker("$FB") == "META"
    assert t._norm_ticker("SQ") == "XYZ"
    # non-aliased names untouched
    assert t._norm_ticker("NVDA") == "NVDA"
    assert t._norm_ticker("GOOGL") == "GOOGL"


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


def test_goog_and_googl_merge_into_one_signal():
    ranked, bd = _merge(reddit={"GOOG": 10}, ddg={}, yahoo=["GOOGL"], twitter={"GOOG": 5})
    tickers = [tk for tk, _ in ranked]
    assert tickers.count("GOOGL") == 1
    assert "GOOG" not in tickers
    # all three sources land on the single canonical entry → confirmation bonus
    assert {"reddit", "yahoo", "twitter"}.issubset(set(bd["GOOGL"]))
    assert bd["GOOGL"].get("multi_source_bonus", 0) > 0
