"""Per-source contribution cap in _merge_signals. A single viral feed must not
dominate a ticker's raw score — each source's total per-ticker contribution is
capped at _MAX_PER_SOURCE_PTS so confirmation breadth wins over one feed's
volume. Reduces single-source-spike false positives."""
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


def _bd(breakdown, tk, src):
    return breakdown.get(tk, {}).get(src, 0.0)


def test_single_source_spike_is_capped():
    # 500 Reddit mentions × 2.0 = 1000 raw, capped to 60 before the solo dampener.
    ranked, bd = _merge(reddit={"SPIKE": 500}, ddg={}, yahoo=[])
    assert _bd(bd, "SPIKE", "reddit") == t._MAX_PER_SOURCE_PTS
    # capped (60) then single-source dampened (×0.7) → 42
    assert dict(ranked)["SPIKE"] <= t._MAX_PER_SOURCE_PTS


def test_normal_counts_unaffected():
    # Ordinary volumes stay below the cap and are unchanged.
    _, bd = _merge(reddit={"NORM": 12}, ddg={}, yahoo=[])
    assert _bd(bd, "NORM", "reddit") == 24.0  # 12 × 2.0, uncapped


def test_breadth_beats_one_capped_source():
    # A name confirmed across 3 modest sources should outrank a single capped
    # mega-spike — confirmation breadth wins.
    ranked, _ = _merge(
        reddit={"SPIKE": 500},                      # capped at 60 (×0.7 solo = 42)
        ddg={"BROAD": 8},                            # 12
        yahoo=["BROAD"],                             # +3
        twitter={"BROAD": 8},                        # +20
        google_news={"BROAD": 8},                    # +12  (4 sources → multi-source bonus)
    )
    scores = dict(ranked)
    assert scores["BROAD"] > scores["SPIKE"]
