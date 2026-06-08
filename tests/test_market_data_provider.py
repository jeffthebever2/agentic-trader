"""Tests for Market Data Provider interface — LOG-1."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.data import (
    CachedProvider,
    MarketDataProvider,
    OHLCVBar,
    OHLCVCache,
)


# ── OHLCVBar ──────────────────────────────────────────────────────────────────

def test_ohlcv_bar_to_dict():
    bar = OHLCVBar(date="2024-01-15", open=150.0, high=155.0, low=149.0,
                   close=153.0, volume=1_000_000, ticker="AAPL")
    d = bar.to_dict()
    assert d["ticker"] == "AAPL"
    assert d["date"] == "2024-01-15"
    assert d["close"] == 153.0


# ── Abstract interface ────────────────────────────────────────────────────────

def test_abstract_provider_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MarketDataProvider()


def test_concrete_provider_must_implement_get_bars():
    """Subclass without get_bars raises TypeError on instantiation."""
    class Partial(MarketDataProvider):
        def get_latest_price(self, ticker):
            return 100.0
        # Missing get_bars

    with pytest.raises(TypeError):
        Partial()


# ── OHLCVCache ────────────────────────────────────────────────────────────────

def _make_bars(ticker="AAPL", dates=("2024-01-10", "2024-01-11", "2024-01-12")):
    return [
        OHLCVBar(date=d, open=100.0, high=105.0, low=99.0, close=103.0,
                 volume=500_000, ticker=ticker)
        for d in dates
    ]


def test_cache_put_and_get(tmp_path):
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    bars = _make_bars()
    written = cache.put(bars)
    assert written == 3

    retrieved = cache.get("AAPL", "2024-01-10", "2024-01-12")
    assert len(retrieved) == 3
    assert retrieved[0].date == "2024-01-10"
    assert retrieved[2].date == "2024-01-12"


def test_cache_get_empty_on_miss(tmp_path):
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    result = cache.get("MSFT", "2024-01-01", "2024-01-31")
    assert result == []


def test_cache_has_range(tmp_path):
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    assert not cache.has_range("AAPL", "2024-01-10", "2024-01-12")
    cache.put(_make_bars())
    assert cache.has_range("AAPL", "2024-01-10", "2024-01-12")
    assert not cache.has_range("AAPL", "2023-01-01", "2023-12-31")


def test_cache_coverage_dates(tmp_path):
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    mn, mx = cache.coverage_dates("AAPL")
    assert mn is None and mx is None

    cache.put(_make_bars())
    mn, mx = cache.coverage_dates("AAPL")
    assert mn == "2024-01-10"
    assert mx == "2024-01-12"


def test_cache_upsert_replaces(tmp_path):
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    bars = _make_bars(dates=("2024-01-10",))
    cache.put(bars)

    updated = [OHLCVBar(date="2024-01-10", open=200.0, high=210.0, low=199.0,
                        close=205.0, volume=1_000, ticker="AAPL")]
    cache.put(updated)

    result = cache.get("AAPL", "2024-01-10", "2024-01-10")
    assert result[0].close == 205.0


# ── CachedProvider ────────────────────────────────────────────────────────────

def _mock_upstream(bars_by_ticker: dict):
    upstream = MagicMock()
    def _get_bars(ticker, start, end, interval="1d"):
        return bars_by_ticker.get(ticker, [])
    upstream.get_bars.side_effect = _get_bars
    upstream.get_bars_bulk.side_effect = lambda tickers, start, end, interval="1d": {
        t: bars_by_ticker.get(t, []) for t in tickers
    }
    upstream.get_latest_price.return_value = 100.0
    upstream.source_name = "mock"
    return upstream


def test_cached_provider_serves_from_cache_on_second_call(tmp_path):
    aapl_bars = _make_bars()
    upstream = _mock_upstream({"AAPL": aapl_bars})
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    provider = CachedProvider(upstream, cache=cache)

    r1 = provider.get_bars("AAPL", "2024-01-10", "2024-01-12")
    r2 = provider.get_bars("AAPL", "2024-01-10", "2024-01-12")

    assert len(r1) == 3
    assert len(r2) == 3
    # Second call should come from cache — upstream only called once
    assert upstream.get_bars.call_count == 1


def test_cached_provider_force_refresh_bypasses_cache(tmp_path):
    aapl_bars = _make_bars()
    upstream = _mock_upstream({"AAPL": aapl_bars})
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    provider = CachedProvider(upstream, cache=cache, force_refresh=True)

    provider.get_bars("AAPL", "2024-01-10", "2024-01-12")
    provider.get_bars("AAPL", "2024-01-10", "2024-01-12")

    assert upstream.get_bars.call_count == 2


def test_cached_provider_source_name():
    upstream = _mock_upstream({})
    cache = OHLCVCache(db_path=":memory:")
    provider = CachedProvider(upstream, cache=cache)
    assert "mock" in provider.source_name


def test_cached_provider_get_latest_price_always_upstream(tmp_path):
    upstream = _mock_upstream({})
    upstream.get_latest_price.return_value = 175.50
    cache = OHLCVCache(db_path=str(tmp_path / "test.db"))
    provider = CachedProvider(upstream, cache=cache)

    price = provider.get_latest_price("AAPL")
    assert price == 175.50
    upstream.get_latest_price.assert_called_once_with("AAPL")
