"""Per-ticker enrichment cache + batcher — thematic full-revamp Stage 0.

The revamp moves fundamental/analyst/sentiment data from blind PRE-merge universe
scans to a POST-merge "dossier" over the top ~20-40 ranked names, then reuses that
one dossier three times (ground the AI pick, size the position, drive data-aware
exits). This module is the shared, daily-TTL cache/batcher that makes those
enrichment fetches cheap and quota-safe — protecting the same FMP/Finnhub keys the
compliance quote gateway depends on.

Pure/sync/network-free: the cache stores, expires, and BATCHES; the actual network
fetch is INJECTED by the caller (the web/api layer). Disk-backed JSON, one file per
field namespace, keyed TICKER -> {v: value, ts: epoch}. Fully unit-testable with a
fake fetcher and an injected clock. Never raises on I/O — a cache miss degrades to
a fetch, and a fetch failure degrades to an absent key.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


@dataclass
class EnrichmentCache:
    """Daily-TTL per-ticker cache for one enrichment field namespace.

    namespace  — logical field group (e.g. "short_interest", "earnings_date").
    base_dir   — directory for the JSON store (enrich_<namespace>.json).
    ttl_hours  — freshness window; <=0 means "never expires" (session-static data).
    now_fn     — injected clock (defaults to time.time) so tests control expiry.
    """
    namespace: str
    base_dir: Path
    ttl_hours: float = 20.0
    now_fn: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        self._path = self.base_dir / f"enrich_{self.namespace}.json"
        self._data: dict = self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            if self._path.exists():
                d = json.loads(self._path.read_text())
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data))
            tmp.replace(self._path)  # atomic
        except Exception:
            pass

    # ── freshness ────────────────────────────────────────────────────────────
    def _fresh(self, entry: object) -> bool:
        if not isinstance(entry, dict) or "ts" not in entry:
            return False
        if self.ttl_hours <= 0:
            return True
        try:
            return (self.now_fn() - float(entry["ts"])) < self.ttl_hours * 3600.0
        except (TypeError, ValueError):
            return False

    # ── single-ticker access ─────────────────────────────────────────────────
    def get(self, ticker: str):
        """Cached value for ``ticker`` if fresh, else None."""
        e = self._data.get(str(ticker).upper())
        return e.get("v") if (e is not None and self._fresh(e)) else None

    def put(self, ticker: str, value) -> None:
        self._data[str(ticker).upper()] = {"v": value, "ts": self.now_fn()}
        self._save()

    def put_many(self, values: dict) -> None:
        ts = self.now_fn()
        for t, v in (values or {}).items():
            self._data[str(t).upper()] = {"v": v, "ts": ts}
        self._save()

    # ── the batcher ──────────────────────────────────────────────────────────
    def get_or_fetch(self, tickers: Iterable[str],
                     fetch_fn: Callable[[list], dict]) -> dict:
        """{TICKER: value} for all ``tickers``: served from cache where fresh, and
        the STALE/MISSING set fetched in ONE batched ``fetch_fn(missing)`` call.

        fetch_fn: (list[str]) -> dict[str, value]. Fetched values are cached. A
        fetch failure degrades to absent keys (never raises); ``None`` values are
        treated as "no data" and neither cached nor returned.
        """
        out: dict = {}
        missing: list[str] = []
        for t in tickers or []:
            tk = str(t).upper()
            if not tk:
                continue
            v = self.get(tk)
            if v is not None:
                out[tk] = v
            elif tk not in missing:
                missing.append(tk)
        if missing:
            try:
                fetched = fetch_fn(list(missing)) or {}
            except Exception:
                fetched = {}
            got: dict = {}
            for t, v in fetched.items():
                tk = str(t).upper()
                if v is not None:
                    out[tk] = v
                    got[tk] = v
            if got:
                self.put_many(got)
        return out

    def stats(self) -> dict:
        fresh = sum(1 for e in self._data.values() if self._fresh(e))
        return {"namespace": self.namespace, "entries": len(self._data),
                "fresh": fresh, "stale": len(self._data) - fresh, "ttl_hours": self.ttl_hours}
