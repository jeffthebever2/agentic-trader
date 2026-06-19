"""Broad-market index ETFs (SPY/QQQ/IWM/...) are social noise, not the single-name
conviction stock picks this book trades — they must never rank or seed a signal.
Sector/leveraged ETFs are intentionally allowed (deliberate momentum vehicles)."""
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


def test_broad_market_etfs_excluded():
    ranked, _ = _merge(
        reddit={"SPY": 30, "QQQ": 25, "IWM": 20, "NVDA": 20},
        ddg={}, yahoo=[],
    )
    tickers = {tk for tk, _ in ranked}
    assert "NVDA" in tickers
    for etf in ("SPY", "QQQ", "IWM"):
        assert etf not in tickers


def test_sector_and_single_names_kept():
    # Sector/leveraged ETF + a real stock both survive (not broad-market).
    ranked, _ = _merge(reddit={"SOXL": 20, "AMD": 20}, ddg={}, yahoo=[])
    tickers = {tk for tk, _ in ranked}
    assert {"SOXL", "AMD"}.issubset(tickers)
