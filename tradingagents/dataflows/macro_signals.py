"""Keyless macro and filing signal helpers."""

from __future__ import annotations

from urllib.parse import urlencode

import requests


def get_sec_edgar_rss(limit: int = 10) -> str:
    try:
        import feedparser
        url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom"
        feed = feedparser.parse(url)
        rows = [
            f"### {entry.get('title', 'SEC filing')}\nDate: {entry.get('updated', '')}\nLink: {entry.get('link', '')}\n"
            for entry in feed.entries[:limit]
        ]
        return "## Recent SEC EDGAR Filings\n\n" + "\n".join(rows) if rows else "No recent SEC filings found"
    except Exception as exc:
        return f"Error fetching SEC EDGAR RSS: {exc}"


def get_fred_csv(series_id: str, api_key: str | None = None) -> str:
    params = {"series_id": series_id, "file_type": "json"}
    if api_key:
        params["api_key"] = api_key
    url = "https://api.stlouisfed.org/fred/series/observations"
    try:
        data = requests.get(url, params=params, timeout=20).json()
        obs = data.get("observations", [])[-10:]
        rows = [f"- {o.get('date')}: {o.get('value')}" for o in obs]
        return f"## FRED {series_id}\n\n" + "\n".join(rows)
    except Exception as exc:
        return f"Error fetching FRED {series_id}: {exc}"


def get_treasury_rates() -> str:
    url = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    try:
        text = requests.get(url, timeout=20).text
        return f"## US Treasury Rates Feed\n\n{text[:2000]}"
    except Exception as exc:
        return f"Error fetching Treasury rates: {exc}"
