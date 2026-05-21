"""Gated Financial Modeling Prep provider.

FMP is treated as quota-protected enrichment. It is never used without an API
key and it caches successful responses to avoid duplicate calls.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import requests

from .alt_config import get_secret
from .config import get_config


BASE_URL = "https://financialmodelingprep.com/stable"


class FMPGate:
    def __init__(self, config: dict | None = None):
        self.config = config or get_config()
        self.enabled = bool(self.config.get("fmp_enabled", True))
        self.daily_limit = int(self.config.get("fmp_daily_limit", 250))
        self.reserve_calls = int(self.config.get("fmp_reserve_calls", 25))
        self.cache_dir = Path(self.config["data_cache_dir"]).expanduser() / "fmp"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.quota_path = self.cache_dir / f"quota-{datetime.now().date().isoformat()}.json"

    @property
    def api_key(self) -> str | None:
        return get_secret("FMP_API_KEY", "fmp") or get_secret("api_key", "fmp")

    def can_call(self, force: bool = False) -> tuple[bool, str]:
        if not self.enabled:
            return False, "FMP disabled"
        if not self.api_key:
            return False, "FMP_API_KEY not configured"
        used = self.used_calls()
        allowed = self.daily_limit if force else max(0, self.daily_limit - self.reserve_calls)
        if used >= allowed:
            return False, f"FMP quota reserve reached ({used}/{self.daily_limit})"
        return True, "OK"

    def used_calls(self) -> int:
        if not self.quota_path.exists():
            return 0
        try:
            return int(json.loads(self.quota_path.read_text(encoding="utf-8")).get("used", 0))
        except Exception:
            return 0

    def increment(self) -> None:
        used = self.used_calls() + 1
        self.quota_path.write_text(json.dumps({"date": datetime.now().date().isoformat(), "used": used}, indent=2), encoding="utf-8")

    def cache_path(self, endpoint: str, params: dict) -> Path:
        safe = endpoint.strip("/").replace("/", "_")
        key = "_".join(f"{k}-{v}" for k, v in sorted(params.items()) if k != "apikey")
        return self.cache_dir / f"{safe}_{key}_{datetime.now().date().isoformat()}.json"


def _request(endpoint: str, params: dict, *, force: bool = False) -> list | dict:
    gate = FMPGate()
    ok, reason = gate.can_call(force=force)
    if not ok:
        return {"error": reason}

    params = {k: v for k, v in params.items() if v is not None}
    cache_path = gate.cache_path(endpoint, params)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    params["apikey"] = gate.api_key
    response = requests.get(f"{BASE_URL}/{endpoint.lstrip('/')}", params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    gate.increment()
    return data


def _render(title: str, data) -> str:
    if isinstance(data, dict) and data.get("error"):
        return f"FMP skipped: {data['error']}"
    if not data:
        return f"No FMP {title.lower()} data found"
    return f"## FMP {title}\n\n```json\n{json.dumps(data[:10] if isinstance(data, list) else data, indent=2)[:6000]}\n```"


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    profile = _request("profile", {"symbol": ticker})
    metrics = _request("key-metrics-ttm", {"symbol": ticker})
    ratios = _request("ratios-ttm", {"symbol": ticker})
    scores = _request("financial-scores", {"symbol": ticker})
    return _render(f"fundamentals for {ticker}", {
        "profile": profile,
        "key_metrics_ttm": metrics,
        "ratios_ttm": ratios,
        "scores": scores,
    })


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(f"income statement for {ticker}", _request("income-statement", {"symbol": ticker, "period": freq, "limit": 8}))


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(f"balance sheet for {ticker}", _request("balance-sheet-statement", {"symbol": ticker, "period": freq, "limit": 8}))


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(f"cash flow for {ticker}", _request("cash-flow-statement", {"symbol": ticker, "period": freq, "limit": 8}))


def get_insider_transactions(ticker: str) -> str:
    return _render(f"insider trading for {ticker}", _request("insider-trading/search", {"symbol": ticker, "limit": 25}))


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    return _render(f"stock news for {ticker}", _request("news/stock", {"symbols": ticker, "from": start_date, "to": end_date, "limit": 20}))


def get_signal_context(ticker: str) -> str:
    earnings = _request("earnings", {"symbol": ticker, "limit": 8}, force=True)
    ratings = _request("ratings-historical", {"symbol": ticker, "limit": 8}, force=True)
    targets = _request("price-target-consensus", {"symbol": ticker}, force=True)
    insider = _request("insider-trading/statistics", {"symbol": ticker}, force=True)
    return _render(f"signal context for {ticker}", {
        "earnings": earnings,
        "ratings": ratings,
        "price_targets": targets,
        "insider_statistics": insider,
    })
