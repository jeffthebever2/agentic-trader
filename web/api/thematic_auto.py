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
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from web.auth import get_current_user

log = logging.getLogger("thematic_auto")

ROOT   = Path(__file__).parent.parent.parent
TMP    = ROOT / "tmp"
SIGNALS_FILE   = TMP / "thematic_signals.json"
STATUS_FILE    = TMP / "thematic_scan_status.json"
PAPER_STATE_FILE = ROOT / "tmp" / "paper_trading_today" / "unified_brain" / "state.json"

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

def extract_tickers(text: str) -> list[str]:
    found = _TICKER_RE.findall(text.upper())
    return [t for t in found if t not in _SKIP and len(t) >= 2]


# ── Reddit scraper (no OAuth — public JSON) ───────────────────────────────────

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket", "SecurityAnalysis"]

async def _reddit_tickers(client: httpx.AsyncClient, limit: int = 25) -> dict[str, int]:
    """Return ticker → mention count across hot posts."""
    counts: dict[str, int] = {}
    headers = {"User-Agent": "AgenticTrader/1.0 (stock research tool)"}
    for sub in SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
            r = await client.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            posts = r.json().get("data", {}).get("children", [])
            for p in posts:
                d = p.get("data", {})
                blob = f"{d.get('title','')} {d.get('selftext','')}"
                for t in extract_tickers(blob):
                    counts[t] = counts.get(t, 0) + 1
        except Exception as e:
            log.warning("Reddit %s: %s", sub, e)
    return counts


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
                            for t in extract_tickers(blob):
                                _counts[t] = _counts.get(t, 0) + 1
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
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 5:
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
                    blob  = f"{title.text if title else ''} {desc.text if desc else ''}"
                    for t in extract_tickers(blob):
                        counts[t] = counts.get(t, 0) + 1
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
                    for t in extract_tickers(blob):
                        counts[t] = counts.get(t, 0) + 2
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

TRUSTED_TWITTER_FEEDS = [
    "https://rss.app/feeds/1cNg7qqGww0N3h0c.xml",
    "https://rss.app/feeds/lYyMa8FTax9n3NC4.xml",
]

async def _trusted_twitter_tickers(client: httpx.AsyncClient) -> dict[str, int]:
    """Parse rss.app-proxied Twitter feeds from trusted traders. Cashtags weighted highest."""
    counts: dict[str, int] = {}
    try:
        from bs4 import BeautifulSoup
        for url in TRUSTED_TWITTER_FEEDS:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code != 200:
                    log.warning("Trusted Twitter RSS %s: %s", url[-20:], r.status_code)
                    continue
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item"):
                    title = item.find("title")
                    desc  = item.find("description")
                    blob  = f"{title.text if title else ''} {desc.text if desc else ''}"
                    # Cashtags = explicit trader picks = highest signal
                    for m in _CASHTAG_RE.findall(blob.upper()):
                        if m not in _SKIP and 2 <= len(m) <= 5 and m.isalpha():
                            counts[m] = counts.get(m, 0) + 5
                    # Plain ticker-like words from title only (lower signal)
                    if title:
                        for t in extract_tickers(title.text):
                            counts[t] = counts.get(t, 0) + 1
                log.info("Trusted Twitter RSS %s: %d tickers", url[-20:], len(counts))
            except Exception as e:
                log.warning("Trusted Twitter RSS %s: %s", url[-20:], e)
    except Exception as e:
        log.warning("Trusted Twitter feeds: %s", e)
    return counts


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
                        for t in extract_tickers(blob):
                            counts[t] = counts.get(t, 0) + 1
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
                # Cashtags ($NVDA) = high confidence
                for t in _CASH.findall(text):
                    if t not in _SKIP and 2 <= len(t) <= 5:
                        counts[t] = counts.get(t, 0) + 4
                # Plain text tickers: 4+ chars only (cuts PART/WALL/HELP noise)
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 4:
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
                # Cashtags ($NVDA) = high confidence signal
                for t in _CASH.findall(text):
                    if t not in _SKIP and 2 <= len(t) <= 5:
                        counts[t] = counts.get(t, 0) + 3
                # Plain text: 4+ chars to avoid PART/WALL/HELP/WORLD noise
                for t in extract_tickers(text):
                    if t not in _SKIP and len(t) >= 4:
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
) -> tuple[list[tuple[str, float]], dict[str, dict[str, float]]]:
    """Combine all sources into ranked list + per-source breakdown.

    Returns:
        ([(ticker, score), ...], {ticker: {source: contribution, ...}})
    """
    scores: dict[str, float] = {}
    breakdown: dict[str, dict[str, float]] = {}
    source_presence: dict[str, set] = {}  # ticker → set of source names

    def _add(ticker: str, source: str, pts: float) -> None:
        scores[ticker] = scores.get(ticker, 0.0) + pts
        breakdown.setdefault(ticker, {})[source] = breakdown.get(ticker, {}).get(source, 0.0) + pts
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
        _add(t, "trusted_twitter", n * 3.5)
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

    # Multi-source confirmation bonus: +3 per source beyond the first (max +15)
    for t, src_set in source_presence.items():
        n_sources = len(src_set)
        if n_sources >= 2:
            bonus = min((n_sources - 1) * 3.0, 15.0)
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

    # Drop crypto / junk, require minimum buzz
    _CRYPTO = {"BTC-USD","ETH-USD","SOL-USD","XRP-USD","DOGE-USD","BTC","ETH","SOL"}
    scores = {t: s for t, s in scores.items()
              if len(t) >= 2 and t not in _CRYPTO and s >= 2}
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
    # are likely OTC/foreign stocks — require at least one quality source OR score >= 60
    _QUALITY_SOURCES = {"trusted_twitter","reddit","seeking_alpha","google_news",
                        "insider","marketaux","twitter","ddg","brave","scan_memory"}
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
        target_pct = float(pick.get("target_pct", 20))
        target_pct = max(5.0, min(100.0, target_pct))
    except (TypeError, ValueError):
        target_pct = 20.0
    try:
        stop_pct = float(pick.get("stop_pct", 7))
        stop_pct = max(2.0, min(25.0, stop_pct))
    except (TypeError, ValueError):
        stop_pct = 6.0
    try:
        hold_days = int(float(pick.get("hold_days", 5)))
        hold_days = max(1, min(30, hold_days))
    except (TypeError, ValueError):
        hold_days = 5
    return {
        "ticker":     ticker,
        "name":       str(pick.get("name", ticker))[:80],
        "conviction": conviction,
        "theme":      theme,
        "thesis":     str(pick.get("thesis", ""))[:500],
        "catalyst":   str(pick.get("catalyst", ""))[:300],
        "bull_case":  str(pick.get("bull_case", ""))[:300],
        "bear_case":  str(pick.get("bear_case", ""))[:300],
        "target_pct": target_pct,
        "stop_pct":   stop_pct,
        "hold_days":  hold_days,
    }


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

    prompt = f"""You are an aggressive momentum stock analyst. You track social buzz, news catalysts, AND insider/congressional buying as conviction signals.

Trending tickers by combined signal score (Reddit buzz + news mentions + insider buys + congressional trades):
{ticker_str}

Recent news headlines:
{news_text}

Analyze the social momentum, news catalysts, insider buying patterns, and sector themes.
Stocks with BOTH social momentum AND insider cluster buying get highest conviction.
Pick the TOP 6 highest-conviction plays from the trending list.

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
    "target_pct": 25,
    "stop_pct": 8,
    "hold_days": 7
  }}
]

theme must be one of: ai_leaders, ai_infrastructure, optical_network, memory_hbm, datacenter_power, nuclear_energy, space_defense, quantum_future, critical_minerals, reshoring, fintech_consumer, future_tech
conviction: 1-10, target_pct: 10-100 (be aggressive — momentum plays can run 20-50%+), stop_pct: 5-15, hold_days: 3-21"""

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
        if not isinstance(picks, list):
            picks = []
        log.info("CF AI returned %d picks", len(picks))
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
    prompt = f"Trending tickers: {ticker_str}\nNews: {news_text}\nPick TOP 6 momentum plays. Return JSON array only with fields: ticker, conviction(1-10), theme, name, thesis, catalyst, bull_case, bear_case, target_pct, stop_pct, hold_days."
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.4, "max_tokens": 2000},
            )
        content = r.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```[a-z]*\n?", "", content); content = re.sub(r"\n?```$", "", content)
        m = re.search(r"\[.*\]", content, re.DOTALL)
        picks = json.loads(m.group(0) if m else content)
        return picks if isinstance(picks, list) else []
    except Exception as e:
        log.error("OpenRouter fallback failed: %s", e)
        return []


# ── Scan orchestrator ─────────────────────────────────────────────────────────

_scan_lock = asyncio.Lock()
# A11/P8: shared lock for all PAPER_STATE_FILE read-modify-write operations.
# os.replace is crash-safe but doesn't prevent concurrent RMW clobbers.
# Both thematic_auto and thematic_portfolio import this lock to serialize
# all paper-state mutations across endpoints and the auto-scan loop.
_paper_state_lock = asyncio.Lock()

SCORE_HISTORY_FILE = TMP / "thematic_score_history.jsonl"
_SCORE_HISTORY_MAX = 500  # max lines before pruning

# Portfolio brain caps
PORTFOLIO_MAX_POSITIONS = 15
PORTFOLIO_MAX_PER_THEME = 3
PORTFOLIO_MAX_SPECULATIVE = 8

# Minimum raw buzz score for a signal to be considered a real buy candidate.
# Raw scores are unbounded (NVDA at 100 Reddit mentions alone = 200pts).
# Typical qualifying signal: 40-150. Strong signal: 150+. Very strong: 300+.
MIN_SIGNAL_SCORE: float = 40.0

# Buzz decay threshold: if current scan score drops to < this fraction of
# the score at entry, trigger a buzz_decay exit (even if stop/target not hit).
BUZZ_DECAY_RATIO: float = 0.40


def _buzz_tier(score: float) -> str:
    """Human-readable tier for an unbounded raw buzz score."""
    if score >= 300: return "🔥 Very Strong"
    if score >= 150: return "Strong"
    if score >= 80:  return "Moderate"
    if score >= 40:  return "Weak"
    return "Low"


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
                for t, s in rec.get("ranked", []):
                    ticker_scores.setdefault(t, []).append(s * weight)
            except Exception:
                continue

        # Weighted average, normalized so max historical score ≈ 30pt bonus cap
        if not ticker_scores:
            return {}
        raw = {t: sum(scores) / n for t, scores in ticker_scores.items()}
        max_raw = max(raw.values()) if raw else 1.0
        # Scale so the strongest historically-trending ticker gets ~30pts bonus
        return {t: round(v / max_raw * 30.0, 1) for t, v in raw.items()}
    except Exception:
        return {}


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


async def _notify_thematic_hil_pending(count: int) -> None:
    """Send SMS to all users who have thematic HIL + sms_notify enabled."""
    try:
        from web import users as user_store
        from scripts.sms_alerts import send_sms
        dashboard_url = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org")
        msg = (
            f"Agentic Trader: {count} new thematic signal{'s' if count != 1 else ''} "
            f"awaiting your approval. Review at {dashboard_url}/#hil"
        )
        all_users = user_store.list_users() if hasattr(user_store, "list_users") else []
        for rec in all_users:
            thil = user_store.get_thematic_hil(rec)
            if not thil.get("enabled") or not thil.get("sms_notify"):
                continue
            phone = (rec.get("phone_number") or os.getenv("PAPER_SMS_NUMBER", "")).strip()
            if not phone:
                continue
            try:
                await asyncio.to_thread(send_sms, phone, msg)
                log.info("Thematic HIL SMS sent to %s", rec.get("email", "?"))
            except Exception as sms_err:
                log.warning("Thematic HIL SMS failed for %s: %s", rec.get("email", "?"), sms_err)
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

        reason = None
        if stop > 0 and price <= stop:
            reason = "stop_hit"
        elif price >= target:
            reason = "target_hit"
        elif age_days >= hold_days:
            reason = "max_hold_exceeded"
        elif age_days >= 2 and latest_scores and ticker not in latest_scores:
            reason = "buzz_collapse"
        elif age_days >= 1 and latest_scores:
            entry_raw = float(pos.get("entry_raw_score", 0) or 0)
            current_raw = latest_scores.get(ticker, 0)
            if entry_raw > 0 and current_raw < entry_raw * BUZZ_DECAY_RATIO:
                reason = "buzz_decay"

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


async def _auto_execute_confirmed_signals(signals: list[dict]) -> None:
    """For each user with auto_trade_paper=True, auto-approve confirmed signals above MIN_SIGNAL_SCORE."""
    try:
        from web import users as user_store
        all_users = user_store.list_users() if hasattr(user_store, "list_users") else []
        for rec in all_users:
            hil = user_store.get_thematic_hil(rec)
            if not hil.get("auto_trade_paper"):
                continue
            email = (rec.get("email") or "").strip()
            if not email:
                continue
            dollar_amount = float(hil.get("dollar_amount", 500.0))
            user_mock = {"email": email}
            for sig in signals:
                if not sig.get("confirmed"):
                    continue
                if float(sig.get("raw_score", 0) or 0) < MIN_SIGNAL_SCORE:
                    continue
                try:
                    body = ApproveBody(dollar_amount=dollar_amount)
                    await approve_signal(sig["id"], body, user_mock)
                    log.info("Auto-trade paper: approved %s for %s ($%.0f)", sig["ticker"], email, dollar_amount)
                except HTTPException as he:
                    log.info("Auto-trade paper skip %s for %s: %s", sig["ticker"], email, he.detail)
                except Exception as e:
                    log.warning("Auto-trade paper error %s for %s: %s", sig["ticker"], email, e)
    except Exception as e:
        log.warning("_auto_execute_confirmed_signals: %s", e)


async def _run_scan() -> None:
    async with _scan_lock:
        has_marketaux = bool(os.getenv("MARKETAUX_API_TOKEN", "").strip())
        source_label = "Reddit · Brave · PR Releases · Finviz · Yahoo Movers · RSS News · Google News · SA · StockAnalysis · Twitter · Insider"
        if has_marketaux:
            source_label += " · Marketaux"
        _set_status("running", f"Scraping {source_label}...")
        try:
            async with httpx.AsyncClient() as client:
                gather_results = await asyncio.gather(
                    _reddit_tickers(client),          # 0
                    _ddg_tickers(client),              # 1
                    _yahoo_trending(client),           # 2
                    _google_news_tickers(client),      # 3
                    _seeking_alpha_tickers(client),    # 4
                    _stockanalysis_trending(client),   # 5
                    _marketaux_tickers(client),        # 6
                    _insider_tickers(client),          # 7
                    _trusted_twitter_tickers(client),  # 8
                    _stocktwits_trending(client),      # 9
                    _finviz_tickers(client),           # 10
                    _rss_tickers(client),              # 11
                    _alphavantage_movers(client),      # 12
                    _yahoo_movers(client),             # 13
                    _brave_tickers(client),            # 14
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
            twitter: dict[str, int] = {}

            _SOURCE_NAMES = ["reddit","ddg","yahoo","google_news","seeking_alpha",
                             "stockanalysis","marketaux","insider","trusted_twitter",
                             "stocktwits","finviz","rss_news","av_movers","yahoo_movers","brave"]
            for i, name in enumerate(_SOURCE_NAMES):
                if isinstance(gather_results[i], Exception):
                    log.warning("Source %s exception: %s", name, gather_results[i])

            _set_status("running", "Ranking tickers...")
            ranked, source_breakdown = await _merge_signals(
                reddit, ddg, yahoo, twitter,
                google_news, seeking_alpha, stockanalysis, marketaux_res, insider_res,
                trusted_twitter, stocktwits_res, finviz_res, rss_res, av_res, yahoo_movers_res,
                brave_res,
            )
            if not ranked:
                _set_status("done", "No tickers found")
                return

            # Collect news blobs for AI context (use fresh variable, NOT gather_results)
            news_blobs: list[str] = []
            try:
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS
                with DDGS() as ddg_inst:
                    for ticker, _ in ranked[:10]:
                        try:
                            nr = list(ddg_inst.news(f"{ticker} stock news", max_results=3))
                            for item in nr:
                                news_blobs.append(item.get("title", ""))
                        except Exception:
                            pass
            except Exception:
                pass

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

            # Replace all pending signals with fresh results from this scan
            data = _load_signals()
            _already_pending = {s["ticker"] for s in data["signals"] if s.get("status") == "pending"}
            data["signals"] = [s for s in data["signals"] if s.get("status") != "pending"]
            now = time.time()
            score_dict = dict(ranked)
            seen: set[str] = set()
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

                c = consistency.get(t, {})
                is_spike = c.get("is_spike", True)   # default True = treat new as spike
                appearances = c.get("appearances", 1)
                confirmed = c.get("confirmed", False)

                data["signals"].append({
                    "id":           f"{t}_{int(now)}",
                    "ticker":       t,
                    "name":         pick["name"],
                    "conviction":   pick["conviction"],
                    "theme":        pick["theme"],
                    "thesis":       pick["thesis"],
                    "catalyst":     pick["catalyst"],
                    "bull_case":    pick["bull_case"],
                    "bear_case":    pick["bear_case"],
                    "target_pct":   pick["target_pct"],
                    "stop_pct":     pick["stop_pct"],
                    "hold_days":    pick["hold_days"],
                    "status":       "pending",
                    "source":       "auto_scan",
                    "ts":           scan_ts,
                    "raw_score":    score_dict.get(t, 0),
                    "source_breakdown": source_breakdown.get(t, {}),
                    "is_spike":     is_spike,
                    "confirmed":    confirmed,
                    "scan_appearances": appearances,
                })

            # Trim old non-pending signals (keep last 50)
            pending   = [s for s in data["signals"] if s.get("status") == "pending"]
            history   = [s for s in data["signals"] if s.get("status") != "pending"][-50:]
            data["signals"] = pending + history
            data["last_scan"] = scan_ts
            _save_signals(data)

            # SMS notify users who have thematic HIL enabled + sms_notify=True
            if pending:
                asyncio.create_task(_notify_thematic_hil_pending(len(pending)))

            # Auto-trade: execute confirmed signals for users with auto_trade_paper=True
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


@router.post("/thematic/auto/scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    if status.get("status") == "running":
        return {"ok": True, "message": "Scan already running", "status": "running"}
    background_tasks.add_task(_run_scan)
    return {"ok": True, "message": "Scan started", "status": "running"}


@router.get("/thematic/auto/status")
async def scan_status(_user: dict = Depends(get_current_user)):
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"status": "idle", "detail": "No scan run yet", "ts": 0}


@router.get("/thematic/auto/signals")
async def get_signals(_user: dict = Depends(get_current_user)):
    data = _load_signals()
    pending = [s for s in data["signals"] if s.get("status") == "pending"]
    # Annotate each signal with buy eligibility, spike status, and scan history
    for sig in pending:
        rs = float(sig.get("raw_score", 0) or 0)
        is_spike  = sig.get("is_spike", True)
        confirmed = sig.get("confirmed", False)
        appearances = sig.get("scan_appearances", 1)
        # will_buy: must clear score threshold AND be confirmed (2+ scans)
        # Spike-only signals are shown but flagged — user can still approve manually
        sig["will_buy"]        = (rs >= MIN_SIGNAL_SCORE) and (not is_spike)
        sig["score_threshold"] = MIN_SIGNAL_SCORE
        sig["buzz_tier"]       = _buzz_tier(rs)
        sig["is_spike"]        = is_spike
        sig["confirmed"]       = confirmed
        sig["scan_appearances"]= appearances
    return {
        "ok": True,
        "signals": pending,
        "last_scan": data.get("last_scan"),
    }


def _conviction_dollar(base: float, conviction: int) -> float:
    """Scale position size by conviction: 10=1.5×, 8=1.2×, 6=1.0×, 4=0.7×, 1=0.4×."""
    scale = 0.4 + (conviction - 1) / 9.0 * 1.1  # linear 0.4→1.5
    return round(base * scale, 2)


def _real_atr(ticker: str, price: float) -> float:
    """Compute approximate 14-day ATR from yfinance. Falls back to 2% of price."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period="20d", auto_adjust=True, progress=False)
        if df.empty or "High" not in df or "Low" not in df or "Close" not in df:
            return round(price * 0.02, 4)
        closes = df["Close"].squeeze()
        highs  = df["High"].squeeze()
        lows   = df["Low"].squeeze()
        prev_c = closes.shift(1)
        tr = (highs - lows).combine(
            (highs - prev_c).abs(), max
        ).combine((lows - prev_c).abs(), max)
        atr = float(tr.dropna().tail(14).mean())
        return round(atr if atr > 0 else price * 0.02, 4)
    except Exception:
        return round(price * 0.02, 4)


def _check_portfolio_circuit_breakers(state: dict, hil: dict, base_dollar: float) -> tuple[bool, str]:
    """Return (allowed, reason). Blocks trade if circuit breakers tripped."""
    cash       = float(state.get("cash", 10000))
    settled    = float(state.get("settled_cash", cash))
    positions  = state.get("positions", {})

    # Portfolio heat: total deployed value vs total cash
    total_value = sum(
        float(p.get("entry_price", 0)) * float(p.get("shares", 0))
        for p in positions.values()
    )
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
    start_cash = float(state.get("starting_cash", 10000))
    realized_today = sum(
        float(t.get("pnl", 0))
        for t in state.get("trades", [])
        if str(t.get("exit_time", t.get("close_date", "")))[:10] == today
    )
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
    dollar_amount: float = 500.0
    stop_pct: float | None = None
    target_pct: float | None = None
    fidelity_trade: bool = False   # if True, also route to Fidelity live trading
    execute_fidelity: bool = False  # must be explicitly True to actually submit
    force: bool = False             # A10: bypass score/spike gate (explicit override)


@router.post("/thematic/auto/signals/{signal_id}/approve")
async def approve_signal(
    signal_id: str,
    body: ApproveBody,
    user: dict = Depends(get_current_user),
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

    # ── Spike gate ────────────────────────────────────────────────────────────
    # A10: one-scan spikes must not trade — unconfirmed trend, no follow-through.
    # Block unless force=True.
    spike_warning = None
    if sig.get("is_spike"):
        if not body.force:
            raise HTTPException(
                status_code=400,
                detail="One-scan spike — unconfirmed trend. Wait for multi-scan confirmation or pass force=true"
            )
        spike_warning = "One-scan spike — force override active"

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
    )
    prices = _fp([ticker])
    price  = prices.get(ticker)
    result = {"portfolio_added": True, "paper_trade": None}
    if price and body.dollar_amount > 0:
        # Conviction-scaled position size
        use_conviction_scale = hil_settings.get("conviction_scale", True)
        alloc = (_conviction_dollar(body.dollar_amount, conviction)
                 if use_conviction_scale else body.dollar_amount)

        shares  = int(alloc / price)
        cost    = round(price * shares, 2)
        stop    = round(price * (1 - stop_pct / 100), 4)
        target  = round(price * (1 + target_pct / 100), 4)
        now_iso = _dt.datetime.now().isoformat(timespec="seconds")
        today   = _dt.date.today().isoformat()

        # Fetch ATR before acquiring lock (IO-bound, no state dependency)
        loop = asyncio.get_running_loop()
        atr = await loop.run_in_executor(None, _real_atr, ticker, price)
        alpha_tier = "A+" if conviction >= 9 else "A" if conviction >= 7 else "B" if conviction >= 5 else "C"

        # A11/P8: hold lock for entire read-modify-write of paper state so
        # concurrent approvals/auto-scans can't clobber each other.
        async with _paper_state_lock:
            PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state: dict = {}
            if PAPER_STATE_FILE.exists():
                try:
                    state = json.loads(PAPER_STATE_FILE.read_text())
                except Exception:
                    pass
            cash    = float(state.get("cash", 10000))
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
                    ticker=ticker,
                    dollar_amount=body.dollar_amount,
                    stop_pct=stop_pct,
                    target_pct=target_pct,
                    also_paper_trade=False,
                    execute=True,
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
                    log.info("Thematic HIL Fidelity trade executed: %s $%.0f", ticker, body.dollar_amount)
            except Exception as fid_err:
                log.warning("Thematic HIL Fidelity trade failed for %s: %s", ticker, fid_err)
                result["fidelity_trade"] = {"error": str(fid_err)}

    # Mark signal approved
    sig["status"] = "approved"
    _save_signals(data)
    resp = {"ok": True, **result}
    warnings = [w for w in [score_warning, rr_warning, spike_warning] if w]
    if warnings:
        resp["warnings"] = warnings
    return resp


@router.post("/thematic/auto/signals/{signal_id}/skip")
async def skip_signal(signal_id: str, _user: dict = Depends(get_current_user)):
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
async def execute_exits(_user: dict = Depends(get_current_user)):
    """Execute exits: close thematic positions meeting stop/target/hold/buzz-collapse criteria."""
    exits = await _check_thematic_exits(execute=True)
    return {"ok": True, "executed": exits, "count": len(exits)}


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
