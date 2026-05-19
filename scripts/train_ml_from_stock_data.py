#!/usr/bin/env python3
"""Pull stock data for the ticker universe, label every stock/date, and train ML gates.

This is the "train on actual stock data" path. It does not require a prior
backtest trade export. It downloads OHLCV, builds one candidate row per
ticker/date, labels forward outcomes, saves the full candidate CSV, then trains
the ML gate model bundle.
"""

import argparse
import contextlib
import csv
import datetime as dt
import pickle
import ssl
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import backtest
from backtest import (
    BATCH_SIZE,
    MIN_HISTORY,
    build_spy_regime,
    download_all,
    load_tickers,
    measure_outcome,
    precompute,
    score_at,
)
from scripts.train_ml_models import train_models

try:
    import certifi
except Exception:
    certifi = None


def _verified_ssl_context() -> ssl.SSLContext | None:
    if certifi is None:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def _safe_cache_name(*parts) -> str:
    text = "_".join(str(p).replace("\\", "_").replace("/", "_").replace(":", "_") for p in parts)
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


ODD_SUFFIXES = (
    "W", "WS", "WT", "U", "UN", "R", "RT",
)


def _looks_like_common_stock(ticker: str) -> bool:
    """Drop obvious warrants/units/rights/SPAC artifacts before Yahoo download."""
    t = ticker.strip().upper()
    if not t or len(t) > 5:
        return False
    if any(ch in t for ch in [".", "-", "/", "^", "$"]):
        return False
    if t.endswith(ODD_SUFFIXES):
        return False
    # Many NASDAQ preferred/notes end in these fifth-letter suffixes.
    if len(t) == 5 and t[-1] in {"L", "M", "N", "O", "P", "Z"}:
        return False
    return True


def _filter_stock_tickers(tickers: list, include_non_common: bool = False) -> tuple:
    if include_non_common:
        return tickers, []
    kept = []
    removed = []
    for ticker in tickers:
        if _looks_like_common_stock(ticker):
            kept.append(ticker)
        else:
            removed.append(ticker)
    return kept, removed


def _read_nasdaqtrader_symbols(url: str) -> pd.DataFrame:
    with urllib.request.urlopen(url, timeout=30, context=_verified_ssl_context()) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    lines = [
        line for line in text.splitlines()
        if line and not line.startswith("File Creation Time")
    ]
    if not lines:
        return pd.DataFrame()
    from io import StringIO
    return pd.read_csv(StringIO("\n".join(lines)), sep="|")


def _active_listed_symbol_map(include_etfs: bool = False) -> dict:
    """Fetch active listed symbols from Nasdaq Trader symbol directories."""
    urls = [
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]
    active = {}
    bad_name_parts = [
        " warrant", "warrant ", " unit", "unit ", " right", "right ",
        " preferred", " preference", " note", " notes", " debenture",
        " bond", " etn", " fund", " trust", " acquisition corp. unit",
    ]
    for url in urls:
        df = _read_nasdaqtrader_symbols(url)
        if df.empty:
            continue
        symbol_col = "Symbol" if "Symbol" in df.columns else "ACT Symbol"
        name_col = "Security Name"
        for _, row in df.iterrows():
            symbol = str(row.get(symbol_col, "")).strip().upper()
            if not symbol or symbol == "NAN":
                continue
            if str(row.get("Test Issue", "N")).strip().upper() == "Y":
                continue
            if not include_etfs and str(row.get("ETF", "N")).strip().upper() == "Y":
                continue
            name = str(row.get(name_col, "")).lower()
            if any(part in f" {name} " for part in bad_name_parts):
                continue
            active[symbol] = str(row.get(name_col, "")).strip()
    return active


def _filter_active_listed_tickers(tickers: list, include_etfs: bool = False) -> tuple:
    active = _active_listed_symbol_map(include_etfs=include_etfs)
    if not active:
        print("WARNING: Could not load Nasdaq Trader active symbol list; skipping active-listed filter.")
        return tickers, []
    kept = [t for t in tickers if t.upper() in active]
    removed = [t for t in tickers if t.upper() not in active]
    return kept, removed


def _completed_dataset_tickers(dataset_path: Path) -> set[str]:
    if not dataset_path.exists():
        return set()
    completed: set[str] = set()
    try:
        for chunk in pd.read_csv(dataset_path, usecols=["ticker"], chunksize=250_000):
            completed.update(str(t).upper() for t in chunk["ticker"].dropna().unique())
    except Exception as exc:
        print(f"WARNING: Could not inspect existing dataset for resume: {exc}")
        return set()
    return completed


def _g(pc: dict, key: str, pos: int):
    try:
        v = pc[key].iloc[pos]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


def _spy_metrics(spy_df: pd.DataFrame, date_ts) -> dict:
    result = {
        "spy_close": None, "spy_sma50": None, "spy_sma200": None,
        "spy_ret1": None, "spy_ret5": None, "spy_ret20": None, "spy_regime": "unknown",
    }
    if spy_df is None or spy_df.empty:
        return result
    i = spy_df.index.searchsorted(date_ts, side="right") - 1
    if i < 0:
        return result
    close = float(spy_df["Close"].iloc[i])
    result["spy_close"] = close
    if i >= 49:
        result["spy_sma50"] = float(spy_df["Close"].iloc[i - 49:i + 1].mean())
    if i >= 199:
        result["spy_sma200"] = float(spy_df["Close"].iloc[i - 199:i + 1].mean())
        result["spy_regime"] = "bull" if close > result["spy_sma200"] else "bear"
    if i >= 1:
        result["spy_ret1"] = float(close / spy_df["Close"].iloc[i - 1] - 1)
    if i >= 5:
        result["spy_ret5"] = float(close / spy_df["Close"].iloc[i - 5] - 1)
    if i >= 20:
        result["spy_ret20"] = float(close / spy_df["Close"].iloc[i - 20] - 1)
    return result


def _vix_metrics(vix_df: pd.DataFrame, vix3m_df: pd.DataFrame, date_ts) -> dict:
    """Compute VIX regime, term structure, and 1-day change for a given date."""
    result = {
        "vix_close": None, "vix_regime": "unknown",
        "vix_ts": None, "vix_1d_chg": None,
    }
    if vix_df is None or vix_df.empty:
        return result
    i = vix_df.index.searchsorted(date_ts, side="right") - 1
    if i < 0:
        return result
    vix_close = float(vix_df["Close"].iloc[i])
    result["vix_close"] = vix_close
    if vix_close < 15:
        result["vix_regime"] = "low_vol"
    elif vix_close < 25:
        result["vix_regime"] = "normal"
    elif vix_close < 35:
        result["vix_regime"] = "elevated"
    else:
        result["vix_regime"] = "crisis"
    if i >= 1:
        prev_vix = float(vix_df["Close"].iloc[i - 1])
        result["vix_1d_chg"] = float(vix_close / prev_vix - 1) if prev_vix > 0 else None
    if vix3m_df is not None and not vix3m_df.empty:
        j = vix3m_df.index.searchsorted(date_ts, side="right") - 1
        if j >= 0:
            vix3m_close = float(vix3m_df["Close"].iloc[j])
            result["vix_ts"] = float(vix3m_close / vix_close) if vix_close > 0 else None
    return result


def _sector_metrics(sector_dfs: dict, date_ts) -> dict:
    """Compute sector breadth (fraction of sectors with positive 20d return)."""
    result = {"sector_breadth": None}
    if not sector_dfs:
        return result
    positive = 0
    total = 0
    for etf, sdf in sector_dfs.items():
        if sdf is None or len(sdf) < 21:
            continue
        j = sdf.index.searchsorted(date_ts, side="right") - 1
        if j < 20:
            continue
        close_now = float(sdf["Close"].iloc[j])
        close_20d = float(sdf["Close"].iloc[j - 20])
        if close_20d > 0:
            total += 1
            if close_now > close_20d:
                positive += 1
    result["sector_breadth"] = float(positive / total) if total > 0 else None
    return result


def _base_features(ticker: str, df: pd.DataFrame, pc: dict, pos: int,
                   date_ts, spy: dict, hold: int, target_mult: float,
                   stop_mult: float, vix: dict = None, sector: dict = None) -> dict:
    close = float(pc["close"].iloc[pos])
    high = float(pc["high"].iloc[pos])
    low = float(pc["low"].iloc[pos])
    atr = _g(pc, "atr14_true", pos) or (close * 0.02)
    sma20 = _g(pc, "sma20", pos)
    sma50 = _g(pc, "sma50", pos)
    sma200 = _g(pc, "sma200", pos)
    high10 = _g(pc, "high10", pos)
    v20 = _g(pc, "vol20", pos) or 0.0
    vol = _g(pc, "volume", pos) or 0.0

    row = {
        "ticker": ticker,
        "scan_date": str(pd.Timestamp(date_ts).date()),
        "day_of_week": int(pd.Timestamp(date_ts).dayofweek),
        "month": str(pd.Timestamp(date_ts).to_period("M")),
        "year": int(pd.Timestamp(date_ts).year),
        "candidate_status": "rejected",
        "score": 0.0,
        "rule_pass": False,
        "rule_score": 0.0,
        "setup_type": "stock_date_candidate",
        "spy_regime": spy.get("spy_regime", "unknown"),
        "vix_regime": (vix or {}).get("vix_regime", "unknown"),
        "entry": round(close, 2),
        "target": round(close + target_mult * atr, 2),
        "stop": round(close - stop_mult * atr, 2),
        "invalidation_level": round(close - stop_mult * atr, 2),
        "atr": round(atr, 4),
        "atr_pct": round(atr / close, 4) if close > 0 else None,
        "dollar_vol20": round(close * v20, 0),
        "vol_ratio_20d": round(vol / v20, 3) if v20 > 0 else None,
        "pct_from_10d_high": round((close - high10) / high10, 4) if high10 else None,
        "rel_ret20_vs_spy": None,
        "spy_ret1": spy.get("spy_ret1"),
        "spy_ret5": spy.get("spy_ret5"),
        "spy_ret20": spy.get("spy_ret20"),
        "sector_breadth": (sector or {}).get("sector_breadth"),
        "vix_ts": (vix or {}).get("vix_ts"),
        "vix_1d_chg": (vix or {}).get("vix_1d_chg"),
        "sma20_dist": round(close / sma20 - 1, 4) if sma20 else None,
        "sma50_dist": round(close / sma50 - 1, 4) if sma50 else None,
        "sma200_dist": round(close / sma200 - 1, 4) if sma200 else None,
        "signal_high": round(high, 2),
        "signal_low": round(low, 2),
    }
    for src, dst in [
        ("ret1d", "ret_1d"), ("ret3d", "ret_3d"), ("ret5d", "ret_5d"),
        ("ret10d", "ret_10d"), ("ret20d", "ret_20d"),
        ("rsi9", "rsi9"), ("rsi14", "rsi14"), ("macd_hist", "macd_hist"),
        ("cci14", "cci14"), ("body_pct", "body_pct"), ("upper_wick", "upper_wick"),
        ("lower_wick", "lower_wick"), ("close_loc", "close_loc"),
        ("day_range_pct", "day_range_pct"), ("vol_ratio_10d", "vol_ratio_10d"),
        ("stoch_k", "stoch_k"), ("stoch_d", "stoch_d"), ("mfi14", "mfi14"),
        ("adx14", "adx14"), ("cmf20", "cmf20"), ("range_rank_52w", "range_rank_52w"),
        ("roc10", "roc10"), ("roc20", "roc20"), ("consec_up", "consec_up"),
        ("consec_down", "consec_down"),
        ("rsi9_slope3", "rsi9_slope3"), ("macd_hist_slope3", "macd_hist_slope3"),
    ]:
        key = src
        if src == "vol_ratio_10d":
            v10 = _g(pc, "vol10", pos) or 0.0
            row[dst] = round(vol / v10, 3) if v10 > 0 else None
        else:
            row[dst] = _g(pc, key, pos)
    if row["spy_ret20"] is not None and row.get("ret_20d") is not None:
        row["rel_ret20_vs_spy"] = round(float(row["ret_20d"]) - float(row["spy_ret20"]), 4)
    return row


def _label_all_stock_outcome(df: pd.DataFrame, pos: int, hold: int,
                             target_mult: float, stop_mult: float, atr: float) -> dict:
    future = df.iloc[pos + 1:pos + 1 + hold]
    if len(future) == 0:
        return {}
    if atr <= 0:
        return {}
    return measure_outcome(
        df, pos, 0.0, 0.0, 0.0, hold,
        entry_timing="trigger_break",
        target_mult=target_mult,
        stop_mult=stop_mult,
        atr=atr,
    ) or {}


def build_stock_dataset(args) -> Path:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = Path(args.dataset_csv) if args.dataset_csv else out_dir / "stock_candidate_training_data.csv"

    if dataset_path.exists() and args.reuse_dataset and not args.rebuild_dataset:
        print(f"Using existing stock/date dataset: {dataset_path.resolve()}")
        return dataset_path

    tickers = load_tickers(args.tickers)
    tickers, removed_tickers = _filter_stock_tickers(
        tickers, include_non_common=args.include_non_common
    )
    if removed_tickers:
        removed_path = out_dir / "removed_non_common_tickers.txt"
        removed_path.write_text("\n".join(removed_tickers))
        print(
            f"Filtered out {len(removed_tickers):,} likely non-common-stock symbols "
            f"(warrants/units/rights/preferred/notes). Kept {len(tickers):,}. "
            f"List saved: {removed_path.resolve()}"
        )
    if not args.no_active_filter:
        tickers, inactive_removed = _filter_active_listed_tickers(
            tickers, include_etfs=args.include_etfs
        )
        if inactive_removed:
            inactive_path = out_dir / "removed_inactive_or_nonstock_tickers.txt"
            inactive_path.write_text("\n".join(inactive_removed))
            print(
                f"Filtered out {len(inactive_removed):,} symbols not in the active listed-stock universe. "
                f"Kept {len(tickers):,}. List saved: {inactive_path.resolve()}"
            )
    if args.max_tickers:
        tickers = tickers[:args.max_tickers]

    lookback_start = (
        dt.datetime.strptime(args.start, "%Y-%m-%d") - dt.timedelta(days=420)
    ).strftime("%Y-%m-%d")
    forward_end = (
        dt.datetime.strptime(args.end, "%Y-%m-%d") + dt.timedelta(days=args.hold + 10)
    ).strftime("%Y-%m-%d")

    all_dl = list(dict.fromkeys(tickers + [args.benchmark]))
    universe_cache = (
        Path(args.price_cache)
        if args.price_cache else
        out_dir / f"price_data_{_safe_cache_name(lookback_start, forward_end, args.benchmark, len(tickers))}.pkl"
    )
    raw = None
    if universe_cache.exists() and not args.no_cache and not args.rebuild_price_cache:
        with universe_cache.open("rb") as f:
            raw = pickle.load(f)
        print(f"Loaded cached price universe: {universe_cache.resolve()} ({len(raw):,} symbols)")
    else:
        yahoo_log = out_dir / "yfinance_download_warnings.log"
        if args.show_yfinance_errors:
            raw = download_all(
                all_dl, lookback_start, forward_end,
                no_cache=args.no_cache,
                batch_size=args.batch_size,
                threads=(args.yfinance_threads or False),
            )
        else:
            with yahoo_log.open("w", encoding="utf-8") as err_log:
                with contextlib.redirect_stderr(err_log):
                    raw = download_all(
                        all_dl, lookback_start, forward_end,
                        no_cache=args.no_cache,
                        batch_size=args.batch_size,
                        threads=(args.yfinance_threads or False),
                    )
            print(f"Yahoo warning details saved: {yahoo_log.resolve()}")
        with universe_cache.open("wb") as f:
            pickle.dump(raw, f)
        print(f"Saved price universe cache: {universe_cache.resolve()} ({len(raw):,} symbols)")

    available = sorted([t for t in raw.keys() if t != args.benchmark])
    unavailable = sorted(set(tickers) - set(available))
    if unavailable:
        unavailable_path = out_dir / "unavailable_yahoo_tickers.txt"
        unavailable_path.write_text("\n".join(unavailable))
        print(
            f"Yahoo returned usable data for {len(available):,}/{len(tickers):,} requested stock symbols; "
            f"skipped {len(unavailable):,}. List saved: {unavailable_path.resolve()}"
        )
    spy_df = raw.pop(args.benchmark, None)
    if spy_df is None or len(spy_df) < 220:
        spy_raw = yf.download(args.benchmark, start=lookback_start, end=forward_end,
                              progress=False, auto_adjust=True)
        if isinstance(spy_raw.columns, pd.MultiIndex):
            spy_raw.columns = spy_raw.columns.get_level_values(0)
        spy_df = spy_raw[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])

    # Download VIX and VIX3M for regime/term-structure features
    def _dl_index(symbol: str) -> pd.DataFrame | None:
        try:
            raw_idx = yf.download(symbol, start=lookback_start, end=forward_end,
                                  progress=False, auto_adjust=True)
            if isinstance(raw_idx.columns, pd.MultiIndex):
                raw_idx.columns = raw_idx.columns.get_level_values(0)
            cols = [c for c in ["Close"] if c in raw_idx.columns]
            if not cols:
                return None
            return raw_idx[cols].dropna(subset=["Close"])
        except Exception as exc:
            print(f"WARNING: Could not download {symbol}: {exc}")
            return None

    print("Downloading VIX / VIX3M for regime features...")
    vix_df = _dl_index("^VIX")
    vix3m_df = _dl_index("^VIX3M")

    # Download sector ETFs for breadth feature
    _SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY",
                    "XLP", "XLU", "XLB", "XLRE", "XLC"]
    sector_dfs: dict = {}
    print("Downloading sector ETFs for breadth feature...")
    for etf in _SECTOR_ETFS:
        sdf = _dl_index(etf)
        if sdf is not None:
            sector_dfs[etf] = sdf
    print(f"  Downloaded {len(sector_dfs)}/{len(_SECTOR_ETFS)} sector ETFs.")

    scan_dates = pd.bdate_range(args.start, args.end, freq=f"{args.freq}B")
    resume_dataset = bool(getattr(args, "resume_dataset", False) and dataset_path.exists())
    completed_tickers = _completed_dataset_tickers(dataset_path) if resume_dataset else set()
    if completed_tickers:
        print(
            f"Resuming dataset build: {len(completed_tickers):,} tickers already present "
            f"in {dataset_path.resolve()}"
        )
    header_written = dataset_path.exists() and resume_dataset
    dataset_columns: list[str] | None = None
    if header_written:
        with dataset_path.open(newline="", encoding="utf-8", errors="replace") as f:
            dataset_columns = next(csv.reader(f), None)
    total_rows = 0
    chunk = []
    backtest.MIN_PRICE = 0.0
    if dataset_path.exists() and args.rebuild_dataset and not resume_dataset:
        dataset_path.unlink()

    for ticker, df in tqdm(raw.items(), desc="Build stock/date ML rows", unit="ticker"):
        if ticker.upper() in completed_tickers:
            continue
        if df is None or len(df) < MIN_HISTORY + args.hold + 2:
            continue
        try:
            pc = precompute(df)
        except Exception:
            continue
        idx = df.index
        for date_ts in scan_dates:
            pos = int(idx.searchsorted(date_ts, side="right")) - 1
            if pos < args.min_history or pos >= len(df) - args.hold - 1:
                continue
            if abs((idx[pos] - date_ts).days) > 5:
                continue

            spy = _spy_metrics(spy_df, date_ts)
            vix = _vix_metrics(vix_df, vix3m_df, date_ts)
            sector = _sector_metrics(sector_dfs, date_ts)
            row = _base_features(
                ticker, df, pc, pos, idx[pos], spy,
                args.hold, args.target_mult, args.stop_mult,
                vix=vix, sector=sector,
            )
            try:
                rule_score, rule_signals = score_at(
                    pc, df, pos, args.target_mult, args.stop_mult,
                    regime=spy.get("spy_regime", "unknown"),
                    vix_reg=vix.get("vix_regime", "unknown"),
                    vix_ts=vix.get("vix_ts"),
                    sector_breadth=sector.get("sector_breadth"),
                    score_mode=args.score_mode,
                    spy_close=spy.get("spy_close"),
                    spy_sma50=spy.get("spy_sma50"),
                    spy_sma200=spy.get("spy_sma200"),
                    spy_ret5=spy.get("spy_ret5"),
                    spy_ret20=spy.get("spy_ret20"),
                    spy_ret1=spy.get("spy_ret1"),
                    vix_1d_chg=vix.get("vix_1d_chg"),
                )
            except Exception:
                rule_score, rule_signals = 0.0, {}

            if rule_signals:
                row.update(rule_signals)
                row["score"] = rule_score
                row["rule_score"] = rule_score
                gate_status = str(rule_signals.get("confirmed_pullback_gates", ""))
                failed = [] if gate_status == "pass" else [r for r in gate_status.split(",") if r]
                row["failed_rule_reasons"] = "|".join(failed)
                row["passed_rule_reasons"] = "confirmed_pullback_rule_pass" if not failed else ""
                row["rule_pass"] = bool(rule_score >= args.rule_threshold and not failed)
                row["candidate_status"] = "executed" if row["rule_pass"] else "rejected"
                row["setup_type"] = "pullback"
            else:
                row["failed_rule_reasons"] = "not_scoreable_or_hard_filter"
                row["passed_rule_reasons"] = ""

            out = _label_all_stock_outcome(
                df, pos, args.hold, args.target_mult, args.stop_mult,
                float(row.get("atr") or 0.0),
            )
            if not out:
                continue
            h = args.hold
            row[f"h{h}_outcome"] = out["outcome"]
            row[f"h{h}_entry"] = out["entry_price"]
            row[f"h{h}_target"] = out["target_price"]
            row[f"h{h}_stop"] = out["stop_price"]
            row[f"h{h}_return"] = out["actual_return"]
            row[f"h{h}_exit"] = out["exit_price"]
            row[f"h{h}_exit_date"] = out["exit_date"]
            row[f"h{h}_days"] = out["days_held"]
            row[f"h{h}_mae"] = out["mae"]
            row[f"h{h}_mfe"] = out["mfe"]
            row[f"h{h}_r_multiple"] = out["r_multiple"]
            row[f"h{h}_target_hit"] = out["hit_target"]
            row[f"h{h}_stopped_out"] = out["hit_stop"]
            row[f"h{h}_direction_correct"] = out["actual_return"] > 0
            row[f"h{h}_bad_loss"] = out["actual_return"] <= args.bad_loss_pct
            row[f"h{h}_strong_win"] = out["actual_return"] >= args.missed_big_win_pct

            chunk.append(row)
            total_rows += 1
            if len(chunk) >= args.write_chunk_size:
                frame = pd.DataFrame(chunk)
                if dataset_columns is None:
                    dataset_columns = list(frame.columns)
                frame.reindex(columns=dataset_columns).to_csv(
                    dataset_path, mode="a", header=not header_written, index=False
                )
                header_written = True
                chunk = []

    if chunk:
        frame = pd.DataFrame(chunk)
        if dataset_columns is None:
            dataset_columns = list(frame.columns)
        frame.reindex(columns=dataset_columns).to_csv(
            dataset_path, mode="a", header=not header_written, index=False
        )

    print(f"\nStock/date training dataset rows: {total_rows:,}")
    print(f"Dataset saved: {dataset_path.resolve()}")
    return dataset_path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pull all stock data, label every ticker/date, and train ML gates."
    )
    parser.add_argument("--tickers", default="all_tickers.txt")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--output-dir", default="ml_models/stock_universe")
    parser.add_argument("--dataset-csv", default=None)
    parser.add_argument("--price-cache", default=None, help="Path to reusable downloaded OHLCV pickle.")
    parser.add_argument("--reuse-dataset", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rebuild-dataset", action="store_true", help="Rebuild dataset CSV even if it already exists.")
    parser.add_argument("--resume-dataset", action="store_true", help="Append to an existing dataset CSV and skip tickers already present.")
    parser.add_argument("--rebuild-price-cache", action="store_true", help="Force rebuilding the trainer price cache.")
    parser.add_argument("--hold", type=int, default=3)
    parser.add_argument("--freq", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--yfinance-threads",
        type=int,
        default=8,
        help="Parallel yfinance workers inside each batch. Use 0 to disable threading.",
    )
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--show-yfinance-errors",
        action="store_true",
        help="Show Yahoo/yfinance per-symbol errors in the console. Default writes them to a log file.",
    )
    parser.add_argument("--max-tickers", type=int, default=None, help="Debug only. Default uses every ticker.")
    parser.add_argument(
        "--include-non-common",
        action="store_true",
        help="Include warrants/units/rights/preferred-like symbols. Default filters them out.",
    )
    parser.add_argument(
        "--no-active-filter",
        action="store_true",
        help="Do not filter against active Nasdaq Trader listed symbols.",
    )
    parser.add_argument(
        "--include-etfs",
        action="store_true",
        help="Include ETFs from the active listed-symbol filter. Default is stocks only.",
    )
    parser.add_argument("--min-history", type=int, default=220)
    parser.add_argument("--write-chunk-size", type=int, default=50000)
    parser.add_argument("--score-mode", default="confirmed_pullback")
    parser.add_argument("--rule-threshold", type=float, default=100.0)
    parser.add_argument("--target-mult", type=float, default=0.9)
    parser.add_argument("--stop-mult", type=float, default=1.1)
    parser.add_argument("--bad-loss-pct", type=float, default=-0.03)
    parser.add_argument("--missed-big-win-pct", type=float, default=0.05)
    parser.add_argument("--skip-train", action="store_true", help="Only build the stock/date dataset CSV.")
    parser.add_argument("--max-train-rows", type=int, default=0, help="Default 0 uses every generated row.")
    parser.add_argument("--min-rows", type=int, default=300)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-samples-leaf", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ml-probability-threshold", type=float, default=0.62)
    parser.add_argument("--ml-expected-return-min", type=float, default=0.0)
    parser.add_argument("--ml-large-loss-max", type=float, default=0.15)
    parser.add_argument("--gate-diagnostics-limit", type=int, default=250)
    return parser.parse_args()


def main():
    args = parse_args()
    dataset = build_stock_dataset(args)
    if args.skip_train:
        return
    train_args = SimpleNamespace(
        input=str(dataset),
        output_dir=args.output_dir,
        hold=args.hold,
        max_rows=args.max_train_rows,
        min_rows=args.min_rows,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        min_samples_leaf=args.min_samples_leaf,
        seed=args.seed,
        ml_probability_threshold=args.ml_probability_threshold,
        ml_expected_return_min=args.ml_expected_return_min,
        ml_large_loss_max=args.ml_large_loss_max,
        gate_diagnostics_limit=args.gate_diagnostics_limit,
    )
    report = train_models(train_args)
    print("\nStock-universe ML training complete")
    print(f"  Dataset      : {dataset.resolve()}")
    print(f"  Rows trained : {report['settings']['rows_used']:,}")
    print(f"  Model bundle : {report['artifacts']['model_bundle']}")
    print(f"  Report       : {report['artifacts']['training_report']}")


if __name__ == "__main__":
    main()
