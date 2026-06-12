"""Multi-provider live quote gateway — execution-freshness layer.

Pulls quotes from several providers in parallel, normalizes them, checks
freshness + cross-source consensus, and picks the best executable quote.
Designed to feed PreTradeGate so live decisions never rest on a single
stale yfinance/Yahoo price.

Provider priority (trusted for execution first):
    1. finnhub      (needs FINNHUB_API_KEY)
    2. twelve_data  (needs TWELVEDATA_API_KEY)
    3. fmp          (needs FMP_API_KEY — last price only, no bid/ask)
    4. yahoo_chart  (no key; consensus/watchlist only, untrusted for execution)
    5. yfinance     (fallback of last resort, untrusted for execution)

Providers without keys are skipped silently, so the gateway degrades
gracefully down to yahoo/yfinance and the PreTradeGate's trusted-source
rules decide what that's worth.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

ROOT = Path(__file__).resolve().parents[2]

# Sources PreTradeGate accepts as execution-grade (keep in sync with
# tradingagents.portfolio.pretrade_gate.PreTradeGate.TRUSTED_SOURCES).
TRUSTED_SOURCES = frozenset({"finnhub", "twelve_data", "fmp"})

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


@dataclass
class Quote:
    """Normalized quote from one provider."""
    symbol: str
    last: float
    source: str
    quote_time: dt.datetime                  # naive local time
    bid: float | None = None
    ask: float | None = None
    latency_ms: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask and self.bid > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2.0
        return None

    @property
    def spread_bps(self) -> float | None:
        mid = self.mid
        if mid and self.bid is not None and self.ask is not None:
            return (self.ask - self.bid) / mid * 10_000
        return None

    @property
    def age_seconds(self) -> float:
        return (dt.datetime.now() - self.quote_time).total_seconds()

    @property
    def trusted(self) -> bool:
        return self.source in TRUSTED_SOURCES

    def reference_price(self) -> float:
        return self.mid or self.last


@dataclass
class GatewayQuote:
    """Aggregated best-quote view across providers."""
    symbol: str
    best: Quote
    all_quotes: list[Quote]
    consensus_ok: bool
    consensus_spread_bps: float | None

    @property
    def backup_sources(self) -> list[str]:
        return [q.source for q in self.all_quotes if q.source != self.best.source]

    def pretrade_kwargs(self) -> dict:
        """kwargs ready to splat into PreTradeGate.check()."""
        return {
            "price": self.best.last,
            "bid": self.best.bid,
            "ask": self.best.ask,
            "price_snapshot_time": self.best.quote_time,
            "quote_source": self.best.source,
            "backup_sources": self.backup_sources,
            "consensus_ok": self.consensus_ok,
        }


@dataclass
class _ProviderHealth:
    ok: int = 0
    fail: int = 0
    last_error: str = ""
    last_latency_ms: float | None = None
    last_success_at: str = ""

    def as_dict(self) -> dict:
        total = self.ok + self.fail
        return {
            "ok": self.ok,
            "fail": self.fail,
            "success_rate": round(self.ok / total, 3) if total else None,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "last_success_at": self.last_success_at,
        }


# ── Provider fetchers ─────────────────────────────────────────────
# Each returns Quote or None. Missing keys → None without network call.

def _fetch_finnhub(symbol: str, timeout: float) -> Quote | None:
    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        return None
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": token}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    last = float(data.get("c") or 0)
    if last <= 0:
        return None
    ts = float(data.get("t") or 0)
    quote_time = dt.datetime.fromtimestamp(ts) if ts > 0 else dt.datetime.now()
    return Quote(symbol=symbol, last=last, source="finnhub", quote_time=quote_time)


def _fetch_twelve_data(symbol: str, timeout: float) -> Quote | None:
    key = os.getenv("TWELVEDATA_API_KEY") or os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        return None
    r = requests.get("https://api.twelvedata.com/quote",
                     params={"symbol": symbol, "apikey": key}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    last = float(data.get("close") or 0)
    if last <= 0:
        return None
    ts = float(data.get("timestamp") or 0)
    quote_time = dt.datetime.fromtimestamp(ts) if ts > 0 else dt.datetime.now()
    return Quote(symbol=symbol, last=last, source="twelve_data", quote_time=quote_time)


def _fetch_fmp(symbol: str, timeout: float) -> Quote | None:
    key = os.getenv("FMP_API_KEY")
    if not key:
        return None
    r = requests.get("https://financialmodelingprep.com/stable/quote",
                     params={"symbol": symbol, "apikey": key}, timeout=timeout)
    r.raise_for_status()
    rows = r.json()
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
    last = float(row.get("price") or 0)
    if last <= 0:
        return None
    ts = float(row.get("timestamp") or 0)
    quote_time = dt.datetime.fromtimestamp(ts) if ts > 0 else dt.datetime.now()
    return Quote(symbol=symbol, last=last, source="fmp", quote_time=quote_time)


def _fetch_yahoo_chart(symbol: str, timeout: float) -> Quote | None:
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": "1m", "range": "1d"},
        headers=_UA, timeout=timeout,
    )
    r.raise_for_status()
    meta = (((r.json().get("chart") or {}).get("result") or [{}])[0].get("meta")) or {}
    last = float(meta.get("regularMarketPrice") or 0)
    if last <= 0:
        return None
    ts = float(meta.get("regularMarketTime") or 0)
    quote_time = dt.datetime.fromtimestamp(ts) if ts > 0 else dt.datetime.now()
    return Quote(symbol=symbol, last=last, source="yahoo_chart", quote_time=quote_time)


def _fetch_yfinance(symbol: str, timeout: float) -> Quote | None:  # noqa: ARG001
    import yfinance as yf  # local import: heavy
    info = yf.Ticker(symbol).fast_info
    last = float(getattr(info, "last_price", 0) or 0)
    if last <= 0:
        return None
    # fast_info carries no timestamp; treat as "now" but the untrusted-source
    # rules in PreTradeGate keep this from being execution evidence by itself.
    return Quote(symbol=symbol, last=last, source="yfinance", quote_time=dt.datetime.now())


_PROVIDERS: list[tuple[str, Callable[[str, float], Optional[Quote]]]] = [
    ("finnhub", _fetch_finnhub),
    ("twelve_data", _fetch_twelve_data),
    ("fmp", _fetch_fmp),
    ("yahoo_chart", _fetch_yahoo_chart),
    ("yfinance", _fetch_yfinance),
]


class QuoteGateway:
    """Fan out to all configured providers, normalize, pick best, score consensus.

    Args:
        consensus_tolerance_bps: quotes "agree" if max pairwise deviation of
            reference prices stays within this (default 50 bps).
        max_quote_age_seconds: quotes older than this are dropped before
            best-quote selection (default 30s — PreTradeGate applies its own
            tighter execution threshold).
        cache_ttl_seconds: per-symbol result cache to avoid hammering
            providers inside one scan loop (default 2s).
        provider_timeout: per-provider HTTP timeout (default 4s).
        shadow_log_path: when set, every get_quote() appends a JSONL row
            comparing the yfinance-style answer vs the gateway answer
            (30-day stale-vs-fresh shadow test).
    """

    def __init__(
        self,
        consensus_tolerance_bps: float = 50.0,
        max_quote_age_seconds: float = 30.0,
        cache_ttl_seconds: float = 2.0,
        provider_timeout: float = 4.0,
        shadow_log_path: Path | str | None = None,
        providers: list[tuple[str, Callable[[str, float], Optional[Quote]]]] | None = None,
    ) -> None:
        self.consensus_tolerance_bps = consensus_tolerance_bps
        self.max_quote_age_seconds = max_quote_age_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.provider_timeout = provider_timeout
        self.shadow_log_path = Path(shadow_log_path) if shadow_log_path else None
        self.providers = providers if providers is not None else list(_PROVIDERS)
        self.health: dict[str, _ProviderHealth] = {name: _ProviderHealth() for name, _ in self.providers}
        self._cache: dict[str, tuple[float, GatewayQuote | None]] = {}
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> GatewayQuote | None:
        symbol = symbol.strip().upper()
        with self._lock:
            hit = self._cache.get(symbol)
            if hit and (time.monotonic() - hit[0]) < self.cache_ttl_seconds:
                return hit[1]

        quotes = self._fan_out(symbol)
        result = self._aggregate(symbol, quotes)
        with self._lock:
            self._cache[symbol] = (time.monotonic(), result)
        if result is not None:
            self._shadow_log(result)
        return result

    def get_quotes(self, symbols: list[str]) -> dict[str, GatewayQuote | None]:
        return {s: self.get_quote(s) for s in symbols}

    def provider_health(self) -> dict:
        return {name: h.as_dict() for name, h in self.health.items()}

    # ── internals ─────────────────────────────────────────────────

    def _fan_out(self, symbol: str) -> list[Quote]:
        quotes: list[Quote] = []
        with ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures = {}
            for name, fetch in self.providers:
                started = time.monotonic()
                futures[pool.submit(self._safe_fetch, name, fetch, symbol)] = (name, started)
            for fut in as_completed(futures, timeout=self.provider_timeout + 2):
                name, started = futures[fut]
                latency = (time.monotonic() - started) * 1000
                try:
                    quote = fut.result()
                except Exception as exc:  # provider blew through _safe_fetch (timeout)
                    self._mark(name, ok=False, error=str(exc), latency=latency)
                    continue
                if quote is None:
                    continue
                quote.latency_ms = round(latency, 1)
                self._mark(name, ok=True, latency=latency)
                quotes.append(quote)
        return quotes

    def _safe_fetch(self, name: str, fetch: Callable, symbol: str) -> Quote | None:
        try:
            return fetch(symbol, self.provider_timeout)
        except Exception as exc:
            self._mark(name, ok=False, error=str(exc))
            return None

    def _mark(self, name: str, ok: bool, error: str = "", latency: float | None = None) -> None:
        h = self.health.setdefault(name, _ProviderHealth())
        if ok:
            h.ok += 1
            h.last_success_at = dt.datetime.now().isoformat(timespec="seconds")
        else:
            h.fail += 1
            h.last_error = error[:200]
        if latency is not None:
            h.last_latency_ms = round(latency, 1)

    def _aggregate(self, symbol: str, quotes: list[Quote]) -> GatewayQuote | None:
        if not quotes:
            return None
        fresh = [q for q in quotes if q.age_seconds <= self.max_quote_age_seconds]
        pool = fresh or quotes  # nothing fresh → still report, gate will reject on age

        # consensus across ALL collected quotes (fresh preferred)
        consensus_ok, consensus_spread = self._consensus(pool)

        # best quote: trusted+bid/ask > trusted > has bid/ask > freshest
        def rank(q: Quote) -> tuple:
            return (
                q.trusted,
                q.bid is not None and q.ask is not None,
                -q.age_seconds,
            )
        best = sorted(pool, key=rank, reverse=True)[0]
        return GatewayQuote(
            symbol=symbol,
            best=best,
            all_quotes=sorted(pool, key=rank, reverse=True),
            consensus_ok=consensus_ok,
            consensus_spread_bps=consensus_spread,
        )

    def _consensus(self, quotes: list[Quote]) -> tuple[bool, float | None]:
        if len(quotes) < 2:
            return (len(quotes) == 1, None)
        prices = [q.reference_price() for q in quotes if q.reference_price() > 0]
        if len(prices) < 2:
            return (False, None)
        lo, hi = min(prices), max(prices)
        mid = (lo + hi) / 2.0
        spread_bps = (hi - lo) / mid * 10_000 if mid > 0 else None
        ok = spread_bps is not None and spread_bps <= self.consensus_tolerance_bps
        return (ok, round(spread_bps, 1) if spread_bps is not None else None)

    def _shadow_log(self, result: GatewayQuote) -> None:
        if self.shadow_log_path is None:
            return
        try:
            yf_quote = next((q for q in result.all_quotes if q.source in ("yfinance", "yahoo_chart")), None)
            best = result.best
            row = {
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "symbol": result.symbol,
                "gateway_source": best.source,
                "gateway_price": best.last,
                "gateway_age_s": round(best.age_seconds, 1),
                "gateway_spread_bps": best.spread_bps,
                "yf_price": yf_quote.last if yf_quote else None,
                "delta_pct": (round((best.last - yf_quote.last) / yf_quote.last * 100, 3)
                              if yf_quote and yf_quote.last > 0 else None),
                "consensus_ok": result.consensus_ok,
                "consensus_spread_bps": result.consensus_spread_bps,
                "sources": [q.source for q in result.all_quotes],
            }
            self.shadow_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.shadow_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            pass


_GATEWAY: QuoteGateway | None = None
_GATEWAY_LOCK = threading.Lock()


def get_gateway() -> QuoteGateway | None:
    """Process-wide singleton. Returns None when QUOTE_GATEWAY=off."""
    if os.getenv("QUOTE_GATEWAY", "on").strip().lower() in ("off", "0", "false", "disabled"):
        return None
    global _GATEWAY
    with _GATEWAY_LOCK:
        if _GATEWAY is None:
            _GATEWAY = QuoteGateway(shadow_log_path=ROOT / "tmp" / "quote_shadow_log.jsonl")
        return _GATEWAY
