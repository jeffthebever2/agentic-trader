import time
import asyncio
import logging
import math
from fastapi import APIRouter, Query

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

WATCHLIST = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'META', 'BTC-USD']


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


async def _fetch_quotes_bg():
    global _quotes_cache, _quotes_ts
    async with _fetch_lock:
        if _quotes_cache and (time.time() - _quotes_ts) < QUOTES_TTL:
            return
        try:
            quotes = []
            for sym in WATCHLIST:
                try:
                    data = await asyncio.get_event_loop().run_in_executor(
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
                    quotes.append({"symbol": sym, "price": round(last, 2),
                                   "change": round(chg, 2), "change_pct": round(pct, 2)})
                except Exception:
                    continue
            _quotes_cache = {"quotes": quotes, "ts": int(time.time()), "count": len(quotes)}
            _quotes_ts = time.time()
        except Exception as e:
            log.error(f"Quote fetch failed: {e}")


@router.get("/market/quotes")
async def market_quotes():
    # Never block — always return immediately (empty or stale) and refresh in background
    if not _quotes_cache or (time.time() - _quotes_ts) >= QUOTES_TTL:
        asyncio.create_task(_fetch_quotes_bg())
    return _quotes_cache or {"quotes": [], "ts": 0, "count": 0}


@router.get("/market/chart")
async def market_chart(symbol: str = Query("SPY"), period: str = Query("5d"), interval: str = Query("1h")):
    key = f"{symbol}_{period}_{interval}"
    cached = _chart_cache.get(key)
    if cached and (time.time() - cached.get("_ts", 0)) < 300:
        return cached
    data = await asyncio.get_event_loop().run_in_executor(
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


@router.get("/market/sparklines")
async def market_sparklines():
    global _sparkline_cache
    if _sparkline_cache and (time.time() - _sparkline_cache.get("_ts", 0)) < 300:
        return _sparkline_cache
    result: dict = {"_ts": int(time.time())}
    for sym in WATCHLIST:
        try:
            data = await asyncio.get_event_loop().run_in_executor(
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
    tickers = await asyncio.get_event_loop().run_in_executor(None, _get_sp500_tickers)
    return {"tickers": tickers, "count": len(tickers)}


_news_cache: dict = {}

@router.get("/market/news")
async def market_news(symbol: str):
    """Fetch recent news for a ticker via yfinance."""
    global _news_cache
    cache_key = symbol.upper()
    cached = _news_cache.get(cache_key)
    if cached and (time.time() - cached.get("_ts", 0)) < 900:  # 15 min TTL
        return cached

    def _fetch():
        import yfinance as yf
        ticker = yf.Ticker(symbol.upper())
        raw = ticker.news or []
        items = []
        for n in raw[:10]:
            # yfinance v0.2+ nests content inside 'content' key
            content = n.get("content", n)
            title = content.get("title", "")
            summary = content.get("summary", "")
            url_obj = content.get("canonicalUrl") or content.get("url") or {}
            url = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj)
            if not url:
                url = n.get("link", "")
            provider = content.get("provider") or {}
            source = provider.get("displayName", "") if isinstance(provider, dict) else str(provider)
            if not source:
                source = n.get("publisher", "")
            pub = content.get("pubDate", "") or str(n.get("providerPublishTime", ""))
            if title:
                items.append({"title": title, "summary": summary, "url": url,
                               "source": source, "published": pub})
        return items

    try:
        items = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as e:
        log.warning(f"News fetch failed for {symbol}: {e}")
        items = []

    result = {"symbol": symbol.upper(), "news": items, "_ts": int(time.time())}
    _news_cache[cache_key] = result
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
        data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as e:
        log.warning(f"Quote detail failed for {symbol}: {e}")
        data = {"symbol": symbol.upper(), "price": None, "change": None, "change_pct": None}
    return data
