"""
Thematic Auto-Picker

Data sources:
  - Reddit (r/wallstreetbets, r/stocks, r/investing) via public JSON API
  - DuckDuckGo news search (free, no API key)
  - Yahoo Finance trending (yfinance)

AI analysis: OpenRouter → generates themed picks with conviction/thesis.

Endpoints:
  POST /api/thematic/auto/scan       — trigger a fresh scan (async)
  GET  /api/thematic/auto/status     — scan status + last result
  GET  /api/thematic/auto/signals    — current signal queue
  POST /api/thematic/auto/signals/{id}/approve  — approve → add to portfolio
  POST /api/thematic/auto/signals/{id}/skip     — skip signal
  GET  /api/thematic/auto/trending   — raw trending tickers from social
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from web.auth import get_current_user, require_admin

log = logging.getLogger("thematic_auto")

ROOT   = Path(__file__).parent.parent.parent
TMP    = ROOT / "tmp"
SIGNALS_FILE   = TMP / "thematic_signals.json"
STATUS_FILE    = TMP / "thematic_scan_status.json"
# Dedicated thematic paper book (isolated from the 15-portfolio competition book).
# Keep this path in sync with web.api.thematic_portfolio.PAPER_STATE_FILE.
PAPER_STATE_FILE = ROOT / "tmp" / "thematic_paper" / "state.json"

router = APIRouter()

# ── Ticker extraction ─────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')
_SKIP = {
    "A","I","AM","AN","AS","AT","BE","BY","DO","GO","IN","IS","IT","ME","MY","NO",
    "OF","ON","OR","SO","TO","UP","US","WE","AI","DD","YOLO","IMO","ATH","ATL",
    "EV","PE","CEO","CFO","CTO","IPO","ETF","GDP","CPI","FED","SEC","NYSE",
    "IRS","LLC","INC","THE","FOR","AND","BUT","NOT","ALL","ARE","WAS",
    "HAS","HAD","PUT","CALL","BEAR","BULL","HOLD","SELL","PUMP","DUMP","MOON",
    "APES","WSB","NEWS","THIS","THAT","WITH","WILL","HAVE","FROM","BEEN","MORE",
    "THEY","THEM","WHEN","THEN","THAN","WHAT","SOME","ALSO","INTO","JUST","OVER",
    "LIKE","EVEN","HERE","WELL","BACK","DOWN","HIGH","LOW","HOW","NOW","DUE",
    "EOD","EOW","EOY","YTD","QOQ","YOY","FOMO","FOMC","PMI","PCE","NFP","VIX",
    "SPX","SPAC","ASS","LOL","WTF","OMG","OK","TV","EPS","MM","BB","COM","NET",
    "OUT","OFF","NEW","OLD","BIG","MAY","CAN","USE","GET","SET","LET","ONE","TWO",
    "RATE","WEEK","YEAR","LAST","NEXT","GOOD","BEST","TOP","DAY","WAY","MAN",
    "TIME","LONG","TERM","REAL","MAKE","MOVE","TAKE","COME","MOST","LESS",
    "STRONG","WEAK","FUND","BOND","CASH","RISK","LOSS","GAIN","DEAL","PLAN",
    "GOLD","BLUE","DARK","FAST","SLOW","FREE","EASY","HARD","SAFE","OPEN",
    # Crypto — not tradeable as equity paper trade
    "BTC","ETH","SOL","XRP","DOGE","SHIB","ADA","DOT","LINK","UNI","AVAX",
    # Geography / politics — common false positives in news RSS
    "CHINA","JAPAN","INDIA","KOREA","EURO","RUSSIA","IRAN","IRAQ","ITALY",
    "SPAIN","FRANCE","EGYPT","TURKEY","CHILE","PERU","GHANA","KENYA","CUBA",
    "TRUMP","BIDEN","SENATE","HOUSE","GOP","NATO","OPEC","BRICS","IMF",
    "ASIA","EMEA","LATAM","MENA","APAC",
    # Market/finance jargon — false positives in news
    "STOCK","STOCKS","SHARE","SHARES","TRADE","TRADES","MARKET","MARKETS",
    "BANK","BANKS","DEBT","EQUITY","YIELD","YIELDS","PRICE","PRICES",
    "REPORT","REPORTS","SALES","PROFIT","LOSS","LOSSES","REVENUE","GROWTH",
    "TECH","DATA","CLOUD","CORP","GROUP","HOLD","RESET","RISE","FALL",
    "ABOVE","BELOW","UNDER","AFTER","BEFORE","SINCE","ABOUT","ACROSS",
    "COULD","WOULD","SHOULD","MIGHT","MUST","EACH","BOTH","SAME","SUCH",
    "SAID","SAYS","TOLD","NOTED","SHOWS","SHOW","SEES","SEE","SAID",
    "MAJOR","MINOR","SMALL","LARGE","FIRST","THIRD","FOURTH","FIFTH",
    "TODAY","DAILY","EARLY","LATE","NOON","CLOSE","FLAT","NEAR","MUCH",
    "ITEM","TITLE","GUID","LINK","TYPE","INFO","TEXT","HTML","TRUE","FALSE",
    "HTTPS","HTTP","RSS","XML","UTF","CDATA","SRC","HREF","ALT","IMG",
    # Common pronouns/articles/conjunctions not already covered
    "HE","HIS","HIM","HER","SHE","ITS","YOU","YOUR","OUR","THEY","THEM",
    "WHO","WHOM","WHICH","THAT","WHEN","WHERE","HOW","WHY","WHAT","WHOSE",
    "IF","AS","AT","BY","IN","IS","IT","OF","ON","OR","TO","UP","WE",
    # Commodities, sectors — commonly appear in financial news text
    "OIL","GAS","COAL","IRON","ZINC","LEAD","TIN","RICE","CORN","SOYA",
    "OIL","GAS","COAL","NUCLEAR","SOLAR","WIND","POWER","ENERGY","WATER",
    "WAR","TAX","CUT","LAW","ACT","BILL","VOTE","POLL","COURT","TRIAL",
    "CEO","CTO","CFO","COO","CFR","CDO","EVP","SVP","VP","MD","GM",
    "USA","USD","EUR","GBP","JPY","CNY","INR","CAD","AUD","CHF",
    "Q1","Q2","Q3","Q4","H1","H2","FY","YOY","QOQ","MOM","WOW",
    "EBIT","EBITDA","FCF","ROE","ROA","ROI","IRR","NPV","DCF","LBO",
    "IPO","SPO","ATM","OTC","ECM","DCM","CLO","CDO","MBS","ABS",
    "AI","ML","NLP","LLM","SaaS","PaaS","IaaS","ERP","CRM","ERP",
    "SAAS","PAAS","IAAS","IOT","AR","VR","XR","AGI","RAG",
    "APOS","NBSP","AMP","LT","GT","QUOT","COPY","REG","TRADE",
    "ALSO","BEEN","WERE","HAVE","BEEN","THAN","THEN","EVEN","JUST",
    "ONLY","VERY","MANY","SOME","ANY","ALL","NONE","BOTH","EACH","MOST",
    "WITH","FROM","INTO","ONTO","UPON","OVER","UNDER","NEAR","PAST",
    "LONG","SHORT","HIGH","LOW","GOOD","BEST","NEXT","LAST","LATE",
    "NEW","OLD","TOP","END","USE","GET","SET","LET","PUT","CALL",
    # Months and time words
    "JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC",
    "JUNE","JULY","MARCH","APRIL","AUGUST","JANUARY","FEBRUARY","OCTOBER",
    "MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY",
    # Common words not yet covered
    "THEIR","THERE","THESE","THOSE","THEM","THEN","THEY","THUS","THIS",
    "WHILE","WHERE","WHOSE","WHICH","WHAT","WHEN","WITH","WERE","WILL",
    "TIMES","SINCE","STILL","AGAIN","AFTER","AHEAD","ALIKE","AMID","AMID",
    "BOOM","BUST","HIKE","JUMP","RISE","DROP","FLAT","STAY","MOVE","GROW",
    "FIRM","HOLD","BEAT","MISS","MEET","BEAT","FACE","SEEN","SAID","TOLD",
    "WEEK","YEAR","DAYS","HOUR","MINS","JUST","EVEN","BOTH","MORE","LESS",
    "AMID","NEAR","PAST","PEAK","LOSS","GAIN","RATE","COST","BASE","CORE",
    "MAKE","TAKE","GIVE","SHOW","KNOW","FIND","KEEP","HELP","LEAD","PLAN",
    "JONES","SMITH","BROWN","WHITE","BLACK","GREEN","GRAY","ROSE","KING",
    "FOUR","FIVE","SEVEN","EIGHT","NINE","ZERO","HALF","FULL","SAME","NEXT",
    "AMID","ONCE","UPON","EVEN","THAN","THEN","THAT","THEM","THEY","WHEN",
    "INTO","ONTO","OVER","PAST","THRU","WITH","FROM","BACK","DOWN","AWAY",
    "FAST","SLOW","HARD","SOFT","WARM","COOL","COLD","DARK","LITE","WIDE",
    # Financial market jargon — appear in headlines, not tickers
    "SURGE","RALLY","GAINS","DROPS","FALLS","RISES","BEATS","MISSES",
    "PENNY","MICRO","SMALL","LARGE","MEGA","SUPER","ULTRA","HYPER",
    "FUNDS","BONDS","NOTES","BILLS","LOANS","RATES","YIELDS","SPREADS",
    "BEARS","BULLS","CRASH","PANIC","MANIA","BUBBLE","BUBBLE",
    "FRESH","CLEAN","CLEAR","CLOSE","OPENS","OPENS","START","STOPS",
    "MONTH","WEEKS","YEARS","HOURS","DAILY","WEEKLY","YEARLY",
    "ABOUT","ABOVE","AFTER","AGAIN","AHEAD","ALONG","AMONG","APART",
    "TESLA","APPLE","GOOGL","AMAZON","MICROSOFT","NVIDIA",  # full company names
    "CHINA","INDIA","JAPAN","EUROPE","GLOBAL","WORLD","LOCAL",
    "FIRST","THIRD","FIFTH","SIXTH","SEVENTH","EIGHTH","NINTH","TENTH",
    "PRICE","SHARE","VALUE","WORTH","TOTAL","GROSS","NETT","HALF",
    "MAJOR","MINOR","OTHER","EVERY","NEVER","ALWAYS","OFTEN","MAYBE",
}

_CASHTAG_EXTRACT = re.compile(r'\$([A-Za-z]{1,5})\b')


def extract_tickers(text: str) -> list[str]:
    """Tickers in free text. CASHTAG-FIRST: if the text uses ``$SYMBOL`` markers
    (tweets/RSS almost always do), trust ONLY those — explicit + unambiguous. This
    avoids the blocklist whack-a-mole where uppercase prose ("SOLD", "CYCLE",
    "SPEND", "GETS", "GOING") was mis-read as tickers, polluting buzz AND the
    intent classifier. Bare-uppercase extraction runs only when no cashtag exists
    (e.g. plain news headlines), still filtered by _SKIP.
    """
    raw = text or ""
    cash = [m.group(1).upper() for m in _CASHTAG_EXTRACT.finditer(raw)]
    if cash:
        # dedupe (one post = one mention per symbol), drop junk/crypto/stopwords
        return [t for t in dict.fromkeys(cash) if t not in _SKIP and len(t) >= 2]
    found = _TICKER_RE.findall(raw.upper())
    return [t for t in found if t not in _SKIP and len(t) >= 2]


# ── Reddit scraper (no OAuth — public JSON) ───────────────────────────────────

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket", "SecurityAnalysis"]

# Per-scan social INTENT stash, keyed by ticker → {buy, sell, hold, watch, news,
# unclear} mention counts + the strongest sell reason. Populated by the social
# text sources (reddit) via the intent classifier and consumed by _merge_signals
# to (a) NOT inflate buzz from sellers and (b) penalize / avoid net-sell names.
# Single web process → module-level is fine (same scope as the other scan caches).
_SOCIAL_INTENT: dict[str, dict] = {}
# Multiple sources write _SOCIAL_INTENT now, incl. DDG which runs in an executor
# THREAD — guard the read-modify-write so concurrent records can't corrupt a bucket.
_intent_lock = threading.Lock()

# Per-ticker net social SENTIMENT in [-1,+1] for this scan (bullish - bearish
# conviction), derived in _merge_signals and blended into composite_score so a
# bearish crowd lowers the final score (the sentiment term was previously LLM-only).
_SOCIAL_SENTIMENT: dict[str, float] = {}


def _reset_social_intent() -> None:
    with _intent_lock:
        _SOCIAL_INTENT.clear()
        _SOCIAL_SENTIMENT.clear()
    _SEEN_HEADLINES.clear()


# ── Cross-source headline dedup ───────────────────────────────────────────────
# One press release syndicates to Google News + PR RSS + generic RSS + Seeking
# Alpha within minutes. Counting each copy stacks per-source points AND fakes the
# multi-source confirmation bonus — four copies of one headline is ONE signal,
# not four. First source to see a headline counts it; later copies are skipped.
_SEEN_HEADLINES: set[str] = set()


def _headline_is_dupe(text: str) -> bool:
    """True if this headline was already counted THIS SCAN by any news source.
    Fingerprint = normalized first 10 words, so syndication suffixes ("- Yahoo
    Finance") and tracking cruft don't defeat the match. Registers the headline
    as seen when new. Too-short texts are never deduped (unsafe fingerprint)."""
    key = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    key = " ".join(key.split()[:10])
    if len(key) < 20:
        return False
    if key in _SEEN_HEADLINES:
        return True
    _SEEN_HEADLINES.add(key)
    return False


def _record_intent(ticker: str, res, weight: float = 1.0) -> None:
    """Fold one IntentResult into the per-scan social-intent stash for a ticker.

    Besides the per-label COUNT buckets, accumulate the conviction-weighted
    bull/bear/neutral sentiment mass (buzz_score.contribution) so the scorer can
    make buzz reflect MARKET CONVICTION, not raw attention. ``weight`` is this
    mention's source weight (e.g. seeking-alpha posts count 2x)."""
    from tradingagents.screening import tweet_intent as ti
    from tradingagents.screening import buzz_score as bz
    bucket = {
        ti.BUY_SIGNAL: "buy", ti.SELL_SIGNAL: "sell", ti.HOLD_SIGNAL: "hold",
        ti.WATCHLIST_ONLY: "watch", ti.NEWS_ONLY: "news", ti.UNCLEAR: "unclear",
    }.get(res.label, "unclear")
    bull_w, bear_w, neut_w = bz.contribution(res, weight=weight)
    with _intent_lock:
        rec = _SOCIAL_INTENT.setdefault(
            ticker, {"buy": 0, "sell": 0, "hold": 0, "watch": 0, "news": 0,
                     "unclear": 0, "sell_reason": "", "sell_conf": 0.0,
                     "bull_w": 0.0, "bear_w": 0.0, "neut_w": 0.0})
        rec[bucket] += 1
        rec["bull_w"] = rec.get("bull_w", 0.0) + bull_w
        rec["bear_w"] = rec.get("bear_w", 0.0) + bear_w
        rec["neut_w"] = rec.get("neut_w", 0.0) + neut_w
        if res.reduce_buy and res.confidence >= rec.get("sell_conf", 0.0):
            rec["sell_reason"] = res.reason
            rec["sell_conf"] = res.confidence


def net_social_buy_intent(ticker: str) -> dict:
    """Public read of a ticker's aggregated social intent for this scan (or {})."""
    return dict(_SOCIAL_INTENT.get(ticker.upper(), {}))


def _blended_sentiment(ticker: str, llm_sentiment) -> float:
    """Blend the LLM pick's sentiment with the crowd's social sentiment for this
    scan (buzz_score.blend_sentiment). A bearish crowd drags the composite down."""
    from tradingagents.screening import buzz_score as bz
    social = _SOCIAL_SENTIMENT.get((ticker or "").upper())
    return round(bz.blend_sentiment(llm_sentiment, social), 3)


def _lexicon_rows(blob: str, weight: int = 1) -> list:
    """Extract tickers from a text blob + lexicon-classify intent per ticker.
    Returns [{ticker, text, lex, weight}] for the shared intent resolver."""
    from tradingagents.screening import tweet_intent as ti
    tks = extract_tickers(blob)
    if not tks:
        return []
    intents = ti.aggregate_for_tickers(blob, tks)
    return [{"ticker": t, "text": blob, "lex": intents.get(t), "weight": weight} for t in tks]


async def _resolve_intent_rows(rows: list, *, use_ai: bool = True) -> dict:
    """Shared intent pipeline for ANY text source (reddit, RSS-tweets, news).

    Lexicon is computed by the caller (in each row's ``lex``). When ``use_ai`` and
    the free AI is available, the rows the lexicon read weakly are batched into one
    free-LLM call and reconciled (a SELL from either source blocks the buy). Records
    every result into _SOCIAL_INTENT and returns ticker→summed-weight of the
    NON-selling mentions (sellers never pad buzz)."""
    from tradingagents.screening import tweet_intent as ti
    ai_map: dict = {}
    if use_ai and _ai_intent_enabled():
        thr = _ai_intent_conf_threshold()
        applicable = [
            i for i, r in enumerate(rows)
            if r["lex"] is None or r["lex"].label == ti.UNCLEAR or r["lex"].confidence < thr
        ]
        if applicable:
            # The AI call (CF 45s client timeout + OpenRouter fallbacks) can exceed
            # the per-source scan budget, which used to time out the WHOLE source and
            # throw away even the lexicon counts (trusted_twitter contributed zero for
            # days). Bound it to a fraction of the source budget and fall back to
            # lexicon-only on timeout — partial intent beats no source at all.
            ai_budget = max(5.0, _scan_source_timeout() * 0.5)
            try:
                ai_res = await asyncio.wait_for(
                    _ai_classify_intents([(rows[i]["ticker"], rows[i]["text"]) for i in applicable]),
                    timeout=ai_budget,
                )
            except asyncio.TimeoutError:
                log.warning("AI intent timed out after %.0fs — lexicon-only for %d posts",
                            ai_budget, len(applicable))
                ai_res = {}
            for li, ar in ai_res.items():
                ai_map[applicable[li]] = ar
    from tradingagents.screening import buzz_score as bz
    counts: dict[str, float] = {}
    for i, r in enumerate(rows):
        t = r["ticker"]
        w = float(r.get("weight", 1) or 1)
        lex = r["lex"] or ti.classify_intent(r["text"], ticker=t)
        final = ti.reconcile_intents(lex, ai_map.get(i))
        if i in ai_map:   # previously-inconclusive source resolved by AI — log it
            log.info("AI intent %s: %s (%s)", t, final.label, (final.reason or "")[:80])
        _record_intent(t, final, weight=w)
        # Conviction-weight the mention: neutral/holding chatter barely pads buzz,
        # sellers don't pad it at all (count_weight -> 0 for reduce_buy).
        cw = bz.count_weight(final)
        if cw > 0:
            counts[t] = counts.get(t, 0.0) + w * cw
    return counts


def _lexicon_sell_tickers(text: str) -> set:
    """Tickers in ``text`` the lexicon reads as SELL/warning (reduce_buy). Records
    intent to _SOCIAL_INTENT and returns the set to SKIP. Lightweight gate for the
    news/PR sources that keep their own cashtag+length weighting — a "downgrade /
    overextended" headline then won't pad their buzz either."""
    from tradingagents.screening import tweet_intent as ti
    out: set = set()
    for r in _lexicon_rows(text):
        t = r["ticker"]
        lex = r["lex"] or ti.classify_intent(r["text"], ticker=t)
        _record_intent(t, lex)
        if lex.reduce_buy:
            out.add(t)
    return out


def _lexicon_counts(blob: str, weight: int = 1) -> dict:
    """Sync lexicon-only intent for NEWS sources (headlines are mostly NEWS_ONLY;
    no AI call needed). Records intent + returns non-selling buy-counts. Lets an
    explicit "warning/overextended/downgrade" headline avoid padding buzz."""
    from tradingagents.screening import tweet_intent as ti
    from tradingagents.screening import buzz_score as bz
    out: dict[str, float] = {}
    for r in _lexicon_rows(blob, weight):
        t = r["ticker"]
        lex = r["lex"] or ti.classify_intent(r["text"], ticker=t)
        _record_intent(t, lex, weight=float(weight))
        cw = bz.count_weight(lex)
        if cw > 0:
            out[t] = out.get(t, 0.0) + weight * cw
    return out


async def _reddit_tickers(client: httpx.AsyncClient, limit: int = 25) -> dict[str, int]:
    """Return ticker → BUY-INTENT mention count across hot posts.

    Each post is intent-classified per ticker (lexicon + free AI on the iffy ones);
    SELL/HOLD/WATCH/NEWS mentions are recorded in _SOCIAL_INTENT instead of padding
    buy buzz — so "selling $NVDA / taking profits" never ranks NVDA like a buy.
    _SOCIAL_INTENT is reset once per scan in _run_scan (not here — many sources
    write it now)."""
    headers = {"User-Agent": "AgenticTrader/1.0 (stock research tool)"}
    rows: list[dict] = []

    async def _fetch_sub(sub: str) -> list[dict]:
        out: list[dict] = []
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
            r = await client.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                for p in r.json().get("data", {}).get("children", []):
                    d = p.get("data", {})
                    out += _lexicon_rows(f"{d.get('title','')} {d.get('selftext','')}")
                return out
            # Reddit 403s unauthenticated JSON from some IPs (source silently went
            # "empty"); the public RSS feed still serves — fall back to it.
            rss = await client.get(
                f"https://www.reddit.com/r/{sub}/hot/.rss?limit={limit}",
                headers=headers, timeout=10,
            )
            if rss.status_code != 200:
                log.warning("Reddit %s: json=%s rss=%s", sub, r.status_code, rss.status_code)
                return out
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(rss.text, "xml")
            for entry in soup.find_all("entry"):
                title = entry.find("title")
                content = entry.find("content")
                content_text = BeautifulSoup(content.text, "html.parser").get_text(" ") if content else ""
                out += _lexicon_rows(f"{title.text if title else ''} {content_text}")
        except Exception as e:
            log.warning("Reddit %s: %s", sub, e)
        return out

    # Sequential on purpose: concurrent hits on reddit.com rate-limit the RSS
    # fallback to 429s (observed live). 5 subs × ~1s is well inside the budget.
    for sub in SUBREDDITS:
        rows += await _fetch_sub(sub)
    return await _resolve_intent_rows(rows, use_ai=True)


TWITTER_COOKIES = TMP / "twitter_cookies.json"
_CASHTAG_RE = re.compile(r'\$([A-Z]{1,5})\b')

TWITTER_SEARCHES = [
    r"($NVDA OR $AMD OR $TSLA OR $META OR $MSFT) lang:en -is:retweet",
    r"($AAPL OR $AMZN OR $GOOGL OR $NVDA) earnings momentum lang:en -is:retweet",
    "stock breakout momentum buy lang:en -is:retweet",
    r"($AI OR $SMCI OR $PLTR OR $COIN OR $HOOD) bullish lang:en -is:retweet",
    r"($MU OR $AVGO OR $ANET OR $ARM) semiconductor lang:en -is:retweet",
]

def _get_tweepy_client():
    """Return tweepy.Client using best available credentials."""
    bearer  = os.getenv("TWITTER_BEARER_TOKEN", "").strip()
    api_key = os.getenv("TWITTER_API_KEY", "").strip()
    api_sec = os.getenv("TWITTER_API_SECRET", "").strip()
    acc_tok = os.getenv("TWITTER_ACCESS_TOKEN", "").strip()
    acc_sec = os.getenv("TWITTER_ACCESS_SECRET", "").strip()

    if not bearer and not (api_key and api_sec and acc_tok and acc_sec):
        return None
    try:
        import tweepy
        if bearer:
            return tweepy.Client(bearer_token=bearer, wait_on_rate_limit=False)
        return tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_sec,
            access_token=acc_tok,
            access_token_secret=acc_sec,
            wait_on_rate_limit=False,
        )
    except Exception as e:
        log.warning("tweepy init: %s", e)
        return None


async def _twitter_tickers() -> dict[str, int]:
    """Twitter API v2 search — requires Basic plan ($100/mo). Returns empty if free tier."""
    return {}  # Free tier 402s on search_recent_tweets — not viable


# ── DuckDuckGo news scraper ───────────────────────────────────────────────────

DDG_QUERIES = [
    "stocks trending today breaking news",
    "hot stock momentum breakout today",
    "best stocks to buy this week AI technology",
    "reddit stocks viral trending picks",
    "small cap stock surge today",
    "short squeeze stock trending",
    "earnings beat stock rally today",
]

async def _ddg_tickers(client: httpx.AsyncClient) -> dict[str, int]:  # noqa: ARG001
    """DuckDuckGo news search. Uses ddgs package (renamed from duckduckgo_search).
    Rate-limits: only run first 3 queries, small sleep between calls."""
    counts: dict[str, int] = {}
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        loop = asyncio.get_running_loop()
        def _run_ddg() -> dict[str, int]:
            _counts: dict[str, int] = {}
            import time as _t
            with DDGS() as ddg:
                for q in DDG_QUERIES[:4]:   # limit queries to avoid rate-limit
                    try:
                        results = list(ddg.news(q, max_results=8))
                        for r in results:
                            blob = f"{r.get('title','')} {r.get('body','')}"
                            # lexicon intent: a "downgrade/warning/overextended"
                            # headline won't pad buzz (records to _SOCIAL_INTENT too).
                            for t, c in _lexicon_counts(blob).items():
                                _counts[t] = _counts.get(t, 0) + c
                        _t.sleep(0.5)   # small gap between queries
                    except Exception as e:
                        log.debug("DDG query '%s': %s", q, e)
            return _counts
        counts = await loop.run_in_executor(None, _run_ddg)
    except Exception as e:
        log.warning("DDG: %s", e)
    return counts


# ── Brave Search fallback ─────────────────────────────────────────────────────
# Free tier: 2000 req/month. We self-limit to 1000/month via a usage counter.
_BRAVE_USAGE_FILE = TMP / "brave_search_usage.json"
_BRAVE_MONTHLY_LIMIT = 1000
_BRAVE_QUERIES_PER_SCAN = 3   # 3 queries × 6 scans/day × 30 days = 540/month

BRAVE_QUERIES = [
    "stock market trending momentum today",
    "best stocks buy this week earnings breakout",
    "hot stocks AI technology semiconductor surge",
]


def _brave_usage() -> tuple[int, str]:
    """Return (used_this_month, year_month_key)."""
    import datetime as _dt2
    ym = _dt2.date.today().strftime("%Y-%m")
    try:
        if _BRAVE_USAGE_FILE.exists():
            data = json.loads(_BRAVE_USAGE_FILE.read_text())
            return data.get(ym, 0), ym
    except Exception:
        pass
    return 0, ym


def _brave_increment(n: int) -> None:
    """Increment monthly usage counter by n."""
    used, ym = _brave_usage()
    try:
        data: dict = {}
        if _BRAVE_USAGE_FILE.exists():
            try: data = json.loads(_BRAVE_USAGE_FILE.read_text())
            except Exception: pass
        data[ym] = data.get(ym, 0) + n
        # prune old months (keep last 2)
        keys = sorted(data.keys())[-2:]
        data = {k: data[k] for k in keys}
        _BRAVE_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_BRAVE_USAGE_FILE.parent, prefix=".tmp_brave_")
        try:
            with os.fdopen(fd, "w") as f: f.write(json.dumps(data))
            os.replace(tmp, _BRAVE_USAGE_FILE)
        except Exception:
            try: os.unlink(tmp)
            except Exception: pass
    except Exception as e:
        log.warning("Brave usage write: %s", e)


async def _brave_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """Brave Search API news — fallback for DDG. Capped at 1000 req/month."""
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return {}

    used, ym = _brave_usage()
    remaining = _BRAVE_MONTHLY_LIMIT - used
    if remaining <= 0:
        log.info("Brave Search: monthly limit %d reached (%s)", _BRAVE_MONTHLY_LIMIT, ym)
        return {}

    n_queries = min(_BRAVE_QUERIES_PER_SCAN, remaining)
    counts: dict[str, int] = {}
    import re as _re
    _CASH = _re.compile(r'\$([A-Z]{1,5})\b')
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    used_count = 0
    for q in BRAVE_QUERIES[:n_queries]:
        try:
            r = await client.get(
                "https://api.search.brave.com/res/v1/news/search",
                params={"q": q, "count": 10, "freshness": "pd"},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 429:
                log.warning("Brave Search rate-limited")
                break
            if r.status_code != 200:
                log.warning("Brave Search %d: %s", r.status_code, q)
                continue
            used_count += 1
            results = r.json().get("results", [])
            for item in results:
                text = f"{item.get('title','')} {item.get('description','')}"
                for t in _CASH.findall(text):
                    if t not in _SKIP and 2 <= len(t) <= 5:
                        counts[t] = counts.get(t, 0) + 3   # $TICKER cashtag = high confidence
                # Plain text: 5+ chars only to cut BOOM/JUNE/AMID noise
                _sell = _lexicon_sell_tickers(text)
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 5 and t not in _sell:
                        counts[t] = counts.get(t, 0) + 1
        except Exception as e:
            log.warning("Brave Search query '%s': %s", q, e)

    if used_count > 0:
        _brave_increment(used_count)
        remaining_after = _BRAVE_MONTHLY_LIMIT - (used + used_count)
        log.info("Brave Search: used %d queries (%d/%d this month, %d remaining)",
                 used_count, used + used_count, _BRAVE_MONTHLY_LIMIT, remaining_after)
    return counts


# ── Google News RSS ───────────────────────────────────────────────────────────

GOOGLE_NEWS_QUERIES = [
    "stock breakout momentum buy today",
    "small cap stock surge",
    "AI semiconductor stock news",
    "earnings beat stock rally",
    "short squeeze stock trending",
    "momentum stocks week",
    "stock market breakout technical",
]

async def _google_news_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        from bs4 import BeautifulSoup
        for q in GOOGLE_NEWS_QUERIES:
            try:
                url = f"https://news.google.com/rss/search?q={q.replace(' ','+')}&hl=en-US&gl=US&ceid=US:en"
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:10]:
                    title = item.find("title")
                    desc  = item.find("description")
                    if _headline_is_dupe(title.text if title else ""):
                        continue
                    blob  = f"{title.text if title else ''} {desc.text if desc else ''}"
                    for t, c in _lexicon_counts(blob).items():
                        counts[t] = counts.get(t, 0) + c
            except Exception as e:
                log.warning("Google News '%s': %s", q[:30], e)
    except Exception as e:
        log.warning("Google News: %s", e)
    return counts


# ── SeekingAlpha RSS ─────────────────────────────────────────────────────────

async def _seeking_alpha_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        from bs4 import BeautifulSoup
        feeds = [
            "https://seekingalpha.com/market_currents.xml",
            "https://seekingalpha.com/tag/wall-st-breakfast.xml",
        ]
        for url in feeds:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:20]:
                    title = item.find("title")
                    blob  = title.text if title else ""
                    if _headline_is_dupe(blob):
                        continue
                    for t, c in _lexicon_counts(blob, 2).items():
                        counts[t] = counts.get(t, 0) + c
            except Exception as e:
                log.warning("SA feed %s: %s", url, e)
    except Exception as e:
        log.warning("SeekingAlpha: %s", e)
    return counts


# ── StockAnalysis trending ────────────────────────────────────────────────────

async def _stockanalysis_trending(client: httpx.AsyncClient) -> list[str]:
    try:
        from bs4 import BeautifulSoup
        r = await client.get(
            "https://stockanalysis.com/trending/",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        tickers = [
            a.text.strip() for a in soup.select('a[href*="/stocks/"]')
            if len(a.text.strip()) >= 2 and len(a.text.strip()) <= 5 and a.text.strip().isupper()
        ]
        return list(dict.fromkeys(tickers))[:30]  # dedup, keep order
    except Exception as e:
        log.warning("StockAnalysis: %s", e)
        return []


# ── Marketaux news + sentiment (free 100 req/day) ────────────────────────────

async def _marketaux_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    api_token = os.getenv("MARKETAUX_API_TOKEN", "").strip()
    if not api_token:
        return {}
    counts: dict[str, int] = {}
    try:
        r = await client.get(
            "https://api.marketaux.com/v1/news/all",
            params={
                "api_token": api_token,
                "language": "en",
                "sentiment_gte": "0",  # positive sentiment only
                "limit": 50,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("Marketaux: %s %s", r.status_code, r.text[:100])
            return {}
        for article in r.json().get("data", []):
            # Entities = identified tickers with sentiment
            for ent in article.get("entities", []):
                sym  = ent.get("symbol", "").upper()
                sent = float(ent.get("sentiment_score", 0) or 0)
                if sym and sym not in _SKIP and len(sym) >= 2 and sent > 0:
                    # Weight by sentiment strength
                    counts[sym] = counts.get(sym, 0) + int(sent * 5) + 2
    except Exception as e:
        log.warning("Marketaux: %s", e)
    return counts


# ── Trusted Twitter RSS feeds (via rss.app) ──────────────────────────────────

# Updated 2026-06-19 (live-validated). Replaced the stale 1cNg7qq…/lYyMa8F…
# feeds with the user's current rss.app feeds. bHJyaHDmDjopM3kM.xml was dropped
# because it returned HTTP 404 at validation time.
TRUSTED_TWITTER_FEEDS = [
    "https://rss.app/feeds/G7CYzS3IpdS7XAgR.xml",
    "https://rss.app/feeds/aDBakalosRFgiYkN.xml",
    "https://rss.app/feeds/2jNBRuvbe4QpbI3S.xml",
    "https://rss.app/feeds/4f8bdh1Ic9bv0D5P.xml",
    "https://rss.app/feeds/rcG2hWdIPifEJymJ.xml",
]

async def _trusted_twitter_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """Parse rss.app-proxied Twitter feeds from trusted traders — now intent-aware.

    Trader tweets carry REAL buy/sell intent ("trimming $NVDA", "loading $ASTS"), so
    each tweet runs through the SAME lexicon+free-AI intent pipeline as Reddit: a
    seller/warning never pads buzz, and net-sell names get the merge penalty. The
    per-tweet text (title+desc) is classified once; cashtag mentions keep the high
    signal weight (5) and non-cashtag title words a lower weight (1)."""
    rows: list[dict] = []
    try:
        from bs4 import BeautifulSoup

        async def _fetch(url: str):
            try:
                return url, await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            except Exception as e:
                return url, e

        # Fetch all feeds concurrently — serial fetches (5 × up to 10s) plus the AI
        # intent call used to blow the per-source scan budget.
        fetched = await asyncio.gather(*[_fetch(u) for u in TRUSTED_TWITTER_FEEDS])
        for url, r in fetched:
            try:
                if isinstance(r, Exception):
                    log.warning("Trusted Twitter RSS %s: %s", url[-20:], r)
                    continue
                if r.status_code != 200:
                    log.warning("Trusted Twitter RSS %s: %s", url[-20:], r.status_code)
                    continue
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item"):
                    title = item.find("title")
                    desc  = item.find("description")
                    blob  = f"{title.text if title else ''} {desc.text if desc else ''}"
                    # weight by signal: a $cashtag in the tweet = a real trader pick (5).
                    cashtags = {m for m in _CASHTAG_RE.findall(blob.upper())
                                if m not in _SKIP and 2 <= len(m) <= 5 and m.isalpha()}
                    from tradingagents.screening import tweet_intent as ti
                    tks = extract_tickers(blob)
                    if not tks:
                        continue
                    intents = ti.aggregate_for_tickers(blob, tks)
                    for t in tks:
                        rows.append({"ticker": t, "text": blob, "lex": intents.get(t),
                                     "weight": 5 if t in cashtags else 1})
            except Exception as e:
                log.warning("Trusted Twitter RSS %s: %s", url[-20:], e)
    except Exception as e:
        log.warning("Trusted Twitter feeds: %s", e)
    return await _resolve_intent_rows(rows, use_ai=True)


# ── Insider & Congressional Trading ──────────────────────────────────────────

CONGRESS_QUERIES = [
    "congress stock trade bought",
    "senator representative stock purchase",
    "pelosi congress stock trade",
]

async def _insider_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """
    OpenInsider cluster buys + SEC Form 4 RSS + congressional trade news.
    Cluster buys = multiple insiders buying same stock = highest conviction signal.
    Congressional purchases = smart money following DC info flow.
    """
    counts: dict[str, int] = {}
    try:
        from bs4 import BeautifulSoup

        # ── OpenInsider cluster buys (multiple insiders buying = strong signal) ──
        try:
            r = await client.get(
                "http://openinsider.com/latest-cluster-buys",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for row in soup.select("table.tinytable tbody tr")[:30]:
                    cells = row.find_all("td")
                    if len(cells) >= 4:
                        ticker = cells[3].text.strip().upper()
                        # Only pure ticker symbols (no index/fund garbage)
                        if ticker and ticker not in _SKIP and 2 <= len(ticker) <= 5 and ticker.isalpha():
                            counts[ticker] = counts.get(ticker, 0) + 5  # cluster = high weight
        except Exception as e:
            log.warning("OpenInsider cluster: %s", e)

        # ── OpenInsider large single buys (>$500k, last 3 days) ──
        try:
            r2 = await client.get(
                "http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=-1&fdr=&td=0&tdr="
                "&fdlyl=&fdlyh=&daysago=3&xp=1&vl=500&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999"
                "&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=30&action=getdata",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if r2.status_code == 200:
                soup2 = BeautifulSoup(r2.text, "html.parser")
                for row in soup2.select("table.tinytable tbody tr")[:30]:
                    cells = row.find_all("td")
                    if len(cells) >= 4:
                        ticker = cells[3].text.strip().upper()
                        if ticker and ticker not in _SKIP and 2 <= len(ticker) <= 5 and ticker.isalpha():
                            counts[ticker] = counts.get(ticker, 0) + 3
        except Exception as e:
            log.warning("OpenInsider screener: %s", e)

        # ── Congressional stock trades via Google News RSS ──
        try:
            for q in CONGRESS_QUERIES:
                url = f"https://news.google.com/rss/search?q={q.replace(' ','+')}&hl=en-US&gl=US&ceid=US:en"
                r3 = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r3.status_code == 200:
                    soup3 = BeautifulSoup(r3.text, "xml")
                    for item in soup3.find_all("item")[:8]:
                        title = item.find("title")
                        blob  = title.text if title else ""
                        # Cashtags in congress news = very high signal
                        for m in _CASHTAG_RE.findall(blob.upper()):
                            if m not in _SKIP and 2 <= len(m) <= 5:
                                counts[m] = counts.get(m, 0) + 4
                        for t, c in _lexicon_counts(blob).items():
                            counts[t] = counts.get(t, 0) + c
        except Exception as e:
            log.warning("Congress news: %s", e)

        log.info("Insider/congress: found %d tickers", len(counts))
    except Exception as e:
        log.warning("Insider tickers: %s", e)
    return counts


# ── Yahoo Finance trending ────────────────────────────────────────────────────

async def _yahoo_trending(client: httpx.AsyncClient) -> list[str]:
    try:
        r = await client.get(
            "https://query1.finance.yahoo.com/v1/finance/trending/US?count=20",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        quotes = r.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
        return [q.get("symbol","").upper() for q in quotes if q.get("symbol")]
    except Exception as e:
        log.warning("Yahoo trending: %s", e)
        return []


# ── Press release / earnings RSS (BusinessWire, PR Newswire, GlobeNewswire) ───

_PR_RSS_FEEDS = [
    "https://www.businesswire.com/rss/home/?rss=G22",        # earnings/financials
    "https://www.prnewswire.com/rss/news-releases-list.rss", # corporate announcements
    "https://www.globenewswire.com/RssFeed/subjectcode/15-Financial+News", # financial
    "https://www.businesswire.com/rss/home/?rss=G7",         # major news
]

async def _stocktwits_trending(client: httpx.AsyncClient) -> dict[str, int]:
    """Press release RSS (BusinessWire, PR Newswire, GlobeNewswire) — free, no key.
    Corporate announcements are high-signal: earnings surprises, deals, guidance."""
    counts: dict[str, int] = {}
    import re as _re
    _CASH  = _re.compile(r'\$([A-Z]{1,5})\b')
    _INNER = _re.compile(r'<(?:title|description|summary)[^>]*>([^<]{10,})</(?:title|description|summary)>', _re.IGNORECASE)
    headers = {"User-Agent": "AgenticTrader/1.0 (financial research)", "Accept": "*/*"}
    for url in _PR_RSS_FEEDS:
        try:
            r = await client.get(url, timeout=8, headers=headers, follow_redirects=True)
            if r.status_code != 200:
                continue
            for text in _INNER.findall(r.text):
                if _headline_is_dupe(text):
                    continue
                # Cashtags ($NVDA) = high confidence
                for t in _CASH.findall(text):
                    if t not in _SKIP and 2 <= len(t) <= 5:
                        counts[t] = counts.get(t, 0) + 4
                # Plain text tickers: 4+ chars only (cuts PART/WALL/HELP noise)
                _sell = _lexicon_sell_tickers(text)
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 4 and t not in _sell:
                        counts[t] = counts.get(t, 0) + 1
        except Exception as e:
            log.warning("PR RSS %s: %s", url.split("/")[2], e)
    return counts


# ── Finviz top gainers / most active ──────────────────────────────────────────

async def _finviz_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """Finviz screener: top gainers and unusual volume — no key required."""
    counts: dict[str, int] = {}
    import re as _re
    urls = [
        ("https://finviz.com/screener.ashx?v=111&f=ta_change_u5&o=-change&r=1", 3),
        ("https://finviz.com/screener.ashx?v=111&f=sh_relvol_o3&o=-volume&r=1", 2),
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finviz.com/",
    }
    for url, pts in urls:
        try:
            r = await client.get(url, timeout=12, headers=headers, follow_redirects=True)
            if r.status_code != 200:
                continue
            # Multiple selector patterns for different Finviz versions
            found = (
                _re.findall(r'data-ticker="([A-Z]{1,5})"', r.text) or
                _re.findall(r'ticker=["\']([A-Z]{1,5})["\']', r.text) or
                _re.findall(r'class="screener-link-primary[^"]*"[^>]*>([A-Z]{1,5})<', r.text)
            )
            for t in found[:20]:
                if t not in _SKIP:
                    counts[t] = counts.get(t, 0) + pts
        except Exception as e:
            log.warning("Finviz scrape: %s", e)
    return counts


# ── RSS news feeds ─────────────────────────────────────────────────────────────

_RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.bloomberg.com/markets/news.rss",
]

async def _rss_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """Parse free RSS financial news feeds for ticker mentions — XML-aware."""
    counts: dict[str, int] = {}
    headers = {"User-Agent": "AgenticTrader/1.0 (financial research)"}
    import re as _re
    _CASH  = _re.compile(r'\$([A-Z]{1,5})\b')
    # Extract only text inside XML tags (title/description), not tag names themselves
    _INNER = _re.compile(r'<(?:title|description|summary)[^>]*>([^<]{10,})</(?:title|description|summary)>', _re.IGNORECASE)
    for url in _RSS_FEEDS:
        try:
            r = await client.get(url, timeout=8, headers=headers, follow_redirects=True)
            if r.status_code != 200:
                continue
            # Only extract from text content, never from XML tag names
            texts: list[str] = _INNER.findall(r.text)
            for text in texts:
                if _headline_is_dupe(text):
                    continue
                # Cashtags ($NVDA) = high confidence signal
                for t in _CASH.findall(text):
                    if t not in _SKIP and 2 <= len(t) <= 5:
                        counts[t] = counts.get(t, 0) + 3
                # Plain text: 4+ chars to avoid PART/WALL/HELP/WORLD noise
                _sell = _lexicon_sell_tickers(text)
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 4 and t not in _sell:
                        counts[t] = counts.get(t, 0) + 1
        except Exception as e:
            log.warning("RSS feed %s: %s", url.split("/")[2], e)
    return counts


# ── AlphaVantage top gainers/losers (free demo key) ───────────────────────────

async def _alphavantage_movers(client: httpx.AsyncClient) -> dict[str, int]:
    """AlphaVantage TOP_GAINERS_LOSERS — requires a real free API key (alphavantage.co).
    Demo key returns foreign/OTC stocks only — skipped unless real key set."""
    counts: dict[str, int] = {}
    av_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    if not av_key or av_key.lower() == "demo":
        return counts  # demo key returns non-US stocks — not useful
    try:
        r = await client.get(
            f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={av_key}",
            timeout=10,
            headers={"User-Agent": "AgenticTrader/1.0"},
        )
        if r.status_code != 200:
            return counts
        data = r.json()
        # Top gainers get +4, most active get +3, losers get +1 (contrarian watch)
        for item in (data.get("top_gainers") or [])[:10]:
            t = str(item.get("ticker", "")).upper().strip()
            if t and t.isalpha() and t not in _SKIP:
                counts[t] = counts.get(t, 0) + 4
        for item in (data.get("most_actively_traded") or [])[:10]:
            t = str(item.get("ticker", "")).upper().strip()
            if t and t.isalpha() and t not in _SKIP:
                counts[t] = counts.get(t, 0) + 3
        for item in (data.get("top_losers") or [])[:5]:
            t = str(item.get("ticker", "")).upper().strip()
            if t and t.isalpha() and t not in _SKIP:
                counts[t] = counts.get(t, 0) + 1
    except Exception as e:
        log.warning("AlphaVantage movers: %s", e)
    return counts


# ── Yahoo Finance gainers / most active ───────────────────────────────────────

async def _yahoo_movers(client: httpx.AsyncClient) -> dict[str, int]:
    """Yahoo Finance day gainers + most active screeners — free, no key."""
    counts: dict[str, int] = {}
    endpoints = [
        ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=25", 3),
        ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=25", 2),
        ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_losers&count=15", 1),
    ]
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    for url, pts in endpoints:
        try:
            r = await client.get(url, timeout=8, headers=headers)
            if r.status_code != 200:
                continue
            quotes = (r.json().get("finance", {}).get("result", [{}])[0]
                      .get("quotes", []))
            for q in quotes:
                t = str(q.get("symbol", "")).upper().strip()
                if t and t.isalpha() and 2 <= len(t) <= 5 and t not in _SKIP:
                    counts[t] = counts.get(t, 0) + pts
        except Exception as e:
            log.warning("Yahoo movers (%s): %s", url.split("scrIds=")[1][:20], e)
    return counts


# ── Merge + rank ──────────────────────────────────────────────────────────────

# Max points any single source may contribute to one ticker's raw score. Bounds
# single-feed spikes so confirmation breadth, not one source's volume, drives the
# score. Multi-source / scan-memory / combo bonuses are added separately and are
# intentionally exempt from this cap.
_MAX_PER_SOURCE_PTS: float = 60.0

# Sources that count as real conviction signal (not OTC/foreign-stock noise from
# screener/mover feeds). Used both for the multi-source confirmation bonus and
# the quality gate — confirmation must come from quality feeds, not two screeners.
_QUALITY_SOURCES = frozenset({
    "trusted_twitter", "reddit", "seeking_alpha", "google_news",
    "insider", "marketaux", "twitter", "ddg", "brave", "scan_memory",
    "google_trends", "discovery", "analyst", "options_flow",
})


# Dual-class / renamed tickers that refer to the same company. Without folding
# these, one name's social signal is split across two tickers (so neither
# confirms) and the picker can propose both. Map to the more-liquid primary line.
_TICKER_ALIASES = {
    "GOOG": "GOOGL",   # Alphabet C → A (one Alphabet position)
    "FB": "META",      # Meta's old ticker still appears in chatter
    "FCAU": "STLA",    # legacy → Stellantis
    "SQ": "XYZ",       # Block renamed
}


def _norm_ticker(raw: object) -> str:
    """Canonical ticker key for cross-source merging: upper-case, stripped of
    whitespace and a leading cashtag '$', then folded across dual-class/renamed
    aliases. Ensures 'nvda', 'NVDA', '$NVDA' collapse to one entry — and that
    GOOG/GOOGL or FB/META don't fragment a single company's signal across two
    tickers (or seed two proposals for the same business)."""
    t = str(raw or "").strip().upper()
    if t.startswith("$"):
        t = t[1:]
    t = t.strip()
    return _TICKER_ALIASES.get(t, t)


async def _merge_signals(
    reddit: dict[str, int],
    ddg: dict[str, int],
    yahoo: list[str],
    twitter: dict[str, int] | None = None,
    google_news: dict[str, int] | None = None,
    seeking_alpha: dict[str, int] | None = None,
    stockanalysis: list[str] | None = None,
    marketaux: dict[str, int] | None = None,
    insider: dict[str, int] | None = None,
    trusted_twitter: dict[str, int] | None = None,
    stocktwits: dict[str, int] | None = None,
    finviz: dict[str, int] | None = None,
    rss_news: dict[str, int] | None = None,
    av_movers: dict[str, int] | None = None,
    yahoo_movers: dict[str, int] | None = None,
    brave: dict[str, int] | None = None,
    google_trends: dict[str, int] | None = None,
    discovery: dict[str, int] | None = None,
    analyst: dict[str, int] | None = None,
    options_flow: dict[str, int] | None = None,
) -> tuple[list[tuple[str, float]], dict[str, dict[str, float]]]:
    """Combine all sources into ranked list + per-source breakdown.

    Returns:
        ([(ticker, score), ...], {ticker: {source: contribution, ...}})
    """
    scores: dict[str, float] = {}
    breakdown: dict[str, dict[str, float]] = {}
    source_presence: dict[str, set] = {}  # ticker → set of source names
    # Adaptive per-source weights learned from forward returns of past signals
    # (signal_outcomes module) — a source whose picks keep going up counts more,
    # one whose picks keep going nowhere counts less. {} until enough history.
    adaptive_wt = _load_source_weights()

    def _add(ticker: str, source: str, pts: float) -> None:
        ticker = _norm_ticker(ticker)
        if not ticker:
            return
        # Cap each source's TOTAL per-ticker contribution. One viral feed (a Reddit
        # thread spamming a ticker 500×) would otherwise dominate the raw score and
        # clear the buy gate on a single source's volume. Capping makes breadth
        # (confirmation across many sources) win over one source's raw count.
        pts = max(0.0, float(pts or 0)) * adaptive_wt.get(source, 1.0)
        prev = breakdown.get(ticker, {}).get(source, 0.0)
        new_total = min(prev + pts, _MAX_PER_SOURCE_PTS)
        delta = new_total - prev
        if delta <= 0:
            return
        scores[ticker] = scores.get(ticker, 0.0) + delta
        breakdown.setdefault(ticker, {})[source] = new_total
        source_presence.setdefault(ticker, set()).add(source)

    for t, n in reddit.items():
        _add(t, "reddit", n * 2.0)
    for t, n in ddg.items():
        _add(t, "ddg", n * 1.5)
    for t in yahoo:
        _add(t, "yahoo", 3.0)
    for t, n in (twitter or {}).items():
        _add(t, "twitter", n * 2.5)
    for t, n in (google_news or {}).items():
        _add(t, "google_news", n * 1.5)
    for t, n in (seeking_alpha or {}).items():
        _add(t, "seeking_alpha", n * 2.0)
    for i, t in enumerate(stockanalysis or []):
        _add(t, "stockanalysis", max(4.0 - i * 0.1, 1.0))
    for t, n in (marketaux or {}).items():
        _add(t, "marketaux", n * 3.0)
    for t, n in (insider or {}).items():
        _add(t, "insider", n * 1.5)
    for t, n in (trusted_twitter or {}).items():
        # Diminishing returns: a name in 50 trader tweets must not pin the same flat
        # cap as one in 5. n*3.5 hit the 60 per-source cap at n>=18, so trusted_twitter
        # contributed an identical ~60 to every popular ticker and washed out differentiation.
        _add(t, "trusted_twitter", min(40.0, 8.0 * (float(n) ** 0.5)))
    # New free sources
    for t, n in (stocktwits or {}).items():
        _add(t, "press_releases", n * 3.0)  # corporate press releases (high signal)
    for t, n in (finviz or {}).items():
        _add(t, "finviz", n * 2.5)        # price/volume momentum
    for t, n in (rss_news or {}).items():
        _add(t, "rss_news", n * 1.5)      # news mention, same weight as ddg
    for t, n in (av_movers or {}).items():
        _add(t, "av_movers", n * 2.0)     # alphavantage top movers
    for t, n in (yahoo_movers or {}).items():
        _add(t, "yahoo_movers", n * 2.0)  # yahoo screener momentum
    for t, n in (brave or {}).items():
        _add(t, "brave", n * 2.0)          # brave news search (same weight as DDG)
    for t, n in (google_trends or {}).items():
        _add(t, "google_trends", n * 2.0)  # rising Google-search interest (leading attention)
    for t, n in (discovery or {}).items():
        _add(t, "discovery", n * 3.0)      # price/volume breakout (no buzz needed)
    for t, n in (analyst or {}).items():
        _add(t, "analyst", n * 2.5)        # fresh analyst upgrade / bullish rec skew
    for t, n in (options_flow or {}).items():
        _add(t, "options_flow", n * 3.0)   # unusual call flow / call-skew confirmation

    # Multi-source confirmation bonus: +3 per QUALITY source beyond the first
    # (max +15). Confirmation must come from real conviction feeds — two screener
    # / mover feeds agreeing is not a confirmed thesis, so they earn no bonus.
    for t, src_set in source_presence.items():
        n_quality = len(src_set & _QUALITY_SOURCES)
        if n_quality >= 2:
            bonus = min((n_quality - 1) * 3.0, 15.0)
            scores[t] = scores.get(t, 0.0) + bonus
            breakdown.setdefault(t, {})["multi_source_bonus"] = bonus

    # ── Scan memory: historical persistence bonus ─────────────────────────────
    # Tickers that were strong in recent scans get a bonus even if quiet today.
    # This prevents NVDA (200pts yesterday, 0 today) from being ignored.
    historical = _get_historical_scores(n_scans=5)
    for t, bonus in historical.items():
        if bonus >= 3.0:  # only apply meaningful bonuses
            if t in scores:
                # Already in today's scan — smaller top-up
                top_up = round(bonus * 0.4, 1)
                scores[t] = scores[t] + top_up
                breakdown.setdefault(t, {})["scan_memory"] = top_up
                source_presence.setdefault(t, set()).add("scan_memory")
            else:
                # Not in today's scan — full historical bonus keeps it alive
                scores[t] = bonus
                breakdown[t] = {"scan_memory": bonus}
                source_presence.setdefault(t, set()).add("scan_memory")

    # ── Sentiment-weighted BUZZ modulation ────────────────────────────────────
    # Buzz must reflect market CONVICTION, not attention. From the per-scan social
    # intent (lexicon for the clear posts, free-AI for the ambiguous ones), RAISE
    # buzz for bullish conviction and LOWER it for bearish (sell calls, profit-
    # taking, dilution, cut guidance, failed catalysts, downgrades). Decompose into
    # bull / bear / neutral / volume contributions, log them, and stash a net social
    # sentiment that feeds composite_score. Replaces the old asymmetric sell-only
    # penalty (which could lower but never raise, and never fed the composite).
    from tradingagents.screening import buzz_score as bz
    _bz_cfg = bz.BuzzConfig.from_env()
    for t in list(scores.keys()):
        intent = _SOCIAL_INTENT.get(t)
        if not intent:
            continue
        bd_buzz = bz.compute_buzz(scores[t], intent, cfg=_bz_cfg)
        scores[t] = round(bd_buzz.buzz, 1)
        _SOCIAL_SENTIMENT[t] = bd_buzz.net_sentiment
        b = breakdown.setdefault(t, {})
        b["bull_contrib"] = round(bd_buzz.bull, 1)
        b["bear_contrib"] = -round(bd_buzz.bear, 1)
        b["neutral_contrib"] = round(bd_buzz.neutral, 1)
        b["volume_contrib"] = round(bd_buzz.volume, 1)
        b["buzz_sentiment"] = round(bd_buzz.net_sentiment, 3)
        b["bull_bear_ratio"] = round(bd_buzz.bull_bear_ratio, 2)
        if bd_buzz.avoid:
            b["avoid"] = True
            b["sell_intent_reason"] = intent.get("sell_reason", "net social selling")
        if bd_buzz.bull > 0 or bd_buzz.bear > 0 or bd_buzz.n_total >= 3:
            log.info(
                "buzz %s: base=%.1f bull=+%.1f bear=-%.1f neut=+%.1f vol=%.1f "
                "ratio=%.2f net_sent=%+.2f buy=%d sell=%d → buzz=%.1f%s",
                t, bd_buzz.base, bd_buzz.bull, bd_buzz.bear, bd_buzz.neutral,
                bd_buzz.volume, bd_buzz.bull_bear_ratio, bd_buzz.net_sentiment,
                bd_buzz.n_bull, bd_buzz.n_bear, bd_buzz.buzz,
                " [AVOID]" if bd_buzz.avoid else "")

    # Drop crypto / junk, require minimum buzz
    _CRYPTO = {"BTC-USD","ETH-USD","SOL-USD","XRP-USD","DOGE-USD","BTC","ETH","SOL"}
    # Broad-market index ETFs are social noise ("buy SPY"), not the single-name
    # conviction stock picks this thematic book trades. Excluded so they can't
    # rank or seed a signal. Sector/leveraged ETFs are intentionally NOT here —
    # those can be a deliberate momentum vehicle.
    _INDEX_ETFS = {
        "SPY","QQQ","IWM","DIA","VOO","VTI","IVV","SPLG","VT","VEA","VWO",
        "VXUS","VIG","SCHB","SCHX","RSP","QQQM","ITOT",
    }
    _EXCLUDE = _CRYPTO | _INDEX_ETFS
    scores = {t: s for t, s in scores.items()
              if len(t) >= 2 and t not in _EXCLUDE and s >= 2}
    breakdown = {t: v for t, v in breakdown.items() if t in scores}

    # Insider + social combo bonus: insider buying AND (twitter OR reddit) = +8
    for t in list(scores.keys()):
        bd = breakdown.get(t, {})
        has_insider = bd.get("insider", 0) > 0
        has_social = bd.get("trusted_twitter", 0) > 0 or bd.get("reddit", 0) > 0
        if has_insider and has_social:
            scores[t] = scores[t] + 8.0
            breakdown[t]["insider_social_combo"] = 8.0

    # Quality gate: tickers from low-signal sources (finviz/movers/rss/pr) only
    # are likely OTC/foreign stocks — require at least one quality source OR score >= 60.
    # Uses the module-level _QUALITY_SOURCES (shared with the confirmation bonus).
    # stockanalysis/finviz/movers can pick up OTC/foreign stocks — require quality source
    # OR very high score (80+) to make it through without a quality source
    _LOW_SIGNAL_ONLY_MIN_SCORE = 80.0
    filtered_by_quality: dict[str, float] = {}
    for t, s in scores.items():
        bd = breakdown.get(t, {})
        has_quality = any(src in bd for src in _QUALITY_SOURCES)
        if has_quality or s >= _LOW_SIGNAL_ONLY_MIN_SCORE:
            filtered_by_quality[t] = s
    scores = filtered_by_quality
    breakdown = {t: v for t, v in breakdown.items() if t in scores}

    # ── Single-source confirmation dampener ───────────────────────────────────
    # A name carried by exactly ONE source with no cross-confirmation is a classic
    # false positive (a single Reddit pump, one DDG hit). Dampen it 0.7× so it
    # has to clear the buy gate on real strength, not one noisy feed. High-trust
    # solo sources (insider cluster buys, vetted-trader/press-release feeds) are
    # exempt — a single one of those is genuine signal on its own.
    _HIGH_TRUST_SOLO = {"insider", "trusted_twitter", "press_releases", "marketaux", "discovery", "options_flow"}
    _SOLO_DAMPEN = 0.7
    for t in list(scores.keys()):
        srcs = source_presence.get(t, set())
        # scan_memory is the ticker's OWN prior-scan history, not an independent
        # live source — it must not count as cross-confirmation here, or a
        # single-live-feed name would dodge the dampener just for having appeared
        # before. Dampen when there is <= 1 live (non-memory) source.
        live = srcs - {"scan_memory"}
        if len(live) <= 1 and not (live & _HIGH_TRUST_SOLO):
            scores[t] = round(scores[t] * _SOLO_DAMPEN, 1)
            breakdown.setdefault(t, {})["single_source_dampener"] = _SOLO_DAMPEN

    # AI ticker validation (cheap, cached): drop symbols the AI confirms are NOT
    # real US-listed tickers (uppercase prose like OLDER/CYCLE/SPEND that slipped
    # past the lexicon blocklist). Fail-open. Runs before the heavier yfinance
    # price check, cutting its load too.
    try:
        ai_valid = await _ai_validate_tickers(list(scores.keys()))
        dropped = [t for t in scores if t not in ai_valid]
        if dropped:
            log.info("AI ticker validation dropped %d non-tickers: %s", len(dropped), dropped[:10])
        scores = {t: s for t, s in scores.items() if t in ai_valid}
        breakdown = {t: v for t, v in breakdown.items() if t in scores}
    except Exception as e:
        log.debug("AI ticker validation skipped: %s", e)

    valid = await _validate_tickers(list(scores.keys()))
    filtered_scores = {t: s for t, s in scores.items() if t in valid}
    filtered_breakdown = {t: v for t, v in breakdown.items() if t in filtered_scores}

    ranked = sorted(filtered_scores.items(), key=lambda x: x[1], reverse=True)[:25]
    return ranked, filtered_breakdown


def _validate_tickers_sync(tickers: list[str]) -> set[str]:
    """Blocking yfinance check — run via executor, never call directly in async path."""
    valid: set[str] = set()
    try:
        import yfinance as yf
        if not tickers:
            return valid
        data = yf.download(tickers, period="1d", auto_adjust=True, progress=False, threads=True)
        if hasattr(data, "columns") and hasattr(data.columns, "get_level_values"):
            syms = data["Close"].columns.tolist() if "Close" in data else []
        elif "Close" in data:
            syms = [tickers[0]]
        else:
            syms = []
        for sym in syms:
            try:
                series = data["Close"][sym] if len(syms) > 1 else data["Close"]
                if series.dropna().__len__() > 0:
                    valid.add(sym.upper())
            except Exception:
                pass
    except Exception as e:
        log.warning("Ticker validation: %s", e)
        valid = set(tickers)  # fallback: trust them all
    return valid


async def _validate_tickers(tickers: list[str]) -> set[str]:
    """Non-blocking wrapper — runs yfinance in thread pool to avoid blocking event loop."""
    if not tickers:
        return set()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _validate_tickers_sync, tickers)


# ── AI analysis ───────────────────────────────────────────────────────────────

THEMES_MAP = {
    "ai_leaders":        ["NVDA","AMD","GOOGL","MSFT","META","AMZN","ORCL"],
    "ai_infrastructure": ["SMCI","DELL","ANET","CRDO","CIEN","LITE","VIAV"],
    "optical_network":   ["CIEN","LITE","CRDO","COHR","VIAV","FNSR"],
    "memory_hbm":        ["MU","SK","KIOXIA","WDC","STX"],
    "datacenter_power":  ["VST","NRG","TALO","CEG","GEV","ETN","EATON"],
    "nuclear_energy":    ["CCJ","UEC","DNN","NXE","SMR","OKLO","BWXT"],
    "space_defense":     ["RKLB","LUNR","LMT","RTX","NOC","BA","SPCE"],
    "quantum_future":    ["IONQ","QUBT","RGTI","IBM","HON","QMCO"],
    "critical_minerals": ["MP","UUUU","NMG","NOVL","LAC","PLL","LITM"],
    "reshoring":         ["CAT","DE","EMR","HON","GE","MMM","ITW"],
    "fintech_consumer":  ["SOFI","AFRM","UPST","PYPL","SQ","COIN","HOOD"],
    "future_tech":       ["CRSP","BEAM","EDIT","NTLA","RXRX","ARKG"],
}

def _guess_theme(ticker: str) -> str:
    ticker = ticker.upper()
    for theme, members in THEMES_MAP.items():
        if ticker in members:
            return theme
    return "future_tech"


_VALID_THEMES = set(THEMES_MAP.keys())


def _validate_pick(pick: Any) -> dict | None:
    """Sanitize and clamp one AI pick. Returns None if pick is unusable."""
    if not isinstance(pick, dict):
        return None
    ticker = str(pick.get("ticker", "")).upper().strip()
    if not ticker or not ticker.isalpha() or len(ticker) > 5 or ticker in _SKIP:
        return None
    theme = str(pick.get("theme", "future_tech"))
    if theme not in _VALID_THEMES:
        theme = _guess_theme(ticker)
    try:
        conviction = int(float(pick.get("conviction", 7)))
        conviction = max(1, min(10, conviction))
    except (TypeError, ValueError):
        conviction = 7
    try:
        # Cap at 300% — let winners run; the AI is told real plays run 50-200%+.
        target_pct = float(pick.get("target_pct", 40))
        target_pct = max(5.0, min(300.0, target_pct))
    except (TypeError, ValueError):
        target_pct = 40.0
    try:
        stop_pct = float(pick.get("stop_pct", 8))
        stop_pct = max(2.0, min(25.0, stop_pct))
    except (TypeError, ValueError):
        stop_pct = 8.0
    try:
        hold_days = int(float(pick.get("hold_days", 7)))
        hold_days = max(1, min(45, hold_days))
    except (TypeError, ValueError):
        hold_days = 7
    try:
        sentiment = max(-1.0, min(1.0, float(pick.get("sentiment", 0.0))))
    except (TypeError, ValueError):
        sentiment = 0.0
    return {
        "ticker":     ticker,
        "name":       str(pick.get("name", ticker))[:80],
        "conviction": conviction,
        "theme":      theme,
        "thesis":     str(pick.get("thesis", ""))[:500],
        "catalyst":   str(pick.get("catalyst", ""))[:300],
        "bull_case":  str(pick.get("bull_case", ""))[:300],
        "bear_case":  str(pick.get("bear_case", ""))[:300],
        "sentiment":  sentiment,
        "crowd_view": str(pick.get("crowd_view", ""))[:240],
        "target_pct": target_pct,
        "stop_pct":   stop_pct,
        "hold_days":  hold_days,
    }


# Common LLM theme variants → the canonical THEMES_MAP key, so a mis-labelled
# theme keeps its real sector (which drives the per-theme concentration cap)
# instead of collapsing into 'future_tech'. Unmapped/unknown still → future_tech.
_THEME_ALIASES = {
    "ai": "ai_leaders", "artificial_intelligence": "ai_leaders", "ai_stocks": "ai_leaders",
    "semis": "ai_infrastructure", "semiconductors": "ai_infrastructure", "chips": "ai_infrastructure",
    "semiconductor": "ai_infrastructure", "gpu": "ai_infrastructure",
    "optical": "optical_network", "networking": "optical_network",
    "memory": "memory_hbm", "hbm": "memory_hbm", "dram": "memory_hbm",
    "datacenter": "datacenter_power", "data_center": "datacenter_power", "power": "datacenter_power",
    "nuclear": "nuclear_energy", "uranium": "nuclear_energy", "smr": "nuclear_energy",
    "defense": "space_defense", "space": "space_defense", "aerospace": "space_defense",
    "quantum": "quantum_future", "quantum_computing": "quantum_future",
    "minerals": "critical_minerals", "rare_earth": "critical_minerals", "rare_earths": "critical_minerals",
    "lithium": "critical_minerals", "mining": "critical_minerals",
    "reshoring": "reshoring", "onshoring": "reshoring", "manufacturing": "reshoring",
    "fintech": "fintech_consumer", "consumer": "fintech_consumer", "payments": "fintech_consumer",
}


def _canonical_theme(theme: object) -> str:
    """Fold an LLM-supplied theme to a canonical THEMES_MAP key, via alias map;
    unknown → 'future_tech'."""
    t = str(theme or "").strip().lower().replace(" ", "_").replace("-", "_")
    if t in _VALID_THEMES:
        return t
    return _THEME_ALIASES.get(t, "future_tech")


# Hard negative-catalyst terms. If a pick's OWN crowd_view/catalyst/thesis text
# mentions one of these, the situation is materially bad regardless of the buzz
# score or the LLM's sentiment number — a classic pump/disaster false positive
# (fraud probes, halts, delistings, short reports, dilution, going-concern).
# NOTE: bear_case is intentionally NOT scanned — it always describes downside.
_RED_FLAG_TERMS = (
    "fraud", "sec investigation", "sec probe", "subpoena", "doj ",
    "halted", "trading halt", "delist", "bankrupt", "chapter 11",
    "going concern", "short report", "short-seller", "short seller",
    "accounting irregular", "restatement", "dilution", "offering priced",
    "going-concern", "default on", "investigation into", "class action",
    "pump and dump", "pump-and-dump", "ponzi", "rug pull", "rug-pull",
    "going to zero", "bankruptcy filing", "chapter 7", "securities fraud",
)


def _has_red_flag(pick: dict) -> bool:
    text = " ".join(str(pick.get(k, "")) for k in ("crowd_view", "catalyst", "thesis", "name")).lower()
    return any(term in text for term in _RED_FLAG_TERMS)


# A pick with no concrete, dated catalyst is momentum-on-hope, not a thesis. These
# generic fillers count as "no catalyst" → conviction is capped (mirrors the
# prompt rule the LLM is asked to follow, enforced deterministically).
_GENERIC_CATALYSTS = frozenset({
    "", "momentum", "social momentum", "buzz", "social buzz", "hype", "n/a",
    "na", "none", "trending", "volume", "unknown", "social media", "interest",
    "attention", "chatter", "tbd",
})


def _weak_catalyst(pick: dict) -> bool:
    cat = str(pick.get("catalyst", "")).strip().lower().rstrip(".")
    return cat in _GENERIC_CATALYSTS


def _clamp_num(val, lo, default, hi, *, as_int=False):
    """Coerce val to a finite number clamped to [lo, hi]; default on garbage."""
    import math as _m
    try:
        x = float(val)
    except (TypeError, ValueError):
        x = float(default)
    if not _m.isfinite(x):
        x = float(default)
    x = max(lo, min(hi, x))
    return int(round(x)) if as_int else x


def _sanitize_picks(picks: object, allowed_tickers: set[str]) -> list[dict[str, Any]]:
    """Clamp LLM pick output back to documented guardrails and drop hallucinations.

    The model is free to return out-of-range numbers or invent a ticker that was
    never in the trending list — either would seed a false-positive trade. So we:
      * drop any pick whose (normalized) ticker is not in the trending input,
      * clamp conviction[1-10], sentiment[-1,1], target_pct[15-300],
        stop_pct[5-15], hold_days[3-30] to their documented ranges,
      * coerce an unknown theme to 'future_tech'.
    Pure + deterministic — the safety floor on the LLM, mirroring holdings_brain.
    """
    if not isinstance(picks, list):
        return []
    out: list[dict[str, Any]] = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        tk = _norm_ticker(p.get("ticker"))
        if not tk or (allowed_tickers and tk not in allowed_tickers):
            continue  # hallucinated / off-list ticker
        q = dict(p)
        q["ticker"]     = tk
        q["conviction"] = _clamp_num(p.get("conviction"), 1, 5, 10, as_int=True)
        q["sentiment"]  = _clamp_num(p.get("sentiment"), -1.0, 0.0, 1.0)
        q["target_pct"] = _clamp_num(p.get("target_pct"), 15, 60, 300)
        q["stop_pct"]   = _clamp_num(p.get("stop_pct"), 5, 10, 15)
        q["hold_days"]  = _clamp_num(p.get("hold_days"), 3, 10, 30, as_int=True)
        q["theme"] = _canonical_theme(q.get("theme"))
        # Weak-catalyst cap: a pick with no concrete catalyst (generic filler) is
        # momentum-on-hope — cap conviction at 6 so it can't size up like a real
        # thesis. Deterministic enforcement of the prompt's catalyst rule.
        if _weak_catalyst(q):
            q["conviction"] = min(int(q["conviction"]), 6)
            q["weak_catalyst"] = True
        # Red-flag veto: a pick whose own narrative cites a hard negative catalyst
        # cannot be bullish, no matter the number the model returned. Force deep
        # bearish (composite_score hard-caps ≤ -0.5 to ≤45 → never auto-tradeable)
        # and knock conviction down so sizing/gates treat it as a probe at most.
        if _has_red_flag(q):
            q["sentiment"] = min(float(q["sentiment"]), -0.5)
            q["conviction"] = min(int(q["conviction"]), 4)
            q["red_flag"] = True
        # Coerce/bound free-text so a malformed (non-str / huge) field can't bloat
        # a proposal or break downstream rendering/serialization.
        q["name"] = str(q.get("name", tk) or tk)[:80]
        for _f in ("thesis", "catalyst", "bull_case", "bear_case", "crowd_view"):
            if _f in q:
                q[_f] = str(q[_f])[:500]
        out.append(q)
    # De-duplicate by ticker: the model sometimes lists the same name twice, which
    # would seed two proposals/trades for one ticker. Keep the highest-conviction
    # instance (tie → first seen) so a single canonical pick survives per name.
    best: dict[str, dict[str, Any]] = {}
    for q in out:
        cur = best.get(q["ticker"])
        if cur is None or int(q["conviction"]) > int(cur["conviction"]):
            best[q["ticker"]] = q
    return list(best.values())


def _build_ai_pick_prompt(ticker_str: str, news_text: str) -> str:
    """Build the thematic pick prompt. Pure (no I/O) so the picking discipline is
    testable. Encodes the accuracy rules the deterministic layer also enforces:
    only the trending tickers, a concrete catalyst, lone-hype down-ranking, and a
    hard skip on red-flag names — so the LLM and the sanitizer pull the same way."""
    return f"""You are a disciplined momentum stock analyst. You track social buzz, news catalysts, AND insider/congressional buying as conviction signals.

Trending tickers by combined signal score (Reddit buzz + news mentions + insider buys + congressional trades):
{ticker_str}

Recent news headlines:
{news_text}

RULES (follow strictly — they drive real position sizing):
1. Pick ONLY from the trending tickers listed above. Never invent or substitute a ticker that is not in that list.
2. Require a CONCRETE catalyst — a specific earnings date, product launch, contract/award, approval, guidance, or insider cluster buy. A name with only vague "momentum" or "hype" and no specific catalyst gets conviction <= 6.
3. A name trending on a SINGLE source with no cross-confirmation is unproven — cap its conviction at 6. Names confirmed across MULTIPLE sources AND with insider buying get the highest conviction.
4. READ WHAT THE CROWD IS ACTUALLY SAYING — bullish (buy/squeeze/breakout) vs bearish (sell/dump/short/crash/overvalued). A name the crowd is BEARISH on is NOT a buy.
5. SKIP entirely any name whose chatter cites fraud, an SEC/DOJ investigation, a trading halt, delisting, going-concern, dilution, or a short-seller report — these are not buys at any buzz level.

Pick the TOP 6 highest-conviction LONG plays that satisfy the rules.

Respond ONLY with a JSON array (no markdown, no explanation):
[
  {{
    "ticker": "NVDA",
    "conviction": 9,
    "theme": "ai_leaders",
    "name": "NVIDIA Corporation",
    "thesis": "Why this is the wave to ride (1-2 sentences)",
    "catalyst": "Specific upcoming catalyst or current momentum driver",
    "bull_case": "What sends it higher",
    "bear_case": "What kills the trade",
    "sentiment": 0.7,
    "crowd_view": "What people are actually saying — quote the vibe (e.g. 'Reddit calling breakout, some warn overbought')",
    "target_pct": 60,
    "stop_pct": 10,
    "hold_days": 10
  }}
]

theme must be one of: ai_leaders, ai_infrastructure, optical_network, memory_hbm, datacenter_power, nuclear_energy, space_defense, quantum_future, critical_minerals, reshoring, fintech_consumer, future_tech
conviction: 1-10
sentiment: -1.0 (crowd says SELL/crash) to +1.0 (crowd euphoric/buying) — your read of the actual crowd polarity
crowd_view: one short sentence on what people are posting, including any 'sell' / bearish takes
target_pct: 15-300 — LET WINNERS RUN. Real momentum/social plays routinely run 50-200%+, not 20%. Set the target where the move realistically tops, not a timid 20%. High conviction + strong catalyst → aim 80-200%.
stop_pct: 5-15, hold_days: 3-30"""


async def _ai_pick(tickers_ranked: list[tuple[str, float]], news_blobs: list[str]) -> list[dict[str, Any]]:
    """Call Cloudflare AI (free) to analyze trending tickers and output conviction picks."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    cf_token   = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    model      = os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast").strip()
    gateway_url = os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip()

    if not (account_id and cf_token):
        # Fallback to OpenRouter if CF not configured
        return await _ai_pick_openrouter(tickers_ranked, news_blobs)

    news_text  = "\n".join(news_blobs[:30])[:3000]
    ticker_str = ", ".join(f"{t}({s:.0f})" for t, s in tickers_ranked[:20])

    prompt = _build_ai_pick_prompt(ticker_str, news_text)

    try:
        if gateway_url:
            # Use AI Gateway (strips trailing slash, appends model)
            base = gateway_url.rstrip("/")
            url  = f"{base}/workers-ai/{model}"
        else:
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {cf_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messages": [
                        {"role": "system", "content": "You are a momentum stock analyst. Always respond with valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.4,
                },
            )
        data = r.json()
        # CF AI wraps response: {"result": {"response": "..."}}
        content = (data.get("result") or {}).get("response", "")
        if not content:
            # fallback path for gateway which may return OpenAI-compatible format
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        content = content.strip()
        content = re.sub(r"^```[a-z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        # Extract JSON array even if model adds preamble text
        m = re.search(r"\[.*\]", content, re.DOTALL)
        if m:
            content = m.group(0)
        picks = json.loads(content)
        allowed = {_norm_ticker(t) for t, _ in tickers_ranked}
        picks = _sanitize_picks(picks, allowed)
        log.info("CF AI returned %d picks (post-sanitize)", len(picks))
        return picks
    except Exception as e:
        log.error("CF AI pick failed: %s", e)
        return await _ai_pick_openrouter(tickers_ranked, news_blobs)


async def _ai_pick_openrouter(tickers_ranked: list[tuple[str, float]], news_blobs: list[str]) -> list[dict[str, Any]]:
    """Fallback: OpenRouter GPT-4o-mini."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return []
    news_text  = "\n".join(news_blobs[:30])[:3000]
    ticker_str = ", ".join(f"{t}({s:.0f})" for t, s in tickers_ranked[:20])
    prompt = f"Trending tickers: {ticker_str}\nNews: {news_text}\nPick TOP 6 momentum LONG plays. Read the crowd: skip names people are bearish on (sell/dump/crash). Let winners run — set realistic high targets (momentum plays run 50-200%+, not 20%). Return JSON array only with fields: ticker, conviction(1-10), theme, name, thesis, catalyst, bull_case, bear_case, sentiment(-1.0 sell..+1.0 buy), crowd_view(what people say), target_pct(15-300), stop_pct(5-15), hold_days(3-30)."
    allowed = {_norm_ticker(t) for t, _ in tickers_ranked}
    # FREE models only (never the user's credit), tried in order, skipping 429s.
    for model in _openrouter_intent_models()[:_OR_INTENT_MAX_ATTEMPTS]:
        if not _openrouter_call_ok():
            log.warning("OpenRouter daily call budget reached — skipping AI pick fallback")
            break
        try:
            _record_openrouter_call()
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.4, "max_tokens": 2000},
                )
            if r.status_code == 429:
                log.info("AI pick OR %s rate-limited (429) — next model", model)
                continue
            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```[a-z]*\n?", "", content); content = re.sub(r"\n?```$", "", content)
            m = re.search(r"\[.*\]", content, re.DOTALL)
            picks = _sanitize_picks(json.loads(m.group(0) if m else content), allowed)
            if picks:
                log.info("AI pick (OR %s): %d picks", model, len(picks))
                return picks
        except Exception as e:
            log.warning("OpenRouter pick %s failed: %s", model, e)
    return []


# ── AI intent classification (free models: Cloudflare → OpenRouter) ─────────────
# The lexicon classifier (tweet_intent) runs on EVERY scraped post (free, instant,
# deterministic). The free LLM is then used "where applicable" — only on the posts
# the lexicon couldn't read confidently — to resolve them, batched into ONE call
# per scan so it stays within the free tiers. Failure degrades to the lexicon.
_AI_INTENT_MAX = 60           # max posts sent to the AI per scan (cost/latency bound)

# Cloudflare Workers AI free tier = 10,000 Neurons/day (resets 00:00 UTC); over it,
# calls hard-FAIL. So (a) intent uses a CHEAP model — not the 70B pick model — and
# (b) we track approx neuron spend per UTC day and stop calling CF before the cap,
# degrading to OpenRouter (separate quota) then the lexicon. Neurons/M (input,output)
# from CF's price table; default intent model llama-3.2-3b is ~6x cheaper than 70B.
_CF_NEURON_RATES = {
    "@cf/meta/llama-3.2-1b-instruct": (2457, 18252),
    "@cf/meta/llama-3.2-3b-instruct": (4625, 30475),
    "@cf/ibm-granite/granite-4.0-h-micro": (1542, 10158),
    "@cf/qwen/qwen3-30b-a3b-fp8": (4625, 30475),
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": (4119, 34868),
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": (26668, 204805),
}
_CF_RATE_FALLBACK = (26668, 204805)   # assume expensive if unknown (conservative)
_NEURON_USAGE_FILE = TMP / "cf_neuron_usage.json"


def _cf_daily_neuron_budget() -> float:
    """Stop calling CF once the day's estimated neurons reach this (headroom under
    the 10k free cap). Env CF_DAILY_NEURON_BUDGET; 0 disables the guard."""
    try:
        return max(0.0, float(os.getenv("CF_DAILY_NEURON_BUDGET", "9000")))
    except (TypeError, ValueError):
        return 9000.0


def _estimate_neurons(model: str, in_tokens: int, out_tokens: int) -> float:
    ri, ro = _CF_NEURON_RATES.get(model, _CF_RATE_FALLBACK)
    return in_tokens / 1e6 * ri + out_tokens / 1e6 * ro


def _neuron_usage_today() -> float:
    import datetime as _dt
    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        d = json.loads(_NEURON_USAGE_FILE.read_text())
        return float(d.get(today, 0.0)) if d.get("date") == today else 0.0
    except Exception:
        return 0.0


def _add_neuron_usage(n: float) -> None:
    import datetime as _dt
    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    cur = _neuron_usage_today()
    try:
        _atomic_write(_NEURON_USAGE_FILE, {"date": today, today: round(cur + n, 1)})
    except Exception:
        pass


def _cf_intent_model() -> str:
    """Cheap, JSON-reliable model for the high-volume intent classifier (NOT the
    70B pick model). Env CLOUDFLARE_INTENT_MODEL; default llama-3.2-3b
    (~31x daily-cap headroom). granite-4.0-h-micro is the ultra-cheap option."""
    return os.getenv("CLOUDFLARE_INTENT_MODEL", "@cf/meta/llama-3.2-3b-instruct").strip()


# OpenRouter free tier = ~1000 requests/day. Track calls per UTC day and stop
# before the cap so the fallback never hard-fails (then we ride the lexicon).
_OR_USAGE_FILE = TMP / "openrouter_usage.json"


def _openrouter_daily_budget() -> int:
    try:
        return max(0, int(float(os.getenv("OPENROUTER_DAILY_CALL_BUDGET", "900"))))
    except (TypeError, ValueError):
        return 900


def _openrouter_calls_today() -> int:
    import datetime as _dt
    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        d = json.loads(_OR_USAGE_FILE.read_text())
        return int(d.get(today, 0)) if d.get("date") == today else 0
    except Exception:
        return 0


def _record_openrouter_call() -> None:
    import datetime as _dt
    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        _atomic_write(_OR_USAGE_FILE, {"date": today, today: _openrouter_calls_today() + 1})
    except Exception:
        pass


def _openrouter_call_ok() -> bool:
    """True if an OpenRouter call is allowed under today's free-tier call budget."""
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return False
    budget = _openrouter_daily_budget()
    return budget <= 0 or _openrouter_calls_today() < budget


# OpenRouter free intent models, tried in order until one responds (the big-name
# free models — llama-3.3-70b, qwen3-80b — are frequently provider-429'd, so a
# single hardcoded model is unreliable). Verified 2026-06-20 against the live
# /models list + test calls: gpt-oss-120b and gemma-4-31b returned clean JSON with
# correct labels; the 70b/qwen were rate-limited. Free tier is CALL-capped not
# token-capped, so we prefer the strongest models. Env OPENROUTER_INTENT_MODELS
# (comma-sep) overrides; OPENROUTER_INTENT_MODEL (singular) is prepended if set.
_OR_INTENT_MODELS_DEFAULT = [
    "openai/gpt-oss-120b:free",
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
]
_OR_INTENT_MAX_ATTEMPTS = 3   # don't burn the daily budget probing many 429s


def _free_only(models: list) -> list:
    """Keep ONLY OpenRouter ``:free`` models — never spend the user's credit.
    A non-:free id (even if set via env) is dropped, so the credit balance is
    never touched by this app."""
    return [m for m in models if m and m.strip().endswith(":free")]


def _openrouter_intent_models() -> list:
    env = os.getenv("OPENROUTER_INTENT_MODELS", "").strip()
    models = [m.strip() for m in env.split(",") if m.strip()] if env else list(_OR_INTENT_MODELS_DEFAULT)
    single = os.getenv("OPENROUTER_INTENT_MODEL", "").strip()
    if single and single not in models:
        models.insert(0, single)
    free = _free_only(models)
    return free or list(_OR_INTENT_MODELS_DEFAULT)   # never fall back to a paid model


def _ai_intent_enabled() -> bool:
    if os.getenv("THEMATIC_AI_INTENT", "true").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    return bool((os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN"))
                or os.getenv("OPENROUTER_API_KEY"))


def _ai_intent_conf_threshold() -> float:
    """Send a post to the AI unless the lexicon is at LEAST this confident — i.e.
    don't let the lexicon make 'iffy' borderline buy/sell calls; defer them to the
    AI. Default 0.85 (only overwhelmingly explicit reads like an outright "sold my
    $NVDA" with multiple cues stay lexicon-only). Env THEMATIC_AI_INTENT_THRESHOLD;
    set 1.0 to route EVERY ticker post to AI, 0.0 to fully trust the lexicon."""
    try:
        return max(0.0, min(1.0, float(os.getenv("THEMATIC_AI_INTENT_THRESHOLD", "0.85"))))
    except (TypeError, ValueError):
        return 0.85


_INTENT_SYS = (
    "You classify a social post's trading intent toward ONE ticker. Read the actual "
    "action, not just hype. A bearish ACTION (selling/trimming/took profits/exiting/"
    "reducing/warning/overextended) beats bullish words. Labels exactly one of: "
    "BUY_SIGNAL, SELL_SIGNAL, HOLD_SIGNAL, WATCHLIST_ONLY, NEWS_ONLY, UNCLEAR. "
    "Respond ONLY with a JSON array."
)


def _build_intent_prompt(items: "list[tuple[str,str]]") -> str:
    lines = [f'{i}. ${tk} :: {text[:240]}' for i, (tk, text) in enumerate(items)]
    return (
        "Classify each item's intent toward its $TICKER. For mixed posts, the ACTION "
        "wins (\"love it but trimming\" = SELL_SIGNAL).\n\n"
        + "\n".join(lines)
        + '\n\nReturn ONLY: [{"i":0,"label":"BUY_SIGNAL","sentiment":0.6,"reason":"..."}, ...]'
        ' — one per item. sentiment is -1.0 (very bearish) .. +1.0 (very bullish);'
        ' use small magnitudes for mixed / uncertain posts.'
    )


def _parse_intent_json(content: str, items: "list[tuple[str,str]]") -> dict:
    from tradingagents.screening import tweet_intent as ti
    content = re.sub(r"^```[a-z]*\n?", "", (content or "").strip())
    content = re.sub(r"\n?```$", "", content)
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if not m:
        return {}
    rows = json.loads(m.group(0))
    valid = {ti.BUY_SIGNAL, ti.SELL_SIGNAL, ti.HOLD_SIGNAL, ti.WATCHLIST_ONLY, ti.NEWS_ONLY, ti.UNCLEAR}
    out: dict[int, "ti.IntentResult"] = {}
    for row in rows:
        try:
            i = int(row.get("i"))
            label = str(row.get("label", "")).upper().strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if label not in valid or not (0 <= i < len(items)):
            continue
        tk = items[i][0]
        try:
            _ai_sent = max(-1.0, min(1.0, float(row.get("sentiment"))))
        except (TypeError, ValueError):
            _ai_sent = (0.6 if label == ti.BUY_SIGNAL else -0.6 if label == ti.SELL_SIGNAL else 0.0)
        out[i] = ti.IntentResult(
            ticker=tk, label=label, action="ai",
            sentiment=_ai_sent,
            confidence=0.75, reason=str(row.get("reason", ""))[:120],
            increase_buy=(label == ti.BUY_SIGNAL), reduce_buy=(label == ti.SELL_SIGNAL),
        )
    return out


async def _ai_classify_intents(items: "list[tuple[str,str]]") -> dict:
    """Classify a batch of (ticker, post_text) via free LLM. Cloudflare first,
    OpenRouter fallback. Returns {index: IntentResult}; {} on any failure (caller
    keeps the lexicon read). Capped at _AI_INTENT_MAX items."""
    if not items or not _ai_intent_enabled():
        return {}
    items = items[:_AI_INTENT_MAX]
    prompt = _build_intent_prompt(items)
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    model = _cf_intent_model()
    gateway_url = os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip()
    if account_id and cf_token:
        # Daily neuron budget guard — skip CF (→ OpenRouter) if this call would
        # push past the free-cap headroom, so we never hit the hard-fail.
        max_out = 1500
        est = _estimate_neurons(model, len(prompt) // 4 + 60, min(max_out, len(items) * 14))
        budget = _cf_daily_neuron_budget()
        if budget > 0 and (_neuron_usage_today() + est) > budget:
            log.warning("CF neuron budget reached (%.0f/%.0f today) — intent → OpenRouter/lexicon",
                        _neuron_usage_today(), budget)
        else:
            try:
                url = (f"{gateway_url.rstrip('/')}/workers-ai/{model}" if gateway_url
                       else f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}")
                async with httpx.AsyncClient(timeout=45) as client:
                    r = await client.post(url, headers={"Authorization": f"Bearer {cf_token}"},
                        json={"messages": [{"role": "system", "content": _INTENT_SYS},
                                           {"role": "user", "content": prompt}],
                              "max_tokens": max_out, "temperature": 0.1})
                data = r.json()
                # Prefer CF's reported usage when present; else our estimate.
                usage = (data.get("result") or {}).get("usage") or data.get("usage") or {}
                actual = _estimate_neurons(model,
                    int(usage.get("prompt_tokens", len(prompt) // 4 + 60)),
                    int(usage.get("completion_tokens", min(max_out, len(items) * 14))))
                _add_neuron_usage(actual)
                content = (data.get("result") or {}).get("response", "") or \
                          (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                res = _parse_intent_json(content, items)
                if res:
                    log.info("AI intent (CF %s): %d/%d posts, ~%.0f neurons (%.0f/day)",
                             model, len(res), len(items), actual, _neuron_usage_today())
                    return res
            except Exception as e:
                log.warning("AI intent CF failed: %s — trying OpenRouter", e)
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if api_key and _openrouter_call_ok():
        for model in _openrouter_intent_models()[:_OR_INTENT_MAX_ATTEMPTS]:
            if not _openrouter_call_ok():
                break
            try:
                _record_openrouter_call()
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post("https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": model,
                              "messages": [{"role": "system", "content": _INTENT_SYS},
                                           {"role": "user", "content": prompt}],
                              "temperature": 0.1, "max_tokens": 1500})
                if r.status_code == 429:        # provider overloaded → try next model
                    log.info("AI intent OR %s rate-limited (429) — next model", model)
                    continue
                content = r.json()["choices"][0]["message"]["content"]
                res = _parse_intent_json(content, items)
                if res:
                    log.info("AI intent (OR %s): %d/%d posts (%d/%d calls today)",
                             model, len(res), len(items), _openrouter_calls_today(), _openrouter_daily_budget())
                    return res
            except Exception as e:
                log.warning("AI intent OR %s failed: %s", model, e)
    elif api_key:
        log.warning("OpenRouter daily call budget reached (%d/%d) — intent → lexicon",
                    _openrouter_calls_today(), _openrouter_daily_budget())
    return {}


# ── Shared free-only AI completion (the one caller all AI features reuse) ────────
# FREE models only (never the $-credit), daily-budget-guarded on both providers.
#   prefer="cheap" → CF cheap intent model (3B) first, then OR free list.
#   prefer="smart" → OR free list (strong models) first, then CF 70B (neuron-budgeted).
# Sync core (httpx.Client) so the brain's sync llm_fn can use it; async wrapper for
# the scan path. Returns raw model text, or None on any failure / budget exhaustion.
_CF_SMART_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def _cf_complete_sync(model: str, system: str, prompt: str, max_tokens: int) -> "str | None":
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not (account_id and cf_token):
        return None
    est = _estimate_neurons(model, len(system + prompt) // 4 + 40, max_tokens)
    budget = _cf_daily_neuron_budget()
    if budget > 0 and (_neuron_usage_today() + est) > budget:
        log.warning("CF neuron budget reached (%.0f/%.0f) — AI → OpenRouter/skip", _neuron_usage_today(), budget)
        return None
    gateway_url = os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip()
    url = (f"{gateway_url.rstrip('/')}/workers-ai/{model}" if gateway_url
           else f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}")
    try:
        import httpx as _hx
        with _hx.Client(timeout=45) as c:
            r = c.post(url, headers={"Authorization": f"Bearer {cf_token}"},
                       json={"messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": prompt}],
                             "max_tokens": max_tokens, "temperature": 0.1})
        data = r.json()
        usage = (data.get("result") or {}).get("usage") or data.get("usage") or {}
        _add_neuron_usage(_estimate_neurons(model,
            int(usage.get("prompt_tokens", len(system + prompt) // 4 + 40)),
            int(usage.get("completion_tokens", max_tokens))))
        return (data.get("result") or {}).get("response", "") or \
               (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        log.warning("CF complete (%s) failed: %s", model, e)
        return None


def _or_complete_sync(system: str, prompt: str, max_tokens: int) -> "str | None":
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    import httpx as _hx
    for model in _openrouter_intent_models()[:_OR_INTENT_MAX_ATTEMPTS]:   # FREE-only list
        if not _openrouter_call_ok():
            log.warning("OpenRouter daily call budget reached — AI skipped")
            break
        try:
            _record_openrouter_call()
            with _hx.Client(timeout=40) as c:
                r = c.post("https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": prompt}],
                          "temperature": 0.1, "max_tokens": max_tokens})
            if r.status_code == 429:
                continue
            txt = r.json()["choices"][0]["message"]["content"]
            if txt:
                return txt
        except Exception as e:
            log.warning("OR complete (%s) failed: %s", model, e)
    return None


def _ai_complete_sync(system: str, prompt: str, *, prefer: str = "smart", max_tokens: int = 600) -> "str | None":
    if prefer == "cheap":
        return (_cf_complete_sync(_cf_intent_model(), system, prompt, max_tokens)
                or _or_complete_sync(system, prompt, max_tokens))
    return (_or_complete_sync(system, prompt, max_tokens)
            or _cf_complete_sync(_CF_SMART_MODEL, system, prompt, max_tokens))


async def _ai_complete(system: str, prompt: str, *, prefer: str = "smart", max_tokens: int = 600) -> "str | None":
    return await asyncio.to_thread(_ai_complete_sync, system, prompt, prefer=prefer, max_tokens=max_tokens)


def _extract_json(text: "str | None"):
    """Pull the first JSON object/array from a model reply (handles ``` fences)."""
    if not text:
        return None
    t = re.sub(r"^```[a-z]*\n?", "", text.strip())
    t = re.sub(r"\n?```$", "", t)
    m = re.search(r"(\{.*\}|\[.*\])", t, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


# ── #2 AI ticker validation — kill bare-word garbage ($OLDER/$CYCLE/$SPEND) ──────
# The lexicon's blocklist can't catch every uppercase prose word. AI confirms which
# extracted symbols are REAL US-listed equities. Persistent cache (a ticker's
# realness never changes) → each symbol is asked at most once, ever. FAIL-OPEN:
# only symbols the AI explicitly calls invalid are dropped, so an AI outage never
# discards genuine tickers.
_TICKER_VALID_FILE = TMP / "ticker_validation.json"
_ticker_valid_cache: dict | None = None


def _load_ticker_cache() -> dict:
    global _ticker_valid_cache
    if _ticker_valid_cache is None:
        try:
            _ticker_valid_cache = json.loads(_TICKER_VALID_FILE.read_text())
        except Exception:
            _ticker_valid_cache = {}
    return _ticker_valid_cache


def _save_ticker_cache() -> None:
    try:
        _atomic_write(_TICKER_VALID_FILE, _ticker_valid_cache or {})
    except Exception:
        pass


async def _ai_validate_tickers(tickers: "list[str]") -> set:
    """Return the subset of ``tickers`` that are NOT AI-confirmed-invalid.

    Cached symbols use their cached verdict; uncached ones are batched into one
    free-AI call. Symbols the AI marks invalid are dropped; everything else (valid,
    unknown, AI-unavailable) is kept (fail-open)."""
    cache = _load_ticker_cache()
    uniq = [t.upper() for t in dict.fromkeys(tickers) if t]
    unknown = [t for t in uniq if t not in cache]
    if unknown and _ai_intent_enabled():
        sys = ("You validate stock ticker symbols. Return ONLY a JSON object mapping "
               "each symbol to true if it is a REAL US-listed equity ticker (NYSE/Nasdaq/"
               "AMEX), false if it is an English word, abbreviation, or not a real ticker.")
        prompt = ("Which of these are real US-listed stock tickers? "
                  + ", ".join(unknown[:60])
                  + '\n\nReturn ONLY: {"NVDA": true, "OLDER": false, ...}')
        try:
            obj = _extract_json(await _ai_complete(sys, prompt, prefer="cheap", max_tokens=500))
            if isinstance(obj, dict):
                for k, v in obj.items():
                    cache[str(k).upper()] = bool(v)
                _save_ticker_cache()
        except Exception as e:
            log.debug("ticker validation failed: %s", e)
    # Drop only the explicitly-invalid; keep valid + unknown (fail-open).
    return {t for t in uniq if cache.get(t, True)}


# ── #3 News catalyst materiality — real catalyst vs noise + plain-English why-now ─
_CATALYST_QUALITY = {"strong", "moderate", "weak", "none"}


async def _ai_catalyst_materiality(signals: "list[dict]") -> dict:
    """Rate each finalist signal's catalyst: is there a REAL, time-relevant driver
    (earnings/contract/approval/product/guidance) or just vibes? Returns
    {ticker: {"catalyst_quality": str, "why_now": str}}. One batched free-AI call;
    {} on failure (callers keep their existing fields)."""
    if not signals or not _ai_intent_enabled():
        return {}
    rows = []
    for i, s in enumerate(signals[:25]):
        ctx = f"{s.get('thesis','')} | catalyst: {s.get('catalyst','')} | crowd: {s.get('crowd_view','')}"
        rows.append(f'{i}. ${s.get("ticker","")} :: {ctx[:240]}')
    sys = ("You judge whether a stock has a REAL, time-relevant CATALYST (specific "
           "earnings date, contract/award, FDA/approval, product launch, guidance, "
           "insider cluster) vs vague momentum/hype. Respond ONLY with a JSON array.")
    prompt = ("Rate each item's catalyst quality (strong|moderate|weak|none) and give "
              "a <=110-char why_now.\n\n" + "\n".join(rows)
              + '\n\nReturn ONLY: [{"i":0,"catalyst_quality":"strong","why_now":"..."}].')
    out: dict = {}
    try:
        arr = _extract_json(await _ai_complete(sys, prompt, prefer="smart", max_tokens=900))
        if isinstance(arr, list):
            for row in arr:
                try:
                    i = int(row.get("i"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if not (0 <= i < len(signals[:25])):
                    continue
                q = str(row.get("catalyst_quality", "")).lower().strip()
                out[signals[i]["ticker"]] = {
                    "catalyst_quality": q if q in _CATALYST_QUALITY else "weak",
                    "why_now": str(row.get("why_now", ""))[:140],
                }
    except Exception as e:
        log.debug("catalyst materiality failed: %s", e)
    return out


# ── #4 News-driven exit classification — bad news (exit) vs attention fade (hold) ─
def _ai_exit_check_enabled() -> bool:
    return os.getenv("THEMATIC_AI_EXIT_CHECK", "true").strip().lower() in ("1", "true", "yes", "on") \
        and _ai_intent_enabled()


async def _fetch_ticker_headlines(ticker: str, n: int = 6) -> "list[str]":
    """A few recent Google-News headlines for a ticker (best-effort, bounded)."""
    try:
        from bs4 import BeautifulSoup
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "xml")
        return [it.find("title").text.strip() for it in soup.find_all("item")[:n]
                if it.find("title")]
    except Exception:
        return []


async def _ai_exit_news_check(ticker: str) -> "tuple[bool, str]":
    """For a buzz-based exit, decide if there's MATERIALLY BAD news (→ confirm exit)
    or just fading attention (→ hold the position). Returns (bad_news, reason).

    Fails CONSERVATIVE: AI/news unavailable ⇒ (True, ...) so the buzz exit proceeds
    as it would today — AI can only RESCUE a position by positively clearing it."""
    if not _ai_exit_check_enabled():
        return True, "ai-exit-check disabled"
    heads = await _fetch_ticker_headlines(ticker)
    if not heads:
        return True, "no headlines / fetch failed"
    sys = ("You decide if a stock has MATERIALLY BAD recent news (guidance cut, earnings "
           "miss, fraud/probe, dilution/offering, downgrade, lawsuit, key exec loss, failed "
           "trial) vs neutral/positive/quiet. Respond ONLY with JSON.")
    prompt = (f"Recent {ticker} headlines:\n" + "\n".join(f"- {h}" for h in heads)
              + '\n\nReturn ONLY: {"bad_news": true|false, "reason": "<=100 chars"}')
    obj = _extract_json(await _ai_complete(sys, prompt, prefer="smart", max_tokens=200))
    if not isinstance(obj, dict) or "bad_news" not in obj:
        return True, "unparseable AI reply"
    return bool(obj.get("bad_news")), str(obj.get("reason", ""))[:120]


# ── #6 AI red-flag deepening — hidden risks the lexicon veto can't pattern-match ──
async def _ai_red_flag_check(signals: "list[dict]") -> dict:
    """Read each finalist's full narrative for HIDDEN risk the keyword veto misses
    (dilution/ATM offering, going-concern, pump-and-dump pattern, accounting probe,
    customer concentration, cash burn). Returns {ticker: {"red_flag": bool,
    "risk": str}}. Conservative-but-not-paranoid: only flags concrete risks. {} on
    failure (the lexicon veto still stands)."""
    if not signals or not _ai_intent_enabled():
        return {}
    rows = []
    for i, s in enumerate(signals[:25]):
        nar = f"{s.get('thesis','')} | bull: {s.get('bull_case','')} | bear: {s.get('bear_case','')}"
        rows.append(f'{i}. ${s.get("ticker","")} :: {nar[:280]}')
    sys = ("You are a short-seller-minded risk auditor. Flag a stock ONLY if its "
           "narrative reveals a CONCRETE downside risk: dilution/ATM/offering, going "
           "concern, pump-and-dump pattern, accounting/SEC probe, fraud, heavy cash "
           "burn, customer concentration, or imminent lockup. Vague/no risk = not "
           "flagged. Respond ONLY with a JSON array.")
    prompt = ("Audit each for a concrete red flag.\n\n" + "\n".join(rows)
              + '\n\nReturn ONLY: [{"i":0,"red_flag":false,"risk":""},'
                '{"i":1,"red_flag":true,"risk":"dilution: $500M ATM"}].')
    out: dict = {}
    try:
        arr = _extract_json(await _ai_complete(sys, prompt, prefer="smart", max_tokens=700))
        if isinstance(arr, list):
            for row in arr:
                try:
                    i = int(row.get("i"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if 0 <= i < len(signals[:25]) and bool(row.get("red_flag")):
                    out[signals[i]["ticker"]] = {"red_flag": True, "risk": str(row.get("risk", ""))[:140]}
    except Exception as e:
        log.debug("AI red-flag check failed: %s", e)
    return out


# ── Scan orchestrator ─────────────────────────────────────────────────────────

_scan_lock = asyncio.Lock()
# A11/P8: shared lock for all PAPER_STATE_FILE read-modify-write operations.
# os.replace is crash-safe but doesn't prevent concurrent RMW clobbers.
# Both thematic_auto and thematic_portfolio import this lock to serialize
# all paper-state mutations across endpoints and the auto-scan loop.
_paper_state_lock = asyncio.Lock()

SCORE_HISTORY_FILE = TMP / "thematic_score_history.jsonl"
_SCORE_HISTORY_MAX = 500  # max lines before pruning

# ── Signal outcome tracking + adaptive source weights ─────────────────────────
# Every scan records its top tickers with a price snapshot; later scans fill in
# 1d/5d forward returns; per-source hit-rates become clamped weight multipliers
# applied in _merge_signals. See tradingagents/screening/signal_outcomes.py.
OUTCOMES_FILE       = TMP / "thematic_signal_outcomes.jsonl"
SOURCE_WEIGHTS_FILE = TMP / "thematic_source_weights.json"
_source_weights_cache: dict = {"mtime": None, "weights": {}}


def _adaptive_weights_enabled() -> bool:
    return os.getenv("THEMATIC_ADAPTIVE_WEIGHTS", "true").strip().lower() in ("1", "true", "yes", "on")


def _load_source_weights() -> dict[str, float]:
    """Learned per-source multipliers, mtime-cached; {} (all 1.0) if disabled,
    missing, or unreadable — the scorer must never depend on this file."""
    if not _adaptive_weights_enabled():
        return {}
    try:
        mtime = SOURCE_WEIGHTS_FILE.stat().st_mtime
    except OSError:
        return {}
    if _source_weights_cache["mtime"] != mtime:
        from tradingagents.screening import signal_outcomes as so
        _source_weights_cache["weights"] = so.load_weights(SOURCE_WEIGHTS_FILE)
        _source_weights_cache["mtime"] = mtime
    return _source_weights_cache["weights"]


def _closed_trades_for_weights() -> list[dict]:
    """Closed thematic trades that carry a source breakdown — real-money/paper
    outcomes fold into the source weights at a higher observation weight."""
    try:
        state = json.loads(PAPER_STATE_FILE.read_text())
        return [
            {"sources": t.get("sources") or {}, "pnl_pct": float(t.get("pnl_pct", 0) or 0)}
            for t in (state.get("trades") or [])
            if isinstance(t, dict) and t.get("sources")
        ]
    except Exception:
        return []


def _update_signal_outcomes(ranked: list, breakdown: dict) -> None:
    """Post-scan (sync, run via to_thread): snapshot prices for the newly ranked
    tickers + any rows awaiting evaluation, fill forward returns, recompute and
    persist the adaptive source weights. Best-effort — never raises."""
    try:
        from datetime import datetime as _dt
        from tradingagents.screening import signal_outcomes as so
        cfg = so.OutcomeConfig.from_env()
        now = _dt.now()
        rows = so.load_rows(OUTCOMES_FILE)
        need = so.pending_tickers(rows, now, cfg) | {str(t).upper() for t, _ in ranked[: cfg.top_n]}
        prices: dict[str, float] = {}
        if need:
            import yfinance as yf
            data = yf.download(sorted(need), period="1d", auto_adjust=True, progress=False, threads=True)
            closes = data.get("Close") if hasattr(data, "get") else None
            if closes is not None:
                if hasattr(closes, "columns"):
                    for sym in closes.columns:
                        try:
                            prices[str(sym).upper()] = float(closes[sym].dropna().iloc[-1])
                        except Exception:
                            pass
                elif len(need) == 1:
                    try:
                        prices[next(iter(need))] = float(closes.dropna().iloc[-1])
                    except Exception:
                        pass
        filled = so.update_forward_returns(rows, prices, now, cfg)
        rows = so.record_scan(rows, ranked, breakdown, prices, now, cfg)
        rows = so.trim_rows(rows, now, cfg)
        so.save_rows(OUTCOMES_FILE, rows)
        weights = so.source_weights(rows, _closed_trades_for_weights(), cfg)
        if weights:
            so.save_weights(SOURCE_WEIGHTS_FILE, weights, now, so.compute_source_stats(rows, None, cfg))
        moved = {k: v for k, v in weights.items() if abs(v - 1.0) >= 0.05}
        log.info("Signal outcomes: %d rows, %d horizons filled, weights(moved)=%s",
                 len(rows), filled, moved or "none-yet")
    except Exception as e:
        log.warning("Signal outcome update failed (non-fatal): %s", e)

# Portfolio brain caps
PORTFOLIO_MAX_PER_THEME = 3
PORTFOLIO_MAX_SPECULATIVE = 8


def _portfolio_max_positions() -> int:
    """Default portfolio capacity (env THEMATIC_MAX_POSITIONS, clamped 10–20)."""
    try:
        return max(10, min(20, int(float(os.getenv("THEMATIC_MAX_POSITIONS", "") or 10))))
    except Exception:
        return 10


# Back-compat alias for legacy references (now config-driven, default 10 not 15).
PORTFOLIO_MAX_POSITIONS = _portfolio_max_positions()


def _thematic_primary_email() -> str:
    """The account whose real portfolio the thematic policy reconciles against in
    the global 4h scan. Prefers an explicit override, else the admin who has live
    Fidelity routing armed (thematic trades land there), else the bootstrap admin.
    Empty string ⇒ thematic-book-only (no broker read)."""
    e = os.getenv("THEMATIC_PRIMARY_EMAIL", "").strip().lower()
    if e:
        return e
    try:
        from web import users as _u
        for rec in _u.list_users():
            if rec.get("role") == "admin" and _u.get_thematic_hil(rec).get("fidelity_trade"):
                return str(rec.get("email", "")).strip().lower()
    except Exception:
        pass
    boot = [x.strip().lower() for x in os.getenv("CF_ACCESS_BOOTSTRAP_ADMIN", "").split(",") if x.strip()]
    return boot[0] if boot else ""

# Minimum raw buzz score for a signal to be considered a real buy candidate.
# Raw scores are unbounded (NVDA at 100 Reddit mentions alone = 200pts).
# Typical qualifying signal: 40-150. Strong signal: 150+. Very strong: 300+.
# Raised 40→48 (2026-06-19): require more social confirmation before a name is
# tradeable — a single-source 40-pt blip no longer clears the buy gate, cutting
# false positives from thin one-off mentions.
MIN_SIGNAL_SCORE: float = 48.0

# Minimum COMPOSITE (0-100) score for a freshly-scanned signal to be ADMITTED to the
# pending queue. Distinct from MIN_SIGNAL_SCORE above (which floors the unbounded *raw
# buzz* for the manual-approve and breakout-fallback gates). The admit decision is made
# on the composite — conviction + buzz + sentiment — so this is the real buy gate.
# Env-tunable: raise toward 72-75 for a more selective (fewer, higher-quality) book.
MIN_COMPOSITE_SCORE: float = float(os.getenv("THEMATIC_MIN_SIGNAL_SCORE", "70") or 70)

# Buzz decay threshold: if current scan score drops to < this fraction of
# the score at entry, trigger a buzz_decay exit (even if stop/target not hit).
BUZZ_DECAY_RATIO: float = 0.40


def _signal_ttl_hours() -> float:
    """How long a pending signal survives across scans before it expires —
    instead of being wiped+rebuilt every scan (which made hot names flip-flop
    in/out and re-page). Env THEMATIC_SIGNAL_TTL_HOURS, default 24h, 0 = no TTL."""
    try:
        return max(0.0, float(os.getenv("THEMATIC_SIGNAL_TTL_HOURS", "24") or 24))
    except (TypeError, ValueError):
        return 24.0


def _signal_ts_epoch(s: dict) -> float:
    """Best-effort creation epoch for a signal (ISO ``ts``, else id suffix)."""
    ts = s.get("ts")
    if ts:
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(str(ts)).timestamp()
        except Exception:
            pass
    sid = str(s.get("id", ""))
    if "_" in sid:
        try:
            return float(sid.rsplit("_", 1)[1])
        except (TypeError, ValueError):
            pass
    return 0.0


def _buzz_tier(score: float) -> str:
    """Human-readable tier for an unbounded raw buzz score."""
    if score >= 300: return "🔥 Very Strong"
    if score >= 150: return "Strong"
    if score >= 80:  return "Moderate"
    if score >= 40:  return "Weak"
    return "Low"


def _conviction_clamp_enabled() -> bool:
    return os.getenv("THEMATIC_CONVICTION_CLAMP", "true").strip().lower() in ("1", "true", "yes", "on")


def conviction_ceiling(confirmed: bool, quality_sources: int, insider_and_social: bool) -> int:
    """Deterministic evidence ladder for LLM conviction — pure.

    Conviction is 75% of the composite score and comes from a FREE 3b/70b model
    that will happily say 9/10 off one hot Reddit thread. High conviction must be
    EARNED by corroboration the model can't hallucinate:

      ≤7  — always allowed (the model's ordinary working range)
       8  — needs scan confirmation (seen ≥2 scans) OR ≥2 quality sources
       9  — needs confirmation AND ≥2 quality sources
      10  — needs confirmation AND (≥3 quality sources OR insider+social combo)
    """
    if confirmed and (quality_sources >= 3 or insider_and_social):
        return 10
    if confirmed and quality_sources >= 2:
        return 9
    if confirmed or quality_sources >= 2:
        return 8
    return 7


def clamp_conviction(conviction: int, source_breakdown: dict, confirmed: bool) -> int:
    """Apply conviction_ceiling using a ticker's per-source breakdown."""
    c = max(1, min(10, int(conviction or 0)))
    srcs = {k for k, v in (source_breakdown or {}).items()
            if isinstance(v, (int, float)) and v > 0}
    quality = len(srcs & _QUALITY_SOURCES)
    insider_and_social = "insider" in srcs and bool(srcs & {"reddit", "trusted_twitter", "twitter"})
    return min(c, conviction_ceiling(bool(confirmed), quality, insider_and_social))


def composite_score(conviction: int, raw_score: float, sentiment: float = 0.0) -> int:
    """ONE 0-100 signal score (replaces the dual 'conviction X/10 · buzz Y pts').

    Conviction (1-10, the analyst's considered call factoring news/insider/buzz)
    is the backbone and contributes up to 75; live social-momentum strength
    (unbounded raw buzz) adds up to 28 via a gently-saturating curve so genuine
    multi-source buzz differentiates names without burying a weak thesis.

    ``sentiment`` ∈ [-1, +1] is crowd POLARITY (are people bullish or saying
    "sell/dump/crash"?). It scales the score ±25%: a heavily-shorted, "everyone's
    bearish" name scores LOW even with huge buzz, and deep-bearish (< -0.5) is hard
    capped so it can never clear an auto-trade gate. Range 0-100.
    """
    c = max(1, min(10, int(conviction or 0)))
    rs = max(0.0, float(raw_score or 0.0))
    s = max(-1.0, min(1.0, float(sentiment or 0.0)))
    base = c * 7.5                                   # conv10 → 75 (was 85 — conviction was ~85% of score, burying buzz)
    buzz_pts = 28.0 * (rs / (rs + 55.0))             # rs55→14, 165→21, →28 asymptote — buzz now materially differentiates
    sent_mult = 1.0 + 0.25 * s                       # -1 → 0.75×, 0 → 1.0×, +1 → 1.25×
    score = (base + buzz_pts) * sent_mult
    if s <= -0.5:                                    # crowd says sell → never auto-tradeable
        score = min(score, 45.0)
    return int(round(max(0.0, min(100.0, score))))


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename (crash-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_signals() -> dict:
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text())
        except Exception:
            backup = SIGNALS_FILE.with_suffix(".json.bak")
            if backup.exists():
                try:
                    return json.loads(backup.read_text())
                except Exception:
                    pass
    return {"signals": [], "last_scan": None}


def _save_signals(data: dict) -> None:
    if SIGNALS_FILE.exists():
        try:
            import shutil
            shutil.copy2(SIGNALS_FILE, SIGNALS_FILE.with_suffix(".json.bak"))
        except Exception:
            pass
    _atomic_write(SIGNALS_FILE, data)


def _set_status(status: str, detail: str = "") -> None:
    _atomic_write(STATUS_FILE, {
        "status": status, "detail": detail, "ts": time.time()
    })


# ── Per-source health tracking ──────────────────────────────────────────────
# Prevents the "scanner silently dead for 17 days" failure: each scan records
# every source's outcome (ok/empty/error/timeout, ticker count, last success,
# consecutive failures) so a quietly-broken feed is visible in /status, not
# hidden behind a stale cache.
_SOURCE_HEALTH_FILE = TMP / "thematic_source_health.json"
_SOURCE_DEAD_FAILS = 3            # consecutive failures before a source is "dead"
_SOURCE_STALE_HOURS = 24.0       # no successful pull in this long while attempted → stale


def _record_source_health(gather_results: list, names: list) -> None:
    """Fold one scan's per-source results into the persistent health file."""
    now = time.time()
    try:
        data = json.loads(_SOURCE_HEALTH_FILE.read_text())
    except Exception:
        data = {}
    for i, name in enumerate(names):
        if i >= len(gather_results):
            continue
        r = gather_results[i]
        rec = data.get(name, {}) or {}
        rec["last_attempt"] = now
        fails = int(rec.get("consecutive_failures", 0))
        if isinstance(r, asyncio.TimeoutError):
            rec.update(status="timeout", consecutive_failures=fails + 1)
        elif isinstance(r, Exception):
            rec.update(status="error", consecutive_failures=fails + 1, last_error=str(r)[:140])
        else:
            try:
                n = len(r)
            except Exception:
                n = 0
            rec["last_count"] = n
            if n > 0:                       # real data → healthy
                rec.update(status="ok", last_success=now, consecutive_failures=0)
            else:                           # returned but empty — not a hard fail, but visible
                rec["status"] = "empty"
        data[name] = rec
    try:
        _atomic_write(_SOURCE_HEALTH_FILE, data)
    except Exception as e:
        log.debug("source health write: %s", e)


def _source_health() -> dict:
    """Read the per-source health map (or {})."""
    try:
        return json.loads(_SOURCE_HEALTH_FILE.read_text())
    except Exception:
        return {}


def _source_health_summary() -> dict:
    """Compact health view for /status: per-source state + a list of DEAD/STALE
    sources (consecutive failures ≥ threshold, or no success in STALE_HOURS while
    still being attempted)."""
    now = time.time()
    health = _source_health()
    sources: dict[str, dict] = {}
    dead: list[str] = []
    for name, rec in health.items():
        fails = int(rec.get("consecutive_failures", 0))
        last_ok = rec.get("last_success")
        ok_age_h = round((now - last_ok) / 3600.0, 1) if last_ok else None
        # DEAD = repeatedly erroring/timing out, OR it used to work and has gone
        # stale (succeeded before but no data in STALE_HOURS). A source that simply
        # returns empty and never errored is "no-data", NOT dead (insider/congress/
        # options-flow are legitimately empty on quiet days).
        is_dead = fails >= _SOURCE_DEAD_FAILS or (
            last_ok is not None and (now - last_ok) > _SOURCE_STALE_HOURS * 3600.0
        )
        entry = {
            "status": rec.get("status", "unknown"),
            "last_count": rec.get("last_count", 0),
            "consecutive_failures": fails,
            "last_success_age_hours": ok_age_h,
            "dead": is_dead,
        }
        if rec.get("last_error"):
            entry["last_error"] = rec["last_error"]
        sources[name] = entry
        if is_dead:
            dead.append(name)
    return {
        "sources": sources,
        "dead_sources": sorted(dead),
        "healthy_count": sum(1 for s in sources.values() if not s["dead"]),
        "total_sources": len(sources),
    }


def _scan_status_stale(status: dict, now: float | None = None) -> bool:
    """True if a 'running' status is older than the max plausible scan time —
    i.e. left over from a scan that crashed or was killed (process restart),
    which would otherwise block every future scan ('already running') forever.

    A non-running status is never stale. Threshold env THEMATIC_SCAN_STALE_SECONDS
    (default 300s); a healthy scan now self-bounds well under this via per-source
    timeouts, so >5min 'running' reliably means a dead scan.
    """
    if (status or {}).get("status") != "running":
        return False
    try:
        age = (now if now is not None else time.time()) - float(status.get("ts", 0) or 0)
    except Exception:
        return True
    try:
        stale_after = float(os.getenv("THEMATIC_SCAN_STALE_SECONDS", "300") or 300)
    except Exception:
        stale_after = 300.0
    return age >= stale_after


def _get_historical_scores(n_scans: int = 5) -> dict[str, float]:
    """Return {ticker: decayed_avg_score} across last n_scans scan history.

    Uses exponential decay so recent scans weight more than old ones.
    A ticker that scored 200pts last scan gets a meaningful bonus even if
    it's quiet today — giving the AI more context beyond the current moment.
    """
    if not SCORE_HISTORY_FILE.exists():
        return {}
    try:
        lines = SCORE_HISTORY_FILE.read_text().splitlines()
        recent_lines = [l for l in lines if l.strip()][-n_scans:]
        if not recent_lines:
            return {}

        # Decay weights: most recent = 1.0, oldest = 0.2
        n = len(recent_lines)
        weights = [0.2 + 0.8 * (i / max(n - 1, 1)) for i in range(n)]

        ticker_scores: dict[str, list[float]] = {}
        for weight, line in zip(weights, recent_lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue  # torn / non-JSON line → skip
            for entry in rec.get("ranked", []) or []:
                try:
                    sym, raw_s = entry[0], entry[1]
                    val = float(raw_s) * weight
                except (TypeError, ValueError, IndexError, KeyError):
                    continue  # skip the bad ENTRY, keep good ones on this line
                if sym:
                    ticker_scores.setdefault(str(sym), []).append(val)

        # Weighted average, normalized so max historical score ≈ 30pt bonus cap
        if not ticker_scores:
            return {}
        raw = {t: sum(scores) / n for t, scores in ticker_scores.items()}
        max_raw = max(raw.values()) if raw else 0.0
        # All-zero history (every ranked score 0) → no bonus, and avoid a
        # divide-by-zero rather than relying on the outer except to mask it.
        if max_raw <= 0:
            return {}
        # Scale so the strongest historically-trending ticker gets ~30pts bonus
        return {t: round(v / max_raw * 30.0, 1) for t, v in raw.items()}
    except Exception:
        return {}


def _brain_held_tickers(tmp_dir: Path | None = None) -> set[str]:
    """Tickers the Holdings-Brain holds or is managing (store + pending proposals),
    read from cheap JSON files (no broker scrape). The thematic scanner must never
    propose BUYING these — otherwise it buys what the brain is trimming/exiting."""
    tmp_dir = tmp_dir or TMP
    held: set[str] = set()
    try:
        for bf in tmp_dir.glob("holdings_brain_*.json"):
            try:
                bd = json.loads(bf.read_text())
            except Exception:
                continue
            if isinstance(bd, dict) and "proposals" in bd:
                for p in bd.get("proposals", []):
                    t = str(p.get("ticker", "")).upper().strip()
                    if t:
                        held.add(t)
            elif isinstance(bd, dict):  # managed store: ticker → plan
                for t, plan in bd.items():
                    if isinstance(plan, dict) and str(plan.get("status")) in ("managed", "adopted"):
                        held.add(str(t).upper().strip())
    except Exception as e:
        log.warning("_brain_held_tickers read failed: %s", e)
    return held


def _get_scan_consistency(n_scans: int = 5) -> dict[str, dict]:
    """Return {ticker: {appearances, avg_score, peak_score, is_spike}} across last n_scans.

    is_spike = True when ticker appeared in only 1 of the last N scans.
    Spikes are noise — single Twitter post, one article — not real momentum.
    Confirmed signals appear in 2+ scans.
    """
    if not SCORE_HISTORY_FILE.exists():
        return {}
    try:
        lines = [l for l in SCORE_HISTORY_FILE.read_text().splitlines() if l.strip()]
        recent = lines[-n_scans:]
        if not recent:
            return {}

        appearances: dict[str, int] = {}
        scores_by_ticker: dict[str, list[float]] = {}

        for line in recent:
            try:
                rec = json.loads(line)
                for t, s in rec.get("ranked", []):
                    appearances[t] = appearances.get(t, 0) + 1
                    scores_by_ticker.setdefault(t, []).append(float(s))
            except Exception:
                continue

        result = {}
        for t, count in appearances.items():
            scores = scores_by_ticker.get(t, [])
            result[t] = {
                "appearances": count,
                "avg_score":   round(sum(scores) / len(scores), 1) if scores else 0,
                "peak_score":  round(max(scores), 1) if scores else 0,
                "is_spike":    count == 1,          # only appeared once = unconfirmed spike
                "confirmed":   count >= 2,           # appeared in 2+ scans = real trend
            }
        return result
    except Exception:
        return {}


def _get_latest_scan_scores() -> dict[str, float]:
    """Return {ticker: raw_score} from the most recent scan, or {} if no history."""
    if not SCORE_HISTORY_FILE.exists():
        return {}
    try:
        lines = SCORE_HISTORY_FILE.read_text().splitlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
                return {t: s for t, s in rec.get("ranked", [])}
            except Exception:
                continue
    except Exception:
        pass
    return {}


EXIT_LOG_FILE = TMP / "thematic_exit_log.jsonl"


def _in_sms_quiet_hours(now: "datetime.datetime | None" = None) -> bool:
    """True when local time is inside the thematic-SMS quiet window — so dense
    overnight scans don't fire trade-request texts at 2-5am. Signals still
    accumulate as pending; texts resume after the window.

    Env ``THEMATIC_SMS_QUIET_HOURS='START-END'`` (24h local, wraps midnight),
    default ``'22-8'`` (10pm-8am). Empty string disables (always text).
    """
    import datetime as _dt
    spec = os.getenv("THEMATIC_SMS_QUIET_HOURS", "22-8").strip()
    if not spec:
        return False
    try:
        a, b = spec.split("-")
        start, end = int(a), int(b)
    except Exception:
        return False
    if start == end:
        return False
    h = (now or _dt.datetime.now()).hour
    if start < end:
        return start <= h < end
    return h >= start or h < end          # window wraps midnight


async def _notify_thematic_hil_pending(count: int) -> None:
    """Send SMS to all users who have thematic HIL + sms_notify enabled."""
    if _in_sms_quiet_hours():
        log.info("Thematic pending-SMS suppressed (quiet hours): %d pending", count)
        return
    try:
        from web import users as user_store
        from scripts.sms_alerts import send_sms
        dashboard_url = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org").rstrip("/")
        msg = (
            f"🔔 Agentic Trader\n"
            f"{count} new thematic signal{'s' if count != 1 else ''} awaiting approval\n\n"
            f"Review 👉 {dashboard_url}/app/hil?tab=approvals"
        )
        all_users = user_store.list_users() if hasattr(user_store, "list_users") else []
        for rec in all_users:
            thil = user_store.get_thematic_hil(rec)
            if not thil.get("enabled") or not thil.get("sms_notify"):
                continue
            phone = (rec.get("phone_number") or os.getenv("PAPER_SMS_NUMBER", "")).strip()
            if not phone:
                continue
            # Cooldown so the "N pending" nudge doesn't repeat every scan/burst.
            from web import alert_cooldown
            email = rec.get("email", "")
            if not alert_cooldown.should_alert(f"thematic_count:{email}", "PENDING", score=float(count)):
                continue
            try:
                await asyncio.to_thread(send_sms, phone, msg)
                alert_cooldown.record_alert(f"thematic_count:{email}", "PENDING", score=float(count))
                log.info("Thematic HIL SMS sent to %s", email or "?")
            except Exception as sms_err:
                log.warning("Thematic HIL SMS failed for %s: %s", email or "?", sms_err)
    except Exception as e:
        log.warning("_notify_thematic_hil_pending: %s", e)


async def _check_thematic_exits(execute: bool = False) -> list[dict]:
    """
    Scan open thematic paper positions for exit conditions.

    Conditions checked:
      stop_hit      — current price ≤ stop
      target_hit    — current price ≥ target
      max_hold      — scans_held ≥ hold_days (or age in days ≥ hold_days)
      buzz_collapse — ticker absent from latest scan AND held > 2 days

    Returns list of exit dicts. If execute=True, removes position from
    PAPER_STATE_FILE and logs to EXIT_LOG_FILE (atomic write).
    """
    import datetime as _dt
    exits: list[dict] = []
    if not PAPER_STATE_FILE.exists():
        return exits

    try:
        state = json.loads(PAPER_STATE_FILE.read_text())
    except Exception as e:
        log.warning("Exit check: cannot read state: %s", e)
        return exits

    positions: dict = state.get("positions", {})
    thematic_tickers = [
        t for t, p in positions.items()
        if p.get("_source", "").startswith("thematic")
    ]
    if not thematic_tickers:
        return exits

    # Fetch current prices in executor
    loop = asyncio.get_running_loop()
    def _fetch_prices_sync(tickers: list[str]) -> dict[str, float]:
        try:
            import yfinance as yf
            data = yf.download(tickers, period="1d", auto_adjust=True, progress=False)
            prices: dict[str, float] = {}
            closes = data.get("Close") if hasattr(data, "get") else None
            if closes is None:
                return prices
            if hasattr(closes, "columns"):
                for sym in closes.columns:
                    try:
                        prices[sym.upper()] = float(closes[sym].dropna().iloc[-1])
                    except Exception:
                        pass
            else:
                if tickers:
                    try:
                        prices[tickers[0].upper()] = float(closes.dropna().iloc[-1])
                    except Exception:
                        pass
            return prices
        except Exception as e:
            log.warning("Exit check price fetch: %s", e)
            return {}

    current_prices = await loop.run_in_executor(None, _fetch_prices_sync, thematic_tickers)
    latest_scores  = _get_latest_scan_scores()
    now_ts         = _dt.datetime.now().isoformat(timespec="seconds")

    modified = False
    for ticker in thematic_tickers:
        pos   = positions[ticker]
        price = current_prices.get(ticker)
        if not price:
            continue

        stop       = float(pos.get("stop", 0) or 0)
        target     = float(pos.get("target", 999999) or 999999)
        hold_days  = int(pos.get("hold_days", 5) or 5)
        scans_held = int(pos.get("scans_held", 0) or 0)
        entry_time = pos.get("entry_time", "")

        # Age in calendar days
        age_days = 0
        if entry_time:
            try:
                entered = _dt.datetime.fromisoformat(entry_time)
                age_days = ((_dt.datetime.now() - entered).total_seconds() / 86400)
            except Exception:
                pass

        # ── Let winners run: at the target, DON'T sell — switch to a trailing stop
        # so the position can capture the +50-200% moves these plays actually make,
        # while still locking in the gain if it rolls over. ──────────────────────
        trail_pct = float(pos.get("trail_pct", 20) or 20)
        if not pos.get("trailing") and target < 999999 and price >= target:
            pos["trailing"] = True
            pos["peak_price"] = price
            modified = True
            log.info("%s hit target %.2f at %.2f — trailing %.0f%% to let it run",
                     ticker, target, price, trail_pct)
        if pos.get("trailing"):
            peak = max(float(pos.get("peak_price", price) or price), price)
            if peak != pos.get("peak_price"):
                pos["peak_price"] = peak
                modified = True
            trail_stop = peak * (1 - trail_pct / 100.0)

        reason = None
        if pos.get("trailing"):
            # Once trailing, the ONLY exit is the trailing stop (or a hard stop
            # breach) — max-hold and buzz no longer force a sale on a live runner.
            if stop > 0 and price <= stop:
                reason = "stop_hit"
            elif price <= peak * (1 - trail_pct / 100.0):
                reason = "trailing_stop"
        elif stop > 0 and price <= stop:
            reason = "stop_hit"
        elif age_days >= hold_days:
            # Don't market-dump a still-running winner at max-hold. If it's
            # meaningfully green, convert to a trailing stop (let the right tail
            # run — the strategy targets 50-200% moves) instead of a hard timed
            # exit. Only flat/losing positions take the timed exit. Threshold env
            # THEMATIC_MAXHOLD_RUN_PCT (default 12%).
            _ep = float(pos.get("entry_price", 0) or 0)
            try:
                _run_buf = 1.0 + max(0.0, float(os.getenv("THEMATIC_MAXHOLD_RUN_PCT", "12"))) / 100.0
            except ValueError:
                _run_buf = 1.12
            if _ep > 0 and price > _ep * _run_buf:
                if not pos.get("trailing"):
                    pos["trailing"] = True
                    pos["peak_price"] = max(price, float(pos.get("peak_price", price) or price))
                    modified = True
                    log.info("%s past max-hold but +%.0f%% — trailing instead of timed exit",
                             ticker, (price / _ep - 1) * 100)
                # held as a runner; no timed exit this cycle
            else:
                reason = "max_hold_exceeded"
        else:
            # Buzz-based exits require PRICE confirmation. Social buzz fading is not
            # a price signal, and the entry is often peak buzz (so next-day decay is
            # normal). Don't liquidate a position that is meaningfully GREEN just
            # because attention pulled back or a flaky scraper didn't re-surface it
            # — its stop/target/trailing manage the downside. Only buzz-exit a flat/
            # red name (env THEMATIC_BUZZ_EXIT_GREEN_PCT, default 3% above entry).
            _entry_px = float(pos.get("entry_price", 0) or 0)
            try:
                _green_buf = 1.0 + max(0.0, float(os.getenv("THEMATIC_BUZZ_EXIT_GREEN_PCT", "3"))) / 100.0
            except ValueError:
                _green_buf = 1.03
            _is_green = _entry_px > 0 and price > _entry_px * _green_buf
            if not _is_green:
                if age_days >= 2 and latest_scores and ticker not in latest_scores:
                    reason = "buzz_collapse"
                elif age_days >= 1 and latest_scores:
                    entry_raw = float(pos.get("entry_raw_score", 0) or 0)
                    current_raw = latest_scores.get(ticker, 0)
                    if entry_raw > 0 and current_raw < entry_raw * BUZZ_DECAY_RATIO:
                        reason = "buzz_decay"
                # #4 AI gate: a buzz exit is about FADING ATTENTION. If the AI reads
                # the headlines and finds no materially-bad news, it's just attention
                # cooling — hold (its stop/target still guard downside). Bad news →
                # let the exit stand. Only touches buzz exits, never price exits.
                if reason in ("buzz_collapse", "buzz_decay"):
                    try:
                        bad, why = await _ai_exit_news_check(ticker)
                        if not bad:
                            log.info("%s %s cancelled — AI: attention fade, no bad news (%s)",
                                     ticker, reason, why)
                            reason = None
                    except Exception as _ee:
                        log.debug("AI exit check skipped for %s: %s", ticker, _ee)

        if reason:
            pnl_pct = round((price - float(pos.get("entry_price", price))) / float(pos.get("entry_price", price)) * 100, 2) if pos.get("entry_price") else 0
            exit_rec = {
                "ticker":     ticker,
                "reason":     reason,
                "price":      price,
                "entry":      pos.get("entry_price"),
                "stop":       stop,
                "target":     target,
                "pnl_pct":    pnl_pct,
                "age_days":   round(age_days, 1),
                "hold_days":  hold_days,
                "ts":         now_ts,
                "executed":   False,
                # entry-time source attribution → adaptive source weights
                "sources":    pos.get("sources") or {},
            }
            if execute:
                shares = pos.get("shares", 0)
                proceeds = round(price * shares, 2) if shares else 0
                cur_cash = float(state.get("cash", 0))
                # settled_cash must fall back to cash, NOT $0 — $0 would corrupt the account
                cur_settled = float(state.get("settled_cash", cur_cash))
                state["cash"]         = round(cur_cash + proceeds, 4)
                state["settled_cash"] = round(cur_settled + proceeds, 4)
                del positions[ticker]
                exit_rec["executed"] = True
                exit_rec["proceeds"] = proceeds
                modified = True
                log.info("Thematic exit EXECUTED: %s reason=%s pnl=%.1f%%", ticker, reason, pnl_pct)
            else:
                log.info("Thematic exit RECOMMENDED: %s reason=%s pnl=%.1f%%", ticker, reason, pnl_pct)
            exits.append(exit_rec)

    if modified and execute:
        state["positions"] = positions
        # A1: append closed trades to state["trades"] so the daily-loss circuit breaker
        # can read them. Without this, realized_today was always 0 (closed_today was never written).
        state.setdefault("trades", [])
        for ex in exits:
            if ex.get("executed"):
                state["trades"].append({
                    "ticker": ex["ticker"],
                    "pnl": round((ex.get("pnl_pct", 0) / 100.0) * (ex.get("proceeds", 0)), 2),
                    "pnl_pct": ex.get("pnl_pct", 0) / 100.0,
                    "exit_time": ex.get("ts", ""),
                    "exit_reason": ex.get("reason", ""),
                    "_source": "thematic",
                    "sources": ex.get("sources") or {},
                })
        # Keep trades list bounded (last 200 entries)
        state["trades"] = state["trades"][-200:]
        _atomic_write(PAPER_STATE_FILE, state)
        # Log exits
        EXIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EXIT_LOG_FILE.open("a") as f:
            for ex in exits:
                f.write(json.dumps(ex) + "\n")

    return exits


def _append_score_history(scan_ts: str, ranked: list[tuple[str, float]], breakdown: dict[str, dict[str, float]]) -> None:
    """Append one scan's ranked scores to rolling history (capped at SCORE_HISTORY_MAX lines)."""
    try:
        TMP.mkdir(parents=True, exist_ok=True)
        # Round scores to 1 decimal to avoid float precision noise (2.5999... → 2.6)
        clean_breakdown = {
            t: {k: round(v, 1) for k, v in breakdown.get(t, {}).items()}
            for t, _ in ranked[:25]
        }
        clean_ranked = [(t, round(s, 1)) for t, s in ranked[:25]]
        record = json.dumps({"ts": scan_ts, "ranked": clean_ranked, "breakdown": clean_breakdown})
        lines: list[str] = []
        if SCORE_HISTORY_FILE.exists():
            try:
                lines = SCORE_HISTORY_FILE.read_text().splitlines()
            except Exception:
                lines = []
        lines.append(record)
        if len(lines) > _SCORE_HISTORY_MAX:
            lines = lines[-_SCORE_HISTORY_MAX:]
        content = "\n".join(lines) + "\n"
        fd, tmp_path = tempfile.mkstemp(dir=SCORE_HISTORY_FILE.parent, prefix=".tmp_sh_")
        try:
            with os.fdopen(fd, "w") as wf: wf.write(content)
            os.replace(tmp_path, SCORE_HISTORY_FILE)
        except Exception:
            try: os.unlink(tmp_path)
            except Exception: pass
            raise
    except Exception as e:
        log.warning("Score history write: %s", e)


def _sig_score(sig: dict) -> float:
    """Unified 0-100 score for a signal (recompute if not stamped)."""
    s = sig.get("score")
    if s is not None:
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
    return float(composite_score(sig.get("conviction", 7),
                                 float(sig.get("raw_score", 0) or 0),
                                 float(sig.get("sentiment", 0) or 0)))


def _fmt_money_compact(n: float) -> str:
    """$1.2k / $3.4M style for SMS."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            return f"${n / div:.1f}{suf}"
    return f"${n:.0f}"


async def _notify_thematic_trade_request(email: str, items: list, *, _single=None) -> int:
    """Text ONE clean digest covering all fresh trade-requests for a user.

    ``items`` = list of (sig, score). Per-ticker cooldown is applied here, so only
    materially-new names are included; if none survive, nothing is sent. Returns
    the number of tickers actually paged about. A single-name page also attaches a
    chart; multi-name digests omit images (the approve screen has per-name charts).
    """
    try:
        from web import users as user_store
        from scripts.sms_alerts import send_sms
        from web import alert_cooldown

        rec = user_store.get_user(email) or {}
        if user_store.get_thematic_hil(rec).get("sms_notify") is False:
            return 0
        if _in_sms_quiet_hours():
            log.info("Thematic trade-request SMS suppressed (quiet hours): %s", email[:20])
            return 0
        phone = (rec.get("phone_number") or os.getenv("PAPER_SMS_NUMBER", "")).strip()
        if not phone:
            return 0

        # Cooldown filter — keep only tickers not recently paged (or whose score
        # moved >= ALERT_RESCORE_DELTA). Highest score first.
        fresh = []
        for sig, score in items:
            tk = str(sig.get("ticker", ""))
            if tk and alert_cooldown.should_alert(f"thematic:{email}", tk, score=score, kind="BUY"):
                fresh.append((sig, float(score)))
        if not fresh:
            log.info("Thematic trade-request SMS suppressed (all on cooldown): %s", email[:20])
            return 0
        fresh.sort(key=lambda x: x[1], reverse=True)

        base = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org").rstrip("/")
        link = f"{base}/app/hil?tab=approvals"

        if len(fresh) == 1:
            sig, score = fresh[0]
            tk = str(sig.get("ticker", ""))
            crowd = (sig.get("crowd_view") or "").strip()
            lines = [
                "📈 Agentic Trader — trade request",
                "",
                f"{tk} · {score:.0f}/100",
                f"Target +{sig.get('target_pct', '?')}%  ·  Stop -{sig.get('stop_pct', '?')}%",
            ]
            if crowd:
                lines.append(f"💬 {crowd[:100]}")
            lines += ["", f"Approve 👉 {link}"]
            msg = "\n".join(lines)
            media_url = None
            try:
                media_url = await _generate_signal_chart(tk, sig)
            except Exception as ce:
                log.debug("trade-request chart skipped: %s", ce)
            await asyncio.to_thread(send_sms, phone, msg, None, media_url)
        else:
            rows = [
                f"• {str(s.get('ticker','')):<5} {sc:>3.0f}/100  +{s.get('target_pct','?')}%"
                for s, sc in fresh
            ]
            top_tk = str(fresh[0][0].get("ticker", ""))
            # One SMS carries one image → attach the TOP name's chart; the rest
            # have per-name charts on the approve screen.
            media_url = None
            try:
                media_url = await _generate_signal_chart(top_tk, fresh[0][0])
            except Exception as ce:
                log.debug("digest chart skipped: %s", ce)
            msg = "\n".join([
                f"📈 Agentic Trader — {len(fresh)} trade requests",
                "",
                *rows,
                "",
                (f"📊 {top_tk} chart below · charts for the rest on the approve screen"
                 if media_url else "Charts on the approve screen"),
                f"Review & approve 👉 {link}",
            ])
            await asyncio.to_thread(send_sms, phone, msg, None, media_url)

        for sig, score in fresh:
            alert_cooldown.record_alert(f"thematic:{email}", str(sig.get("ticker", "")), score=score, kind="BUY")
        log.info("Thematic trade-request digest sent to %s (%d tickers)", email[:20], len(fresh))
        return len(fresh)
    except Exception as e:
        log.warning("_notify_thematic_trade_request failed: %s", e)
        return 0


async def _auto_execute_confirmed_signals(signals: list[dict]) -> None:
    """Per user: auto-trade confirmed signals at/above their auto_trade_score.

    Paper leg auto-executes when auto_trade_paper is on. The live leg never
    auto-fires (compliance/step-up) — instead a per-signal trade-request SMS with
    an approve link is sent so the user one-taps it through. Both gated on the
    unified 0-100 composite score (sentiment-adjusted), not raw buzz.
    """
    try:
        from web import users as user_store
        all_users = user_store.list_users() if hasattr(user_store, "list_users") else []
        for rec in all_users:
            hil = user_store.get_thematic_hil(rec)
            auto_paper = bool(hil.get("auto_trade_paper"))
            sms_on = hil.get("sms_notify") is not False
            if not (auto_paper or sms_on):
                continue
            email = (rec.get("email") or "").strip()
            if not email:
                continue
            threshold = float(hil.get("auto_trade_score", 75.0))
            user_mock = {"email": email}
            to_notify: list = []   # (sig, score) collected → ONE digest text per user
            for sig in signals:
                score = _sig_score(sig)
                if score < threshold:
                    continue
                confirmed = bool(sig.get("confirmed"))
                breakout_ok = False
                if not confirmed and sig.get("is_spike"):
                    raw_score = float(sig.get("raw_score", 0) or 0)
                    eff_min = MIN_SIGNAL_SCORE * _regime_threshold_multiplier()
                    if raw_score >= eff_min and _breakout_confirm_enabled():
                        try:
                            breakout_ok = _ticker_breakout(str(sig.get("ticker", "")))
                        except Exception:
                            breakout_ok = False
                        if breakout_ok:
                            sig["breakout_confirmed"] = True
                if not (confirmed or breakout_ok):
                    continue
                # Paper auto-execute (adaptive sizing happens inside approve_signal).
                if auto_paper:
                    try:
                        await approve_signal(sig["id"], ApproveBody(), None, user_mock)
                        log.info("Auto-trade paper: approved %s for %s (score %.0f ≥ %.0f)",
                                 sig["ticker"], email, score, threshold)
                    except HTTPException as he:
                        log.info("Auto-trade paper skip %s for %s: %s", sig["ticker"], email, he.detail)
                    except Exception as e:
                        log.warning("Auto-trade paper error %s for %s: %s", sig["ticker"], email, e)
                if sms_on:
                    to_notify.append((sig, score))
            # Live leg: ONE batched trade-request digest per user (cooldown-filtered
            # inside) instead of a separate text per ticker.
            if to_notify:
                await _notify_thematic_trade_request(email, to_notify)
    except Exception as e:
        log.warning("_auto_execute_confirmed_signals: %s", e)


def _scan_source_timeout() -> float:
    """Per-source hard cap (s) for scan scrapers so one hung/rate-limited source
    can't stall the whole scan. Env THEMATIC_SOURCE_TIMEOUT, default 25, floor 5."""
    try:
        return max(5.0, float(os.getenv("THEMATIC_SOURCE_TIMEOUT", "25") or 25))
    except Exception:
        return 25.0


# ── Google Trends source (trendspyg) ─────────────────────────────────────────
# Rising web-search interest is a leading ATTENTION signal — it often precedes
# the social-chatter spike. trendspyg is the maintained pytrends replacement
# (the original repo is archived). OFF by default (env THEMATIC_GOOGLE_TRENDS)
# because it is an unofficial scrape that can rate-limit; bounded + graceful so a
# block/timeout/missing-dep degrades to {} and never stalls or breaks a scan.

def _trends_momentum(series: "list[float]") -> int:
    """Convert a Google-Trends interest-over-time series (0-100 values, oldest →
    newest) into a small positive 'rising interest' weight, else 0.

    Pure + deterministic so the weighting is testable without scraping. Rejects
    noise (sub-floor interest) and only rewards a MEANINGFUL rise of the recent
    window over the earlier baseline, so steady/declining interest scores 0."""
    import math as _m
    vals = [float(v) for v in (series or [])
            if isinstance(v, (int, float)) and _m.isfinite(float(v))]
    if len(vals) < 4:
        return 0
    n = len(vals)
    recent = sum(vals[-2:]) / 2.0
    half = max(1, n // 2)
    baseline = sum(vals[:half]) / half
    if recent < 15:                         # below the attention noise floor
        return 0
    if baseline <= 0:                       # rose from no interest at all
        return max(1, min(int(recent / 12), 8))
    accel = recent / baseline
    if accel < 1.25:                        # not meaningfully accelerating
        return 0
    return max(1, min(int(round((accel - 1.0) * 5)), 8))


def _google_trends_enabled() -> bool:
    return os.getenv("THEMATIC_GOOGLE_TRENDS", "false").strip().lower() in ("1", "true", "yes", "on")


def _google_trends_watch_terms() -> "dict[str, str]":
    """{ticker: query} watchlist for Google-Trends scanning. Trends needs seed
    terms (it can't discover from nothing), so we watch a configurable set —
    env THEMATIC_TRENDS_TICKERS (comma list), else a small catalyst-theme set
    skewed to the explosive small/mid-cap names a buzz scanner enters late."""
    raw = [x.strip().upper() for x in os.getenv("THEMATIC_TRENDS_TICKERS", "").split(",") if x.strip()]
    tickers = raw or [
        "IREN", "CIFR", "WULF", "APLD", "OKLO", "SMR", "RGTI", "IONQ",
        "RCAT", "ONDS", "NVDA", "AMD", "AVGO", "MU", "VRT",
    ]
    return {t: f"{t} stock" for t in tickers}


def _fetch_google_trend_series(query: str) -> "list[float]":
    """Best-effort fetch of a Google-Trends interest series via trendspyg. Returns
    [] on any problem (missing dep / API drift / block) so callers degrade
    gracefully. Kept isolated + injectable so the rest is testable offline."""
    try:
        import trendspyg  # type: ignore
    except Exception:
        return []
    try:
        # trendspyg's surface has shifted across versions; try the documented
        # entry points and normalize whatever interest-over-time it returns.
        fn = getattr(trendspyg, "download_trends", None) or getattr(trendspyg, "get_trends", None)
        if fn is None:
            return []
        data = fn(query)
        # Accept list[number] | dict-like | DataFrame-like → flatten to floats.
        if hasattr(data, "values") and not isinstance(data, dict):
            try:
                return [float(x) for x in list(data.values.flatten())]  # DataFrame
            except Exception:
                pass
        if isinstance(data, dict):
            data = list(data.values())
        return [float(x) for x in data if isinstance(x, (int, float))]
    except Exception as e:
        log.debug("google_trends fetch failed for %s: %s", query, e)
        return []


async def _google_trends_tickers(client=None, *, fetch=None, terms=None) -> "dict[str, int]":
    """Source scraper: rising-search-interest tickers as {ticker: weight}. Each
    term fetched off-loop (trendspyg is sync) and bounded by the caller's
    per-source timeout. Disabled unless THEMATIC_GOOGLE_TRENDS is set."""
    if not _google_trends_enabled():
        return {}
    terms = terms if terms is not None else _google_trends_watch_terms()
    if not terms:
        return {}
    fetch = fetch or _fetch_google_trend_series
    out: dict[str, int] = {}
    for ticker, query in terms.items():
        try:
            series = await asyncio.to_thread(fetch, query)
            w = _trends_momentum(series or [])
            if w > 0:
                tk = _norm_ticker(ticker)
                if tk:
                    out[tk] = out.get(tk, 0) + w
        except Exception as e:
            log.debug("google_trends term %s: %s", ticker, e)
            continue
    return out


async def _run_scan() -> None:
    async with _scan_lock:
        has_marketaux = bool(os.getenv("MARKETAUX_API_TOKEN", "").strip())
        source_label = "Reddit · Brave · PR Releases · Finviz · Yahoo Movers · RSS News · Google News · SA · StockAnalysis · Twitter · Insider"
        if has_marketaux:
            source_label += " · Marketaux"
        _set_status("running", f"Scraping {source_label}...")
        try:
            _reset_social_intent()   # fresh per scan — many sources write it (reddit,
                                     # RSS-tweets, news); must reset ONCE before the gather.
            _st = _scan_source_timeout()
            def _b(coro):
                # Bound each source: a hung/rate-limited scraper times out to a
                # captured exception (return_exceptions=True → _safe default) instead
                # of stalling the whole gather forever.
                return asyncio.wait_for(coro, _st)
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                gather_results = await asyncio.gather(
                    _b(_reddit_tickers(client)),          # 0
                    _b(_ddg_tickers(client)),              # 1
                    _b(_yahoo_trending(client)),           # 2
                    _b(_google_news_tickers(client)),      # 3
                    _b(_seeking_alpha_tickers(client)),    # 4
                    _b(_stockanalysis_trending(client)),   # 5
                    _b(_marketaux_tickers(client)),        # 6
                    _b(_insider_tickers(client)),          # 7
                    _b(_trusted_twitter_tickers(client)),  # 8
                    _b(_stocktwits_trending(client)),      # 9
                    _b(_finviz_tickers(client)),           # 10
                    _b(_rss_tickers(client)),              # 11
                    _b(_alphavantage_movers(client)),      # 12
                    _b(_yahoo_movers(client)),             # 13
                    _b(_brave_tickers(client)),            # 14
                    _b(_google_trends_tickers(client)),    # 15
                    _b(_discovery_tickers()),              # 16
                    _b(_analyst_tickers()),                # 17
                    _b(_options_flow_tickers()),           # 18
                    return_exceptions=True,
                )

            def _safe(r, default):
                return r if not isinstance(r, Exception) else default

            reddit          = _safe(gather_results[0], {})
            ddg             = _safe(gather_results[1], {})
            yahoo           = _safe(gather_results[2], [])
            google_news     = _safe(gather_results[3], {})
            seeking_alpha   = _safe(gather_results[4], {})
            stockanalysis   = _safe(gather_results[5], [])
            marketaux_res   = _safe(gather_results[6], {})
            insider_res     = _safe(gather_results[7], {})
            trusted_twitter = _safe(gather_results[8], {})
            stocktwits_res  = _safe(gather_results[9], {})
            finviz_res      = _safe(gather_results[10], {})
            rss_res         = _safe(gather_results[11], {})
            av_res          = _safe(gather_results[12], {})
            yahoo_movers_res= _safe(gather_results[13], {})
            brave_res       = _safe(gather_results[14], {})
            google_trends_res = _safe(gather_results[15], {})
            discovery_res   = _safe(gather_results[16], {})
            analyst_res     = _safe(gather_results[17], {})
            options_flow_res= _safe(gather_results[18], {})
            twitter: dict[str, int] = {}

            _SOURCE_NAMES = ["reddit","ddg","yahoo","google_news","seeking_alpha",
                             "stockanalysis","marketaux","insider","trusted_twitter",
                             "stocktwits","finviz","rss_news","av_movers","yahoo_movers","brave",
                             "google_trends","discovery","analyst","options_flow"]
            for i, name in enumerate(_SOURCE_NAMES):
                if isinstance(gather_results[i], Exception):
                    log.warning("Source %s exception: %s", name, gather_results[i])
            # Record per-source health (ok/empty/error/timeout + counts) so a
            # silently-dead feed shows up in /status instead of staying hidden.
            _record_source_health(gather_results, _SOURCE_NAMES)
            _dead = _source_health_summary().get("dead_sources", [])
            if _dead:
                log.warning("Thematic source health: DEAD/STALE sources: %s", _dead)

            _set_status("running", "Ranking tickers...")
            ranked, source_breakdown = await _merge_signals(
                reddit, ddg, yahoo, twitter,
                google_news, seeking_alpha, stockanalysis, marketaux_res, insider_res,
                trusted_twitter, stocktwits_res, finviz_res, rss_res, av_res, yahoo_movers_res,
                brave_res, google_trends=google_trends_res, discovery=discovery_res,
                analyst=analyst_res, options_flow=options_flow_res,
            )
            if not ranked:
                _set_status("done", "No tickers found")
                return

            # Collect news blobs for AI context. DDGS is SYNCHRONOUS and can hang /
            # rate-limit; calling it directly on the event loop froze scans on
            # "Scraping..." indefinitely. Run it off-loop in a thread with a hard
            # timeout so it can never stall the scan.
            news_blobs: list[str] = []
            def _collect_news(rk: list) -> list[str]:
                out: list[str] = []
                try:
                    try:
                        from ddgs import DDGS
                    except ImportError:
                        from duckduckgo_search import DDGS
                    with DDGS() as ddg_inst:
                        for ticker, _ in rk[:10]:
                            try:
                                nr = list(ddg_inst.news(f"{ticker} stock news", max_results=3))
                                for item in nr:
                                    out.append(item.get("title", ""))
                            except Exception:
                                pass
                except Exception:
                    pass
                return out
            try:
                news_blobs = await asyncio.wait_for(
                    asyncio.to_thread(_collect_news, ranked), _scan_source_timeout()
                )
            except Exception:
                news_blobs = []

            _set_status("running", "AI analyzing picks...")
            raw_picks = await _ai_pick(ranked, news_blobs)

            # Validate + sanitize each AI pick
            import datetime as _dt
            picks: list[dict] = []
            for raw in raw_picks:
                validated = _validate_pick(raw)
                if validated:
                    picks.append(validated)

            # Append score history
            scan_ts = _dt.datetime.now().isoformat()
            _append_score_history(scan_ts, ranked, source_breakdown)

            # Outcome tracking + adaptive source weights (best-effort, bounded —
            # a slow yfinance batch must not stall the scan).
            if _adaptive_weights_enabled():
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(_update_signal_outcomes, ranked, source_breakdown),
                        timeout=60,
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    log.warning("Signal outcome update skipped: %s", e)

            # Scan consistency data for spike detection
            consistency = _get_scan_consistency(n_scans=5)

            # Get tickers already in paper state — skip re-proposing open positions
            _existing_portfolio: set[str] = set()
            if PAPER_STATE_FILE.exists():
                try:
                    _pstate = json.loads(PAPER_STATE_FILE.read_text())
                    _existing_portfolio.update(k.upper() for k in _pstate.get("positions", {}).keys())
                except Exception:
                    pass
            # Also scan all thematic portfolio files (all users)
            try:
                for _pfile in TMP.glob("thematic_portfolio_*.json"):
                    try:
                        _port = json.loads(_pfile.read_text())
                        _existing_portfolio.update(k.upper() for k in _port.get("positions", {}).keys())
                    except Exception:
                        pass
            except Exception:
                pass

            # CRITICAL: never propose BUYING a name the Holdings-Brain already holds
            # or is managing — otherwise the scan buys what the brain is trimming/
            # exiting (contradiction). Cheap file reads (no broker scrape), so this
            # holds even when the live positions scrape fails.
            _existing_portfolio |= _brain_held_tickers()

            # Carry forward still-valid pending signals instead of wiping them.
            # The old wipe gave every pending a 1-scan lifetime, so a persistently
            # hot name dropped out then re-appeared the next scan (flip-flop) and
            # re-paged the user. Now a pending survives until approved/skipped or it
            # ages past THEMATIC_SIGNAL_TTL_HOURS; its score is refreshed in place.
            data = _load_signals()
            now = time.time()
            score_dict = dict(ranked)
            ttl_sec = _signal_ttl_hours() * 3600.0
            carried_pending: list[dict] = []
            prior_history: list[dict] = []
            for s in data["signals"]:
                if s.get("status") != "pending":
                    prior_history.append(s)
                    continue
                if ttl_sec > 0 and (now - _signal_ts_epoch(s)) > ttl_sec:
                    s["status"] = "expired"
                    s["expired_at"] = now
                    prior_history.append(s)
                    continue
                t = s.get("ticker")
                if t in score_dict:   # refresh live score; keep notified_at/entry ctx
                    s["raw_score"] = score_dict.get(t, s.get("raw_score", 0))
                    _bs = _blended_sentiment(t, float(s.get("sentiment", 0) or 0))
                    s["sentiment"] = _bs
                    s["score"] = composite_score(
                        int(s.get("conviction", 7) or 7),
                        float(s.get("raw_score", 0) or 0),
                        _bs,
                    )
                # Drop a carried signal whose score fell below the buy floor — it's
                # no longer a valid candidate, so expiring it won't re-add/flip-flop.
                if float(s.get("score", 0) or 0) < MIN_COMPOSITE_SCORE:
                    s["status"] = "expired"
                    s["expired_at"] = now
                    prior_history.append(s)
                    continue
                carried_pending.append(s)
            _already_pending = {s["ticker"] for s in carried_pending}
            data["signals"] = carried_pending + prior_history
            created_this_scan: list[str] = []
            seen: set[str] = set()

            # ── Portfolio-manager policy: manage-first, capacity, top-N ──────────
            # Reconcile fresh picks against the UNIFIED portfolio (real Fidelity
            # holdings + open thematic positions). Suppresses new generation when
            # the book is full or strong positions are still progressing, and caps
            # output to the top few actionable ideas. Must never break scanning →
            # any failure falls back to the legacy "all validated picks" flow.
            from tradingagents.portfolio import portfolio_policy as _pol
            policy_by_ticker: dict | None = {}
            policy_summary: dict = {}
            try:
                from web.api.holdings_brain import get_unified_existing
                primary = _thematic_primary_email()
                _unified, _acct_val = await get_unified_existing(primary)
                _held = {p.ticker.upper() for p in _unified}
                _cands = [
                    _pol.Candidate(
                        ticker=p["ticker"].upper(),
                        conviction=int(p.get("conviction", 5)),
                        expected_return=float(p.get("target_pct", 0) or 0),
                        confirmed=bool(consistency.get(p["ticker"], {}).get("confirmed", False)),
                        raw_score=float(score_dict.get(p["ticker"], 0) or 0),
                    )
                    for p in picks
                    if p["ticker"].upper() not in _held and p["ticker"] not in _already_pending
                ]
                _result = _pol.evaluate(_unified, _cands, _pol.PolicyConfig.from_env())
                policy_summary = {**_result.to_dict(), "account_value": _acct_val, "primary_email": primary}
                for d in _result.decisions:
                    if d.kind in (_pol.KIND_NEW, _pol.KIND_REPLACE):
                        policy_by_ticker[d.ticker.upper()] = d.to_dict()
                if _result.suppress_generation:
                    log.info("Thematic policy: suppress new signals — %s", _result.reason)
            except Exception as _pe:
                log.warning("Thematic policy skipped (%s) — legacy signal flow", _pe)
                policy_by_ticker = None  # sentinel: policy unavailable → allow all picks

            for pick in picks:
                t = pick["ticker"]
                if t in seen:
                    continue
                seen.add(t)

                # Skip if already in portfolio (don't re-propose what we own)
                if t in _existing_portfolio:
                    log.debug("Signal skip: %s already in portfolio", t)
                    continue

                # Skip if already pending (don't flood with same ticker every scan)
                if t in _already_pending:
                    log.debug("Signal skip: %s already pending approval", t)
                    continue

                # BLOCK net-sell names: social chatter is net-selling/warning, so
                # don't open a BUY here even if residual buzz ranked it (intent
                # classifier). The avoid flag + reason ride source_breakdown.
                if source_breakdown.get(t, {}).get("avoid"):
                    log.info("Signal skip: %s flagged avoid (net social selling: %s)",
                             t, source_breakdown.get(t, {}).get("sell_intent_reason", ""))
                    continue

                # Policy gate: when active, only the top actionable tickers pass
                # (suppress_generation ⇒ empty dict ⇒ nothing passes this cycle).
                if policy_by_ticker is not None and t.upper() not in policy_by_ticker:
                    log.debug("Signal skip: %s not in policy top-N", t)
                    continue
                _pd = (policy_by_ticker or {}).get(t.upper(), {})

                c = consistency.get(t, {})
                is_spike = c.get("is_spike", True)   # default True = treat new as spike
                appearances = c.get("appearances", 1)
                confirmed = c.get("confirmed", False)

                # Evidence ceiling on LLM conviction: a free-model 9/10 without
                # corroboration (scan confirmation + quality sources) is clamped
                # before it drives composite, sizing, and policy ranking.
                if _conviction_clamp_enabled():
                    _cv = clamp_conviction(pick["conviction"], source_breakdown.get(t, {}), confirmed)
                    if _cv != pick["conviction"]:
                        log.info("Conviction clamp %s: %d → %d (confirmed=%s, sources=%s)",
                                 t, pick["conviction"], _cv, confirmed,
                                 sorted(source_breakdown.get(t, {}).keys()))
                        pick["conviction_raw"] = pick["conviction"]
                        pick["conviction"] = _cv

                # Composite admit gate — the real buy floor. Reject sub-threshold names
                # HERE (the single chokepoint every new signal passes) so a low score can't
                # leak in via top-N policy selection or the policy-unavailable fallback.
                _sent = _blended_sentiment(t, pick.get("sentiment", 0.0))
                _comp = composite_score(pick["conviction"], score_dict.get(t, 0), _sent)

                # Price/volume confirmation: the tape agrees (accumulation) →
                # small boost; the tape contradicts (distribution on heavy
                # volume) → cut. Fail-neutral 1.0 when bars are unavailable.
                _pc = 1.0
                if _price_confirm_enabled():
                    try:
                        _pc = await asyncio.wait_for(
                            asyncio.to_thread(_price_confirm_for, t), timeout=12)
                    except Exception:
                        _pc = 1.0
                    if _pc != 1.0:
                        _adj = int(round(min(100.0, _comp * _pc)))
                        log.info("Price confirm %s: ×%.3f (composite %d → %d)", t, _pc, _comp, _adj)
                        _comp = _adj

                if _comp < MIN_COMPOSITE_SCORE:
                    log.info("Signal skip: %s composite %d < admit floor %d",
                             t, _comp, int(MIN_COMPOSITE_SCORE))
                    continue

                data["signals"].append({
                    "id":           f"{t}_{int(now)}",
                    "ticker":       t,
                    "name":         pick["name"],
                    "conviction":   pick["conviction"],
                    "conviction_raw": pick.get("conviction_raw", pick["conviction"]),
                    "theme":        pick["theme"],
                    "thesis":       pick["thesis"],
                    "catalyst":     pick["catalyst"],
                    "bull_case":    pick["bull_case"],
                    "bear_case":    pick["bear_case"],
                    "sentiment":    _sent,
                    "crowd_view":   pick.get("crowd_view", ""),
                    "target_pct":   pick["target_pct"],
                    "stop_pct":     pick["stop_pct"],
                    "hold_days":    pick["hold_days"],
                    "status":       "pending",
                    "source":       "auto_scan",
                    "ts":           scan_ts,
                    "raw_score":    score_dict.get(t, 0),
                    "score":        _comp,
                    "price_confirm": _pc,
                    "source_breakdown": source_breakdown.get(t, {}),
                    "is_spike":     is_spike,
                    "confirmed":    confirmed,
                    "scan_appearances": appearances,
                    # Portfolio-policy tags (NEW / REPLACE + capacity context)
                    "policy_kind":    _pd.get("kind", "NEW"),
                    "replace_target": _pd.get("replace_target"),
                    "size_factor":    _pd.get("size_factor", 1.0),
                    "capacity_note":  _pd.get("capacity_note", ""),
                    "policy_reason":  _pd.get("reason", ""),
                })
                created_this_scan.append(t)

            # Trim old non-pending signals (keep last 50)
            pending   = [s for s in data["signals"] if s.get("status") == "pending"]
            history   = [s for s in data["signals"] if s.get("status") != "pending"][-50:]

            # #3 Catalyst materiality: AI rates each pending signal's catalyst +
            # writes a why_now (shown on the HIL card / SMS). A "none" catalyst is a
            # momentum-on-hope tell → modest score dampen (mirrors the conviction
            # cap for catalyst-less names). Best-effort; absence changes nothing.
            try:
                mat = await _ai_catalyst_materiality(pending)
                for s in pending:
                    m = mat.get(s.get("ticker"))
                    if not m:
                        continue
                    s["catalyst_quality"] = m["catalyst_quality"]
                    s["why_now"] = m["why_now"]
                    if m["catalyst_quality"] == "none" and not s.get("_catalyst_dampened"):
                        s["score"] = int(round(float(s.get("score", 0)) * 0.9))
                        s["_catalyst_dampened"] = True
            except Exception as _ce:
                log.debug("catalyst materiality pass skipped: %s", _ce)

            # #6 AI red-flag deepening: hidden risks the keyword veto misses. A flag
            # caps the score to the bearish ceiling (≤45, same as the lexicon veto)
            # so it can never clear an auto-trade gate, and stamps the risk for the
            # HIL card. Best-effort; the lexicon veto remains regardless.
            try:
                rf = await _ai_red_flag_check(pending)
                for s in pending:
                    r = rf.get(s.get("ticker"))
                    if not r:
                        continue
                    s["red_flag"] = True
                    s["red_flag_reason"] = r["risk"]
                    s["score"] = min(int(s.get("score", 0) or 0), 45)
                    s["sentiment"] = min(float(s.get("sentiment", 0) or 0), -0.5)
                    log.info("AI red-flag %s: %s — score capped 45", s.get("ticker"), r["risk"][:60])
            except Exception as _rfe:
                log.debug("AI red-flag pass skipped: %s", _rfe)

            data["signals"] = pending + history
            data["last_scan"] = scan_ts
            data["policy"] = policy_summary
            _save_signals(data)

            # SMS notify only when this scan produced GENUINELY NEW pending signals —
            # carried-forward ones were already announced (no re-page on every scan).
            if created_this_scan:
                asyncio.create_task(_notify_thematic_hil_pending(len(pending)))

            # Auto-trade evaluates all pending; the per-signal trade-request SMS and
            # paper auto-exec inside are themselves cooldown-gated, so carried signals
            # don't re-page unless their score/kind materially changed.
            if pending:
                asyncio.create_task(_auto_execute_confirmed_signals(pending))

            # Auto-check exits after each scan (non-blocking, execute=True)
            try:
                exited = await _check_thematic_exits(execute=True)
                if exited:
                    log.info("Auto-exit: closed %d thematic position(s) this scan", len(exited))
            except Exception as ex_err:
                log.warning("Auto-exit check failed (non-fatal): %s", ex_err)

            _set_status("done", f"Found {len(picks)} signals")
        except Exception as e:
            log.exception("Scan failed")
            _set_status("error", str(e))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/thematic/auto/twitter-status")
async def twitter_status(_user: dict = Depends(get_current_user)):
    return {
        "ok": True,
        "connected": False,
        "reason": "Twitter API v2 search requires Basic plan ($100/mo). Free tier only allows posting.",
        "sources_active": ["Reddit WSB/stocks/investing", "DuckDuckGo news", "Yahoo Finance trending"],
    }


def _min_scan_interval_min() -> float:
    """Minimum minutes between manual scans — a re-scan moments after the last
    just churns the queue and re-pages. Env THEMATIC_MIN_SCAN_INTERVAL_MIN,
    default 30; 0 disables. The 4h auto-loop is unaffected (well above this)."""
    try:
        return max(0.0, float(os.getenv("THEMATIC_MIN_SCAN_INTERVAL_MIN", "30") or 30))
    except (TypeError, ValueError):
        return 30.0


def _last_scan_done_epoch() -> float:
    """Epoch of the last completed scan (SIGNALS_FILE.last_scan), 0 if unknown."""
    try:
        ls = _load_signals().get("last_scan")
        if ls:
            import datetime as _dt
            return _dt.datetime.fromisoformat(str(ls)).timestamp()
    except Exception:
        pass
    return 0.0


@router.post("/thematic/auto/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    force: bool = False,
):
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    if status.get("status") == "running" and not _scan_status_stale(status):
        return {"ok": True, "message": "Scan already running", "status": "running"}
    # Min-interval guard: refuse a manual re-scan too soon after the last completed
    # one (it only churns the queue + re-pages). Bypass with force=true.
    if not force:
        last = _last_scan_done_epoch()
        gap = _min_scan_interval_min() * 60.0
        if last and gap > 0 and (time.time() - last) < gap:
            ago = int((time.time() - last) / 60)
            wait = max(1, int((gap - (time.time() - last)) / 60))
            return {
                "ok": True, "skipped": True, "status": "throttled",
                "message": (
                    f"Last scan {ago}m ago (min interval {int(gap / 60)}m). "
                    f"Retry in ~{wait}m or pass force=true."
                ),
            }
    if _scan_status_stale(status):
        log.warning("Thematic scan status stale (ts=%s) — overriding, starting fresh scan", status.get("ts"))
    background_tasks.add_task(_run_scan)
    return {"ok": True, "message": "Scan started", "status": "running"}


@router.get("/thematic/auto/status")
async def scan_status(_user: dict = Depends(get_current_user)):
    base = {"status": "idle", "detail": "No scan run yet", "ts": 0}
    if STATUS_FILE.exists():
        try:
            base = json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    # Per-source health so a silently-dead feed is visible here, not hidden.
    try:
        base["source_health"] = _source_health_summary()
    except Exception:
        pass
    return base


@router.get("/thematic/auto/signals")
async def get_signals(_user: dict = Depends(get_current_user)):
    data = _load_signals()
    pending = [s for s in data["signals"] if s.get("status") == "pending"]
    # Regime-adaptive buy gate: in risk-off markets demand stronger signals
    # (env THEMATIC_REGIME_GATE, default off → multiplier 1.0, no change).
    _regime_mult = _regime_threshold_multiplier()
    _eff_min = MIN_SIGNAL_SCORE * _regime_mult
    # Short-pressure risk overlay (env THEMATIC_SHORT_OVERLAY, default off → {}).
    _short_map = _finra_short_map()
    _true_short_map = _true_short_interest_map(universe=[s.get("ticker", "") for s in pending])
    # Annotate each signal with buy eligibility, spike status, and scan history
    for sig in pending:
        rs = float(sig.get("raw_score", 0) or 0)
        is_spike  = sig.get("is_spike", True)
        confirmed = sig.get("confirmed", False)
        appearances = sig.get("scan_appearances", 1)
        # Breakout fast-lane (IREN-class): a genuine new-high-on-volume breakout is
        # a real-time PRICE confirmation, so it releases a spike's BUY immediately
        # instead of waiting ~8h for a 2nd scan — only when enabled
        # (THEMATIC_BREAKOUT_CONFIRM, default off). Still HIL-approved; this only
        # flips will_buy on a name that already clears the score gate.
        breakout_ok = False
        if is_spike and rs >= _eff_min and _breakout_confirm_enabled():
            try:
                breakout_ok = _ticker_breakout(str(sig.get("ticker", "")))
            except Exception:
                breakout_ok = False
        if breakout_ok:
            sig["breakout_confirmed"] = True
        # will_buy: clear the (regime-adjusted) score threshold AND (confirmed by
        # 2+ scans OR a price breakout). Spike-only signals are shown but flagged.
        _wb = (rs >= _eff_min) and (not is_spike or breakout_ok)
        # Short-pressure overlay: surface the level, and VETO will_buy on extreme
        # short volume (don't auto-suggest buying into a shorts-pressing-hard name;
        # HIL can still approve manually). Additive-to-score is intentionally NOT done.
        if _short_map:
            _lvl = _short_pressure_level(_short_map.get(str(sig.get("ticker", "")).upper()))
            if _lvl in ("high", "extreme"):
                sig["short_pressure"] = _lvl
            if _lvl == "extreme":
                _wb = False
        if _true_short_map:
            _slvl = _true_short_map.get(str(sig.get("ticker", "")).upper(), "unknown")
            if _slvl in ("high", "extreme"):
                sig["short_interest_pressure"] = _slvl
            if _slvl == "extreme":
                _wb = False
        sig["will_buy"]        = _wb
        sig["score_threshold"] = round(_eff_min, 1)
        if _regime_mult != 1.0:
            sig["regime_multiplier"] = round(_regime_mult, 2)
        sig["buzz_tier"]       = _buzz_tier(rs)
        sig["score"]           = composite_score(sig.get("conviction", 7), rs, sig.get("sentiment", 0.0))  # unified 0-100
        sig["is_spike"]        = is_spike
        sig["confirmed"]       = confirmed
        sig["scan_appearances"]= appearances
    return {
        "ok": True,
        "signals": pending,
        "last_scan": data.get("last_scan"),
        "policy": data.get("policy") or {},
    }


def _conviction_dollar(base: float, conviction: int) -> float:
    """Scale position size by conviction: 10=1.5×, 8=1.2×, 6=1.0×, 4=0.7×, 1=0.4×.

    Conviction is clamped to [1, 10] (matching composite_score) so a malformed
    signal (conviction 0/15/NaN) can never inflate or zero out a position size;
    a non-finite/negative base collapses to 0. Money-sizing — fail closed.
    """
    import math
    try:
        b = float(base)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(b) or b <= 0:
        return 0.0
    try:
        c = int(conviction or 0)
    except (TypeError, ValueError):
        c = 0
    c = max(1, min(10, c))
    scale = 0.4 + (c - 1) / 9.0 * 1.1  # linear 0.4→1.5
    return round(b * scale, 2)


def _thematic_account_value(email: str) -> float:
    """Reference portfolio value for adaptive sizing — the thematic paper book's
    cash + deployed value (no network). Fund this book to mirror your real account
    and sizing scales to it. 0 if unavailable (caller falls back to flat base)."""
    import math as _m
    try:
        from web.api.thematic_portfolio import PAPER_STATE_FILE
        st = json.loads(PAPER_STATE_FILE.read_text()) if PAPER_STATE_FILE.exists() else {}
    except Exception:
        return 0.0

    def _f(x) -> float:
        # Garbage/non-finite values in state must not crash money sizing — treat
        # them as 0 (caller falls back to the flat base size).
        try:
            v = float(x or 0)
        except (TypeError, ValueError):
            return 0.0
        return v if _m.isfinite(v) else 0.0

    try:
        cash = _f(st.get("cash", 0))
        deployed = sum(
            _f(p.get("entry_price", 0)) * _f(p.get("shares", 0))
            for p in (st.get("positions", {}) or {}).values()
        )
    except Exception:
        return 0.0
    val = round(cash + deployed, 2)
    return val if (_m.isfinite(val) and val >= 0) else 0.0


def _adaptive_dollar(account_value: float, score: float, target_pct: float, hil: dict) -> float:
    """Position size adaptive to portfolio AND signal quality.

    dollar = account_value × base_pct × score_multiplier × target_boost, clamped to
    [min_dollar, MAX_POSITION_PCT_OF_ACCOUNT% of account]. A high-score / high-target
    conviction play gets a bigger slice; a marginal one gets a small probe. Replaces
    the flat $1,000-for-everything sizing.
    """
    import math
    if not math.isfinite(float(account_value or 0)) or account_value <= 0:
        return 0.0
    # Non-finite score/target must not poison the multiplier (NaN propagates
    # through min/max and would yield a NaN dollar size). Coerce to safe values.
    _score = float(score) if math.isfinite(float(score or 0)) else 0.0
    _target = float(target_pct) if math.isfinite(float(target_pct or 0)) else 0.0
    base_pct = float(hil.get("base_position_pct", 4.0)) / 100.0
    # score 50→0.6×, 70→1.12×, 85→1.5×, 100→1.9× (clamped 0.4–2.0)
    score_mult = max(0.4, min(2.0, 0.6 + (_score - 50.0) / 50.0 * 1.3))
    # let conviction in a big runner add a little extra (target ≥80% → up to +20%)
    target_boost = 1.0 + min(0.2, max(0.0, (_target - 40.0) / 300.0))
    dollar = account_value * base_pct * score_mult * target_boost
    try:
        from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT as _CAP
    except Exception:
        _CAP = 10.0
    cap = account_value * (float(_CAP) / 100.0)
    floor = float(hil.get("min_dollar", 25.0))
    return round(max(floor, min(dollar, cap)), 2)


# ── Portfolio-aware sizing (whole-book optimization, not size-in-isolation) ──────
_SECTOR_CACHE: dict[str, str] = {}
_SECTOR_CACHE_FILE = TMP / "sector_cache.json"
_GICS_SECTORS = {
    "Technology", "Health Care", "Financials", "Consumer Discretionary",
    "Consumer Staples", "Energy", "Industrials", "Materials", "Utilities",
    "Real Estate", "Communication Services",
}


def _load_sector_cache() -> None:
    if _SECTOR_CACHE:
        return
    try:
        _SECTOR_CACHE.update(json.loads(_SECTOR_CACHE_FILE.read_text()))
    except Exception:
        pass


def _ai_sector_fill(ticker: str) -> str:
    """#5 Ask the free AI for a ticker's GICS sector when yfinance has none (common
    for small-caps the portfolio sizer must still sector-cap). Returns "" if AI off
    or the reply isn't a recognized sector."""
    if not _ai_intent_enabled():
        return ""
    sys = ("You map a US stock ticker to its GICS sector. Reply with ONLY the sector "
           "name, exactly one of: Technology, Health Care, Financials, Consumer "
           "Discretionary, Consumer Staples, Energy, Industrials, Materials, Utilities, "
           "Real Estate, Communication Services. If unknown, reply Unknown.")
    txt = (_ai_complete_sync(sys, f"Ticker: {ticker}. Sector?", prefer="cheap", max_tokens=20) or "").strip()
    for s in _GICS_SECTORS:
        if s.lower() in txt.lower():
            return s
    return ""


def _sector_of(ticker: str) -> "str | None":
    """Best-effort GICS sector for a ticker, cached (persistent). yfinance first;
    if it has none, the free AI fills it (so the sizer's sector-concentration cap
    works on small-caps too). None only when both fail."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    _load_sector_cache()
    if t in _SECTOR_CACHE:
        return _SECTOR_CACHE[t] or None
    sec = ""
    try:
        import yfinance as yf
        info = getattr(yf.Ticker(t), "info", None) or {}
        sec = (info.get("sector") or "").strip()
    except Exception:
        sec = ""
    if not sec:
        sec = _ai_sector_fill(t)        # AI gap-fill for small-caps
    _SECTOR_CACHE[t] = sec
    try:
        _atomic_write(_SECTOR_CACHE_FILE, _SECTOR_CACHE)
    except Exception:
        pass
    return sec or None


def _closes_map(tickers: "list[str]", period: str = "6mo") -> "dict[str, list[float]]":
    """One batched daily-close download for vol/correlation. Best-effort → {}."""
    out: dict[str, list[float]] = {}
    uniq = list(dict.fromkeys(t.upper() for t in tickers if t))
    if not uniq:
        return out
    try:
        import yfinance as yf
        df = yf.download(uniq, period=period, auto_adjust=True, progress=False, threads=False)
        if df is None or getattr(df, "empty", True):
            return out
        close = df["Close"] if "Close" in getattr(df, "columns", []) else df
        if hasattr(close, "columns"):           # multi-ticker frame
            for t in uniq:
                if t in close.columns:
                    s = close[t].dropna().tolist()
                    if len(s) >= 5:
                        out[t] = s
        else:                                    # single-ticker series
            s = close.dropna().tolist()
            if len(s) >= 5:
                out[uniq[0]] = s
    except Exception:
        pass
    return out


def _adv_dollars_for(ticker: str, period: str = "1mo") -> "float | None":
    """Average daily DOLLAR volume (mean of last ~20d share volume × last close).
    Best-effort liquidity input for the sizer; None on any failure → neutral."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    try:
        import math as _m
        import yfinance as yf
        df = yf.download(t, period=period, auto_adjust=True, progress=False, threads=False)
        if df is None or getattr(df, "empty", True):
            return None
        cols = getattr(df, "columns", [])
        if "Volume" not in cols or "Close" not in cols:
            return None
        vol, close = df["Volume"], df["Close"]
        if hasattr(vol, "columns"):       # multi-ticker frame → take first column
            vol, close = vol.iloc[:, 0], close.iloc[:, 0]
        v = float(vol.dropna().tail(20).mean())
        p = float(close.dropna().iloc[-1])
        adv = v * p
        return adv if (_m.isfinite(adv) and adv > 0) else None
    except Exception:
        return None


async def _portfolio_aware_dollar(email, sig, account_value, target_pct, stop_pct, hil):
    """Whole-portfolio dollar size for a thematic approval.

    Returns ``(dollars, info_dict)`` on success (dollars may be 0.0 if no room —
    e.g. sector/heat cap binding), or ``(None, None)`` if portfolio context could
    not be built (caller then falls back to the legacy adaptive sizer). Volatility,
    correlation, and sector are best-effort, bounded by a timeout, and degrade to a
    neutral factor — they never block sizing.
    """
    try:
        from tradingagents.portfolio import position_sizer as ps
        from web.api.thematic_portfolio import PAPER_STATE_FILE

        ticker = str(sig.get("ticker", "")).upper()
        conviction = int(sig.get("conviction", 7) or 7)
        score = float(sig.get("score") or 0)
        av = float(account_value or 0)
        if av <= 0:
            return None, None

        st = json.loads(PAPER_STATE_FILE.read_text()) if PAPER_STATE_FILE.exists() else {}
        positions = st.get("positions", {}) or {}
        cash = float(st.get("cash", 0) or 0)

        # Bounded best-effort enrichment: sectors (book + candidate) + closes for
        # vol/correlation. On timeout/failure we keep neutral factors and still
        # size off conviction/score + the hard portfolio constraints.
        vol = None
        max_corr = None
        adv_dollars = None
        sectors: dict[str, str | None] = {}
        book_tickers = [str(tk).upper() for tk in positions.keys()]
        try:
            async def _enrich():
                want = list(dict.fromkeys([ticker] + book_tickers))
                secs, closes, adv = await asyncio.gather(
                    asyncio.gather(*[asyncio.to_thread(_sector_of, t) for t in want]),
                    asyncio.to_thread(_closes_map, want),
                    asyncio.to_thread(_adv_dollars_for, ticker),
                )
                return {t: s for t, s in zip(want, secs)}, closes, adv
            sectors, closes, adv_dollars = await asyncio.wait_for(_enrich(), timeout=12.0)
            cand_closes = closes.get(ticker)
            if cand_closes:
                vol = ps.realized_vol_pct(cand_closes)
                book = {t: c for t, c in closes.items() if t != ticker}
                if book:
                    from tradingagents.portfolio.correlation import max_correlation
                    mc = max_correlation(cand_closes, book)
                    max_corr = mc.get("max_corr") if isinstance(mc, dict) else None
        except Exception:
            sectors = {}

        existing = []
        for tk, p in positions.items():
            val = float(p.get("entry_price", 0) or 0) * float(p.get("shares", 0) or 0)
            existing.append(ps.BookPosition(
                ticker=str(tk).upper(),
                weight_pct=(val / av * 100.0) if av > 0 else 0.0,
                sector=sectors.get(str(tk).upper()),
            ))
        cand = ps.SizingCandidate(
            ticker=ticker, conviction=conviction, score=score,
            expected_return_pct=float(target_pct or 0), stop_pct=float(stop_pct or 0),
            volatility_pct=vol, sector=sectors.get(ticker), max_corr=max_corr,
            adv_dollars=adv_dollars,
        )
        res = ps.size_position(av, cand, existing, cash_available=cash, cfg=ps.SizerConfig.from_env(hil))
        log.info("Portfolio-aware size %s: $%.0f (%.1f%%) bound=%s factors=%s",
                 ticker, res.dollars, res.weight_pct, res.binding_constraint, res.factors)
        return res.dollars, res.to_dict()
    except Exception as e:
        log.warning("portfolio-aware sizing failed (%s) — fallback to adaptive", e)
        return None, None


# ── Breakout confirmation (IREN-class catalyst-mover fast-lane) ──────────────
# Missed-trade analysis: the multi-scan (is_spike) gate enters catalyst-gap
# movers ~8h late, near the top of the first leg, and never finds the cheap,
# no-buzz base. A genuine price breakout (new high on heavy relative volume) is a
# real-time confirmation the move is happening — a *price* substitute for the
# *social* confirmation gate, orthogonal to buzz. Used to optionally release a
# spike's BUY immediately (still HIL-approved). Pure helper = testable offline.

def _breakout_signal(
    highs: "list[float]",
    closes: "list[float]",
    volumes: "list[float]",
    *,
    rvol_min: float = 3.0,
    lookback: int = 20,
) -> dict:
    """Detect a volume breakout to a new high from recent daily bars (oldest →
    newest, today last). Returns {is_breakout, rvol, new_high}. A breakout needs
    BOTH a new `lookback`-day high close AND relative volume >= rvol_min, so a
    quiet drift to a high (no volume) and a high-volume churn that isn't a new
    high are both rejected (keeps the RGTI-style froth out)."""
    import math as _m

    def _clean(xs):
        return [float(x) for x in (xs or [])
                if isinstance(x, (int, float)) and _m.isfinite(float(x))]

    h, c, v = _clean(highs), _clean(closes), _clean(volumes)
    if min(len(h), len(c), len(v)) < lookback + 1:
        return {"is_breakout": False, "rvol": 0.0, "new_high": False}
    today_close, today_vol = c[-1], v[-1]
    prior_vol = v[-(lookback + 1):-1]
    avg_vol = sum(prior_vol) / len(prior_vol) if prior_vol else 0.0
    rvol = (today_vol / avg_vol) if avg_vol > 0 else 0.0
    prior_high = max(h[-(lookback + 1):-1])
    new_high = bool(prior_high > 0 and today_close >= prior_high)
    is_breakout = bool(rvol >= rvol_min and new_high)
    return {"is_breakout": is_breakout, "rvol": round(rvol, 2), "new_high": new_high}


def _breakout_confirm_enabled() -> bool:
    return os.getenv("THEMATIC_BREAKOUT_CONFIRM", "false").strip().lower() in ("1", "true", "yes", "on")


# ── Price/volume confirmation of social signals ───────────────────────────────
def _price_confirm_enabled() -> bool:
    return os.getenv("THEMATIC_PRICE_CONFIRM", "true").strip().lower() in ("1", "true", "yes", "on")


def price_confirmation(
    closes: "list[float]",
    volumes: "list[float]",
    *,
    lo: float = 0.85,
    hi: float = 1.12,
) -> float:
    """Pure multiplier confirming (or contradicting) a social signal with tape.

    The composite score is conviction + buzz + sentiment — pure narrative, zero
    price information, so "everyone's loading up" scores identically whether the
    stock is being accumulated or dumped. This reads the last week of daily bars:

    * price UP over ~5 sessions, heavier-than-normal volume → accumulation
      confirms the buzz → boost toward ``hi``.
    * price DOWN on heavy volume → distribution contradicts the buzz (bull-trap
      / bag-holder chatter) → cut toward ``lo``.
    * quiet / mixed tape, or not enough bars → 1.0 (fail-neutral).

    Bars oldest → newest, today last. A ±8% 5-day move saturates the direction;
    relative volume (today+yesterday vs 20d avg) scales how much the move counts.
    """
    import math as _m

    def _clean(xs):
        return [float(x) for x in (xs or [])
                if isinstance(x, (int, float)) and _m.isfinite(float(x))]

    c, v = _clean(closes), _clean(volumes)
    if len(c) < 10:
        return 1.0
    ret5 = c[-1] / c[-6] - 1.0 if c[-6] > 0 else 0.0
    direction = max(-1.0, min(1.0, ret5 / 0.08))
    prior = v[-22:-2]
    avg_vol = sum(prior) / len(prior) if prior else 0.0
    recent = v[-2:]
    rvol = (sum(recent) / len(recent) / avg_vol) if (avg_vol > 0 and recent) else 1.0
    # Volume scales conviction in the move: quiet tape (rvol 0.5) counts ~70%,
    # heavy tape (rvol >= 2) counts ~130% of the direction.
    vol_amp = 0.7 + 0.3 * max(0.0, min(2.0, rvol - 0.5))
    x = direction * vol_amp
    mult = 1.0 + (x * (hi - 1.0) if x >= 0 else x * (1.0 - lo))
    return round(max(lo, min(hi, mult)), 4)


def _price_confirm_for(ticker: str, *, fetch=None) -> float:
    """Network wrapper: bars → price_confirmation. 1.0 on any failure."""
    fetch = fetch or _fetch_daily_bars
    try:
        bars = fetch(ticker) or {}
        return price_confirmation(bars.get("closes", []), bars.get("volumes", []))
    except Exception as e:
        log.debug("price confirm %s: %s", ticker, e)
        return 1.0


def _fetch_daily_bars(ticker: str, period: str = "3mo") -> dict:
    """Best-effort daily OHLCV for breakout detection. {} on any failure so the
    caller degrades to the normal social gate. Isolated + injectable for tests."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df is None or df.empty:
            return {}
        return {
            "highs": [float(x) for x in df["High"].squeeze().tolist()],
            "closes": [float(x) for x in df["Close"].squeeze().tolist()],
            "volumes": [float(x) for x in df["Volume"].squeeze().tolist()],
        }
    except Exception as e:
        log.debug("breakout bars fetch failed for %s: %s", ticker, e)
        return {}


def _ticker_breakout(ticker: str, *, fetch=None) -> bool:
    """True if `ticker` is currently breaking out (new high on heavy volume).
    Disabled unless THEMATIC_BREAKOUT_CONFIRM. Network-isolated via `fetch`."""
    if not _breakout_confirm_enabled():
        return False
    fetch = fetch or _fetch_daily_bars
    try:
        bars = fetch(ticker) or {}
    except Exception:
        return False
    sig = _breakout_signal(bars.get("highs", []), bars.get("closes", []), bars.get("volumes", []))
    return bool(sig.get("is_breakout"))


def _atr_stops_enabled() -> bool:
    return os.getenv("THEMATIC_ATR_STOPS", "false").strip().lower() in ("1", "true", "yes", "on")


# ── Trade-request chart (TradingView-style PNG attached to the SMS) ───────────
_CHART_DIR = Path(__file__).resolve().parent.parent / "static" / "charts"


def _chart_sms_enabled() -> bool:
    return os.getenv("THEMATIC_CHART_SMS", "false").strip().lower() in ("1", "true", "yes", "on")


def _fetch_ohlcv_df(ticker: str, period: str = "1y"):
    """Daily OHLCV DataFrame for charting (MultiIndex columns flattened). None on
    failure. Isolated + injectable for tests."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        log.debug("ohlcv fetch %s: %s", ticker, e)
        return None


def _chart_upload_enabled() -> bool:
    return os.getenv("THEMATIC_CHART_UPLOAD", "false").strip().lower() in ("1", "true", "yes", "on")


def _upload_chart_public(path: str) -> "str | None":
    """Upload a chart PNG to a public no-auth host so an external SMS provider can
    fetch it (the tunnel /charts is behind Cloudflare Access). Returns the public
    URL or None. Used only when THEMATIC_CHART_UPLOAD is set; otherwise the helper
    returns the (CF-gated) tunnel URL, which works once a CF Access bypass exists
    for /charts/*. Host is overridable via THEMATIC_CHART_UPLOAD_URL."""
    endpoint = os.getenv("THEMATIC_CHART_UPLOAD_URL", "https://catbox.moe/user/api.php")
    try:
        with open(path, "rb") as fh:
            r = httpx.post(endpoint,
                           data={"reqtype": "fileupload"},
                           files={"fileToUpload": (os.path.basename(path), fh, "image/png")},
                           timeout=20.0)
        url = (r.text or "").strip()
        return url if url.startswith("http") else None
    except Exception as e:
        log.debug("chart public upload failed: %s", e)
        return None


async def _generate_signal_chart(ticker: str, sig: dict, *, fetch=None, uploader=None) -> "str | None":
    """Render a trade chart for a signal and return its PUBLIC url (None if
    disabled / on any failure → the SMS still sends, just without an image).
    entry = last close; stop/target from the signal's stop_pct/target_pct."""
    if not _chart_sms_enabled():
        return None
    fetch = fetch or _fetch_ohlcv_df
    df = await asyncio.to_thread(fetch, ticker)
    if df is None or len(df) < 20 or "Close" not in getattr(df, "columns", []):
        return None
    try:
        from tradingagents.portfolio.chart import compute_levels, render_trade_chart
        last = float(df["Close"].iloc[-1])
        sp = float(sig.get("stop_pct", 8) or 8)
        tp = float(sig.get("target_pct", 30) or 30)
        entry = round(last, 2)
        stop = round(last * (1 - sp / 100.0), 2)
        target = round(last * (1 + tp / 100.0), 2)
        levels = compute_levels(df["High"].tolist(), df["Low"].tolist(), df["Close"].tolist())
        _CHART_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{_norm_ticker(ticker) or 'X'}_{int(time.time())}.png"
        out = str(_CHART_DIR / fname)
        res = await asyncio.to_thread(
            render_trade_chart, ticker, df,
            entry=entry, stop=stop, target=target, out_path=out, levels=levels,
        )
        if not res:
            return None
        # Public delivery. The tunnel /charts is behind Cloudflare Access, so an
        # external SMS provider can't fetch it; when THEMATIC_CHART_UPLOAD is set we
        # push to a public host instead. Else return the tunnel URL (works once a
        # CF Access bypass for /charts/* exists).
        if _chart_upload_enabled():
            up = await asyncio.to_thread(uploader or _upload_chart_public, out)
            if up:
                return up
            # upload failed → fall through to the tunnel URL
        base = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org").rstrip("/")
        return f"{base}/charts/{fname}"
    except Exception as e:
        log.debug("signal chart %s: %s", ticker, e)
        return None


# ── FINRA short-volume RISK OVERLAY (NOT a buzz source) ──────────────────────
# Heavy short volume pressing into a long is a RISK, not a bullish signal — so it
# must never be additive to the buzz score (that would wrongly rank UP shorted
# names). It is surfaced as a per-signal risk annotation only. One daily file
# covers all symbols (cached); pure parser + injectable fetch = testable offline.
_finra_short_cache: dict = {"day": "", "map": {}}


def _short_overlay_enabled() -> bool:
    return os.getenv("THEMATIC_SHORT_OVERLAY", "false").strip().lower() in ("1", "true", "yes", "on")


def _true_short_interest_enabled() -> bool:
    return os.getenv("THEMATIC_TRUE_SHORT_INTEREST", "false").strip().lower() in ("1", "true", "yes", "on")


def _parse_finra_short_volume(text: str) -> "dict[str, float]":
    """Parse a FINRA daily short-volume file (pipe-delimited; header row
    Date|Symbol|ShortVolume|...|TotalVolume|...) → {symbol: short/total ratio}."""
    out: dict[str, float] = {}
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return out
    header = [h.strip().lower() for h in lines[0].split("|")]
    try:
        i_sym, i_short, i_total = header.index("symbol"), header.index("shortvolume"), header.index("totalvolume")
    except ValueError:
        return out
    for ln in lines[1:]:
        parts = ln.split("|")
        if len(parts) <= max(i_sym, i_short, i_total):
            continue
        try:
            sym = parts[i_sym].strip().upper()
            sv, tv = float(parts[i_short]), float(parts[i_total])
        except (ValueError, IndexError):
            continue
        if sym and tv > 0:
            out[sym] = round(sv / tv, 4)
    return out


def _short_pressure_level(ratio: "float | None") -> str:
    """'extreme' (>=0.60) | 'high' (>=0.50) | 'normal' | 'unknown'. ~0.40-0.45 is
    typical market-wide, so >=0.50 means shorts are unusually active."""
    if ratio is None:
        return "unknown"
    import math as _m
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return "unknown"
    if not _m.isfinite(r):
        return "unknown"
    if r >= 0.60:
        return "extreme"
    if r >= 0.50:
        return "high"
    return "normal"


def _short_interest_level(row: "dict | None") -> str:
    """True short-interest / borrow pressure level from vendor fields such as
    shortPercentOfFloat, shortFloat, daysToCover, borrowFee. FINRA short-volume is
    intraday pressure; this is structural crowding."""
    if not isinstance(row, dict):
        return "unknown"

    def _num(*keys):
        for k in keys:
            try:
                v = float(row.get(k))
            except (TypeError, ValueError):
                continue
            if v >= 0:
                return v
        return None

    sf = _num("shortPercentOfFloat", "shortFloat", "short_float", "shortPercent")
    dtc = _num("daysToCover", "shortRatio", "days_to_cover")
    fee = _num("borrowFee", "borrow_fee", "fee")
    if sf is not None and sf > 1.0:
        sf = sf / 100.0
    extreme = (sf is not None and sf >= 0.25) or (dtc is not None and dtc >= 7.0) or (fee is not None and fee >= 50.0)
    high = (sf is not None and sf >= 0.15) or (dtc is not None and dtc >= 4.0) or (fee is not None and fee >= 20.0)
    if extreme:
        return "extreme"
    if high:
        return "high"
    return "normal"


def _fetch_finra_short_file() -> str:
    """Today's FINRA consolidated short-volume file (no-auth CDN). '' on failure."""
    import datetime as _d
    ymd = _d.date.today().strftime("%Y%m%d")
    try:
        r = httpx.get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt", timeout=10.0)
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        log.debug("finra file fetch: %s", e)
        return ""


def _finra_short_map(*, fetch=None, day=None) -> "dict[str, float]":
    """Cached {symbol: short_ratio} for today. {} unless THEMATIC_SHORT_OVERLAY."""
    if not _short_overlay_enabled():
        return {}
    import datetime as _d
    today = day or _d.date.today().isoformat()
    if _finra_short_cache.get("day") == today and _finra_short_cache.get("map"):
        return _finra_short_cache["map"]
    fetch = fetch or _fetch_finra_short_file
    try:
        m = _parse_finra_short_volume(fetch() or "")
    except Exception as e:
        log.debug("finra short overlay: %s", e)
        return {}
    if m:
        _finra_short_cache["day"], _finra_short_cache["map"] = today, m
    return m


_true_short_cache: dict = {"day": "", "map": {}}


def _fetch_fmp_short_interest(ticker: str) -> "dict":
    """Best-effort true short-interest payload. FMP endpoint names have varied
    across versions, so try stable and legacy paths. {} on any failure/no key."""
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return {}
    endpoints = (
        ("https://financialmodelingprep.com/stable/short-interest", {"symbol": ticker, "apikey": key}),
        ("https://financialmodelingprep.com/stable/short-interest-ratio", {"symbol": ticker, "apikey": key}),
        ("https://financialmodelingprep.com/api/v4/short-interest", {"symbol": ticker, "apikey": key}),
    )
    for url, params in endpoints:
        try:
            r = httpx.get(url, params=params, timeout=10.0)
            data = r.json()
            if isinstance(data, list) and data:
                return data[0] if isinstance(data[0], dict) else {}
            if isinstance(data, dict) and data:
                return data
        except Exception:
            continue
    return {}


def _true_short_interest_map(*, universe=None, fetch=None, day=None) -> "dict[str, str]":
    """{ticker: level}. Disabled unless THEMATIC_TRUE_SHORT_INTEREST. Non-additive."""
    if not _true_short_interest_enabled():
        return {}
    import datetime as _d
    today = day or _d.date.today().isoformat()
    if universe is None and _true_short_cache.get("day") == today and _true_short_cache.get("map"):
        return _true_short_cache["map"]
    fetch = fetch or _fetch_fmp_short_interest
    universe = universe if universe is not None else _discovery_universe()
    out: dict[str, str] = {}
    for tk in universe:
        try:
            level = _short_interest_level(fetch(tk) or {})
        except Exception:
            level = "unknown"
        if level in ("high", "extreme"):
            norm = _norm_ticker(tk)
            if norm:
                out[norm] = level
    if out and universe is None:
        _true_short_cache["day"], _true_short_cache["map"] = today, out
    return out


# ── Analyst signals (FMP grade changes + Finnhub recommendation trends) ──────
# Fresh analyst upgrades and a bullish recommendation skew are real, orthogonal
# CONFIRMATION of a thesis (Wall-Street agreeing with the crowd). Keys are
# already configured. Pure weight helpers = testable; the live fetch is gated.

def _fmp_grade_weight(actions: "list[dict]") -> int:
    """Net recent FMP grade actions → bullish weight 0..6. Upgrades add, downgrades
    subtract (a downgraded name is not confirmation). Pure."""
    up = down = 0
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        act = str(a.get("action", a.get("gradeAction", ""))).lower()
        if "up" in act:        # upgrade / up
            up += 1
        elif "down" in act:    # downgrade / down
            down += 1
    return max(0, min((up - down) * 2, 6))


def _recommendation_weight(trend: "dict") -> int:
    """Finnhub recommendation bucket {strongBuy,buy,hold,sell,strongSell} → bullish
    weight 0..6 from the net buy-vs-sell skew. Pure."""
    if not isinstance(trend, dict):
        return 0
    def _g(k):
        try:
            return max(0.0, float(trend.get(k, 0) or 0))
        except (TypeError, ValueError):
            return 0.0
    sb, b, h, s, ss = _g("strongBuy"), _g("buy"), _g("hold"), _g("sell"), _g("strongSell")
    total = sb + b + h + s + ss
    if total <= 0:
        return 0
    net_frac = ((2 * sb + b) - (2 * ss + s)) / (2 * total)   # ~[-1, 1]
    return max(0, min(int(round(net_frac * 6)), 6))


def _earnings_surprise_weight(rows: "list[dict]") -> int:
    """Recent positive earnings surprise / beat weight 0..6. Accepts FMP-style
    records with surprisePercentage or actual/estimated EPS fields."""
    best = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pct = row.get("surprisePercentage", row.get("surprise_percent", row.get("surprise")))
        try:
            pct_v = float(pct)
        except (TypeError, ValueError):
            try:
                actual = float(row.get("actualEarningResult", row.get("epsActual", row.get("actual"))))
                estimate = float(row.get("estimatedEarning", row.get("epsEstimated", row.get("estimate"))))
                pct_v = ((actual - estimate) / abs(estimate) * 100.0) if estimate else 0.0
            except (TypeError, ValueError):
                pct_v = 0.0
        best = max(best, pct_v)
    if best <= 0:
        return 0
    return max(1, min(int(round(best / 3.0)), 6))


def _price_target_weight(targets: "dict | list[dict]", price: "float | None" = None) -> int:
    """Consensus target upside weight 0..6. Needs current price; if FMP returns a
    target-only payload without price, caller can inject the quote separately."""
    row = targets[0] if isinstance(targets, list) and targets else targets
    if not isinstance(row, dict):
        return 0

    def _num(*keys):
        for k in keys:
            try:
                v = float(row.get(k))
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
        return None

    target = _num("targetConsensus", "target_consensus", "consensus", "priceTarget", "targetMedian", "target")
    px = price
    if px is None:
        px = _num("price", "lastPrice", "currentPrice")
    try:
        px = float(px)
    except (TypeError, ValueError):
        px = 0.0
    if not target or px <= 0:
        return 0
    upside = (target / px - 1.0) * 100.0
    if upside < 10:
        return 0
    return max(1, min(int(round(upside / 10.0)), 6))


def _analyst_enabled() -> bool:
    return os.getenv("THEMATIC_ANALYST", "false").strip().lower() in ("1", "true", "yes", "on")


def _fetch_fmp_grades(ticker: str) -> "list[dict]":
    """Recent FMP analyst grade actions for a ticker. [] on any failure / no key."""
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return []
    try:
        r = httpx.get(f"https://financialmodelingprep.com/api/v3/grade/{ticker}",
                      params={"apikey": key}, timeout=10.0)
        data = r.json()
        return data[:10] if isinstance(data, list) else []
    except Exception as e:
        log.debug("fmp grades %s: %s", ticker, e)
        return []


def _fetch_finnhub_recs(ticker: str) -> "dict":
    """Latest Finnhub recommendation-trend bucket for a ticker. {} on failure."""
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return {}
    try:
        r = httpx.get("https://finnhub.io/api/v1/stock/recommendation",
                      params={"symbol": ticker, "token": key}, timeout=10.0)
        data = r.json()
        return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    except Exception as e:
        log.debug("finnhub recs %s: %s", ticker, e)
        return {}


def _fetch_fmp_earnings_surprises(ticker: str) -> "list[dict]":
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return []
    endpoints = (
        ("https://financialmodelingprep.com/stable/earnings-surprises", {"symbol": ticker, "limit": 4, "apikey": key}),
        ("https://financialmodelingprep.com/api/v3/earnings-surprises/" + ticker, {"apikey": key}),
    )
    for url, params in endpoints:
        try:
            r = httpx.get(url, params=params, timeout=10.0)
            data = r.json()
            if isinstance(data, list):
                return data[:4]
        except Exception:
            continue
    return []


def _fetch_fmp_price_targets(ticker: str) -> "dict | list[dict]":
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return {}
    endpoints = (
        ("https://financialmodelingprep.com/stable/price-target-consensus", {"symbol": ticker, "apikey": key}),
        ("https://financialmodelingprep.com/stable/price-target-summary", {"symbol": ticker, "apikey": key}),
        ("https://financialmodelingprep.com/api/v4/price-target-consensus", {"symbol": ticker, "apikey": key}),
    )
    for url, params in endpoints:
        try:
            r = httpx.get(url, params=params, timeout=10.0)
            data = r.json()
            if data:
                return data
        except Exception:
            continue
    return {}


def _fetch_quote_price(ticker: str) -> "float | None":
    try:
        from tradingagents.data.quote_gateway import get_gateway
        gw = get_gateway()
        q = gw.get_quote(ticker) if gw else None
        return q.best.reference_price() if q else None
    except Exception:
        return None


async def _analyst_tickers(
    *,
    universe=None,
    fetch_grades=None,
    fetch_recs=None,
    fetch_earnings=None,
    fetch_targets=None,
    fetch_price=None,
) -> "dict[str, int]":
    """Source: bullish analyst confirmation as {ticker: weight} = FMP-grade +
    Finnhub-rec weight. Disabled unless THEMATIC_ANALYST. Fetchers default to the
    live endpoints (graceful []/{} without keys); injected in tests."""
    if not _analyst_enabled():
        return {}
    fetch_grades = fetch_grades or _fetch_fmp_grades
    fetch_recs = fetch_recs or _fetch_finnhub_recs
    fetch_earnings = fetch_earnings or _fetch_fmp_earnings_surprises
    fetch_targets = fetch_targets or _fetch_fmp_price_targets
    fetch_price = fetch_price or _fetch_quote_price
    universe = universe if universe is not None else _discovery_universe()
    out: dict[str, int] = {}
    for tk in universe:
        w = 0
        try:
            w += _fmp_grade_weight(await asyncio.to_thread(fetch_grades, tk) or [])
        except Exception:
            pass
        try:
            w += _recommendation_weight(await asyncio.to_thread(fetch_recs, tk) or {})
        except Exception:
            pass
        try:
            w += _earnings_surprise_weight(await asyncio.to_thread(fetch_earnings, tk) or [])
        except Exception:
            pass
        try:
            price = await asyncio.to_thread(fetch_price, tk)
            w += _price_target_weight(await asyncio.to_thread(fetch_targets, tk) or {}, price)
        except Exception:
            pass
        if w > 0:
            norm = _norm_ticker(tk)
            if norm:
                out[norm] = min(w, 12)
    return out


# ── Unusual options-flow confirmation (optional Tradier chain) ───────────────
def _options_flow_enabled() -> bool:
    return os.getenv("THEMATIC_OPTIONS_FLOW", "false").strip().lower() in ("1", "true", "yes", "on")


def _options_flow_weight(options: "list[dict]") -> int:
    """Bullish unusual-call-flow weight 0..8 from option-chain rows. This is not
    order flow tape; it is a conservative proxy using call volume, OI, and put/call
    skew from a current chain."""
    call_vol = put_vol = call_oi = put_oi = 0.0
    unusual = 0
    for opt in options or []:
        if not isinstance(opt, dict):
            continue
        typ = str(opt.get("option_type", opt.get("type", ""))).lower()
        try:
            vol = max(0.0, float(opt.get("volume", 0) or 0))
            oi = max(0.0, float(opt.get("open_interest", opt.get("openInterest", 0)) or 0))
        except (TypeError, ValueError):
            continue
        if typ == "call":
            call_vol += vol
            call_oi += oi
            if vol >= 500 and (oi <= 0 or vol / max(oi, 1.0) >= 0.75):
                unusual += 1
        elif typ == "put":
            put_vol += vol
            put_oi += oi
    total_vol = call_vol + put_vol
    if total_vol <= 0 or call_vol < 500:
        return 0
    call_share = call_vol / total_vol
    oi_skew = call_oi / max(call_oi + put_oi, 1.0)
    score = 0
    if call_share >= 0.65:
        score += 3
    if call_share >= 0.80:
        score += 2
    if oi_skew >= 0.60:
        score += 1
    score += min(unusual, 2)
    return max(0, min(score, 8))


def _fetch_tradier_expirations(ticker: str) -> "list[str]":
    token = os.getenv("TRADIER_API_TOKEN", "").strip()
    if not token:
        return []
    base = os.getenv("TRADIER_API_BASE", "https://api.tradier.com/v1").rstrip("/")
    try:
        r = httpx.get(
            f"{base}/markets/options/expirations",
            params={"symbol": ticker, "includeAllRoots": "true"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10.0,
        )
        data = r.json()
        dates = ((data.get("expirations") or {}).get("date") if isinstance(data, dict) else []) or []
        return dates if isinstance(dates, list) else [dates]
    except Exception as e:
        log.debug("tradier expirations %s: %s", ticker, e)
        return []


def _fetch_tradier_chain(ticker: str) -> "list[dict]":
    token = os.getenv("TRADIER_API_TOKEN", "").strip()
    if not token:
        return []
    expiration = os.getenv("TRADIER_OPTIONS_EXPIRATION", "").strip()
    if not expiration:
        dates = _fetch_tradier_expirations(ticker)
        expiration = dates[0] if dates else ""
    if not expiration:
        return []
    base = os.getenv("TRADIER_API_BASE", "https://api.tradier.com/v1").rstrip("/")
    try:
        r = httpx.get(
            f"{base}/markets/options/chains",
            params={"symbol": ticker, "expiration": expiration, "greeks": "false"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10.0,
        )
        data = r.json()
        opts = ((data.get("options") or {}).get("option") if isinstance(data, dict) else []) or []
        return opts if isinstance(opts, list) else [opts]
    except Exception as e:
        log.debug("tradier chain %s: %s", ticker, e)
        return []


async def _options_flow_tickers(*, universe=None, fetch_chain=None) -> "dict[str, int]":
    if not _options_flow_enabled():
        return {}
    fetch_chain = fetch_chain or _fetch_tradier_chain
    universe = universe if universe is not None else _discovery_universe()
    out: dict[str, int] = {}
    for tk in universe:
        try:
            w = _options_flow_weight(await asyncio.to_thread(fetch_chain, tk) or [])
        except Exception:
            w = 0
        if w > 0:
            norm = _norm_ticker(tk)
            if norm:
                out[norm] = w
    return out


# ── Discovery scanner (no-buzz price/volume breakouts — the IREN-$5 solver) ───
def _discovery_enabled() -> bool:
    return os.getenv("THEMATIC_DISCOVERY", "false").strip().lower() in ("1", "true", "yes", "on")


_DISCOVERY_SEED_UNIVERSE = [
    "IREN", "CIFR", "WULF", "APLD", "CORZ", "OKLO", "SMR", "NNE", "RGTI",
    "IONQ", "QBTS", "RCAT", "ONDS", "AVAV", "KTOS", "VRT", "MU", "ALAB",
]


def _discovery_universe_cap() -> int:
    """Bound default discovery breadth so the source does useful work inside the
    scan timeout. Set THEMATIC_DISCOVERY_MAX_UNIVERSE higher when the data fetcher
    is cached/batched."""
    try:
        return max(1, int(float(os.getenv("THEMATIC_DISCOVERY_MAX_UNIVERSE", "350") or 350)))
    except Exception:
        return 350


def _read_ticker_file(path: Path, *, limit: int) -> "list[str]":
    out: list[str] = []
    try:
        for line in path.read_text().splitlines():
            tk = _norm_ticker(line.strip().split(",")[0])
            if tk:
                out.append(tk)
                if len(out) >= limit:
                    break
    except Exception:
        return []
    return out


def _discovery_universe() -> "list[str]":
    """Tickers to price/volume-scan for breakouts independent of buzz. Env
    THEMATIC_DISCOVERY_UNIVERSE (comma list) wins. Otherwise use the repo's liquid
    ticker universe, capped for runtime, plus the IREN-class catalyst seeds so the
    no-buzz breakout scan is materially broader than a hand-picked watchlist."""
    raw = [x.strip().upper() for x in os.getenv("THEMATIC_DISCOVERY_UNIVERSE", "").split(",") if x.strip()]
    if raw:
        return [t for t in (_norm_ticker(x) for x in raw) if t]

    cap = _discovery_universe_cap()
    file_override = os.getenv("THEMATIC_DISCOVERY_UNIVERSE_FILE", "").strip()
    candidates: list[str] = []
    if file_override:
        candidates = _read_ticker_file(Path(file_override).expanduser(), limit=cap)
    if not candidates:
        for name in ("tickers_liquid.txt", "tickers_quality.txt", "all_tickers.txt"):
            candidates = _read_ticker_file(ROOT / name, limit=cap)
            if candidates:
                break

    seen: set[str] = set()
    out: list[str] = []
    for tk in [*_DISCOVERY_SEED_UNIVERSE, *candidates]:
        norm = _norm_ticker(tk)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


async def _discovery_tickers(*, fetch_bars=None, fetch_bench=None, universe=None) -> "dict[str, int]":
    """Source scraper: names breaking out on price+volume (no buzz needed) as
    {ticker: weight}. Disabled unless THEMATIC_DISCOVERY. Bars/bench injected for
    tests; bounded by the caller's per-source timeout."""
    if not _discovery_enabled():
        return {}
    fetch_bars = fetch_bars or _fetch_daily_bars
    if fetch_bench is None:
        def fetch_bench():
            return (_fetch_daily_bars("SPY") or {}).get("closes", [])
    universe = universe if universe is not None else _discovery_universe()
    try:
        from tradingagents.portfolio.discovery import is_discovery_candidate
    except Exception:
        return {}
    try:
        bench = await asyncio.to_thread(fetch_bench)
    except Exception:
        bench = []
    out: dict[str, int] = {}
    for tk in universe:
        try:
            bars = await asyncio.to_thread(fetch_bars, tk) or {}
            res = is_discovery_candidate(bars, bench)
            if res.get("qualifies"):
                # score 60→3 … 100→8: a real, price-confirmed standalone signal
                w = max(3, min(int((float(res["score"]) - 50) / 7), 8))
                norm = _norm_ticker(tk)
                if norm:
                    out[norm] = w
        except Exception as e:
            log.debug("discovery %s: %s", tk, e)
            continue
    return out


def _atr_stop_pct(price: float, atr: float, base_pct: float, *,
                  k: float = 2.0, floor_pct: float = 4.0, cap_pct: float = 15.0) -> float:
    """Volatility-aware stop distance (%). A flat % stop is simultaneously too
    tight on a high-ATR small-cap (shaken out of a winner) and too wide on a
    low-ATR mega-cap (oversized loss). Size the stop to k×ATR, clamped to
    [floor_pct, cap_pct]. Falls back to base_pct when ATR/price are unusable, so a
    missing ATR never changes behavior. Pure + testable."""
    import math as _m
    try:
        p, a = float(price), float(atr)
    except (TypeError, ValueError):
        return base_pct
    if not (_m.isfinite(p) and _m.isfinite(a)) or p <= 0 or a <= 0:
        return base_pct
    atr_pct = (k * a / p) * 100.0
    return max(floor_pct, min(atr_pct, cap_pct))


def _risk_sizing_enabled() -> bool:
    return os.getenv("THEMATIC_RISK_SIZING", "false").strip().lower() in ("1", "true", "yes", "on")


def _correlation_guard_enabled() -> bool:
    return os.getenv("THEMATIC_CORRELATION_GUARD", "false").strip().lower() in ("1", "true", "yes", "on")


def _correlation_guard_for_book(
    ticker: str,
    existing_tickers: "list[str]",
    *,
    fetch_bars=None,
    max_corr: "float | None" = None,
) -> "tuple[bool, str]":
    """Advisory concentration guard for thematic approvals. It is default-off and
    fail-open because missing price history should not corrupt approvals; when data
    exists, block adding a name whose recent returns are too correlated with the
    current book."""
    if not _correlation_guard_enabled() or not existing_tickers:
        return True, "correlation guard disabled or empty book"
    try:
        threshold = float(max_corr if max_corr is not None else os.getenv("THEMATIC_MAX_CORRELATION", "0.85"))
    except Exception:
        threshold = 0.85
    threshold = max(0.50, min(threshold, 0.99))
    fetch_bars = fetch_bars or _fetch_daily_bars
    try:
        from tradingagents.portfolio.correlation import max_correlation
        cand = (fetch_bars(ticker) or {}).get("closes", [])
        existing = {}
        for tk in existing_tickers:
            if str(tk).upper() == str(ticker).upper():
                continue
            closes = (fetch_bars(str(tk).upper()) or {}).get("closes", [])
            if closes:
                existing[str(tk).upper()] = closes
        res = max_correlation(cand, existing)
        mc = res.get("max_corr")
        if mc is None:
            return True, "correlation data unavailable"
        if float(mc) >= threshold:
            return False, f"{ticker} correlation {mc:.2f} with {res.get('with_ticker')} >= {threshold:.2f}"
        return True, f"correlation {mc:.2f} below {threshold:.2f}"
    except Exception as e:
        log.debug("correlation guard unavailable for %s: %s", ticker, e)
        return True, "correlation guard unavailable"


# ── Regime gate (adaptive buy threshold by market regime) ────────────────────
_regime_cache: dict = {"ts": 0.0, "mult": 1.0}


def _regime_gate_enabled() -> bool:
    return os.getenv("THEMATIC_REGIME_GATE", "false").strip().lower() in ("1", "true", "yes", "on")


def _fetch_spy_closes() -> "list[float]":
    """Daily SPY closes (~1y) for regime detection. [] on any failure → caller
    keeps the normal gate. Isolated + injectable for tests."""
    try:
        import yfinance as yf
        df = yf.download("SPY", period="1y", auto_adjust=True, progress=False)
        if df is None or df.empty:
            return []
        return [float(x) for x in df["Close"].squeeze().tolist()]
    except Exception as e:
        log.debug("regime SPY fetch failed: %s", e)
        return []


def _regime_threshold_multiplier(*, fetch=None, now=None) -> float:
    """Multiplier on the buy-score gate for the current market regime (1.0 when
    disabled or data missing → no behavior change; up to 1.5 in risk-off).
    Cached ~1h so the per-signal annotation is cheap."""
    if not _regime_gate_enabled():
        return 1.0
    import time as _t
    _now = now if now is not None else _t.time()
    if _now - _regime_cache.get("ts", 0.0) < 3600 and _regime_cache.get("mult"):
        return _regime_cache["mult"]
    closes = (fetch or _fetch_spy_closes)()
    if not closes:
        return 1.0  # missing data must not change behavior
    try:
        from tradingagents.portfolio.regime import assess_regime
        mult = float(assess_regime(spy_closes=closes)["threshold_multiplier"])
    except Exception as e:
        log.debug("regime assess failed: %s", e)
        return 1.0
    _regime_cache["ts"], _regime_cache["mult"] = _now, mult
    return mult


def _risk_pct_per_trade() -> float:
    """Account fraction risked if the stop is hit (env THEMATIC_RISK_PCT_PER_TRADE,
    default 1%, clamped to (0, 5])."""
    try:
        return max(0.05, min(5.0, float(os.getenv("THEMATIC_RISK_PCT_PER_TRADE", "1") or 1)))
    except ValueError:
        return 1.0


def _risk_based_shares(account_value: float, price: float, stop: float,
                       risk_pct: float, *, max_position_pct: float = 10.0) -> int:
    """Volatility-targeted share count: size so a stop-out loses ~risk_pct of the
    account (`shares = risk_budget / (entry − stop)`), bounded by the position cap.

    This makes *dollar risk* constant across names regardless of their volatility
    — the right way to size, vs equal-dollar which puts wildly different risk on a
    quiet mega-cap and a volatile small-cap. Returns 0 on unusable inputs (caller
    falls back to the dollar-allocation path). Pure + testable."""
    import math as _m
    try:
        av, p, st, rp = float(account_value), float(price), float(stop), float(risk_pct)
    except (TypeError, ValueError):
        return 0
    if not all(_m.isfinite(x) for x in (av, p, st, rp)):
        return 0
    if av <= 0 or p <= 0 or st <= 0 or st >= p or rp <= 0:
        return 0
    risk_per_share = p - st
    risk_budget = av * (rp / 100.0)
    shares = int(risk_budget / risk_per_share)
    cap_shares = int(av * (max_position_pct / 100.0) / p)   # never exceed position cap
    return max(0, min(shares, cap_shares))


def _real_atr(ticker: str, price: float) -> float:
    """Compute approximate 14-day ATR from yfinance. Falls back to 2% of price.

    Always returns a finite, non-negative value: a NaN/0/negative price or a
    NaN ATR (thin/bad data) would otherwise feed a garbage stop distance."""
    import math as _m
    try:
        px = float(price)
    except (TypeError, ValueError):
        px = 0.0
    if not _m.isfinite(px) or px <= 0:
        px = 0.0
    fallback = round(px * 0.02, 4)  # 0.0 when price is unusable
    try:
        import yfinance as yf
        df = yf.download(ticker, period="20d", auto_adjust=True, progress=False)
        if df.empty or "High" not in df or "Low" not in df or "Close" not in df:
            return fallback
        closes = df["Close"].squeeze()
        highs  = df["High"].squeeze()
        lows   = df["Low"].squeeze()
        prev_c = closes.shift(1)
        tr = (highs - lows).combine(
            (highs - prev_c).abs(), max
        ).combine((lows - prev_c).abs(), max)
        atr = float(tr.dropna().tail(14).mean())
        if not _m.isfinite(atr) or atr <= 0:
            return fallback
        return round(atr, 4)
    except Exception:
        return fallback


def _check_portfolio_circuit_breakers(state: dict, hil: dict, base_dollar: float) -> tuple[bool, str]:
    """Return (allowed, reason). Blocks trade if circuit breakers tripped.

    FAILS CLOSED: if core account state is non-finite/unreadable, block the trade
    rather than silently skipping the breakers. A NaN would make 'account_value
    > 0' / 'start_cash > 0' evaluate False (NaN compares False) and disable the
    heat and daily-loss breakers — the wrong direction for a safety gate."""
    import math as _m

    def _f(x, default=None):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        return v if _m.isfinite(v) else default

    cash = _f(state.get("cash", 10000))
    if cash is None:
        return False, "Circuit breaker: account cash is unreadable — blocking entry"
    settled = _f(state.get("settled_cash", cash))
    if settled is None:
        return False, "Circuit breaker: settled cash is unreadable — blocking entry"
    positions = state.get("positions", {}) or {}

    # Portfolio heat: total deployed value vs total cash. A single non-finite
    # position value fails closed rather than silently disabling the heat gate.
    total_value = 0.0
    for p in positions.values():
        ep = _f(p.get("entry_price", 0), default=None)
        sh = _f(p.get("shares", 0), default=None)
        if ep is None or sh is None:
            return False, "Circuit breaker: a position has unreadable price/shares — blocking entry"
        total_value += ep * sh
    account_value = cash + total_value
    if account_value > 0:
        heat_pct = total_value / account_value * 100
        max_heat = float(hil.get("max_portfolio_heat", 80.0))
        if heat_pct >= max_heat:
            return False, f"Portfolio heat {heat_pct:.1f}% ≥ max {max_heat:.0f}% — reduce positions first"

    # Daily loss limit
    # A1: state["closed_today"] keyed on close_date was never written by anything in the codebase.
    # Closed trades live in state["trades"] keyed by exit_time/pnl. Read from there.
    import datetime as _dtcb
    today = _dtcb.date.today().isoformat()
    start_cash = _f(state.get("starting_cash", 10000))
    if start_cash is None:
        return False, "Circuit breaker: starting cash is unreadable — blocking entry"
    realized_today = 0.0
    for t in state.get("trades", []) or []:
        if str(t.get("exit_time", t.get("close_date", "")))[:10] == today:
            pnl = _f(t.get("pnl", 0), default=0.0)  # a single bad pnl row → treat as 0, not NaN
            realized_today += pnl
    daily_loss_limit = float(hil.get("daily_loss_limit_pct", 3.0))
    if start_cash > 0 and realized_today < 0:
        loss_pct = abs(realized_today) / start_cash * 100
        if loss_pct >= daily_loss_limit:
            return False, f"Daily loss {loss_pct:.1f}% ≥ limit {daily_loss_limit:.0f}% — no new entries today"

    # Insufficient settled cash
    if base_dollar > settled:
        return False, f"Insufficient settled cash ${settled:.0f} < ${base_dollar:.0f} needed"

    return True, "ok"


class ApproveBody(BaseModel):
    dollar_amount: float | None = None  # None ⇒ auto-size from HIL base × conviction
    stop_pct: float | None = None
    target_pct: float | None = None
    fidelity_trade: bool = False   # if True, also route to Fidelity live trading
    execute_fidelity: bool = False  # must be explicitly True to actually submit
    fidelity_quote_time: str | None = None
    fidelity_quote_source: str | None = None
    fidelity_backup_sources: list[str] = Field(default_factory=list)
    fidelity_consensus_ok: bool | None = None
    fidelity_bid: float | None = None
    fidelity_ask: float | None = None
    fidelity_market_open: bool | None = None
    force: bool = False             # A10: bypass score/spike gate (explicit override)


def _fidelity_request_kwargs_from_approval(
    ticker: str,
    body: ApproveBody,
    *,
    stop_pct: float,
    target_pct: float,
    dollar_amount: float,
) -> dict:
    return {
        "ticker": ticker,
        "dollar_amount": dollar_amount,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "also_paper_trade": False,
        "execute": True,
        "quote_time": body.fidelity_quote_time,
        "quote_source": body.fidelity_quote_source,
        "backup_sources": body.fidelity_backup_sources,
        "consensus_ok": body.fidelity_consensus_ok,
        "bid": body.fidelity_bid,
        "ask": body.fidelity_ask,
        "market_open": body.fidelity_market_open,
    }


@router.post("/thematic/auto/signals/{signal_id}/approve")
async def approve_signal(
    signal_id: str,
    body: ApproveBody,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Approve a signal: add to thematic portfolio + inject paper trade.
    If fidelity_trade=True and execute_fidelity=True, also route to Fidelity."""
    data = _load_signals()
    sig = next((s for s in data["signals"] if s["id"] == signal_id), None)
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")

    stop_pct   = body.stop_pct   or sig.get("stop_pct", 7)
    target_pct = body.target_pct or sig.get("target_pct", 20)
    conviction = int(sig.get("conviction", 7))
    # A3: ticker must be defined BEFORE the R:R log block below — 'ticker' in dir() is
    # always False so the log always printed '?'. Move assignment here.
    ticker = sig["ticker"]

    # ── R:R gate ──────────────────────────────────────────────────────────────
    from web import users as _us
    _urec = _us.get_user(user["email"]) or {}
    hil_settings = _us.get_thematic_hil(_urec)
    min_rr = float(hil_settings.get("min_rr", 1.5))
    # Auto-size: use the explicit amount if supplied, else the user's HIL base
    # (conviction scaling applied below). Lets the UI approve with one click.
    base_dollar = (
        float(body.dollar_amount)
        if (body.dollar_amount and body.dollar_amount > 0)
        else float(hil_settings.get("dollar_amount", 500.0))
    )
    # ── Policy sizing ──────────────────────────────────────────────────────────
    # The scan stamped a size_factor (conviction × concentration-room against the
    # unified portfolio). Honor it as the single allocation for BOTH the paper and
    # the real-Fidelity leg; fall back to plain conviction scaling for legacy /
    # un-stamped signals. An explicit user-supplied dollar_amount is taken as-is.
    use_conviction_scale = bool(hil_settings.get("conviction_scale", True))
    _explicit_dollar = bool(body.dollar_amount and body.dollar_amount > 0)
    _size_factor = float(sig.get("size_factor") or 0)
    _adaptive = bool(hil_settings.get("adaptive_sizing", True))
    _sig_score = float(sig.get("score") or composite_score(conviction, float(sig.get("raw_score", 0) or 0), float(sig.get("sentiment", 0) or 0)))
    _acct_val = _thematic_account_value(user["email"]) if _adaptive else 0.0
    sizing_info: dict | None = None
    if _explicit_dollar or not use_conviction_scale:
        policy_alloc = base_dollar
    elif _adaptive and _acct_val > 0:
        # Portfolio-aware: size vs the WHOLE book — conviction × reward/risk ×
        # inverse-volatility × diversification, hard-capped per-position / per-sector
        # / portfolio-heat / cash. Falls back to the legacy adaptive sizer only if
        # portfolio context can't be built (never a silent naive oversize).
        _pa_dollars, sizing_info = await _portfolio_aware_dollar(
            user["email"], sig, _acct_val, target_pct, stop_pct, hil_settings
        )
        if sizing_info is None:                       # context unavailable → legacy
            policy_alloc = _adaptive_dollar(_acct_val, _sig_score, target_pct, hil_settings)
            log.info("Adaptive (fallback) size %s: acct=$%.0f score=%.0f → $%.0f",
                     ticker, _acct_val, _sig_score, policy_alloc)
        elif _pa_dollars and _pa_dollars > 0:
            policy_alloc = _pa_dollars
        elif body.force:                              # no room, but user forced
            policy_alloc = _adaptive_dollar(_acct_val, _sig_score, target_pct, hil_settings)
            log.info("Portfolio-aware found no room for %s but force=true → adaptive $%.0f",
                     ticker, policy_alloc)
        else:                                         # no room → block with the reason
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No portfolio room for {ticker}: "
                    f"{(sizing_info or {}).get('notes') or (sizing_info or {}).get('binding_constraint')}. "
                    f"Trim/rotate an existing position or pass force=true."
                ),
            )
    elif _size_factor > 0:
        policy_alloc = round(base_dollar * _size_factor, 2)
    else:
        policy_alloc = _conviction_dollar(base_dollar, conviction)
    rr = target_pct / stop_pct if stop_pct > 0 else 0
    rr_warning = None
    if rr < min_rr:
        if not body.force and not hil_settings.get("allow_rr_widening", False):
            raise HTTPException(
                status_code=400,
                detail=f"R:R {rr:.2f} < minimum {min_rr:.1f} — tighten stop or widen target (or pass force=true)"
            )
        # Widening allowed — persist flag for honest reporting
        rr_warning = f"R:R {rr:.2f} < min {min_rr:.1f} — target widened to {round(stop_pct * min_rr, 1)}% (rr_widened)"
        target_pct = round(stop_pct * min_rr, 1)
        log.info("R:R gate: widening allowed for %s — target → %.1f%%", ticker, target_pct)

    # ── Score gate ────────────────────────────────────────────────────────────
    # A10: previously only attached a warning — a 1-scan spike (score=3) trade went
    # through anyway. Enforce: block sub-threshold signals unless force=True.
    raw_score = float(sig.get("raw_score", 0) or 0)
    score_warning = None
    if raw_score < MIN_SIGNAL_SCORE and not body.force:
        raise HTTPException(
            status_code=400,
            detail=f"Signal score {raw_score:.0f} < threshold {MIN_SIGNAL_SCORE:.0f} — "
                   f"wait for confirmation or pass force=true to override"
        )
    if raw_score < MIN_SIGNAL_SCORE:
        score_warning = f"Score {raw_score:.0f} below threshold {MIN_SIGNAL_SCORE:.0f} — force override active"

    approval_warnings: list[str] = []

    # ── Runtime buy gates ───────────────────────────────────────────────────────
    # Mirror the GET /signals `will_buy` logic here so direct approval cannot bypass
    # regime-adjusted thresholds, short-pressure vetoes, or the breakout fast-lane.
    regime_mult = _regime_threshold_multiplier()
    eff_min_score = MIN_SIGNAL_SCORE * regime_mult
    if raw_score < eff_min_score:
        msg = f"Signal score {raw_score:.0f} < regime-adjusted threshold {eff_min_score:.0f}"
        if not body.force:
            raise HTTPException(status_code=400, detail=f"{msg} — pass force=true to override")
        approval_warnings.append(f"{msg} — force override active")

    short_map = _finra_short_map()
    if short_map:
        short_level = _short_pressure_level(short_map.get(str(ticker).upper()))
        if short_level in ("high", "extreme"):
            sig["short_pressure"] = short_level
        if short_level == "extreme":
            msg = "Extreme FINRA short-volume pressure veto"
            if not body.force:
                raise HTTPException(status_code=400, detail=f"{msg} — pass force=true to override")
            approval_warnings.append(f"{msg} — force override active")
    true_short_map = _true_short_interest_map(universe=[ticker])
    if true_short_map:
        structural_short_level = true_short_map.get(str(ticker).upper(), "unknown")
        if structural_short_level in ("high", "extreme"):
            sig["short_interest_pressure"] = structural_short_level
        if structural_short_level == "extreme":
            msg = "Extreme structural short-interest pressure veto"
            if not body.force:
                raise HTTPException(status_code=400, detail=f"{msg} — pass force=true to override")
            approval_warnings.append(f"{msg} — force override active")

    breakout_ok = False
    if sig.get("is_spike") and raw_score >= eff_min_score and _breakout_confirm_enabled():
        try:
            breakout_ok = _ticker_breakout(str(ticker))
        except Exception:
            breakout_ok = False
        if breakout_ok:
            sig["breakout_confirmed"] = True

    # ── Spike gate ────────────────────────────────────────────────────────────
    # A10: one-scan spikes must not trade — unconfirmed trend, no follow-through.
    # Block unless force=True or the breakout fast-lane supplies price confirmation.
    spike_warning = None
    if sig.get("is_spike") and not breakout_ok:
        if not body.force:
            raise HTTPException(
                status_code=400,
                detail="One-scan spike — unconfirmed trend. Wait for multi-scan confirmation or pass force=true"
            )
        spike_warning = "One-scan spike — force override active"

    # ── Step-up 2FA gate for the live-Fidelity leg ────────────────────────────
    # Paper-only approvals stay frictionless (require_admin). When this approval
    # will place a REAL order, require the same fresh X-Step-Up-Token as every
    # other live order endpoint — enforced BEFORE any paper/portfolio write so a
    # missing/expired token aborts cleanly with nothing booked.
    if body.fidelity_trade and body.execute_fidelity:
        from web.auth import enforce_step_up
        await enforce_step_up(request, user)

    # 1. Add to thematic portfolio
    from web.api.thematic_portfolio import _load, _save, _fetch_prices, DEFAULT_THEMES
    import datetime as _dt
    port = _load(user["email"])
    # ticker already defined above (A3 fix)
    if ticker not in port["positions"]:
        port["positions"][ticker] = {
            "ticker":      ticker,
            "name":        sig.get("name", ticker),
            "theme":       sig.get("theme", "future_tech"),
            "conviction":  sig.get("conviction", 7),
            "risk_level":  "high" if sig.get("conviction", 7) >= 8 else "medium",
            "category":    "speculative",
            "thesis":      sig.get("thesis", ""),
            "catalyst":    sig.get("catalyst", ""),
            "thesis_bull": sig.get("bull_case", ""),
            "thesis_bear": sig.get("bear_case", ""),
            "risk_warning":"Auto-picked via social momentum scan",
            "tags":        ["auto", "momentum"],
            "added_at":    _dt.datetime.now().isoformat(),
            "entry_price": 0,
            "shares":      0,
        }
        _save(user["email"], port)

    # 2. Paper trade injection
    from web.api.thematic_portfolio import (
        PAPER_STATE_FILE, THEMATIC_TRADES_FILE, _fetch_prices as _fp,
        _ensure_thematic_paper_state, THEMATIC_PAPER_START_CASH,
    )
    prices = _fp([ticker])
    price  = prices.get(ticker)
    result = {"portfolio_added": True, "paper_trade": None}
    if approval_warnings:
        result["approval_warnings"] = approval_warnings
    live_stop_pct = stop_pct
    live_alloc = policy_alloc
    if price and base_dollar > 0:
        # Conviction-scaled position size (auto-sized from HIL base when caller omits)
        # Policy-sized allocation (conviction × concentration), computed above.
        alloc = policy_alloc

        # Fetch ATR before acquiring lock (IO-bound, no state dependency)
        loop = asyncio.get_running_loop()
        atr = await loop.run_in_executor(None, _real_atr, ticker, price)
        # Volatility-aware stop (env THEMATIC_ATR_STOPS, default off → flat %).
        _eff_stop_pct = _atr_stop_pct(price, atr, stop_pct) if _atr_stops_enabled() else stop_pct
        stop    = round(price * (1 - _eff_stop_pct / 100), 4)
        target  = round(price * (1 + target_pct / 100), 4)
        # Position size. Risk-based (env THEMATIC_RISK_SIZING, default off): size so
        # a stop-out loses ~THEMATIC_RISK_PCT_PER_TRADE of the account (constant
        # dollar risk across names), bounded by the 10% cap. Else the policy dollar
        # alloc. Risk-sizing of 0 (unusable inputs) falls back to the alloc path.
        _risk_shares = _risk_based_shares(_acct_val, price, stop, _risk_pct_per_trade()) \
            if (_risk_sizing_enabled() and _acct_val > 0) else 0
        shares  = _risk_shares if _risk_shares > 0 else int(alloc / price)
        cost    = round(price * shares, 2)
        live_stop_pct = _eff_stop_pct
        if _risk_shares > 0 and cost > 0:
            live_alloc = cost
        now_iso = _dt.datetime.now().isoformat(timespec="seconds")
        today   = _dt.date.today().isoformat()
        alpha_tier = "A+" if conviction >= 9 else "A" if conviction >= 7 else "B" if conviction >= 5 else "C"

        # A11/P8: hold lock for entire read-modify-write of paper state so
        # concurrent approvals/auto-scans can't clobber each other.
        async with _paper_state_lock:
            PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _ensure_thematic_paper_state()
            state: dict = {}
            if PAPER_STATE_FILE.exists():
                try:
                    state = json.loads(PAPER_STATE_FILE.read_text())
                except Exception:
                    pass
            cash    = float(state.get("cash", THEMATIC_PAPER_START_CASH))
            settled = float(state.get("settled_cash", cash))

            # ── Portfolio brain cap checks ────────────────────────────────────
            open_positions = state.get("positions", {})
            thematic_count = sum(1 for p in open_positions.values() if p.get("_source", "").startswith("thematic"))
            theme_count    = sum(1 for p in open_positions.values() if p.get("sector") == "thematic" and p.get("theme") == sig.get("theme"))

            cap_reason = None
            if len(open_positions) >= PORTFOLIO_MAX_POSITIONS:
                cap_reason = f"Portfolio at max {PORTFOLIO_MAX_POSITIONS} positions"
            elif theme_count >= PORTFOLIO_MAX_PER_THEME:
                cap_reason = f"Theme '{sig.get('theme')}' at max {PORTFOLIO_MAX_PER_THEME} positions"
            elif thematic_count >= PORTFOLIO_MAX_SPECULATIVE:
                cap_reason = f"Thematic/speculative positions at max {PORTFOLIO_MAX_SPECULATIVE}"

            if not cap_reason and ticker not in open_positions:
                corr_ok, corr_reason = _correlation_guard_for_book(ticker, list(open_positions.keys()))
                if not corr_ok:
                    cap_reason = corr_reason

            # ── Circuit breakers ──────────────────────────────────────────────
            cb_ok, cb_reason = _check_portfolio_circuit_breakers(state, hil_settings, cost)
            if not cb_ok:
                cap_reason = cap_reason or cb_reason

            if shares > 0 and ticker not in open_positions and not cap_reason:
                open_positions[ticker] = {
                    "ticker": ticker, "shares": shares, "entry_price": price,
                    "stop": stop, "target": target, "entry_time": now_iso,
                    "signal_date": today, "score": float(conviction) * 10,
                    "alpha_tier": alpha_tier, "atr": atr,
                    "breakeven_moved": False, "peak_price": price, "scans_held": 0,
                    "trailing": False, "trail_pct": float(hil_settings.get("trail_pct", 20.0)),
                    "partial_sold": False, "defensive_trimmed": False, "scaled_in": False,
                    "sector": "thematic", "theme": sig.get("theme", "future_tech"),
                    "entry_date": today,
                    "funded_by_unsettled": False, "unsettled_settle_date": "",
                    "regime_at_entry": "thematic", "regime_score_at_entry": None,
                    "crash_risk_at_entry": None, "regime_confidence_at_entry": None,
                    "strategy_label": "thematic_momentum",
                    "thesis": sig.get("thesis", ""),
                    "catalyst": sig.get("catalyst", ""),
                    "hold_days": sig.get("hold_days", 5),
                    "exit_plan": f"Target {target_pct}%, stop {stop_pct}% (R:R {rr:.1f}), max {sig.get('hold_days',5)} days",
                    "entry_raw_score": float(sig.get("raw_score", 0) or 0),
                    "confirmed_scans": sig.get("scan_appearances", 1),
                    "_source": "thematic_auto",
                    # entry-time source attribution → adaptive source weights
                    "sources": {
                        k: float(v) for k, v in (sig.get("source_breakdown") or {}).items()
                        if isinstance(v, (int, float)) and v > 0
                    },
                }
                state["positions"]    = open_positions
                state["cash"]         = round(cash - cost, 4)
                state["settled_cash"] = round(settled - cost, 4)
                _atomic_write(PAPER_STATE_FILE, state)
                result["paper_trade"] = {
                    "shares": shares, "price": price, "cost": cost,
                    "stop": stop, "target": target, "rr": round(rr, 2),
                    "atr": atr, "conviction_scaled": use_conviction_scale,
                    "alloc_used": alloc,
                }
            elif cap_reason:
                result["paper_trade"] = None
                result["cap_reason"]  = cap_reason

    # Optional Fidelity live trade routing
    result["fidelity_trade"] = None
    if body.fidelity_trade and body.execute_fidelity:
        from tradingagents.compliance import LIVE_TRADING_HARD_BLOCKED, live_trading_enabled
        if LIVE_TRADING_HARD_BLOCKED:
            result["fidelity_trade"] = {"skipped": True, "reason": "LIVE_TRADING_HARD_BLOCKED=True"}
        elif not live_trading_enabled():
            result["fidelity_trade"] = {"skipped": True, "reason": "LIVE_TRADING_ENABLED not set"}
        else:
            try:
                from web.api.fidelity import (
                    _fidelity_thematic_trade_inner,
                    FidelityThematicTradeRequest,
                    _validate_account_number,
                    _get_order_lock,
                    _ORDER_LOCKS_META,
                )
                fid_body = FidelityThematicTradeRequest(
                    **_fidelity_request_kwargs_from_approval(
                        ticker,
                        body,
                        stop_pct=live_stop_pct,
                        target_pct=target_pct,
                        dollar_amount=live_alloc,
                    )
                )
                fid_account = _validate_account_number(None)
                lock_key = f"{user['email']}:{ticker}"
                order_lock = _get_order_lock(lock_key)
                if order_lock.locked():
                    result["fidelity_trade"] = {"skipped": True, "reason": "order already in progress"}
                else:
                    async with order_lock:
                        _ORDER_LOCKS_META[lock_key] = __import__("time").time()
                        fid_result = await _fidelity_thematic_trade_inner(fid_body, user, ticker, fid_account)
                    result["fidelity_trade"] = fid_result
                    log.info("Thematic HIL Fidelity trade executed: %s $%.0f", ticker, policy_alloc)
            except Exception as fid_err:
                log.warning("Thematic HIL Fidelity trade failed for %s: %s", ticker, fid_err)
                result["fidelity_trade"] = {"error": str(fid_err)}

    # Surface a REPLACE suggestion (propose-only — never auto-exits). The weakest
    # holding the policy flagged is offered as a separate, human-approved exit
    # through the compliance-gated Holdings Brain; approving this entry does NOT
    # close it.
    if sig.get("replace_target"):
        result["replace_suggestion"] = {
            "exit_ticker": sig["replace_target"],
            "note": (
                f"Policy suggests freeing capacity by exiting {sig['replace_target']}. "
                f"Approve that exit separately in the Holdings Brain tab — it was NOT closed."
            ),
        }

    # Mark signal approved
    sig["status"] = "approved"
    _save_signals(data)
    resp = {"ok": True, **result}
    warnings = [w for w in [score_warning, rr_warning, spike_warning] if w]
    if warnings:
        resp["warnings"] = warnings
    return resp


@router.post("/thematic/auto/signals/{signal_id}/skip")
async def skip_signal(signal_id: str, _user: dict = Depends(require_admin)):
    data = _load_signals()
    sig = next((s for s in data["signals"] if s["id"] == signal_id), None)
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")
    sig["status"] = "skipped"
    _save_signals(data)
    return {"ok": True}


@router.get("/thematic/auto/exit-check")
async def check_exits_dry(_user: dict = Depends(get_current_user)):
    """Dry-run: return positions that would be exited without closing them."""
    exits = await _check_thematic_exits(execute=False)
    return {"ok": True, "exits": exits, "count": len(exits)}


@router.post("/thematic/auto/exit-check")
async def execute_exits(_user: dict = Depends(require_admin)):
    """Execute exits: close thematic positions meeting stop/target/hold/buzz-collapse criteria."""
    exits = await _check_thematic_exits(execute=True)
    return {"ok": True, "executed": exits, "count": len(exits)}


@router.get("/thematic/auto/outcomes")
async def get_signal_outcomes(_user: dict = Depends(get_current_user)):
    """Signal-accuracy report from the outcome tracker: per-source learned weights
    and stats, plus hit-rate by score bucket (is a scored-85 pick actually better
    than a scored-70 one?). Read-only view of the learning loop's state."""
    from tradingagents.screening import signal_outcomes as so
    rows = so.load_rows(OUTCOMES_FILE)
    evaluated = [r for r in rows if so._row_outcome(r) is not None]

    buckets: dict[str, dict] = {}
    for r in evaluated:
        ret = so._row_outcome(r)
        s = float(r.get("score", 0) or 0)
        label = ("<40" if s < 40 else "40-60" if s < 60 else "60-80" if s < 80
                 else "80-100" if s < 100 else "100+")
        b = buckets.setdefault(label, {"n": 0, "wins": 0, "ret_sum": 0.0})
        b["n"] += 1
        b["wins"] += 1 if ret > 0 else 0
        b["ret_sum"] += ret
    calibration = {
        k: {"n": v["n"], "hit_rate": round(v["wins"] / v["n"], 3),
            "avg_ret_pct": round(v["ret_sum"] / v["n"] * 100, 2)}
        for k, v in sorted(buckets.items()) if v["n"] > 0
    }

    stats = so.compute_source_stats(evaluated, _closed_trades_for_weights())
    weights = so.load_weights(SOURCE_WEIGHTS_FILE)
    return {
        "ok": True,
        "rows_total": len(rows),
        "rows_evaluated": len(evaluated),
        "rows_pending": len(rows) - len(evaluated),
        "calibration_by_score": calibration,
        "source_stats": {
            k: {"n_eff": round(v.n_eff, 2), "hit_rate": round(v.hit_rate, 3),
                "avg_ret_pct": round(v.avg_ret * 100, 2),
                "weight": weights.get(k, 1.0)}
            for k, v in sorted(stats.items(), key=lambda kv: -kv[1].n_eff)
        },
        "adaptive_weights_enabled": _adaptive_weights_enabled(),
    }


@router.get("/thematic/auto/exit-log")
async def get_exit_log(_user: dict = Depends(get_current_user), limit: int = 50):
    """Return recent thematic exit history."""
    entries: list[dict] = []
    if EXIT_LOG_FILE.exists():
        try:
            lines = EXIT_LOG_FILE.read_text().splitlines()
            for line in lines[-limit:]:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    return {"ok": True, "exits": list(reversed(entries)), "count": len(entries)}


@router.get("/thematic/auto/score-history")
async def get_score_history(_user: dict = Depends(get_current_user), limit: int = 20):
    """Return last N scan score snapshots (ticker rankings + source breakdown per scan)."""
    lines: list[dict] = []
    if SCORE_HISTORY_FILE.exists():
        try:
            raw = SCORE_HISTORY_FILE.read_text().splitlines()
            for line in raw[-limit:]:
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    return {"ok": True, "history": list(reversed(lines)), "count": len(lines)}


@router.get("/thematic/auto/brave-usage")
async def get_brave_usage(_user: dict = Depends(get_current_user)):
    """Return Brave Search API monthly usage vs 1000-req limit."""
    used, ym = _brave_usage()
    key_set = bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())
    return {
        "ok": True,
        "month": ym,
        "used": used,
        "limit": _BRAVE_MONTHLY_LIMIT,
        "remaining": max(0, _BRAVE_MONTHLY_LIMIT - used),
        "pct_used": round(used / _BRAVE_MONTHLY_LIMIT * 100, 1),
        "api_key_set": key_set,
    }


@router.get("/thematic/auto/trending")
async def get_trending(_user: dict = Depends(get_current_user)):
    """Raw trending data without AI analysis — quick preview. Uses all sources."""
    _reset_social_intent()   # sources write per-scan intent; reset before the gather
    async with httpx.AsyncClient() as client:
        gather_results = await asyncio.gather(
            _reddit_tickers(client), _ddg_tickers(client), _yahoo_trending(client),
            _google_news_tickers(client), _trusted_twitter_tickers(client),
            _yahoo_movers(client), _brave_tickers(client),
            return_exceptions=True,
        )
    def _s(r, d): return r if not isinstance(r, Exception) else d
    reddit  = _s(gather_results[0], {})
    ddg     = _s(gather_results[1], {})
    yahoo   = _s(gather_results[2], [])
    gnews   = _s(gather_results[3], {})
    ttwit   = _s(gather_results[4], {})
    ymovers = _s(gather_results[5], {})
    brave   = _s(gather_results[6], {})
    ranked, breakdown = await _merge_signals(
        reddit, ddg, yahoo, trusted_twitter=ttwit,
        google_news=gnews, yahoo_movers=ymovers, brave=brave,
    )
    active = [s for s in ["reddit","ddg","google_news","trusted_twitter","yahoo_movers","brave"]
              if gather_results[["reddit","ddg","yahoo","google_news","trusted_twitter","yahoo_movers","brave"].index(s)] and
              not isinstance(gather_results[["reddit","ddg","yahoo","google_news","trusted_twitter","yahoo_movers","brave"].index(s)], Exception)]
    return {
        "ok": True,
        "trending": [{"ticker": t, "score": s, "sources": list(breakdown.get(t,{}).keys())} for t, s in ranked],
        "yahoo_trending": yahoo[:10],
        "top_reddit": sorted(reddit.items(), key=lambda x: x[1], reverse=True)[:10],
        "sources": ["Reddit", "DDG", "Google News", "Trusted Twitter", "Yahoo Movers", "Brave Search"],
    }


# ── Thematic HIL settings ─────────────────────────────────────────────────────

class ThematicHilBody(BaseModel):
    enabled: bool | None = None
    fidelity_trade: bool | None = None
    dollar_amount: float | None = None
    sms_notify: bool | None = None
    auto_trade_paper: bool | None = None
    auto_trade_fidelity: bool | None = None
    min_rr: float | None = None
    max_portfolio_heat: float | None = None
    daily_loss_limit_pct: float | None = None
    conviction_scale: bool | None = None


@router.get("/thematic/auto/hil-settings")
async def get_thematic_hil_settings(user: dict = Depends(get_current_user)):
    from web import users as user_store
    rec = user_store.get_user(user["email"]) or {}
    return {"ok": True, "hil": user_store.get_thematic_hil(rec)}


@router.post("/thematic/auto/hil-settings")
async def save_thematic_hil_settings(
    body: ThematicHilBody,
    user: dict = Depends(get_current_user),
):
    from web import users as user_store
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "dollar_amount" in update and (update["dollar_amount"] <= 0 or update["dollar_amount"] > 50000):
        raise HTTPException(status_code=400, detail="dollar_amount must be 1–50000")
    rec = user_store.set_thematic_hil(user["email"], update)
    return {"ok": True, "hil": user_store.get_thematic_hil(rec)}
