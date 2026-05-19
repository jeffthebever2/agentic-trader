"""Attention and hype-cycle signals."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

import requests


def get_wikipedia_pageviews(page_title: str, days: int = 30) -> str:
    end = datetime.utcnow().date() - timedelta(days=1)
    start = end - timedelta(days=days)
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{quote(page_title, safe='')}/daily/"
        f"{start.strftime('%Y%m%d')}00/{end.strftime('%Y%m%d')}00"
    )
    try:
        data = requests.get(url, headers={"User-Agent": "TradingAgents/0.2.4"}, timeout=20).json()
        items = data.get("items", [])
        if not items:
            return f"No Wikipedia pageview data for {page_title}"
        total = sum(int(item.get("views", 0)) for item in items)
        recent = items[-1].get("views", 0)
        avg = total / len(items)
        return (
            f"## Wikipedia Attention: {page_title}\n\n"
            f"- {days}d total views: {total:,}\n"
            f"- Latest daily views: {recent:,}\n"
            f"- Average daily views: {avg:,.0f}"
        )
    except Exception as exc:
        return f"Error fetching Wikipedia pageviews for {page_title}: {exc}"


def get_youtube_metadata(url_or_channel: str, limit: int = 10) -> str:
    try:
        import yt_dlp
    except Exception as exc:
        return f"yt-dlp unavailable: {exc}"

    opts = {"quiet": True, "extract_flat": True, "playlistend": limit, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url_or_channel, download=False)
        entries = info.get("entries") or [info]
        rows = [
            f"### {entry.get('title', 'Untitled')}\n{entry.get('description', '')[:500]}\nURL: {entry.get('url', '')}\n"
            for entry in entries[:limit]
        ]
        return "## YouTube Metadata Signal\n\n" + "\n".join(rows)
    except Exception as exc:
        return f"Error fetching YouTube metadata: {exc}"
