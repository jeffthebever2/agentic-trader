"""DuckDuckGo/DDGS search and news provider."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable


def _format_results(title: str, results: Iterable[dict], limit: int = 10) -> str:
    rows = []
    for item in list(results)[:limit]:
        item_title = item.get("title") or item.get("heading") or "Untitled"
        source = item.get("source") or item.get("publisher") or "DuckDuckGo"
        url = item.get("href") or item.get("url") or item.get("link") or ""
        snippet = item.get("body") or item.get("snippet") or item.get("excerpt") or ""
        date = item.get("date") or item.get("published") or ""
        rows.append(f"### {item_title} (source: {source})\nDate: {date}\n{snippet}\nLink: {url}\n")
    if not rows:
        return f"No DuckDuckGo results found for {title}"
    return f"## DuckDuckGo Results: {title}\n\n" + "\n".join(rows)


def _ddgs():
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception as exc:
            raise RuntimeError(f"DuckDuckGo search unavailable: {exc}") from exc
    return DDGS(timeout=15)


def search_text(query: str, limit: int = 10, timelimit: str | None = None) -> list[dict]:
    with _ddgs() as client:
        return list(client.text(query, region="us-en", safesearch="moderate", timelimit=timelimit, max_results=limit))


def search_news(query: str, limit: int = 10, timelimit: str | None = "w") -> list[dict]:
    with _ddgs() as client:
        return list(client.news(query, region="us-en", safesearch="moderate", timelimit=timelimit, max_results=limit))


def get_news_duckduckgo(ticker: str, start_date: str, end_date: str) -> str:
    try:
        if datetime.fromisoformat(end_date[:10]) < datetime.now() - timedelta(days=14):
            return (
                f"DuckDuckGo news skipped for {ticker}: provider only returns recent news, "
                f"not point-in-time historical news for {start_date} to {end_date}."
            )
    except Exception:
        pass
    query = f"{ticker} stock earnings guidance downgrade upgrade lawsuit acquisition"
    try:
        return _format_results(f"{ticker} company news", search_news(query, limit=12, timelimit="w"), 12)
    except Exception as exc:
        return f"Error fetching DuckDuckGo news for {ticker}: {exc}"


def get_global_news_duckduckgo(curr_date: str, look_back_days: int = 7, limit: int = 10) -> str:
    try:
        if datetime.fromisoformat(curr_date[:10]) < datetime.now() - timedelta(days=14):
            return (
                "DuckDuckGo global news skipped: provider only returns recent news, "
                f"not point-in-time historical macro news for {curr_date}."
            )
    except Exception:
        pass
    queries = [
        "stock market economy Federal Reserve inflation jobs report",
        "S&P 500 Nasdaq market breadth VIX rates",
        "global markets trading macroeconomic outlook",
    ]
    results = []
    try:
        for query in queries:
            results.extend(search_news(query, limit=max(3, limit // len(queries)), timelimit="w"))
            if len(results) >= limit:
                break
        return _format_results(f"global macro news through {curr_date}", results, limit)
    except Exception as exc:
        return f"Error fetching DuckDuckGo global news: {exc}"


def get_google_news_rss(ticker: str, limit: int = 10) -> str:
    """Keyless Google News RSS fallback for a ticker."""
    try:
        import feedparser
        from urllib.parse import quote_plus

        url = f"https://news.google.com/rss/search?q={quote_plus(ticker + ' stock')}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        rows = []
        for entry in feed.entries[:limit]:
            rows.append(
                f"### {entry.get('title', 'Untitled')} (source: Google News RSS)\n"
                f"Date: {entry.get('published', '')}\nLink: {entry.get('link', '')}\n"
            )
        return f"## Google News RSS for {ticker}\n\n" + "\n".join(rows) if rows else f"No Google News RSS results for {ticker}"
    except Exception as exc:
        return f"Error fetching Google News RSS for {ticker}: {exc}"


def is_recent_enough(date_text: str, start_date: str) -> bool:
    try:
        return datetime.fromisoformat(date_text[:10]) >= datetime.fromisoformat(start_date)
    except Exception:
        try:
            return datetime.now() - timedelta(days=14) <= datetime.now()
        except Exception:
            return True
