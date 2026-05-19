#!/usr/bin/env python3
"""
Pre-download and cache all price data for a date range so backtests run instantly.

Usage:
    python scripts/warm_cache.py --start 2025-11-01 --end 2026-05-01
    python scripts/warm_cache.py --start 2022-01-01 --end 2022-12-31
"""

import argparse
import datetime
import hashlib
import pickle
import sys
from pathlib import Path

import yfinance as yf

# Add parent dir so we can import backtest constants
sys.path.insert(0, str(Path(__file__).parent.parent))

CACHE_DIR = Path(".backtest_cache")
BATCH_SIZE = 100
SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY",
               "XLP", "XLU", "XLB", "XLRE", "XLC"]


def download_batch(tickers: list, start: str, end: str) -> dict:
    try:
        raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                          progress=False, threads=True)
        if raw.empty:
            return {}
        result = {}
        if isinstance(raw.columns, yf.multi.MultiIndex if hasattr(yf, 'multi') else type(raw.columns)):
            for t in tickers:
                try:
                    df = raw.xs(t, axis=1, level=1).dropna(how="all")
                    if not df.empty:
                        result[t] = df
                except (KeyError, Exception):
                    pass
        else:
            # Single ticker
            if not raw.empty and len(tickers) == 1:
                result[tickers[0]] = raw.dropna(how="all")
        return result
    except Exception as e:
        print(f"  Error: {e}")
        return {}


def warm(tickers: list, start: str, end: str):
    CACHE_DIR.mkdir(exist_ok=True)
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print(f"Warming cache: {len(tickers)} tickers in {len(batches)} batches ({start} → {end})")

    cached = 0
    downloaded = 0
    for i, batch in enumerate(batches, 1):
        batch_sig = hashlib.sha256(",".join(batch).encode()).hexdigest()[:16]
        cache_key = f"{start}_{end}_bs{BATCH_SIZE}_{batch_sig}"
        cache_path = CACHE_DIR / f"batch_{cache_key}.pkl"

        if cache_path.exists():
            print(f"  [{i}/{len(batches)}] Already cached — skip")
            cached += 1
            continue

        print(f"  [{i}/{len(batches)}] Downloading {len(batch)} tickers...", end="", flush=True)
        data = download_batch(batch, start, end)
        if data:
            with open(cache_path, "wb") as f:
                pickle.dump(data, f)
            print(f" saved {len(data)} tickers")
        else:
            print(" no data")
        downloaded += 1

    print(f"\nTicker batches: {cached} already cached, {downloaded} downloaded")

    # SPY / benchmark
    for sym in ["SPY"]:
        spy_cache = CACHE_DIR / f"spy_{sym}_{start}_{end}.pkl"
        if not spy_cache.exists():
            print(f"Downloading {sym}...", end="", flush=True)
            try:
                df = yf.download(sym, start=start, end=end, auto_adjust=True, progress=False)
                if not df.empty:
                    with open(spy_cache, "wb") as f:
                        pickle.dump(df, f)
                    print(" saved")
                else:
                    print(" no data")
            except Exception as e:
                print(f" error: {e}")
        else:
            print(f"{sym}: already cached")

    # VIX / VIX3M
    for sym, prefix in [("^VIX", "vix"), ("^VIX3M", "vix3m")]:
        vcache = CACHE_DIR / f"{prefix}_{start}_{end}.pkl"
        if not vcache.exists():
            print(f"Downloading {sym}...", end="", flush=True)
            try:
                df = yf.download(sym, start=start, end=end, auto_adjust=True, progress=False)
                if not df.empty:
                    with open(vcache, "wb") as f:
                        pickle.dump(df, f)
                    print(" saved")
                else:
                    print(" no data")
            except Exception as e:
                print(f" error: {e}")
        else:
            print(f"{sym}: already cached")

    # Sector ETFs
    sector_cache = CACHE_DIR / f"sectors_{start}_{end}.pkl"
    if not sector_cache.exists():
        print(f"Downloading sector ETFs...", end="", flush=True)
        try:
            sector_dfs = {}
            for etf in SECTOR_ETFS:
                df = yf.download(etf, start=start, end=end, auto_adjust=True, progress=False)
                if not df.empty:
                    sector_dfs[etf] = df
            if sector_dfs:
                with open(sector_cache, "wb") as f:
                    pickle.dump(sector_dfs, f)
                print(f" saved {len(sector_dfs)}/{len(SECTOR_ETFS)} ETFs")
            else:
                print(" no data")
        except Exception as e:
            print(f" error: {e}")
    else:
        print("Sectors: already cached")

    print("\nCache warm complete. Future backtests on this range will be instant.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="Backtest start date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="Backtest end date YYYY-MM-DD")
    ap.add_argument("--tickers", default="/tmp/sp500_tickers.txt",
                    help="File with one ticker per line")
    ap.add_argument("--lookback-days", type=int, default=420,
                    help="Lookback window before start (default 420 = backtest.py default)")
    ap.add_argument("--forward-days", type=int, default=15,
                    help="Forward window after end (default 15)")
    args = ap.parse_args()

    start_dt = datetime.date.fromisoformat(args.start)
    end_dt = datetime.date.fromisoformat(args.end)
    dl_start = str(start_dt - datetime.timedelta(days=args.lookback_days))
    dl_end = str(end_dt + datetime.timedelta(days=args.forward_days))

    print(f"Backtest window : {args.start} → {args.end}")
    print(f"Download window : {dl_start} → {dl_end} (incl. {args.lookback_days}d lookback)")

    tickers_path = Path(args.tickers)
    if not tickers_path.exists():
        print(f"Tickers file not found: {args.tickers}")
        sys.exit(1)
    tickers = [t.strip() for t in tickers_path.read_text().splitlines() if t.strip()]
    print(f"Tickers: {len(tickers)}")

    warm(tickers, dl_start, dl_end)


if __name__ == "__main__":
    main()
