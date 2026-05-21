"""Keyless and optional social-signal collection."""

from __future__ import annotations

from urllib.parse import quote_plus

import requests

from .duckduckgo_search import search_text


USER_AGENT = "TradingAgents/0.2.4 social-signal collector"


def _score_text(text: str) -> float:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return float(SentimentIntensityAnalyzer().polarity_scores(text or "")["compound"])
    except Exception:
        return 0.0


def _summarize(items: list[dict], title: str) -> str:
    if not items:
        return f"No social sentiment results found for {title}"
    avg = sum(float(item.get("sentiment", 0.0)) for item in items) / len(items)
    rows = [
        f"- Sentiment average: {avg:+.2f}",
        f"- Items scored: {len(items)}",
        "",
    ]
    for item in items[:20]:
        rows.append(
            f"### {item.get('title', 'Untitled')} (source: {item.get('source', 'unknown')})\n"
            f"Sentiment: {item.get('sentiment', 0.0):+.2f}\n"
            f"{item.get('text', '')[:500]}\n"
            f"Link: {item.get('url', '')}\n"
        )
    return f"## Social Sentiment: {title}\n\n" + "\n".join(rows)


def get_reddit_json_mentions(ticker: str, limit: int = 20) -> list[dict]:
    items = []
    subreddits = ("stocks", "investing", "wallstreetbets", "options")
    headers = {"User-Agent": USER_AGENT}
    for subreddit in subreddits:
        url = f"https://www.reddit.com/r/{subreddit}/search.json?q={quote_plus(ticker)}&restrict_sr=1&sort=new&limit={limit}"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            for child in response.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                text = f"{data.get('title', '')}\n{data.get('selftext', '')}"
                items.append({
                    "source": f"reddit/r/{subreddit}",
                    "title": data.get("title", ""),
                    "text": text.strip(),
                    "url": "https://reddit.com" + data.get("permalink", ""),
                    "sentiment": _score_text(text),
                })
        except Exception:
            continue
    return items[:limit]


def get_stocktwits_mentions(ticker: str, limit: int = 20) -> list[dict]:
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker.upper()}.json"
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        messages = response.json().get("messages", [])
    except Exception:
        return []

    items = []
    for msg in messages[:limit]:
        body = msg.get("body", "")
        user = msg.get("user", {}).get("username", "unknown")
        items.append({
            "source": "stocktwits",
            "title": f"StockTwits @{user}",
            "text": body,
            "url": f"https://stocktwits.com/{user}/message/{msg.get('id')}",
            "sentiment": _score_text(body),
        })
    return items


def get_hn_mentions(ticker: str, limit: int = 10) -> list[dict]:
    url = f"https://hn.algolia.com/api/v1/search?query={quote_plus(ticker)}&tags=story"
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        hits = response.json().get("hits", [])
    except Exception:
        return []

    items = []
    for hit in hits[:limit]:
        text = hit.get("title") or hit.get("story_text") or ""
        items.append({
            "source": "hacker-news",
            "title": hit.get("title", "HN item"),
            "text": text,
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "sentiment": _score_text(text),
        })
    return items


def get_duckduckgo_social_mentions(ticker: str, limit: int = 10) -> list[dict]:
    queries = [
        f"site:x.com {ticker} stock",
        f"site:reddit.com {ticker} stock",
        f"site:stocktwits.com {ticker}",
    ]
    items = []
    for query in queries:
        try:
            for result in search_text(query, limit=max(2, limit // len(queries)), timelimit="w"):
                text = f"{result.get('title', '')}\n{result.get('body', '')}"
                items.append({
                    "source": "duckduckgo-social",
                    "title": result.get("title", ""),
                    "text": result.get("body", ""),
                    "url": result.get("href", ""),
                    "sentiment": _score_text(text),
                })
        except Exception:
            continue
    return items[:limit]


def get_social_sentiment(ticker: str, start_date: str, end_date: str, limit: int = 30) -> str:
    items = []
    items.extend(get_reddit_json_mentions(ticker, limit=limit // 3 or 1))
    items.extend(get_stocktwits_mentions(ticker, limit=limit // 3 or 1))
    items.extend(get_hn_mentions(ticker, limit=limit // 6 or 1))
    items.extend(get_duckduckgo_social_mentions(ticker, limit=limit // 3 or 1))

    seen = set()
    deduped = []
    for item in items:
        key = item.get("url") or item.get("title")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return _summarize(deduped[:limit], ticker.upper())
