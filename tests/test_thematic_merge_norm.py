"""Cross-source ticker normalization in _merge_signals. Case variants and
cashtags ('nvda', 'NVDA', '$NVDA') must collapse to one entry so the
multi-source confirmation bonus fires and scores aren't fragmented — accuracy
improvement that reduces split/duplicate signals."""
import asyncio

import web.api.thematic_auto as t


def test_norm_ticker():
    assert t._norm_ticker("nvda") == "NVDA"
    assert t._norm_ticker(" NVDA ") == "NVDA"
    assert t._norm_ticker("$NVDA") == "NVDA"
    assert t._norm_ticker("$nvda ") == "NVDA"
    assert t._norm_ticker(None) == ""
    assert t._norm_ticker("") == ""


def _merge(**kwargs):
    """Call _merge_signals with network validation stubbed to identity."""
    async def _identity(tickers):
        return {x.upper() for x in tickers}

    orig_validate = t._validate_tickers
    orig_hist = t._get_historical_scores
    t._validate_tickers = _identity
    t._get_historical_scores = lambda n_scans=5: {}
    try:
        return asyncio.run(t._merge_signals(**kwargs))
    finally:
        t._validate_tickers = orig_validate
        t._get_historical_scores = orig_hist


def test_case_and_cashtag_variants_merge():
    # Same name arrives lower-case (reddit), upper (yahoo), cashtag (twitter).
    ranked, breakdown = _merge(
        reddit={"nvda": 5},
        ddg={},
        yahoo=["NVDA"],
        twitter={"$NVDA": 3},
    )
    tickers = [tk for tk, _ in ranked]
    # Exactly one canonical NVDA entry, not three fragments.
    assert tickers.count("NVDA") == 1
    assert "nvda" not in tickers and "$NVDA" not in tickers
    # All three sources recorded against the single entry → multi-source bonus.
    bd = breakdown["NVDA"]
    assert {"reddit", "yahoo", "twitter"}.issubset(set(bd))
    assert bd.get("multi_source_bonus", 0) > 0


def test_distinct_tickers_not_merged():
    ranked, _ = _merge(reddit={"AAPL": 4, "msft": 4}, ddg={}, yahoo=[])
    tickers = {tk for tk, _ in ranked}
    assert {"AAPL", "MSFT"}.issubset(tickers)
