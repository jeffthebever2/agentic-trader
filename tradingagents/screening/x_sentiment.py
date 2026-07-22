"""X (Twitter) API v2 cashtag sentiment — pure core (thematic revamp).

Replaces the fragile 5-feed rss.app `trusted_twitter` scrape with real X data. This
module is the PURE part: build ONE batched cashtag search query for a shortlist, and
fold the returned posts into a per-ticker attention + bull/bear signal. No network —
the web/api layer holds the Bearer token and calls `/2/tweets/search/recent`; it feeds
the raw `data` array here. Engagement-weighted so a 5,000-like post outweighs a bot
reply, and routed through the existing `tweet_intent` lexicon for bull/bear.

Cost note: X self-serve billing charges per returned Post read, so the caller must
batch cashtags, cap `max_results`, and run the credit-aware budget guard. The query
builder chunks cashtags to stay under X's query-length limit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# X recent-search query length caps: 512 (Basic) / 1024 (Pro). Stay conservative.
_QUERY_MAX = 480
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")


def build_cashtag_queries(tickers, suffix: str = "lang:en -is:retweet",
                          query_max: int = _QUERY_MAX) -> list[str]:
    """Batch tickers into as FEW X recent-search queries as fit the length cap.

    Each query is ``($AAA OR $BBB OR ...) <suffix>``. A shortlist of ~20-40 names
    fits in ONE query (one API call / scan). Returns [] for no tickers.
    """
    tks = [str(t).upper().strip() for t in (tickers or []) if str(t).strip().isalpha()]
    tks = list(dict.fromkeys(tks))
    if not tks:
        return []
    suffix = (" " + suffix.strip()) if suffix.strip() else ""
    queries: list[str] = []
    chunk: list[str] = []

    def _q(names: list[str]) -> str:
        return "(" + " OR ".join(f"${n}" for n in names) + ")" + suffix

    for t in tks:
        trial = chunk + [t]
        if len(_q(trial)) > query_max and chunk:
            queries.append(_q(chunk))
            chunk = [t]
        else:
            chunk = trial
    if chunk:
        queries.append(_q(chunk))
    return queries


@dataclass
class TickerBuzz:
    ticker: str
    mentions: int = 0
    engagement: float = 0.0     # Σ (likes + retweets + quotes + replies) per mention
    impressions: float = 0.0
    texts: list = field(default_factory=list)   # up to N post texts for intent scoring

    def to_dict(self) -> dict:
        return {"ticker": self.ticker, "mentions": self.mentions,
                "engagement": round(self.engagement, 1), "impressions": round(self.impressions, 1)}


def _engagement(pm: dict) -> float:
    if not isinstance(pm, dict):
        return 0.0
    return float((pm.get("like_count") or 0) + (pm.get("retweet_count") or 0)
                 + (pm.get("quote_count") or 0) + (pm.get("reply_count") or 0))


def parse_tweets(tweets, tickers, *, max_texts_per_ticker: int = 8) -> dict:
    """Fold an X v2 ``data`` array into {TICKER: TickerBuzz} for the requested set.

    A post counts toward each in-set cashtag it mentions (multi-ticker posts are
    common). Engagement/impressions accumulate per mention; a bounded sample of
    texts is kept for the downstream bull/bear intent pass. Only tickers in the
    requested ``tickers`` set are tracked (ignores the noise cashtags a post drags in).
    """
    want = {str(t).upper().strip() for t in (tickers or [])}
    out: dict = {}
    for tw in tweets or []:
        if not isinstance(tw, dict):
            continue
        text = str(tw.get("text", ""))
        pm = tw.get("public_metrics") or {}
        eng = _engagement(pm)
        impr = float(pm.get("impression_count") or 0)
        seen = set()
        for m in _CASHTAG_RE.finditer(text):
            sym = m.group(1).upper()
            if sym in seen or (want and sym not in want):
                continue
            seen.add(sym)
            b = out.get(sym)
            if b is None:
                b = out[sym] = TickerBuzz(ticker=sym)
            b.mentions += 1
            b.engagement += eng
            b.impressions += impr
            if len(b.texts) < max_texts_per_ticker:
                b.texts.append(text)
    return out


def score_buzz(buzz: TickerBuzz, intent_fn=None) -> dict:
    """Per-ticker X signal: attention (engagement-weighted mentions) + optional
    bull/bear from an injected intent classifier over the sampled texts.

    intent_fn(text) -> 'BUY'|'SELL'|'HOLD'|... (e.g. tweet_intent.classify). None →
    attention only. Returns {mentions, attention, bull, bear, net_intent}."""
    attention = buzz.mentions + 0.001 * buzz.engagement  # 1 mention ≈ 1000 engagements
    bull = bear = 0
    if intent_fn is not None:
        for txt in buzz.texts:
            try:
                lab = str(intent_fn(txt) or "").upper()
            except Exception:
                continue
            if lab in ("BUY", "WATCHLIST"):
                bull += 1
            elif lab == "SELL":
                bear += 1
    n = bull + bear
    net = ((bull - bear) / n) if n else None
    return {"mentions": buzz.mentions, "attention": round(attention, 2),
            "bull": bull, "bear": bear, "net_intent": (round(net, 3) if net is not None else None)}
