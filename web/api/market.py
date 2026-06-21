import time
import asyncio
import hashlib
import logging
import math
import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response

router = APIRouter()
log = logging.getLogger(__name__)

_sp500_tickers: list[str] = []
_sp500_ts: float = 0
SP500_TTL = 86400

_quotes_cache: dict = {}
_quotes_ts: float = 0
QUOTES_TTL = 300

_chart_cache: dict = {}
_sparkline_cache: dict = {}

_fetch_lock = asyncio.Lock()

WATCHLIST = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'META', 'GOOGL', 'BRK-B', 'BTC-USD']


def _download_symbol(symbol: str, *, period: str, interval: str):
    import yfinance as yf

    return yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )


def _flatten_yfinance_columns(data):
    """Normalize yfinance's single-ticker MultiIndex shape."""
    if getattr(data, "empty", True):
        return data
    columns = getattr(data, "columns", None)
    if columns is not None and getattr(columns, "nlevels", 1) > 1:
        data = data.copy()
        data.columns = columns.get_level_values(0)
    return data


def _close_series(data):
    data = _flatten_yfinance_columns(data)
    if getattr(data, "empty", True) or "Close" not in data:
        return None
    series = data["Close"]
    if hasattr(series, "ndim") and series.ndim > 1:
        series = series.iloc[:, 0]
    return series.dropna()


def _get_sp500_tickers() -> list[str]:
    global _sp500_tickers, _sp500_ts
    if _sp500_tickers and (time.time() - _sp500_ts) < SP500_TTL:
        return _sp500_tickers
    try:
        import pandas as pd
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        tickers = [t.replace(".", "-") for t in tables[0]["Symbol"].tolist()]
        _sp500_tickers = tickers
        _sp500_ts = time.time()
        log.info(f"Loaded {len(tickers)} S&P 500 tickers")
        return tickers
    except Exception as e:
        log.warning(f"S&P 500 fetch failed: {e}")
        fallback = [
            'AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','BRK-B','JPM','V',
            'XOM','UNH','AVGO','LLY','MA','HD','COST','PG','MRK','ABBV',
            'JNJ','WMT','BAC','CRM','ORCL','CVX','MCD','NFLX','AMD','TMO',
            'GE','CAT','PEP','QCOM','INTU','AMAT','AXP','ISRG','AMGN','IBM',
            'GS','SPGI','BKNG','VRTX','TXN','RTX','PFE','GILD','SPY','QQQ',
        ]
        return fallback


async def _fetch_syms(syms: list[str]) -> dict:
    result = {}
    for sym in syms:
        try:
            data = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda symbol=sym: _download_symbol(symbol, period="5d", interval="1d"),
            )
            prices = _close_series(data)
            if prices is None or len(prices) < 1:
                continue
            last = float(prices.iloc[-1])
            prev = float(prices.iloc[-2]) if len(prices) >= 2 else last
            chg = last - prev
            pct = (chg / prev * 100) if prev else 0
            result[sym] = {"price": round(last, 2), "change": round(chg, 2), "change_pct": round(pct, 2)}
        except Exception:
            continue
    return result


async def _fetch_quotes_bg():
    global _quotes_cache, _quotes_ts
    async with _fetch_lock:
        if _quotes_cache and (time.time() - _quotes_ts) < QUOTES_TTL:
            return
        try:
            fetched = await _fetch_syms(WATCHLIST)
            _quotes_cache = fetched
            _quotes_ts = time.time()
        except Exception as e:
            log.error(f"Quote fetch failed: {e}")


@router.get("/market/quotes")
async def market_quotes(tickers: str = Query("")):
    requested = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else []
    # Warm cache in background if stale
    if not _quotes_cache or (time.time() - _quotes_ts) >= QUOTES_TTL:
        asyncio.create_task(_fetch_quotes_bg())
    # For tickers not in cache, fetch synchronously (first hit only)
    missing = [t for t in requested if t not in _quotes_cache] if requested else []
    if missing:
        fresh = await _fetch_syms(missing)
        _quotes_cache.update(fresh)
    if requested:
        return {t: _quotes_cache[t] for t in requested if t in _quotes_cache}
    return dict(_quotes_cache)


@router.get("/market/chart")
async def market_chart(symbol: str = Query("SPY"), period: str = Query("5d"), interval: str = Query("1h")):
    key = f"{symbol}_{period}_{interval}"
    cached = _chart_cache.get(key)
    if cached and (time.time() - cached.get("_ts", 0)) < 300:
        return cached
    data = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: _download_symbol(symbol, period=period, interval=interval),
    )
    if data.empty:
        return {"symbol": symbol, "dates": [], "close": [], "volume": []}

    data = _flatten_yfinance_columns(data)

    def _col(col):
        s = data[col]
        if hasattr(s, 'ndim') and s.ndim > 1:
            s = s.iloc[:, 0]
        return s

    def _finite_number(value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _price_list(col: str) -> list[float | None]:
        values: list[float | None] = []
        for value in _col(col).tolist():
            number = _finite_number(value)
            values.append(round(number, 2) if number is not None else None)
        return values

    def _volume_list() -> list[int]:
        values: list[int] = []
        for value in _col("Volume").tolist():
            number = _finite_number(value)
            values.append(int(number) if number is not None and number > 0 else 0)
        return values

    # Preserve full ISO datetime for intraday intervals
    dates = [d.isoformat() for d in data.index.tolist()]
    open_values = _price_list("Open")
    high_values = _price_list("High")
    low_values = _price_list("Low")
    close_values = _price_list("Close")
    volume_values = _volume_list()

    rows = [
        row for row in zip(dates, open_values, high_values, low_values, close_values, volume_values)
        if None not in row[1:5]
    ]
    if not rows:
        return {"symbol": symbol, "dates": [], "close": [], "volume": []}

    clean_dates, clean_open, clean_high, clean_low, clean_close, clean_volume = map(list, zip(*rows))
    result = {
        "symbol": symbol,
        "interval": interval,
        "dates": clean_dates,
        "open": clean_open,
        "high": clean_high,
        "low": clean_low,
        "close": clean_close,
        "volume": clean_volume,
        "_ts": int(time.time()),
    }
    _chart_cache[key] = result
    return result


# ── HIL trade chart (TradingView-style PNG with real entry/stop/target) ──────
_TRADE_CHART_DIR = Path(__file__).resolve().parent.parent / "static" / "charts"
_trade_chart_cache: dict = {}     # key -> (path, ts)
TRADE_CHART_TTL = 900             # 15 min — daily bars; "last" barely moves intraday


def _render_trade_chart_sync(symbol, entry, stop, target, stop_pct, target_pct, out_path):
    """Fetch ~1y daily OHLCV, resolve real levels, render the trade PNG. Sync +
    network — call via asyncio.to_thread. Returns out_path or None."""
    data = _flatten_yfinance_columns(_download_symbol(symbol, period="1y", interval="1d"))
    if getattr(data, "empty", True) or "Close" not in data or len(data) < 20:
        return None
    last = float(data["Close"].iloc[-1])
    e = entry if entry else round(last, 2)
    s = stop if stop else (round(e * (1 - stop_pct / 100.0), 2) if stop_pct else None)
    t = target if target else (round(e * (1 + target_pct / 100.0), 2) if target_pct else None)
    from tradingagents.portfolio.chart import compute_levels, render_trade_chart
    levels = compute_levels(data["High"].tolist(), data["Low"].tolist(), data["Close"].tolist())
    return render_trade_chart(
        symbol, data, entry=e, stop=s, target=t, out_path=out_path, levels=levels,
    )


@router.get("/market/trade-chart.png")
async def trade_chart_png(
    ticker: str = Query(...),
    entry: float | None = Query(None),
    stop: float | None = Query(None),
    target: float | None = Query(None),
    stop_pct: float | None = Query(None),
    target_pct: float | None = Query(None),
):
    """On-demand trade chart for the HIL approval cards. Pass absolute
    entry/stop/target, or stop_pct/target_pct (entry defaults to last close).
    404s (so the <img> hides) on any fetch/render failure. Cached 15 min."""
    sym = (ticker or "").strip().upper()
    if not sym or not sym.replace("-", "").replace(".", "").isalnum() or len(sym) > 12:
        return Response(status_code=404)

    def _r(v):
        try:
            return round(float(v), 4) if v is not None else None
        except (TypeError, ValueError):
            return None
    e, s, t, sp, tp = _r(entry), _r(stop), _r(target), _r(stop_pct), _r(target_pct)
    key = f"{sym}|{e}|{s}|{t}|{sp}|{tp}"
    now = time.time()
    cached = _trade_chart_cache.get(key)
    headers = {"Cache-Control": "public, max-age=900"}
    if cached and (now - cached[1]) < TRADE_CHART_TTL and os.path.exists(cached[0]):
        return FileResponse(cached[0], media_type="image/png", headers=headers)
    try:
        _TRADE_CHART_DIR.mkdir(parents=True, exist_ok=True)
        out = str(_TRADE_CHART_DIR / f"hil_{hashlib.sha1(key.encode()).hexdigest()[:16]}.png")
        res = await asyncio.to_thread(_render_trade_chart_sync, sym, e, s, t, sp, tp, out)
    except Exception as ex:
        log.debug("trade-chart %s: %s", sym, ex)
        res = None
    if not res:
        return Response(status_code=404)
    _trade_chart_cache[key] = (out, now)
    return FileResponse(out, media_type="image/png", headers=headers)


@router.get("/market/sparklines")
async def market_sparklines():
    global _sparkline_cache
    if _sparkline_cache and (time.time() - _sparkline_cache.get("_ts", 0)) < 300:
        return _sparkline_cache
    result: dict = {"_ts": int(time.time())}
    for sym in WATCHLIST:
        try:
            data = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda symbol=sym: _download_symbol(symbol, period="5d", interval="1h"),
            )
            close = _close_series(data)
            if close is None:
                result[sym] = []
                continue
            prices = [round(float(v), 2) for v in close.tolist()]
            result[sym] = prices
        except Exception:
            result[sym] = []
    _sparkline_cache = result
    return result


@router.get("/market/sp500-list")
async def sp500_list():
    tickers = await asyncio.get_running_loop().run_in_executor(None, _get_sp500_tickers)
    return {"tickers": tickers, "count": len(tickers)}


_news_cache: dict = {}
_summary_cache: dict = {}


def _parse_pub_date(raw: str) -> str:
    """Parse RFC 2822 or ISO date string → ISO 8601. Returns raw on failure."""
    if not raw:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        return raw


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


def _fetch_google_news(symbol: str) -> list[dict]:
    """Primary source: Google News RSS — no auth, broad coverage."""
    import feedparser
    import requests as req

    query = f"{symbol.upper()} stock"
    url = f"https://news.google.com/rss/search?q={req.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TradingAgents/1.0)"}
    r = req.get(url, timeout=10, headers=headers)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    items = []
    for entry in feed.entries[:15]:
        title = entry.get("title", "").strip()
        if not title:
            continue
        source_obj = entry.get("source", {})
        source = source_obj.get("title", "") if isinstance(source_obj, dict) else str(source_obj)
        items.append({
            "title": title,
            "summary": _strip_html(entry.get("summary", ""))[:300],
            "url": entry.get("link", ""),
            "source": source,
            "published": _parse_pub_date(entry.get("published", "")),
        })
    return items


def _fetch_finviz_news(symbol: str) -> list[dict]:
    """Fallback 1: Finviz stock page news table — detailed, ticker-specific."""
    import requests as req
    from bs4 import BeautifulSoup
    import re, datetime

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = req.get(f"https://finviz.com/quote.ashx?t={symbol.upper()}", headers=headers, timeout=12)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="fullview-news-outer")
    if not table:
        return []

    items = []
    today = datetime.date.today()
    last_date = today
    for row in table.find_all("tr")[:15]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        time_str = cells[0].text.strip()
        title_cell = cells[1]
        a = title_cell.find("a")
        if not a:
            continue
        title = a.text.strip()
        url = a.get("href", "")
        span = title_cell.find("span")
        source = span.text.strip().strip("()") if span else ""

        # Parse time — Finviz shows "Today HH:MMam" or "Dec-20-24 HH:MMam"
        try:
            if time_str.startswith("Today"):
                t = datetime.datetime.strptime(time_str, "Today %I:%M%p").replace(
                    year=today.year, month=today.month, day=today.day)
            elif re.match(r"^\d{1,2}:\d{2}", time_str):
                t = datetime.datetime.strptime(time_str, "%I:%M%p").replace(
                    year=last_date.year, month=last_date.month, day=last_date.day)
            else:
                # e.g. "May-27-26 01:48PM"
                parts = time_str.split()
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else "12:00PM"
                t = datetime.datetime.strptime(f"{date_part} {time_part}", "%b-%d-%y %I:%M%p")
                last_date = t.date()
            pub = t.isoformat()
        except Exception:
            pub = time_str

        items.append({"title": title, "summary": "", "url": url, "source": source, "published": pub})
    return items


def _fetch_seeking_alpha_rss(symbol: str) -> list[dict]:
    """Fallback 2: Seeking Alpha RSS (public, no auth required)."""
    import feedparser
    import requests as req

    url = f"https://seekingalpha.com/api/sa/combined/{symbol.upper()}.xml"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TradingAgents/1.0)"}
    r = req.get(url, timeout=10, headers=headers)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    items = []
    for entry in feed.entries[:12]:
        title = entry.get("title", "").strip()
        if not title:
            continue
        items.append({
            "title": title,
            "summary": _strip_html(entry.get("summary", ""))[:300],
            "url": entry.get("link", ""),
            "source": "Seeking Alpha",
            "published": _parse_pub_date(entry.get("published", "")),
        })
    return items


def _fetch_news_with_fallbacks(symbol: str) -> tuple[list[dict], str]:
    """Try each source in order; return (items, source_used)."""
    sources = [
        ("google_rss",    _fetch_google_news),
        ("finviz",        _fetch_finviz_news),
        ("seeking_alpha", _fetch_seeking_alpha_rss),
    ]
    last_err = None
    for name, fn in sources:
        try:
            items = fn(symbol)
            if items:
                return items, name
        except Exception as e:
            last_err = e
            log.warning(f"News source '{name}' failed for {symbol}: {e}")
    log.error(f"All news sources failed for {symbol}: {last_err}")
    return [], "none"


@router.get("/market/news")
async def market_news(symbol: str):
    """Fetch recent news with 3-source fallback chain (Google RSS → Finviz → Seeking Alpha)."""
    global _news_cache
    cache_key = symbol.upper()
    cached = _news_cache.get(cache_key)
    if cached and (time.time() - cached.get("_ts", 0)) < 900:  # 15 min TTL
        return cached

    items, source = await asyncio.get_running_loop().run_in_executor(
        None, _fetch_news_with_fallbacks, symbol
    )

    result = {
        "symbol": symbol.upper(),
        "news": items,
        "_source": source,
        "_ts": int(time.time()),
    }
    _news_cache[cache_key] = result
    return result


def _call_cf_ai(prompt: str, max_tokens: int = 400) -> str:
    """Call Cloudflare Workers AI (Llama 3.3 70B). Returns text or raises."""
    import os, httpx
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token   = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    gateway = os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip().rstrip("/")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN not set")

    if gateway:
        if gateway.endswith("/chat/completions"):
            gateway = gateway[: -len("/chat/completions")].rstrip("/")
        base = gateway if gateway.endswith("/compat") else gateway + "/compat"
    else:
        base = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"

    model = os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    req_model = model  # compat endpoint uses model name as-is (no workers-ai/ prefix)

    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            base + "/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": req_model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens},
        )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


@router.get("/market/news-summary")
async def market_news_summary(symbol: str):
    """
    AI-powered news impact summary for a ticker.
    Fetches latest news then uses Cloudflare Workers AI (Llama 3.3 70B)
    to produce: sentiment, key themes, risk/opportunity analysis, price impact.
    Cached 30 min per symbol.
    """
    global _summary_cache, _news_cache
    cache_key = symbol.upper()
    cached = _summary_cache.get(cache_key)
    if cached and (time.time() - cached.get("_ts", 0)) < 1800:  # 30 min TTL
        return cached

    # Get news (reuse cache or fetch fresh)
    news_cached = _news_cache.get(cache_key)
    if news_cached and news_cached.get("news"):
        articles = news_cached["news"]
    else:
        articles, _ = await asyncio.get_running_loop().run_in_executor(
            None, _fetch_news_with_fallbacks, symbol
        )

    if not articles:
        result = {
            "symbol": cache_key,
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "summary": "No recent news available to analyze.",
            "key_themes": [],
            "risks": [],
            "opportunities": [],
            "price_impact": "Unknown",
            "_ts": int(time.time()),
        }
        _summary_cache[cache_key] = result
        return result

    # Build prompt
    headlines = "\n".join(
        f"- [{a.get('source','?')}] {a['title']}" for a in articles[:12]
    )
    prompt = f"""You are a quantitative analyst. Analyze these recent news headlines for {symbol.upper()} stock and provide a structured assessment.

HEADLINES:
{headlines}

Respond ONLY with a valid JSON object (no markdown, no explanation) with exactly these fields:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "sentiment_score": <float -1.0 to 1.0>,
  "summary": "<2-3 sentence plain-English summary of the news landscape and its likely near-term impact on {symbol}>",
  "key_themes": ["<theme1>", "<theme2>", "<theme3>"],
  "risks": ["<risk1>", "<risk2>"],
  "opportunities": ["<opp1>", "<opp2>"],
  "price_impact": "strong upside" | "mild upside" | "neutral" | "mild downside" | "strong downside"
}}"""

    def _do_summary():
        for attempt in range(2):
            try:
                if attempt == 0:
                    raw = _call_cf_ai(prompt, max_tokens=500)
                else:
                    import os, httpx as _httpx
                    key = os.getenv("OPENROUTER_API_KEY", "")
                    if not key:
                        return None
                    r = _httpx.Client(timeout=20).post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
                    )
                    r.raise_for_status()
                    raw = r.json()["choices"][0]["message"]["content"].strip()
                import json as _json, re as _re
                clean = _re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
                m = _re.search(r"\{.*\}", clean, _re.DOTALL)
                if m:
                    clean = m.group(0)
                return _json.loads(clean)
            except Exception as e:
                log.warning(f"AI summary attempt {attempt} failed for {symbol}: {e}")
        return None

    parsed = await asyncio.get_running_loop().run_in_executor(None, _do_summary)

    if parsed and isinstance(parsed, dict):
        result = {
            "symbol": cache_key,
            "sentiment": parsed.get("sentiment", "neutral"),
            "sentiment_score": float(parsed.get("sentiment_score", 0.0)),
            "summary": parsed.get("summary", ""),
            "key_themes": parsed.get("key_themes", []),
            "risks": parsed.get("risks", []),
            "opportunities": parsed.get("opportunities", []),
            "price_impact": parsed.get("price_impact", "neutral"),
            "_ts": int(time.time()),
        }
    else:
        result = {
            "symbol": cache_key,
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "summary": "AI summary temporarily unavailable.",
            "key_themes": [],
            "risks": [],
            "opportunities": [],
            "price_impact": "Unknown",
            "_ts": int(time.time()),
        }

    _summary_cache[cache_key] = result
    return result


@router.get("/market/quote-detail")
async def market_quote_detail(symbol: str):
    """Fetch price, change, 52w range, volume for a single ticker."""
    def _fetch():
        import yfinance as yf
        t = yf.Ticker(symbol.upper())
        info = t.info or {}
        # Fallback to fast_info if info is empty
        fi = getattr(t, "fast_info", None)
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is None and fi:
            price = getattr(fi, "last_price", None)
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if prev_close is None and fi:
            prev_close = getattr(fi, "previous_close", None)
        change = round(float(price) - float(prev_close), 2) if price and prev_close else None
        change_pct = round(change / float(prev_close) * 100, 2) if change and prev_close else None
        return {
            "symbol": symbol.upper(),
            "price": round(float(price), 2) if price else None,
            "change": change,
            "change_pct": change_pct,
            "prev_close": round(float(prev_close), 2) if prev_close else None,
            "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
            "day_low": info.get("dayLow") or info.get("regularMarketDayLow"),
            "week52_high": info.get("fiftyTwoWeekHigh"),
            "week52_low": info.get("fiftyTwoWeekLow"),
            "volume": info.get("volume") or info.get("regularMarketVolume"),
            "avg_volume": info.get("averageVolume"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "short_name": info.get("shortName") or info.get("longName") or symbol.upper(),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }

    try:
        data = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    except Exception as e:
        log.warning(f"Quote detail failed for {symbol}: {e}")
        data = {"symbol": symbol.upper(), "price": None, "change": None, "change_pct": None}
    return data


@router.get("/market/watchlist")
async def market_watchlist():
    """Return the default watchlist tickers."""
    return {"tickers": WATCHLIST, "watchlist": WATCHLIST}


_opp_cache: dict = {}
OPP_TTL = 300  # 5 minutes


@router.get("/market/opportunities")
async def market_opportunities(mode: str = "gainers"):
    """Return market movers: gainers, losers, or active (by volume)."""
    now = time.time()
    cache_key = mode
    if cache_key in _opp_cache and now - _opp_cache.get("_ts", 0) < OPP_TTL:
        return _opp_cache[cache_key]

    def _fetch():
        import yfinance as yf
        import pandas as pd

        screener_map = {
            "gainers": "day_gainers",
            "losers": "day_losers",
            "active": "most_actives",
        }
        screener_key = screener_map.get(mode, "day_gainers")
        try:
            df: pd.DataFrame = yf.screen(screener_key, count=20)
            if df is None or df.empty:
                return []
            results = []
            for _, row in df.iterrows():
                symbol = str(row.get("symbol", ""))
                if not symbol:
                    continue
                results.append({
                    "ticker": symbol,
                    "symbol": symbol,
                    "price": row.get("regularMarketPrice") or row.get("price"),
                    "change": row.get("regularMarketChange") or row.get("change"),
                    "change_pct": row.get("regularMarketChangePercent") or row.get("changesPercentage"),
                    "volume": row.get("regularMarketVolume") or row.get("volume"),
                    "name": row.get("shortName") or row.get("displayName") or symbol,
                })
            return results
        except Exception:
            return []

    try:
        results = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    except Exception:
        results = []

    _opp_cache[cache_key] = results
    _opp_cache["_ts"] = time.time()
    return results


@router.get("/market/gateway-quote")
async def market_gateway_quote(symbol: str = Query(..., min_length=1, max_length=10)):
    """Best live quote across all configured providers (quote gateway)."""
    def _fetch():
        from tradingagents.data.quote_gateway import get_gateway
        gw = get_gateway()
        if gw is None:
            return {"error": "gateway_disabled"}
        q = gw.get_quote(symbol)
        if q is None:
            return {"symbol": symbol.upper(), "quote": None}
        best = q.best
        return {
            "symbol": q.symbol,
            "quote": {
                "last": best.last,
                "bid": best.bid,
                "ask": best.ask,
                "mid": best.mid,
                "spread_bps": round(best.spread_bps, 1) if best.spread_bps is not None else None,
                "source": best.source,
                "trusted": best.trusted,
                "age_seconds": round(best.age_seconds, 1),
                "quote_time": best.quote_time.isoformat(),
            },
            "backup_sources": q.backup_sources,
            "consensus_ok": q.consensus_ok,
            "consensus_spread_bps": q.consensus_spread_bps,
            "all_quotes": [
                {"source": x.source, "last": x.last, "age_seconds": round(x.age_seconds, 1)}
                for x in q.all_quotes
            ],
        }

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)


@router.get("/market/gateway-health")
async def market_gateway_health():
    """Per-provider success/failure/latency stats for the quote gateway."""
    def _fetch():
        from tradingagents.data.quote_gateway import get_gateway
        gw = get_gateway()
        if gw is None:
            return {"enabled": False, "providers": {}}
        return {"enabled": True, "providers": gw.provider_health()}

    return await asyncio.get_running_loop().run_in_executor(None, _fetch)
