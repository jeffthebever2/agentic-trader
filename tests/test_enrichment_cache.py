"""Tests for the per-ticker enrichment cache/batcher (thematic revamp Stage 0)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from tradingagents.screening.enrichment_cache import EnrichmentCache


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _cache(ttl=20.0, clock=None):
    return EnrichmentCache("test", Path(tempfile.mkdtemp()), ttl_hours=ttl,
                           now_fn=clock or _Clock())


def test_put_get_roundtrip():
    c = _cache()
    c.put("nvda", {"short_pct": 3.2})
    assert c.get("NVDA") == {"short_pct": 3.2}
    assert c.get("MISSING") is None


def test_ttl_expiry():
    clk = _Clock(1000.0)
    c = _cache(ttl=1.0, clock=clk)  # 1h TTL
    c.put("AAPL", 42)
    assert c.get("AAPL") == 42
    clk.t += 3599            # still < 1h
    assert c.get("AAPL") == 42
    clk.t += 2               # now > 1h
    assert c.get("AAPL") is None


def test_ttl_zero_never_expires():
    clk = _Clock(1000.0)
    c = _cache(ttl=0.0, clock=clk)
    c.put("MSFT", 1)
    clk.t += 10_000_000
    assert c.get("MSFT") == 1


def test_persistence_across_instances():
    d = Path(tempfile.mkdtemp())
    c1 = EnrichmentCache("earnings", d, ttl_hours=20.0)
    c1.put("TSLA", {"date": "2026-07-20"})
    c2 = EnrichmentCache("earnings", d, ttl_hours=20.0)  # fresh instance, same dir
    assert c2.get("TSLA") == {"date": "2026-07-20"}


def test_get_or_fetch_batches_only_missing():
    c = _cache()
    c.put("NVDA", 1)  # pre-seed one
    calls = []

    def fetch(missing):
        calls.append(list(missing))
        return {t: len(t) for t in missing}

    out = c.get_or_fetch(["nvda", "amd", "pltr"], fetch)
    assert out == {"NVDA": 1, "AMD": 3, "PLTR": 4}
    # fetch_fn called ONCE, with only the missing tickers (not the cached NVDA)
    assert calls == [["AMD", "PLTR"]]
    # fetched values are now cached → second call fetches nothing
    calls.clear()
    out2 = c.get_or_fetch(["nvda", "amd", "pltr"], fetch)
    assert out2 == {"NVDA": 1, "AMD": 3, "PLTR": 4}
    assert calls == []


def test_get_or_fetch_failure_degrades():
    c = _cache()

    def boom(missing):
        raise RuntimeError("api down")

    out = c.get_or_fetch(["AMD", "PLTR"], boom)
    assert out == {}  # no crash, just absent keys


def test_get_or_fetch_none_values_not_cached():
    c = _cache()

    def fetch(missing):
        return {"AMD": None, "PLTR": 5}  # AMD has no data

    out = c.get_or_fetch(["AMD", "PLTR"], fetch)
    assert out == {"PLTR": 5}
    assert c.get("AMD") is None and c.get("PLTR") == 5


def test_stats():
    c = _cache()
    c.put_many({"A": 1, "B": 2})
    s = c.stats()
    assert s["entries"] == 2 and s["fresh"] == 2 and s["namespace"] == "test"
