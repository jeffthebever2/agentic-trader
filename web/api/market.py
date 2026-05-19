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
            import yfinance as yf
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: yf.download(WATCHLIST, period="2d", interval="1d",
                                     auto_adjust=True, progress=False, threads=True)
            )
            close = data.get("Close", data)
            quotes = []
            for sym in WATCHLIST:
                try:
                    if hasattr(close, "columns") and sym in close.columns:
                        prices = close[sym].dropna()
                    elif len(WATCHLIST) == 1:
                        prices = close.dropna()
                    else:
                        continue
                    if len(prices) < 1:
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
    import yfinance as yf
    data = await asyncio.get_event_loop().run_in_executor(
        None, lambda: yf.download(symbol, period=period, interval=interval,
                                   auto_adjust=True, progress=False)
    )
    if data.empty:
        return {"symbol": symbol, "dates": [], "close": [], "volume": []}

    # Flatten multi-level columns (newer yfinance returns MultiIndex even for single ticker)
    if data.columns.nlevels > 1:
        data.columns = data.columns.get_level_values(0)

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
    import yfinance as yf
    data = await asyncio.get_event_loop().run_in_executor(
        None, lambda: yf.download(WATCHLIST, period="5d", interval="1h",
                                   auto_adjust=True, progress=False, threads=True)
    )
    close = data.get("Close", data)
    result: dict = {"_ts": int(time.time())}
    for sym in WATCHLIST:
        try:
            prices = [round(float(v), 2) for v in close[sym].dropna().tolist()]
            result[sym] = prices
        except Exception:
            result[sym] = []
    _sparkline_cache = result
    return result


@router.get("/market/sp500-list")
async def sp500_list():
    tickers = await asyncio.get_event_loop().run_in_executor(None, _get_sp500_tickers)
    return {"tickers": tickers, "count": len(tickers)}
