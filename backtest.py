#!/usr/bin/env python3
"""
TradingAgents Backtest v3
Scans all tickers over a long historical period and measures ACTUAL outcomes
using real High/Low data for intraday stop/target detection.

Collects every signal imaginable so we can analyse what actually predicts
next-day / multi-day returns and improve the screener from real data.

Usage:
    python backtest.py                                   # defaults: 2020-2024, threshold 65
    python backtest.py --start 2019-01-01 --end 2024-12-31
    python backtest.py --threshold 70 --no-cache
    python backtest.py --tickers all_tickers.txt
    python backtest.py --grid-search --grid-thresholds 55 60 65 70 75
    python backtest.py --walk-forward --wf-window 252 --wf-step 63
    python backtest.py --monte-carlo --mc-sims 1000
    python backtest.py --export-csv results.csv

Caches raw price data to .backtest_cache/ so re-runs are instant.
Output: backtest_results_<timestamp>.json
"""

import argparse
import hashlib
import json
import pickle
import random
import datetime
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm
import time

# ── Config ────────────────────────────────────────────────────────────────────
BATCH_SIZE   = 50
MIN_PRICE    = 5.0        # filter stocks under $5 (overridden by --min-price)
MIN_HISTORY  = 80         # min trading days needed to score
HOLD_PERIODS = [1, 2, 3, 5]
CACHE_DIR    = Path(".backtest_cache")


# ── Ticker loading ────────────────────────────────────────────────────────────

def load_tickers(path: str) -> list:
    lines = Path(path).read_text().strip().splitlines()
    seen = {}
    for t in lines:
        t = t.strip().upper()
        if t and t not in seen:
            seen[t] = True
    return list(seen.keys())


# ── Data download with disk cache ─────────────────────────────────────────────

def download_all(tickers: list, start: str, end: str, no_cache: bool = False,
                 batch_size: int = BATCH_SIZE, threads=False) -> dict:
    """Download OHLCV for all tickers in batches, caching each batch to disk."""
    CACHE_DIR.mkdir(exist_ok=True)
    batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
    all_data: dict = {}
    failed_batches = 0

    print(f"\nDownloading {len(tickers)} tickers in {len(batches)} batches "
          f"({start} → {end}, batch_size={batch_size})...")
    print("Cached batches load instantly. First run may take 15-30 min.\n")

    # Round end date to next Monday so cache is stable across a full week
    import datetime as _dt
    try:
        _end_d = _dt.date.fromisoformat(str(end))
        # snap forward to next Monday (or keep if already Monday)
        _days_to_monday = (7 - _end_d.weekday()) % 7
        _cache_end = (_end_d + _dt.timedelta(days=_days_to_monday)).isoformat()
    except Exception:
        _cache_end = str(end)

    for i, batch in enumerate(tqdm(batches, desc="Batches", unit="batch")):
        batch_sig = hashlib.sha256(",".join(batch).encode("utf-8")).hexdigest()[:16]
        cache_key  = f"{start}_{_cache_end}_bs{batch_size}_{batch_sig}"
        cache_path = CACHE_DIR / f"batch_{cache_key}.pkl"

        if not no_cache and cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    all_data.update(pickle.load(f))
                continue
            except Exception:
                pass

        batch_data = {}
        for attempt in range(3):
            try:
                raw = yf.download(
                    batch, start=start, end=end,
                    progress=False, auto_adjust=True, threads=threads,
                )
                batch_data = _extract_ticker_dfs(raw, batch)
                break
            except Exception as exc:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    tqdm.write(f"  Batch {i} failed after 3 attempts: {exc}")
                    failed_batches += 1

        if batch_data:
            with open(cache_path, "wb") as f:
                pickle.dump(batch_data, f)
            all_data.update(batch_data)

    if failed_batches:
        print(f"\n  WARNING: {failed_batches}/{len(batches)} batches failed. "
              f"Re-run with --no-cache to retry failed batches.")

    return all_data


def _extract_ticker_dfs(raw: pd.DataFrame, tickers: list) -> dict:
    result = {}
    cols = ["Open", "High", "Low", "Close", "Volume"]
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = raw.columns.get_level_values(0)
        for ticker in tickers:
            try:
                df_cols = {c: raw[c][ticker] for c in cols
                           if c in level0 and ticker in raw[c].columns}
                if "Close" not in df_cols:
                    continue
                df = pd.DataFrame(df_cols).dropna(subset=["Close"])
                if len(df) >= 10:
                    result[ticker] = df
            except Exception:
                pass
    elif len(tickers) == 1:
        df = raw[[c for c in cols if c in raw.columns]].dropna(subset=["Close"])
        if len(df) >= 10:
            result[tickers[0]] = df
    return result


# ── Price / volume filter ─────────────────────────────────────────────────────

def filter_by_price(data: dict, min_price: float = MIN_PRICE,
                    max_price: float = None) -> dict:
    # Do not filter on whole-history median price: that leaks future information.
    kept = {
        ticker: df for ticker, df in data.items()
        if df is not None and not df.empty and "Close" in df.columns
    }
    removed = len(data) - len(kept)
    print(
        "Static price pre-filter disabled to avoid look-ahead bias; "
        f"removed {removed} empty datasets, kept {len(kept)}. "
        "Min/max price is enforced at each scan date."
    )
    return kept


# ── New indicator helper functions ────────────────────────────────────────────

def _atr_series(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14) -> pd.Series:
    """Proper Wilder ATR as a pandas Series."""
    prev_c = c.shift(1).fillna(c)
    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _stochastic_series(h: pd.Series, l: pd.Series, c: pd.Series,
                        k: int = 14, d: int = 3):
    """Stochastic oscillator. Returns (stoch_k, stoch_d)."""
    min_l  = l.rolling(k).min()
    max_h  = h.rolling(k).max()
    denom  = (max_h - min_l).replace(0, np.nan)
    stoch_k = 100 * (c - min_l) / denom
    stoch_d = stoch_k.rolling(d).mean()
    return stoch_k, stoch_d


def _adx_series(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14):
    """Wilder ADX with +DI and -DI. Returns (adx, pdi, mdi)."""
    prev_c   = c.shift(1).fillna(c)
    tr       = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    up       = h.diff()
    dn       = -l.diff()
    plus_dm  = pd.Series(
        np.where((up > dn) & (up > 0), up, 0.0), index=h.index
    )
    minus_dm = pd.Series(
        np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index
    )
    atr_w    = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    pdi      = 100 * plus_dm.ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr_w.replace(0, np.nan)
    mdi      = 100 * minus_dm.ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr_w.replace(0, np.nan)
    dx       = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx      = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx, pdi, mdi


def _obv_series(c: pd.Series, v: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(c.diff()).fillna(0)
    return (direction * v).cumsum()


def _mfi_series(h: pd.Series, l: pd.Series, c: pd.Series,
                v: pd.Series, period: int = 14) -> pd.Series:
    """Money Flow Index."""
    tp  = (h + l + c) / 3
    mf  = tp * v
    pos = pd.Series(np.where(tp > tp.shift(1), mf, 0.0), index=c.index)
    neg = pd.Series(np.where(tp < tp.shift(1), mf, 0.0), index=c.index)
    mfr = (pos.rolling(period).sum()
           / neg.rolling(period).sum().replace(0, np.nan))
    return 100 - (100 / (1 + mfr))


def _cmf_series(h: pd.Series, l: pd.Series, c: pd.Series,
                v: pd.Series, period: int = 20) -> pd.Series:
    """Chaikin Money Flow."""
    clv = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)
    return (clv * v).rolling(period).sum() / v.rolling(period).sum().replace(0, np.nan)


def _cci_series(h: pd.Series, l: pd.Series, c: pd.Series,
                period: int = 14) -> pd.Series:
    """Commodity Channel Index."""
    tp  = (h + l + c) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def _roc_series(c: pd.Series, period: int) -> pd.Series:
    """Rate of Change."""
    return (c / c.shift(period) - 1) * 100


def _streak(s: pd.Series) -> pd.Series:
    """Count consecutive True values, resetting on False."""
    result = [0] * len(s)
    cnt = 0
    for i, val in enumerate(s):
        cnt = cnt + 1 if val else 0
        result[i] = cnt
    return pd.Series(result, index=s.index)


# ── Original indicator helpers ────────────────────────────────────────────────

def _rsi_series(closes: pd.Series, period: int = 9) -> pd.Series:
    delta  = closes.diff()
    gains  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs     = gains / losses.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(100)


def _macd_series(closes: pd.Series):
    ema12  = closes.ewm(span=12, adjust=False).mean()
    ema26  = closes.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return macd, signal, hist


def _bb_series(closes: pd.Series, window: int = 20):
    mid   = closes.rolling(window).mean()
    std   = closes.rolling(window).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    pct_b = (closes - lower) / (upper - lower).replace(0, np.nan)
    return pct_b


def _atr_at(highs, lows, closes, pos: int, period: int = 14) -> float:
    start  = max(0, pos - period * 2)
    h      = highs.iloc[start:pos + 1].values
    l      = lows.iloc[start:pos + 1].values
    c      = closes.iloc[start:pos + 1].values
    if len(c) < 2:
        return float(h[-1] - l[-1]) if len(h) else 1.0
    prev_c = np.roll(c, 1); prev_c[0] = c[0]
    tr     = np.maximum(h - l,
             np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    tail   = tr[-period:] if len(tr) >= period else tr
    return float(tail.mean())


# ── Per-ticker precomputation ─────────────────────────────────────────────────

def precompute(df: pd.DataFrame) -> dict:
    """
    Compute every rolling series for a ticker upfront.
    Scoring at any date is then just array indexing — fast.
    """
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    o = df["Open"]
    v = df["Volume"]

    macd, macd_sig, macd_hist = _macd_series(c)
    bb_pct                    = _bb_series(c)

    # New indicators
    atr14_true              = _atr_series(h, l, c, 14)
    stoch_k, stoch_d        = _stochastic_series(h, l, c, 14, 3)
    adx14, dmi_plus, dmi_minus = _adx_series(h, l, c, 14)
    obv                     = _obv_series(c, v)
    mfi14                   = _mfi_series(h, l, c, v, 14)
    cmf20                   = _cmf_series(h, l, c, v, 20)
    cci14                   = _cci_series(h, l, c, 14)
    roc10                   = _roc_series(c, 10)
    roc20                   = _roc_series(c, 20)
    ema20                   = c.ewm(span=20, adjust=False).mean()
    ema21                   = c.ewm(span=21, adjust=False).mean()

    # Keltner channels
    kelt_upper = ema20 + 2 * atr14_true
    kelt_lower = ema20 - 2 * atr14_true

    # Bollinger bands (for squeeze detection)
    bb_mid   = c.rolling(20).mean()
    bb_std   = c.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Squeeze: BB inside Keltner
    squeeze  = ((bb_upper < kelt_upper) & (bb_lower > kelt_lower)).astype(float)

    # PVT
    pvt_raw  = ((c.diff() / c.shift(1).replace(0, np.nan)) * v).cumsum()

    # Consecutive up/down streaks
    up_mask   = (c > c.shift(1)).fillna(False)
    dn_mask   = (c < c.shift(1)).fillna(False)
    consec_up   = _streak(up_mask)
    consec_down = _streak(dn_mask)

    rsi9_series = _rsi_series(c, 9)

    # RSI slope: RSI change over 3 days (positive = recovering from oversold)
    rsi9_slope3 = rsi9_series - rsi9_series.shift(3)

    # MACD histogram slope: histogram change over 3 days (positive = building momentum)
    macd_hist_slope3 = macd_hist - macd_hist.shift(3)

    return {
        # ── Raw OHLCV ─────────────────────────────────────────────────────
        "close":  c, "high": h, "low": l, "open": o, "volume": v,

        # ── Trend ─────────────────────────────────────────────────────────
        "sma20":  c.rolling(20).mean(),
        "sma50":  c.rolling(50).mean(),
        "sma200": c.rolling(200).mean(),
        "ema9":   c.ewm(span=9, adjust=False).mean(),
        "ema21":  ema21,

        # ── Momentum ──────────────────────────────────────────────────────
        "rsi9":   _rsi_series(c, 9),
        "rsi14":  _rsi_series(c, 14),
        "ret1d":  c.pct_change(1),
        "ret3d":  c.pct_change(3),
        "ret5d":  c.pct_change(5),
        "ret10d": c.pct_change(10),
        "ret20d": c.pct_change(20),

        # ── MACD ──────────────────────────────────────────────────────────
        "macd":      macd,
        "macd_sig":  macd_sig,
        "macd_hist": macd_hist,

        # ── Bollinger ─────────────────────────────────────────────────────
        "bb_pct": bb_pct,

        # ── Volume ────────────────────────────────────────────────────────
        # Shift by 1: exclude today from avg so vol_ratio_20d has no self-reference
        "vol20":  v.shift(1).rolling(20).mean(),
        "vol50":  v.shift(1).rolling(50).mean(),
        "vol10":  v.shift(1).rolling(10).mean(),
        "vol5":   v.shift(1).rolling(5).mean(),
        "vol3p":  v.shift(1).rolling(3).mean(),
        "vol_ob": v.shift(1).rolling(5).mean() / v.shift(1).rolling(20).mean().replace(0, np.nan),

        # ── Range / volatility ────────────────────────────────────────────
        "range5h":  h.rolling(5).max(),
        "range5l":  l.rolling(5).min(),
        "range20h": h.rolling(20).max(),
        "range20l": l.rolling(20).min(),
        "high10":   h.rolling(10).max(),
        "high50":   h.rolling(50).max(),   # 50-day high for breakout_v2
        "high52w":  h.rolling(252).max(),
        "low52w":   l.rolling(252).min(),
        "atr14":    (h - l).rolling(14).mean(),    # simplified ATR proxy

        # ── Wilder ATR (proper) ────────────────────────────────────────────
        "atr14_true": atr14_true,

        # ── Stochastic ────────────────────────────────────────────────────
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,

        # ── ADX / DMI ─────────────────────────────────────────────────────
        "adx14":     adx14,
        "dmi_plus":  dmi_plus,
        "dmi_minus": dmi_minus,

        # ── OBV ───────────────────────────────────────────────────────────
        "obv":      obv,
        "obv_sma20": obv.rolling(20).mean(),

        # ── MFI / CMF / CCI ───────────────────────────────────────────────
        "mfi14": mfi14,
        "cmf20": cmf20,
        "cci14": cci14,

        # ── ROC ───────────────────────────────────────────────────────────
        "roc10": roc10,
        "roc20": roc20,

        # ── Donchian channels (20-day) ────────────────────────────────────
        "donch_upper20": h.rolling(20).max(),
        "donch_lower20": l.rolling(20).min(),

        # ── Bollinger Band width (for breakout compression features) ─────
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid":   bb_mid,
        "bb_width": (bb_upper - bb_lower) / bb_mid.replace(0, np.nan),

        # ── Keltner channels ──────────────────────────────────────────────
        "kelt_upper": kelt_upper,
        "kelt_lower": kelt_lower,

        # ── Squeeze ───────────────────────────────────────────────────────
        "squeeze": squeeze,

        # ── Close location value ──────────────────────────────────────────
        "close_loc": (c - l) / (h - l).replace(0, np.nan),

        # ── 52-week range rank ────────────────────────────────────────────
        "range_rank_52w": (
            (c - l.rolling(252).min()) /
            (h.rolling(252).max() - l.rolling(252).min()).replace(0, np.nan)
        ),

        # ── SMA slopes ───────────────────────────────────────────────────
        "slope_sma20": c.rolling(20).mean() - c.rolling(20).mean().shift(5),
        "slope_sma50": c.rolling(50).mean() - c.rolling(50).mean().shift(5),
        "slope_sma200_20d": c.rolling(200).mean() - c.rolling(200).mean().shift(20),

        # ── PVT ───────────────────────────────────────────────────────────
        "pvt":      pvt_raw,
        "pvt_sma20": pvt_raw.rolling(20).mean(),

        # ── Consecutive streaks ───────────────────────────────────────────
        "consec_up":   consec_up,
        "consec_down": consec_down,

        # ── Momentum slopes ────────────────────────────────────────────────
        "rsi9_slope3":       rsi9_slope3,
        "macd_hist_slope3":  macd_hist_slope3,

        # ── Candle features ───────────────────────────────────────────────
        "day_range_pct": (h - l) / c.replace(0, np.nan),
        "body_pct":      (c - o) / (h - l).replace(0, np.nan),
        "upper_wick":    (h - c.clip(lower=o)) / (h - l).replace(0, np.nan),
        "lower_wick":    (c.clip(upper=o) - l) / (h - l).replace(0, np.nan),
        "gap_pct":       (o / c.shift(1) - 1),
        "inside_day":    ((h <= h.shift(1)) & (l >= l.shift(1))).astype(float),
        "engulf_bull":   ((o < l.shift(1)) & (c > h.shift(1))).astype(float),
    }


# ── Swing scoring ─────────────────────────────────────────────────────────────

def score_at(pc: dict, df: pd.DataFrame, pos: int,
             target_mult: float = 2.0, stop_mult: float = 1.0,
             regime: str = "unknown", vix_reg: str = "unknown",
             vix_ts: float = None, sector_breadth: float = None,
             score_mode: str = "breakout",
             spy_close: float = None,
             spy_sma50: float = None,
             spy_sma200: float = None,
             spy_ret5: float = None,
             spy_ret20: float = None,
             spy_ret1: float = None,
             vix_1d_chg: float = None) -> tuple:
    """
    Score the swing setup at integer position `pos`.
    Returns (score, signals_dict) or (0, {}) if not scoreable.
    Records ALL extra features for post-analysis even if they don't affect score.
    """
    price = float(pc["close"].iloc[pos])
    if price < MIN_PRICE:
        return 0.0, {}

    # Hard filters: very high ATR% stocks (>8%) lose money
    # ATR bucket analysis: very_high (>8%) pf=0.984 (negative).
    atr_quick = float(pc["atr14_true"].iloc[pos]) if pd.notna(pc["atr14_true"].iloc[pos]) else (price * 0.02)
    if atr_quick / price > 0.08:
        return 0.0, {}
    score_penalty_100 = False

    sig   = {}
    score = 0.0

    def g(key):
        try:
            val = pc[key].iloc[pos]
            return float(val) if pd.notna(val) else None
        except Exception:
            return None

    # ── Raw features (always recorded) ────────────────────────────────────
    sig["ret_1d"]  = g("ret1d")  or 0.0
    sig["ret_3d"]  = g("ret3d")  or 0.0
    sig["ret_5d"]  = g("ret5d")  or 0.0
    sig["ret_10d"] = g("ret10d") or 0.0
    sig["ret_20d"] = g("ret20d") or 0.0

    rsi9  = g("rsi9")  or 50.0
    rsi14 = g("rsi14") or 50.0
    sig["rsi9"]  = round(rsi9,  1)
    sig["rsi14"] = round(rsi14, 1)

    # Momentum slope features
    rsi9_slope3 = g("rsi9_slope3")
    sig["rsi9_slope3"] = round(rsi9_slope3, 2) if rsi9_slope3 is not None else None
    macd_hist_slope3 = g("macd_hist_slope3")
    sig["macd_hist_slope3"] = round(macd_hist_slope3, 5) if macd_hist_slope3 is not None else None

    # SPY 1-day return and VIX 1-day change
    sig["spy_ret1"] = round(spy_ret1, 4) if spy_ret1 is not None else None
    sig["vix_1d_chg"] = round(vix_1d_chg, 4) if vix_1d_chg is not None else None

    macd_h = g("macd_hist") or 0.0
    sig["macd_hist"] = round(macd_h, 4)
    sig["macd_bull"] = 1 if macd_h > 0 else 0

    bb = g("bb_pct")
    sig["bb_pct"] = round(bb, 3) if bb is not None else None

    sig["gap_pct"]       = round(g("gap_pct")    or 0.0, 4)
    sig["body_pct"]      = round(g("body_pct")   or 0.0, 3)
    sig["upper_wick"]    = round(g("upper_wick") or 0.0, 3)
    sig["lower_wick"]    = round(g("lower_wick") or 0.0, 3)
    sig["day_range_pct"] = round(g("day_range_pct") or 0.0, 4)
    sig["inside_day"]    = int(g("inside_day")   or 0)
    sig["engulf_bull"]   = int(g("engulf_bull")  or 0)

    sma20  = g("sma20");  sig["sma20"]  = round(sma20,  2) if sma20  else None
    sma50  = g("sma50");  sig["sma50"]  = round(sma50,  2) if sma50  else None
    sma200 = g("sma200"); sig["sma200"] = round(sma200, 2) if sma200 else None

    h52w = g("high52w")
    l52w = g("low52w")
    sig["pct_from_52w_high"] = round((price - h52w) / h52w, 4) if h52w else None
    sig["pct_from_52w_low"]  = round((price - l52w) / l52w, 4) if l52w else None

    atr = _atr_at(pc["high"], pc["low"], pc["close"], pos)
    sig["atr"]     = round(atr, 3)
    sig["atr_pct"] = round(atr / price, 4) if price > 0 else None

    vol_today = float(pc["volume"].iloc[pos])
    v20 = g("vol20") or 0.0
    v10 = g("vol10") or 0.0
    sig["vol_ratio_20d"] = round(vol_today / v20, 3) if v20 > 0 else None
    sig["vol_ratio_10d"] = round(vol_today / v10, 3) if v10 > 0 else None
    sig["vol_trend"]     = round(v10 / v20, 3) if v20 > 0 else None

    # New indicator values recorded
    stoch_k_val = g("stoch_k")
    stoch_d_val = g("stoch_d")
    mfi14_val   = g("mfi14")
    cci14_val   = g("cci14")
    adx14_val   = g("adx14")
    obv_val     = g("obv")
    obv_sma_val = g("obv_sma20")
    cmf20_val   = g("cmf20")
    close_loc_v = g("close_loc")
    squeeze_val = g("squeeze")
    slope20_val = g("slope_sma20")
    pvt_val     = g("pvt")
    pvt_sma_val = g("pvt_sma20")
    rr52w_val   = g("range_rank_52w")

    sig["stoch_k"]      = round(stoch_k_val, 2) if stoch_k_val is not None else None
    sig["stoch_d"]      = round(stoch_d_val, 2) if stoch_d_val is not None else None
    sig["mfi14"]        = round(mfi14_val, 2)   if mfi14_val   is not None else None
    sig["cci14"]        = round(cci14_val, 2)   if cci14_val   is not None else None
    sig["adx14"]        = round(adx14_val, 2)   if adx14_val   is not None else None
    sig["cmf20"]        = round(cmf20_val, 4)   if cmf20_val   is not None else None
    sig["close_loc"]    = round(close_loc_v, 3) if close_loc_v is not None else None
    sig["squeeze"]      = int(squeeze_val)       if squeeze_val is not None else 0
    sig["range_rank_52w"] = round(rr52w_val, 3) if rr52w_val   is not None else None
    sig["roc10"]        = round(g("roc10") or 0.0, 3)
    sig["roc20"]        = round(g("roc20") or 0.0, 3)
    sig["consec_up"]    = int(g("consec_up")   or 0)
    sig["consec_down"]  = int(g("consec_down") or 0)

    if score_mode == "confirmed_pullback":
        gates_failed = []
        dollar_vol20 = price * v20 if v20 > 0 else 0.0
        atr_pct = atr / price if price > 0 else 0.0
        if not (5.0 <= price <= 500.0):
            gates_failed.append("price_not_5_500")
        if dollar_vol20 <= 5_000_000:
            gates_failed.append("dollar_volume_below_5m")
        if not (0.005 <= atr_pct <= 0.08):
            gates_failed.append("atr_pct_not_0p5_to_8")
        if not (spy_close and spy_sma50 and spy_sma200
                and spy_close > spy_sma50 and spy_close > spy_sma200):
            gates_failed.append("spy_not_above_50_200")
        if spy_ret5 is None or spy_ret5 <= -0.03:
            gates_failed.append("spy_5d_too_weak")
        # Block elevated VIX (25-35): grinding bear, pullbacks continue lower
        if vix_reg == "elevated":
            gates_failed.append("vix_elevated_regime")
        slope200 = g("slope_sma200_20d")
        if not (sma200 and price > sma200):
            gates_failed.append("stock_not_above_sma200")
        if slope200 is None or slope200 <= 0:
            gates_failed.append("sma200_not_rising_20d")
        # Stock must be near or above SMA50 — 3% tolerance for pullbacks to 50d MA
        if not (sma50 and price >= sma50 * 0.97):
            gates_failed.append("stock_below_sma50_by_3pct")
        # Tighter relative strength: stock must nearly match SPY 20d return (not lag >3%)
        if spy_ret20 is None or (sig["ret_20d"] - spy_ret20) <= -0.03:
            gates_failed.append("relative_20d_too_weak")
        h10 = g("high10")
        pct_high = (price - h10) / h10 if h10 and h10 > 0 else None
        sig["pct_from_10d_high"] = round(pct_high, 4) if pct_high is not None else None
        if pct_high is None or not (-0.08 <= pct_high <= -0.02):
            gates_failed.append("not_2_to_8pct_below_10d_high")
        # Tighter RSI band: 39-52 = healthy pullback, not extreme oversold or barely dipped
        if not (39 <= rsi9 <= 52):
            gates_failed.append("rsi9_not_39_52")
        cci_prev = float(pc["cci14"].iloc[pos - 1]) if pos > 0 and pd.notna(pc["cci14"].iloc[pos - 1]) else None
        if cci14_val is None or cci_prev is None or cci14_val <= cci_prev:
            gates_failed.append("cci14_not_improving")
        macd_prev1 = float(pc["macd_hist"].iloc[pos - 1]) if pos > 0 and pd.notna(pc["macd_hist"].iloc[pos - 1]) else None
        macd_prev2 = float(pc["macd_hist"].iloc[pos - 2]) if pos > 1 and pd.notna(pc["macd_hist"].iloc[pos - 2]) else None
        if macd_prev1 is None or macd_prev2 is None or not (macd_h > macd_prev1 > macd_prev2):
            gates_failed.append("macd_hist_not_improving_2d")
        # MACD histogram must still be negative — negative+rising = base building (best pattern)
        # Positive+rising = momentum already resumed, late entry with poor R:R
        if macd_h is not None and macd_h >= 0:
            gates_failed.append("macd_hist_positive_late_entry")
        # Tighter volume dryup: ≤0.90 of 20d avg — real contraction, not just stable
        if sig["vol_ratio_20d"] is None or sig["vol_ratio_20d"] > 0.90:
            gates_failed.append("volume_not_drying_up")

        sig["dollar_vol20"] = round(dollar_vol20, 0)
        sig["sma200_rising_20d"] = round(slope200, 4) if slope200 is not None else None
        sig["spy_ret1"] = round(spy_ret1, 4) if spy_ret1 is not None else None
        sig["spy_ret5"] = round(spy_ret5, 4) if spy_ret5 is not None else None
        sig["spy_ret20"] = round(spy_ret20, 4) if spy_ret20 is not None else None
        sig["rel_ret20_vs_spy"] = round(sig["ret_20d"] - spy_ret20, 4) if spy_ret20 is not None else None
        # ── Per-stock regime: stock's relative trend vs SPY ──────────────────
        # "outperforming"  : stock 20d return > SPY 20d return + 3%  (leading)
        # "neutral"        : within ±3% of SPY                        (tracking)
        # "underperforming": stock 20d return < SPY 20d return - 3%  (lagging)
        # Pullback-in-uptrend buys from "neutral"/"outperforming" have better edge.
        # Buying laggards = trying to catch falling knives.
        if spy_ret20 is not None and sig.get("ret_20d") is not None:
            _rel = sig["ret_20d"] - spy_ret20
            sig["stock_regime"] = (
                "outperforming" if _rel > 0.03
                else ("underperforming" if _rel < -0.03 else "neutral")
            )
        else:
            sig["stock_regime"] = "unknown"
        sig["vix_1d_chg"] = round(vix_1d_chg, 4) if vix_1d_chg is not None else None
        sig["cci14_prev"] = round(cci_prev, 2) if cci_prev is not None else None
        sig["macd_hist_prev1"] = round(macd_prev1, 4) if macd_prev1 is not None else None
        sig["macd_hist_prev2"] = round(macd_prev2, 4) if macd_prev2 is not None else None
        # ── Additional features (were in ML_NUMERIC_FEATURES but missing from sig dict) ──
        slope20_cp = g("slope_sma20")
        slope50_cp = g("slope_sma50")
        sig["slope_sma20"] = round(slope20_cp, 4) if slope20_cp is not None else None
        sig["slope_sma50"] = round(slope50_cp, 4) if slope50_cp is not None else None
        sig["obv_above_sma"] = int(obv_val > obv_sma_val) if (obv_val is not None and obv_sma_val is not None) else None
        sig["pvt_above_sma"] = int(pvt_val > pvt_sma_val) if (pvt_val is not None and pvt_sma_val is not None) else None
        dmi_p_cp = g("dmi_plus")
        dmi_m_cp = g("dmi_minus")
        sig["dmi_bull"] = int(dmi_p_cp > dmi_m_cp) if (dmi_p_cp is not None and dmi_m_cp is not None) else None
        # ATR expansion: ratio of today's ATR vs rolling 20d average ATR
        # Values > 1.0 = volatility expansion (breakout-like); < 1.0 = compression
        atr14_today = float(pc["atr14_true"].iloc[pos]) if pd.notna(pc["atr14_true"].iloc[pos]) else None
        atr14_roll20 = float(pc["atr14_true"].iloc[max(0, pos-20):pos].mean()) if pos >= 5 else None
        sig["atr_expansion"] = round(atr14_today / atr14_roll20, 3) if (atr14_today and atr14_roll20 and atr14_roll20 > 0) else None
        signal_high = float(pc["high"].iloc[pos])
        signal_low = float(pc["low"].iloc[pos])
        trigger = signal_high + 0.05 * atr
        sig["vix_ts"] = round(vix_ts, 3) if vix_ts is not None else None
        sig["sector_breadth"] = round(sector_breadth, 3) if sector_breadth is not None else None
        sig["entry"] = round(trigger, 2)
        sig["trigger"] = round(trigger, 2)
        sig["signal_high"] = round(signal_high, 2)
        sig["signal_low"] = round(signal_low, 2)
        sig["target"] = round(trigger + target_mult * atr, 2)
        sig["stop"] = round(max(signal_low - 0.2 * atr, trigger - stop_mult * atr), 2)
        sig["risk_reward"] = round((sig["target"] - sig["entry"]) / max(sig["entry"] - sig["stop"], 0.01), 2)
        sig["confirmed_pullback_gates"] = "pass" if not gates_failed else ",".join(gates_failed)
        if gates_failed:
            sig["coil_pts"] = 0.0
            sig["brk_pts"] = 0.0
            sig["trend_pts"] = 0.0
            sig["vol_pts"] = 0.0
            sig["regime_adj"] = 0.0
            return 0.0, sig
        sig["coil_pts"] = 0.0
        sig["brk_pts"] = 25.0
        sig["trend_pts"] = 25.0
        sig["vol_pts"] = 25.0
        sig["regime_adj"] = 25.0
        return 100.0, sig

    if score_mode == "breakout":
        coil = 0.0
        r5h = g("range5h"); r5l = g("range5l")
        r20h = g("range20h"); r20l = g("range20l")
        if r5h is not None and r5l is not None and r20h is not None and r20l is not None:
            range5 = r5h - r5l
            range20 = r20h - r20l
            contr = range5 / range20 if range20 > 0 else 1.0
            sig["contraction"] = round(contr, 3)
            if contr <= 0.30: coil += 15.0
            elif contr <= 0.45: coil += 10.0
            elif contr <= 0.60: coil += 5.0
        v3p = g("vol3p")
        if v3p is not None and v20 > 0:
            dryup = v3p / v20
            sig["vol_dryup"] = round(dryup, 3)
            if dryup <= 0.65: coil += 10.0
            elif dryup <= 0.80: coil += 6.0
            elif dryup <= 0.95: coil += 3.0
        coil = min(coil, 25.0)

        brk = 0.0
        h10 = g("high10")
        if h10 and h10 > 0:
            pct_high = (price - h10) / h10
            sig["pct_from_10d_high"] = round(pct_high, 4)
            if pct_high >= -0.005: brk += 15.0
            elif pct_high >= -0.02: brk += 10.0
            elif pct_high >= -0.04: brk += 5.0
        if 52 <= rsi9 <= 65: brk += 10.0
        elif 65 < rsi9 <= 72: brk += 5.0
        elif 45 <= rsi9 < 52: brk += 4.0
        brk = min(brk, 25.0)

        trend = 0.0
        if sma20 and price > sma20: trend += 12.0
        if sma50 and price > sma50: trend += 13.0
        trend = min(trend, 25.0)

        vol_pts = 0.0
        ratio = sig["vol_ratio_20d"] or 1.0
        if ratio >= 2.0: vol_pts = 25.0
        elif ratio >= 1.5: vol_pts = 17.0
        elif ratio >= 1.2: vol_pts = 10.0
        elif ratio >= 1.0: vol_pts = 5.0

        score = coil + brk + trend + vol_pts
        sig["coil_pts"] = round(coil, 1)
        sig["brk_pts"] = round(brk, 1)
        sig["trend_pts"] = round(trend, 1)
        sig["vol_pts"] = round(vol_pts, 1)
        sig["regime_adj"] = 0.0
        sig["vix_ts"] = round(vix_ts, 3) if vix_ts is not None else None
        sig["sector_breadth"] = round(sector_breadth, 3) if sector_breadth is not None else None
        sig["entry"] = round(price, 2)
        sig["target"] = round(price + target_mult * atr, 2)
        sig["stop"] = round(price - stop_mult * atr, 2)
        sig["risk_reward"] = round(target_mult / stop_mult, 2)
        return round(min(score, 100.0), 1), sig

    # ── breakout_v2: VCP / range-breakout scoring (full feature set) ──────────
    if score_mode == "breakout_v2":
        # Hard liquidity / quality gates
        dollar_vol20_bv2 = price * v20 if v20 > 0 else 0.0
        atr_pct_bv2 = atr / price if price > 0 else 1.0
        if price < 5.0 or price > 600.0:
            return 0.0, {}
        if dollar_vol20_bv2 < 5_000_000:
            return 0.0, {}
        if atr_pct_bv2 > 0.08:
            return 0.0, {}

        # ── Resistance proximity ──────────────────────────────────────────
        h20  = g("range20h")  # 20-day high (pre-shifted in precompute — no leakage)
        h50  = g("high50")
        h52w = g("high52w")
        pct_from_20d_high = (price - h20) / h20 if h20 and h20 > 0 else None
        if pct_from_20d_high is not None:
            sig["pct_from_20d_high"] = round(pct_from_20d_high, 4)
        if h50 and h50 > 0:
            sig["pct_from_50d_high"] = round((price - h50) / h50, 4)
        if h52w and h52w > 0:
            sig["pct_from_52w_high"] = round((price - h52w) / h52w, 4)

        # ── Range contraction (VCP) ───────────────────────────────────────
        r5h = g("range5h"); r5l = g("range5l")
        r20h = g("range20h"); r20l = g("range20l")
        range_contraction = None
        if r5h and r5l and r20h and r20l:
            r5 = r5h - r5l; r20 = r20h - r20l
            if r20 > 0:
                range_contraction = r5 / r20
                sig["range_contraction_5_20"] = round(range_contraction, 3)

        # ── ATR compression = 5d ATR / 20d ATR (< 0.8 = compressed) ─────
        if pos >= 20:
            atr5_val  = float(pc["atr14_true"].iloc[max(0, pos-4):pos+1].mean()) if pd.notna(pc["atr14_true"].iloc[pos]) else None
            atr20_val = float(pc["atr14_true"].iloc[max(0, pos-19):pos+1].mean())
            if atr5_val and atr20_val and atr20_val > 0:
                atr_compr = atr5_val / atr20_val
                sig["atr_compression"] = round(atr_compr, 3)

        # ── ATR expansion (today vs rolling 20d) ─────────────────────────
        if pos >= 20:
            atr20_mean = float(pc["atr14_true"].iloc[max(0, pos-20):pos].mean())
            if atr and atr20_mean and atr20_mean > 0:
                sig["atr_expansion"] = round(atr / atr20_mean, 3)

        # ── Bollinger Band width ──────────────────────────────────────────
        bb_w = g("bb_width")
        if bb_w is not None:
            sig["bb_width"] = round(bb_w, 4)

        # ── Keltner squeeze ───────────────────────────────────────────────
        sig["keltner_squeeze"] = int(squeeze_val) if squeeze_val is not None else 0

        # ── Volume features ───────────────────────────────────────────────
        vol_surge_1d = sig.get("vol_ratio_20d") or 1.0
        v3p = g("vol3p")
        if v3p and v20 > 0:
            sig["vol_surge_3d"] = round(v3p / v20, 3)
        v5_prior = g("vol5")  # prior 5d avg volume (shifted so no today)
        vol_dryup_5d = None
        if v5_prior and v20 > 0:
            vol_dryup_5d = v5_prior / v20
            sig["vol_dryup_5d"] = round(vol_dryup_5d, 3)

        # ── OBV slope (5d change normalized) ─────────────────────────────
        if pos >= 5 and pd.notna(pc["obv"].iloc[pos]) and pd.notna(pc["obv"].iloc[pos-5]):
            obv_now_v  = float(pc["obv"].iloc[pos])
            obv_5d_ago = float(pc["obv"].iloc[pos-5])
            norm = price * v20 * 5 if (v20 > 0) else 1.0
            sig["obv_slope_5d"] = round((obv_now_v - obv_5d_ago) / norm, 6) if norm > 0 else None

        # ── Trend alignment ───────────────────────────────────────────────
        sig["above_sma20"]  = int(bool(sma20  and price > sma20))
        sig["above_sma50"]  = int(bool(sma50  and price > sma50))
        sig["above_sma200"] = int(bool(sma200 and price > sma200))
        sma_alignment = 1 if (sma20 and sma50 and sma200 and sma20 > sma50 > sma200) else 0
        sig["sma_alignment"] = sma_alignment

        ema9_val = g("ema9")
        if ema9_val and atr > 0:
            sig["price_vs_ema9"] = round((price - ema9_val) / atr, 3)

        # ── Relative strength vs SPY ──────────────────────────────────────
        rel_str_5d = None; rel_str_20d = None
        if sig.get("ret_5d") is not None and spy_ret5 is not None:
            rel_str_5d = sig["ret_5d"] - spy_ret5
            sig["rel_strength_5d"] = round(rel_str_5d, 4)
        if sig.get("ret_20d") is not None and spy_ret20 is not None:
            rel_str_20d = sig["ret_20d"] - spy_ret20
            sig["rel_strength_20d"] = round(rel_str_20d, 4)
        if rel_str_5d is not None and rel_str_20d is not None:
            sig["rs_momentum"] = int(rel_str_5d > rel_str_20d)

        # ── Failed breakout detection ─────────────────────────────────────
        # consec_failed_highs: days in last 5 where new 5d high but closed lower
        cfh = 0
        if pos >= 5:
            for k in range(pos - 4, pos + 1):
                ph = float(pc["high"].iloc[k]) if pd.notna(pc["high"].iloc[k]) else 0.0
                pk_h5 = float(pc["high"].iloc[max(0, k-5):k].max()) if k > 0 else 0.0
                pk_c  = float(pc["close"].iloc[k]) if pd.notna(pc["close"].iloc[k]) else 0.0
                pk_o  = float(pc["open"].iloc[k])  if pd.notna(pc["open"].iloc[k])  else pk_c
                if ph > 0 and pk_h5 > 0 and ph >= pk_h5 * 0.999 and pk_c < pk_o:
                    cfh += 1
        sig["consec_failed_highs"] = cfh

        # prev_breakout_failed: prior vol surge + near high that gave back gains
        prev_fail = 0
        if pos >= 10:
            for k in range(pos - 9, pos - 4):
                v20k = float(pc["vol20"].iloc[k]) if pd.notna(pc["vol20"].iloc[k]) else 0.0
                vk   = float(pc["volume"].iloc[k]) if pd.notna(pc["volume"].iloc[k]) else 0.0
                hk   = float(pc["high"].iloc[k])   if pd.notna(pc["high"].iloc[k])   else 0.0
                h20k = float(pc["range20h"].iloc[k]) if pd.notna(pc["range20h"].iloc[k]) else 0.0
                ck   = float(pc["close"].iloc[k])  if pd.notna(pc["close"].iloc[k])  else 0.0
                if v20k > 0 and vk / v20k >= 1.5 and h20k > 0 and hk >= h20k * 0.99:
                    for j in range(k + 1, min(k + 6, pos + 1)):
                        cj = float(pc["close"].iloc[j]) if pd.notna(pc["close"].iloc[j]) else 0.0
                        if cj < ck:
                            prev_fail = 1
                            break
                if prev_fail:
                    break
        sig["prev_breakout_failed"] = prev_fail

        # ── Additional fields for ML compat ──────────────────────────────
        sig["vix_ts"]         = round(vix_ts, 3) if vix_ts is not None else None
        sig["sector_breadth"] = round(sector_breadth, 3) if sector_breadth is not None else None
        sig["spy_ret5"]       = round(spy_ret5, 4) if spy_ret5 is not None else None
        sig["spy_ret20"]      = round(spy_ret20, 4) if spy_ret20 is not None else None
        sig["dollar_vol20"]   = round(dollar_vol20_bv2, 0)
        sig["atr_pct"]        = round(atr_pct_bv2, 4)

        # ── Scoring: 4 components × 25 pts ───────────────────────────────
        # Compression (25 pts): is stock coiling?
        compression_pts = 0.0
        if range_contraction is not None:
            if   range_contraction <= 0.30: compression_pts += 12
            elif range_contraction <= 0.45: compression_pts += 8
            elif range_contraction <= 0.60: compression_pts += 4
        if squeeze_val and squeeze_val > 0.5:
            compression_pts += 5
        if vol_dryup_5d is not None:
            if   vol_dryup_5d <= 0.65: compression_pts += 8
            elif vol_dryup_5d <= 0.80: compression_pts += 5

        # Rejection day hard gate: large upper wick kills compression credit
        uw_val = sig.get("upper_wick") or 0.0
        if uw_val > 0.60:
            compression_pts = 0.0

        compression_pts = min(compression_pts, 25.0)

        # Confirmation (25 pts): is today the breakout day?
        confirmation_pts = 0.0
        if pct_from_20d_high is not None:
            if   pct_from_20d_high >= -0.005: confirmation_pts += 15
            elif pct_from_20d_high >= -0.020: confirmation_pts += 10
            elif pct_from_20d_high >= -0.040: confirmation_pts += 5
        rsi14_bv2 = sig.get("rsi14") or 50.0
        if   50 <= rsi14_bv2 <= 65: confirmation_pts += 10
        elif 65 < rsi14_bv2 <= 75:  confirmation_pts += 5
        if uw_val < 0.30:
            confirmation_pts += 3  # clean day, no major rejection
        confirmation_pts = min(confirmation_pts, 25.0)

        # Trend (25 pts): trend favorable?
        trend_bv2 = 0.0
        if sig.get("above_sma20"):  trend_bv2 += 8
        if sig.get("above_sma50"):  trend_bv2 += 9
        if sig.get("above_sma200"): trend_bv2 += 8
        trend_bv2 = min(trend_bv2, 25.0)

        # Volume (25 pts): volume confirming?
        volume_pts = 0.0
        if   vol_surge_1d >= 2.0: volume_pts = 25.0
        elif vol_surge_1d >= 1.5: volume_pts = 17.0
        elif vol_surge_1d >= 1.2: volume_pts = 10.0
        elif vol_surge_1d >= 1.0: volume_pts = 5.0

        breakout_score = compression_pts + confirmation_pts + trend_bv2 + volume_pts

        # Classify breakout type
        bv2_type = "consolidation_setup"
        if prev_fail and uw_val > 0.40 and (sig.get("close_loc") or 0.5) < 0.40:
            bv2_type = "failed_breakout_risk"
        elif (sig.get("gap_pct") or 0.0) > 0.01 and sig.get("above_sma20") and sig.get("above_sma50") and vol_surge_1d >= 1.5:
            bv2_type = "gap_continuation"
        elif pct_from_20d_high is not None and pct_from_20d_high >= -0.01 and vol_surge_1d >= 1.5 and uw_val < 0.35:
            bv2_type = "range_breakout"
        elif vol_surge_1d >= 2.0 and sig.get("above_sma50"):
            bv2_type = "volume_breakout"
        elif sma_alignment and pct_from_20d_high is not None and pct_from_20d_high >= -0.05:
            bv2_type = "trend_continuation"
        sig["breakout_type"] = bv2_type

        # Store score components (aliased for ML compat)
        sig["coil_pts"]        = round(compression_pts, 1)
        sig["brk_pts"]         = round(confirmation_pts, 1)
        sig["trend_pts"]       = round(trend_bv2, 1)
        sig["vol_pts"]         = round(volume_pts, 1)
        sig["compression_pts"] = round(compression_pts, 1)
        sig["confirmation_pts"] = round(confirmation_pts, 1)
        sig["breakout_score"]  = round(breakout_score, 1)
        sig["regime_adj"]      = 0.0
        sig["confirmed_pullback_gates"] = "pass"  # ML frame compat

        # Price targets (breakout uses 3× ATR target, 1.5× ATR stop)
        sig["entry"]       = round(price, 2)
        sig["target"]      = round(price + 3.0 * atr, 2)
        sig["stop"]        = round(price - 1.5 * atr, 2)
        sig["risk_reward"] = 2.0

        return round(min(breakout_score, 100.0), 1), sig

    # ── 1. Consolidation / coiling (25 pts) ───────────────────────────────
    # Require BOTH range contraction AND volume dry-up for full points.
    # Either condition alone gets reduced credit — true coils need both.
    coil = 0.0
    r5h  = g("range5h"); r5l  = g("range5l")
    r20h = g("range20h"); r20l = g("range20l")
    contr = 1.0
    has_contraction = False
    if r5h is not None and r20h is not None:
        range5  = r5h - r5l
        range20 = r20h - r20l
        contr   = range5 / range20 if range20 > 0 else 1.0
        sig["contraction"] = round(contr, 3)
        if   contr <= 0.30:  has_contraction = True;  coil += 10.0
        elif contr <= 0.45:  has_contraction = True;  coil +=  7.0
        elif contr <= 0.60:                           coil +=  3.0

    v3p = g("vol3p")
    has_dryup = False
    if v3p is not None and v20 > 0:
        dryup = v3p / v20
        sig["vol_dryup"] = round(dryup, 3)
        if   dryup <= 0.65:  has_dryup = True;  coil += 8.0
        elif dryup <= 0.80:  has_dryup = True;  coil += 5.0
        elif dryup <= 0.95:                     coil += 2.0

    # Bonus when BOTH conditions confirm — true coil setup
    if has_contraction and has_dryup:
        coil += 5.0

    # Squeeze (BB inside Keltner): additional coil confirmation
    if squeeze_val and squeeze_val > 0.5:
        coil += 4.0

    # ADX < 20 = no meaningful trend = coiling energy building
    if adx14_val is not None and adx14_val < 20:
        coil += 3.0
    elif adx14_val is not None and adx14_val < 25:
        coil += 1.0

    coil = min(coil, 25.0)
    score += coil
    sig["coil_pts"] = round(coil, 1)

    # ── 2. Position quality (25 pts) ─────────────────────────────────────
    brk = 0.0
    h10 = g("high10")
    if h10 and h10 > 0:
        pct_high = (price - h10) / h10
        sig["pct_from_10d_high"] = round(pct_high, 4)
        if   -0.06 <= pct_high < -0.01:  brk += 10.0
        elif  pct_high >= -0.01:          brk +=  4.0
        elif -0.10 <= pct_high < -0.06:   brk +=  6.0

    # Reward oversold RSI — this is a mean-reversion pullback strategy.
    # Oversold RSI signals real selling exhaustion; neutral RSI is less interesting.
    if   rsi9 < 30:            brk += 14.0   # extreme oversold = max pullback
    elif rsi9 < 38:            brk += 11.0
    elif 38 <= rsi9 < 48:      brk +=  8.0
    elif 48 <= rsi9 < 58:      brk +=  4.0
    # RSI > 58: no bonus (extended, not a pullback entry)

    dist_52w = sig.get("pct_from_52w_high")
    if dist_52w is not None:
        if   dist_52w < -0.25:  brk += 5.0
        elif dist_52w < -0.10:  brk += 3.0
        elif dist_52w > -0.03:  brk -= 3.0

    # Stochastic %K < 45 (not overbought)
    if stoch_k_val is not None and stoch_k_val < 45:
        brk += 3.0

    # MFI 30–60 range (balanced money flow)
    if mfi14_val is not None and 30 <= mfi14_val <= 60:
        brk += 3.0

    brk = max(0.0, min(brk, 25.0))
    score += brk
    sig["brk_pts"] = round(brk, 1)

    # ── 3. Trend health (25 pts) ─────────────────────────────────────────
    # Reward pullback setups: stock in a longer-term uptrend (above SMA200)
    # but currently below the shorter SMAs (pulled back to support).
    # Best setup: above SMA200 AND below SMA50 (healthy pullback in uptrend).
    trend = 0.0
    above_sma200 = sma200 and price > sma200
    above_sma50  = sma50  and price > sma50
    above_sma20  = sma20  and price > sma20

    if above_sma200 and not above_sma50:
        # Ideal: pulled back below SMA50 but long-term uptrend intact
        trend += 12.0
    elif above_sma200 and above_sma50 and not above_sma20:
        # Moderate: pulled back below SMA20, still above SMA50
        trend += 8.0
    elif above_sma200 and above_sma50 and above_sma20:
        # Extended: above all SMAs — not a pullback, less interesting
        trend += 4.0
    # Below SMA200: broken long-term trend — no trend points

    # OBV above its 20-day SMA: accumulation during pullback = bullish divergence
    if (obv_val is not None and obv_sma_val is not None
            and obv_val > obv_sma_val):
        trend += 6.0

    # SMA20 slope negative + SMA50 positive = healthy correction, not breakdown
    slope50_val = g("slope_sma50")
    if (slope20_val is not None and slope20_val < 0
            and slope50_val is not None and slope50_val > 0):
        trend += 4.0
    elif slope50_val is not None and slope50_val > 0:
        trend += 2.0

    # DMI+: bullish momentum. Reward when +DI > -DI (buyers still in control)
    dmi_p = g("dmi_plus")
    dmi_m = g("dmi_minus")
    if dmi_p is not None and dmi_m is not None and dmi_p > dmi_m:
        trend += 3.0

    trend = min(trend, 25.0)
    score += trend
    sig["trend_pts"] = round(trend, 1)

    # ── 4. Volume quality and volatility filter (25 pts) ─────────────────
    vol_pts = 0.0
    ratio   = sig["vol_ratio_20d"] or 1.0

    if   ratio <= 0.70:  vol_pts += 12.0
    elif ratio <= 0.90:  vol_pts +=  8.0
    elif ratio <= 1.10:  vol_pts +=  4.0

    dr = sig["day_range_pct"] or 0.0
    if   dr <= 0.015:  vol_pts += 8.0
    elif dr <= 0.025:  vol_pts += 5.0
    elif dr <= 0.035:  vol_pts += 2.0

    uw = sig["upper_wick"] or 0.0
    if   uw >= 0.40:  vol_pts += 5.0
    elif uw >= 0.25:  vol_pts += 3.0

    r1d = sig["ret_1d"]
    r3d = sig["ret_3d"]
    if r1d > 0.04:    vol_pts -= 8.0
    elif r1d > 0.02:  vol_pts -= 4.0
    if r3d > 0.07:    vol_pts -= 5.0

    # CMF: corr=-0.0212 — higher CMF *hurts*. Invert: reward accumulation quiet (CMF slightly negative = sellers exhausted)
    # Removed the CMF>0 bonus. CMF < -0.05 would be too oversold; we just skip it as a positive signal.

    # PVT rising (pvt > pvt_sma20)
    if (pvt_val is not None and pvt_sma_val is not None
            and pvt_val > pvt_sma_val):
        vol_pts += 2.0

    # Close location: corr=-0.0259 — stocks closing in LOWER part of day's range outperform.
    # Winners avg close_loc=0.477, losers=0.501. Reward close near bottom (mean-reversion setup).
    if close_loc_v is not None:
        if   close_loc_v < 0.35:  vol_pts += 4.0   # closed near low = buyers absorbed selling
        elif close_loc_v < 0.50:  vol_pts += 2.0   # mid-range close = neutral
        # close_loc > 0.60: no bonus (closed near high = extended)

    # Bearish candle body on signal day = mean-reversion setup (body_pct corr=-0.0098)
    # Winners avg body_pct=-0.012 (bearish candle), losers=+0.009 (bullish candle)
    bp = sig["body_pct"]
    if bp < -0.15:   vol_pts += 3.0   # strong bearish candle = pullback day = good entry
    elif bp < -0.05: vol_pts += 1.0

    vol_pts = max(0.0, min(vol_pts, 25.0))
    score   += vol_pts
    sig["vol_pts"] = round(vol_pts, 1)

    # ── 5. Market regime adjustment (up to +17 / down to -20) ─────────────
    # VIX low_vol (< 15): PF=0.887, Sortino=-0.111 — loses money.
    # VIX elevated (25-35): PF=0.989, Sortino=-0.011 — grinding bear, also loses.
    # VIX normal (15-25): PF=1.257, Sortino=0.199 — best environment.
    # VIX crisis (> 35): PF=1.582, Sortino=0.352 — capitulation, strongest edge.
    regime_adj = 0.0
    if   vix_reg == "crisis":    regime_adj += 8.0   # capitulation = high edge
    elif vix_reg == "elevated":  regime_adj -= 6.0   # grinding bear = mean-rev fails
    elif vix_reg == "normal":    regime_adj += 1.0
    elif vix_reg == "low_vol":   regime_adj -= 10.0  # no fear = no edge

    if regime == "bear":
        regime_adj += 5.0
    elif regime == "bull" and vix_reg == "low_vol":
        regime_adj -= 3.0   # double penalty: calm bull market = worst conditions

    # VIX term structure (from v5): backwardation (ratio < 1.0) = near-term fear
    # spike > long-term vol — highest risk of false breakdowns; skip longs.
    if vix_ts is not None:
        if   vix_ts < 0.90:  regime_adj -= 8.0   # deep backwardation = fear spike
        elif vix_ts < 1.00:  regime_adj -= 3.0   # mild backwardation = caution
        elif vix_ts > 1.15:  regime_adj += 2.0   # deep contango = calm/rising

    # Sector breadth (from v5): fraction of 11 SPDR sectors with positive 20d return.
    # Broad sector weakness = bad environment for individual stock longs.
    if sector_breadth is not None:
        if   sector_breadth >= 0.73:  regime_adj += 3.0   # 8+ of 11 sectors rising
        elif sector_breadth >= 0.55:  regime_adj += 1.0   # majority positive
        elif sector_breadth <= 0.27:  regime_adj -= 5.0   # 3- of 11 sectors rising
        elif sector_breadth <= 0.45:  regime_adj -= 2.0   # minority positive

    regime_adj = max(-20.0, min(regime_adj, 17.0))
    score += regime_adj
    sig["regime_adj"]      = round(regime_adj, 1)
    sig["vix_ts"]          = round(vix_ts, 3) if vix_ts is not None else None
    sig["sector_breadth"]  = round(sector_breadth, 3) if sector_breadth is not None else None

    # ── Price targets ──────────────────────────────────────────────────────
    sig["entry"]       = round(price, 2)
    sig["target"]      = round(price + target_mult * atr, 2)
    sig["stop"]        = round(price - stop_mult  * atr, 2)
    sig["risk_reward"] = round(target_mult / stop_mult, 2)

    if score_mode == "oversold_bounce":
        atr_pct_ob = atr / price if price > 0 else 1.0
        dollar_vol20_ob = price * v20 if v20 > 0 else 0.0

        # Core liquidity / volatility gate (all tiers)
        if price < 5.0 or atr_pct_ob >= 0.03 or dollar_vol20_ob < 1_000_000:
            return 0.0, {}
        if mfi14_val is None:
            return 0.0, {}

        # Skip extreme fear — stocks keep falling regardless of oversold reading
        if vix_reg == "extreme_fear":
            return 0.0, {}

        # ── Tier 1: Bear + normal VIX (controlled selloff) ──────────────────
        # Highest win-rate setup. Wide RSI/MFI gate catches all oversold stocks.
        # Strict breadth + term-structure filters ensure temporary, localized panic.
        if regime == "bear" and vix_reg == "normal":
            if rsi14 >= 50 or mfi14_val >= 50:
                return 0.0, {}
            # Localized panic only — broad bear (many sectors oversold) keeps falling
            if sector_breadth is not None and sector_breadth >= 0.30:
                return 0.0, {}
            # Near-contango VIX term structure = temporary panic, not sustained fear
            if vix_ts is not None and vix_ts >= 1.08:
                return 0.0, {}
            # KNOWLEDGE GATE: RSI not in freefall — wait for selling to slow
            rsi9_slope = g("rsi9_slope3")
            if rsi9_slope is not None and rsi9_slope < -10:
                return 0.0, {}
            tier = 1

        # ── Tier 2: Bull market individual-stock panic ───────────────────────
        # Stock punished hard (earnings miss, sector news) while broad market is healthy.
        # Require deeply oversold (RSI/MFI < 35) to filter noise; bull trend pulls it back.
        elif regime == "bull":
            if rsi14 >= 35 or mfi14_val >= 35:
                return 0.0, {}
            # Skip if VIX is elevated/high — means broad market stress, not isolated panic
            if vix_reg in ("elevated", "extreme_fear"):
                return 0.0, {}
            # KNOWLEDGE GATE: Stock must be above SMA50 — buying a pullback in an uptrend,
            # not a breakdown. Below SMA50 = stock in downtrend, bounces fail at SMA50 resistance.
            if sma50 and price < sma50:
                return 0.0, {}
            # KNOWLEDGE GATE: RSI must not be in freefall (slope > -8 over 3 days).
            # Entering while RSI is still collapsing = catching a falling knife.
            # Wait for selling pressure to slow — the turn, not the fall.
            rsi9_slope = g("rsi9_slope3")
            if rsi9_slope is not None and rsi9_slope < -8:
                return 0.0, {}
            # KNOWLEDGE GATE: Volume must be drying up (below 10-day average).
            # Heavy continued selling volume = no exhaustion yet; wait for sellers to leave.
            vol_ratio_10d_ob = g("vol_ratio_10d")
            if vol_ratio_10d_ob is not None and vol_ratio_10d_ob >= 1.5:
                return 0.0, {}
            tier = 2

        else:
            return 0.0, {}

        # Confidence score — higher = bigger position
        h52w = g("high52w")
        pct_from_high = abs((price - h52w) / h52w) if h52w and h52w > 0 else 0.0

        rsi_base  = 50.0 if tier == 1 else 35.0
        mfi_base  = 50.0 if tier == 1 else 35.0
        rsi_conf   = max(0.0, (rsi_base - rsi14) / rsi_base)
        mfi_conf   = max(0.0, (mfi_base - mfi14_val) / mfi_base)
        atr_conf   = max(0.0, 1.0 - atr_pct_ob / 0.03)
        depth_conf = min(1.0, pct_from_high / 0.25)

        # Tier 1 trades get a confidence bonus (proven higher win rate)
        tier_boost = 0.10 if tier == 1 else 0.0
        confidence = min(1.0, round(
            rsi_conf * 0.35 + mfi_conf * 0.35 + atr_conf * 0.15 + depth_conf * 0.15 + tier_boost, 3
        ))

        sig["confidence"]      = confidence
        sig["ob_tier"]         = tier
        sig["atr_pct"]         = round(atr_pct_ob, 4)
        sig["dollar_vol20"]    = round(dollar_vol20_ob, 0)
        sig["vix_ts"]          = round(vix_ts, 3) if vix_ts is not None else None
        sig["sector_breadth"]  = round(sector_breadth, 3) if sector_breadth is not None else None
        sig["vix_1d_chg"]      = round(vix_1d_chg, 4) if vix_1d_chg is not None else None
        sig["spy_ret1"]        = round(spy_ret1, 4) if spy_ret1 is not None else None
        sig["entry"]           = round(price, 2)
        sig["target"]          = round(price + target_mult * atr, 2)
        sig["stop"]            = round(price - stop_mult * atr, 2)
        sig["risk_reward"]     = round(target_mult / stop_mult, 2)
        sig["coil_pts"]        = 25.0
        sig["brk_pts"]         = 25.0
        sig["trend_pts"]       = 25.0
        sig["vol_pts"]         = 25.0
        sig["regime_adj"]      = 0.0
        sig["confirmed_pullback_gates"] = "pass"
        return 100.0, sig

    # ── Dead-zone correction ───────────────────────────────────────────────
    # Score bucket 92-97 showed PF=1.014 (near-zero edge) while 97+ recovers.
    # Stocks scoring 92-97 tend to be "too perfect" on paper but extended in reality.
    # Push them down 5 pts so they either fall below threshold or land in the 87-92
    # bucket where edge exists. Scores 97+ are kept — small sample but genuine outliers.
    final_score = min(score, 100.0)
    if 92.0 <= final_score < 97.0:
        final_score -= 5.0

    return round(final_score, 1), sig


# ── Outcome measurement ───────────────────────────────────────────────────────

def measure_outcome(df: pd.DataFrame, signal_pos: int, entry: float,
                    target: float, stop: float, hold_days: int,
                    entry_timing: str = "next_open",
                    target_mult: float = None,
                    stop_mult: float = None,
                    atr: float = None) -> dict:
    """
    Check what ACTUALLY happened using real High/Low each day.
      - Gap open below stop  → stopped at open
      - Gap open above target → target hit at open
      - Both intraday → infer from close vs midpoint
      - Neither in hold_days → exit at final close
    Also computes MAE, MFE, and R-multiple.
    """
    future = df.iloc[signal_pos + 1 : signal_pos + 1 + hold_days]
    if len(future) == 0:
        return None

    if entry_timing == "next_open":
        entry = float(future["Open"].iloc[0])
        if atr is not None and target_mult is not None and stop_mult is not None:
            target = entry + target_mult * atr
            stop = entry - stop_mult * atr
    elif entry_timing == "trigger_break":
        if atr is None:
            return None
        signal_high = float(df["High"].iloc[signal_pos])
        signal_low = float(df["Low"].iloc[signal_pos])
        signal_close = float(df["Close"].iloc[signal_pos])
        trigger = signal_high + 0.05 * atr
        next_open = float(future["Open"].iloc[0])
        next_high = float(future["High"].iloc[0])
        if next_open > signal_close + 0.7 * atr:
            return None
        if next_high < trigger:
            return None
        entry = next_open if next_open >= trigger else trigger
        target = entry + (target_mult if target_mult is not None else 0.9) * atr
        stop = max(signal_low - 0.2 * atr, entry - (stop_mult if stop_mult is not None else 1.1) * atr)

    outcome   = "TIMED_OUT"
    exit_px   = float(future["Close"].iloc[-1])
    exit_date = str(future.index[-1].date())
    days_held = len(future)

    # MAE / MFE tracking
    mae = 0.0   # max adverse excursion (fraction, positive = bad)
    mfe = 0.0   # max favorable excursion (fraction, positive = good)

    for i, (dt, row) in enumerate(future.iterrows(), 1):
        o  = float(row.get("Open",  entry))
        hi = float(row.get("High",  entry))
        lo = float(row.get("Low",   entry))
        cl = float(row.get("Close", entry))

        # Update MAE/MFE regardless of stop/target
        if entry > 0:
            adverse   = (entry - lo) / entry
            favorable = (hi - entry) / entry
            if adverse   > mae: mae = adverse
            if favorable > mfe: mfe = favorable

        if o <= stop:
            outcome   = "STOP_HIT"
            exit_px   = o
            exit_date = str(dt.date())
            days_held = i
            break
        if o >= target:
            outcome   = "TARGET_HIT"
            exit_px   = o
            exit_date = str(dt.date())
            days_held = i
            break

        hit_t = hi >= target
        hit_s = lo <= stop

        if hit_t and hit_s:
            mid       = (target + stop) / 2.0
            outcome   = "TARGET_HIT" if cl >= mid else "STOP_HIT"
            exit_px   = target       if cl >= mid else stop
            exit_date = str(dt.date())
            days_held = i
            break
        elif hit_t:
            outcome   = "TARGET_HIT"
            exit_px   = target
            exit_date = str(dt.date())
            days_held = i
            break
        elif hit_s:
            outcome   = "STOP_HIT"
            exit_px   = stop
            exit_date = str(dt.date())
            days_held = i
            break

    actual_ret = (exit_px - entry) / entry if entry > 0 else 0.0
    risk       = entry - stop
    r_multiple = (exit_px - entry) / risk if risk > 0 else 0.0

    return {
        "outcome":       outcome,
        "entry_price":    round(entry,      2),
        "target_price":   round(target,     2),
        "stop_price":     round(stop,       2),
        "exit_price":    round(exit_px,    2),
        "exit_date":     exit_date,
        "actual_return": round(actual_ret, 4),
        "days_held":     days_held,
        "hit_target":    outcome == "TARGET_HIT",
        "hit_stop":      outcome == "STOP_HIT",
        "mae":           round(mae,        4),
        "mfe":           round(mfe,        4),
        "r_multiple":    round(r_multiple, 3),
    }


# ── SPY / VIX helpers ─────────────────────────────────────────────────────────

def build_spy_regime(spy_df: pd.DataFrame) -> pd.Series:
    """Classify each day into 5 SPY regime levels.

    Levels:
      bull     — price > SMA200 + SMA50 > SMA200 (strong uptrend)
      uptrend  — price > SMA200 but SMA50 <= SMA200 (recovering/early bull)
      sideways — price within ±2% of SMA200 (chop/consolidation)
      downtrend— price < SMA200 but > SMA200 * 0.95 (early bear)
      bear     — price < SMA200 * 0.95 (deep bear)

    Old callers using "bull" / "bear" boolean still work since:
      bull/uptrend → treated as bull in regime_size_factor logic
      bear/downtrend → treated as bear
    """
    close  = spy_df["Close"]
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    regime = pd.Series("unknown", index=spy_df.index)
    # Need at least 200 rows of SMA200; early rows stay "unknown"
    valid = sma200.notna()
    # sideways first (widest band — overridden by stronger signals)
    regime[valid & (close >= sma200 * 0.98) & (close <= sma200 * 1.02)] = "sideways"
    # downtrend: below SMA200 but not deeply
    regime[valid & (close < sma200 * 0.98) & (close >= sma200 * 0.95)] = "downtrend"
    # bear: price deeply below SMA200
    regime[valid & (close < sma200 * 0.95)] = "bear"
    # uptrend: above SMA200 but SMA50 not yet above SMA200
    regime[valid & (close > sma200 * 1.02) & sma50.notna() & (sma50 <= sma200)] = "uptrend"
    # bull: above SMA200, SMA50 above SMA200 (confirmed uptrend)
    regime[valid & (close > sma200 * 1.02) & sma50.notna() & (sma50 > sma200)] = "bull"

    return regime


def build_combined_regime(spy_df: pd.DataFrame, vix_df: pd.DataFrame) -> pd.Series:
    """Merge SPY regime with VIX for composite regime label.

    Additional levels beyond build_spy_regime:
      crash_risk      — bear + VIX > 35 (avoid all new longs)
      high_vol_bear   — bear + VIX > 25 (minimal exposure)
      high_vol_bull   — bull + VIX > 25 (reduce size, no new setups without strong signal)
      crash_rebound   — price recovering from crash_risk (VIX falling from >35, price >SMA200*0.95)
    """
    spy_reg = build_spy_regime(spy_df)
    vix_close = vix_df["Close"].reindex(spy_df.index, method="ffill")

    combined = spy_reg.copy()
    is_bear = spy_reg.isin(["bear", "downtrend"])
    is_bull = spy_reg.isin(["bull", "uptrend"])

    # Crash risk: bear + VIX crisis
    mask_crash = is_bear & (vix_close > 35)
    combined[mask_crash] = "crash_risk"

    # Crash rebound: VIX was in crisis (>35) within last 10 trading days
    # AND now falling + price recovering above SMA200 * 0.95
    # This is a distinct regime where mean-reversion bounces have strong historical edge
    vix_rolling_max10 = vix_close.rolling(10).max()
    sma200 = spy_df["Close"].rolling(200).mean()
    close = spy_df["Close"]
    mask_rebound = (
        (vix_rolling_max10 > 35)          # was in crisis recently
        & (vix_close <= 35)               # VIX now cooling
        & (vix_close < vix_rolling_max10 * 0.85)  # VIX fallen at least 15% from recent peak
        & (close >= sma200 * 0.93)        # not yet in deep bear (partial recovery)
    )
    combined[mask_rebound] = "crash_rebound"

    # High vol bear (non-crash)
    mask_hvbear = is_bear & (vix_close > 25) & (vix_close <= 35)
    combined[mask_hvbear] = "high_vol_bear"

    # High vol bull
    mask_hvbull = is_bull & (vix_close > 25)
    combined[mask_hvbull] = "high_vol_bull"

    return combined


def build_vix_regime(vix_df: pd.DataFrame) -> pd.Series:
    """Classify each day into VIX regime: low_vol / normal / elevated / crisis."""
    def classify(v):
        if v < 15:  return "low_vol"
        if v < 25:  return "normal"
        if v < 35:  return "elevated"
        return "crisis"
    return vix_df["Close"].apply(classify)


def build_vix_term_structure(vix_df: pd.DataFrame,
                             vix3m_df: pd.DataFrame) -> pd.Series:
    """
    VIX term structure ratio: VIX3M / VIX.
    > 1.0 = contango (normal, fear diminishing).
    < 1.0 = backwardation (fear spike, near-term uncertainty > long-term).
    Borrowed from v5: backwardation = skip long setups.
    """
    vix_aligned   = vix_df["Close"].reindex(vix3m_df.index, method="ffill")
    vix3m_aligned = vix3m_df["Close"]
    ratio = vix3m_aligned / (vix_aligned.replace(0, np.nan))
    return ratio.rename("vix_ts")


SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY",
               "XLP", "XLU", "XLB", "XLRE", "XLC"]


def build_sector_breadth(sector_dfs: dict, lookback: int = 20) -> pd.Series:
    """
    Daily fraction of 11 SPDR sectors with positive 20d return (0.0 – 1.0).
    Borrowed from v5 sector rotation: a breadth score across all sectors.
    Value near 1.0 = almost all sectors rising (broad strength).
    Value near 0.0 = most sectors falling (risk-off).
    """
    if not sector_dfs:
        return pd.Series(dtype=float)
    closes = pd.concat(
        [df["Close"].rename(s) for s, df in sector_dfs.items()], axis=1
    ).ffill()
    ret20 = closes.pct_change(lookback)
    breadth = (ret20 > 0).sum(axis=1) / ret20.notna().sum(axis=1).replace(0, np.nan)
    return breadth.rename("sector_breadth")


def spy_return_over(spy_df: pd.DataFrame, signal_pos: int, days: int) -> float:
    """SPY return over the same hold window — for alpha calculation."""
    future    = spy_df.iloc[signal_pos + 1 : signal_pos + 1 + days]
    if len(future) == 0:
        return 0.0
    entry_spy = float(spy_df["Close"].iloc[signal_pos])
    exit_spy  = float(future["Close"].iloc[-1])
    return round((exit_spy - entry_spy) / entry_spy, 4) if entry_spy > 0 else 0.0


# ── Stats helper ──────────────────────────────────────────────────────────────

def _bool_rate(df, col):
    if col not in df.columns:
        return None
    s = df[col]
    if len(s) == 0:
        return None
    return round(float(s.mean()), 4)


def _stats(df: pd.DataFrame, hold: int) -> dict:
    """Extended stats including Sortino, Calmar, Kelly, Expectancy."""
    ret_col = f"h{hold}_return"
    out_col = f"h{hold}_outcome"
    if ret_col not in df.columns or len(df) == 0:
        return {}
    rets     = pd.to_numeric(df[ret_col], errors="coerce").dropna()
    outcomes = df[out_col] if out_col in df.columns else pd.Series(dtype=str)
    if len(rets) == 0:
        return {}

    wins   = rets[rets > 0]
    losses = rets[rets <= 0]
    pf     = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    sharpe = float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0

    # Sortino
    neg_rets   = rets[rets < 0]
    down_std   = float(neg_rets.std()) if len(neg_rets) > 1 else 0.0
    sortino    = float(rets.mean() / down_std) if down_std > 0 else 0.0

    # Max drawdown (on cumulative product)
    cum        = (1 + rets).cumprod()
    rolling_max = cum.cummax()
    dd         = (rolling_max - cum) / rolling_max.replace(0, np.nan)
    max_dd     = float(dd.max()) if len(dd) > 0 else 0.0

    # Calmar — annualize by trades-per-year for this hold period, not raw *252
    ann_ret    = float(rets.mean()) * (252 / max(hold, 1))
    calmar     = ann_ret / max_dd if max_dd > 0 else 0.0

    # Kelly criterion: f* = W - (1-W)/R, reported as % of capital
    # Cap at 25% (quarter-Kelly is safer in practice; raw Kelly is theoretically optimal but volatile)
    W        = float((rets > 0).mean())
    avg_win  = float(wins.mean())   if len(wins)   else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    R        = avg_win / abs(avg_loss) if avg_loss != 0 else 0.0
    kelly_raw = W - (1 - W) / R if R > 0 else 0.0
    kelly     = max(0.0, min(kelly_raw, 0.25))   # cap at 25% of capital
    half_k    = kelly / 2

    # Expectancy per trade (in %)
    expectancy = W * avg_win * 100 + (1 - W) * avg_loss * 100

    alpha_col = f"h{hold}_alpha"
    avg_alpha = None
    if alpha_col in df.columns:
        avg_alpha = round(
            float(pd.to_numeric(df[alpha_col], errors="coerce").mean()), 4
        )

    return {
        "trades":                len(rets),
        # ── Prediction accuracy ──────────────────────────────────────────
        "direction_correct_rate": _bool_rate(df, f"h{hold}_direction_correct"),
        "target_hit_rate":        _bool_rate(df, f"h{hold}_target_hit"),
        "stopped_out_rate":       _bool_rate(df, f"h{hold}_stopped_out"),
        "strong_win_rate":        _bool_rate(df, f"h{hold}_strong_win"),
        "bad_loss_rate":          _bool_rate(df, f"h{hold}_bad_loss"),
        "beat_spy_rate":          _bool_rate(df, f"h{hold}_beat_spy"),
        # ── Returns ──────────────────────────────────────────────────────
        "win_rate":               round(float((rets > 0).mean()), 4),
        "avg_win_pct":            round(avg_win   * 100, 3) if len(wins)   else 0,
        "avg_loss_pct":           round(avg_loss  * 100, 3) if len(losses) else 0,
        "avg_return_pct":         round(float(rets.mean()   * 100), 3),
        "median_return_pct":      round(float(rets.median() * 100), 3),
        "profit_factor":          round(pf,      3),
        "sharpe_ratio":           round(sharpe,  3),
        "sortino_ratio":          round(sortino, 3),
        "calmar_ratio":           round(calmar,  3),
        "kelly_pct":              round(kelly * 100, 2),
        "half_kelly_pct":         round(half_k * 100, 2),
        "expectancy_per_trade_pct": round(expectancy, 3),
        "max_drawdown":           round(max_dd,  4),
        "avg_alpha_vs_spy":       avg_alpha,
        # ── Outcome breakdown ─────────────────────────────────────────────
        "target_hit_count":       int((outcomes == "TARGET_HIT").sum()),
        "stop_hit_count":         int((outcomes == "STOP_HIT").sum()),
        "timeout_count":          int((outcomes == "TIMED_OUT").sum()),
    }


# ── ASCII output helpers ──────────────────────────────────────────────────────

def _ascii_histogram(values, bins=10, width=40, title=""):
    """Print a simple ASCII histogram."""
    if not values:
        return
    arr    = np.array(values, dtype=float)
    counts, edges = np.histogram(arr, bins=bins)
    max_count = max(counts) or 1
    if title:
        print(f"\n  {title}")
    for i, count in enumerate(counts):
        edge = edges[i]
        bar  = "█" * int(width * count / max_count)
        print(f"  {edge:>+8.3f} │{bar:<{width}} {count:>5}")
    print(f"  {'':>9}└{'─' * width}")


def _ascii_bar_chart(data: dict, value_key: str, width: int = 30, title: str = ""):
    """Horizontal bar chart for dict of {label: stats_dict}."""
    if not data:
        return
    values = {k: v.get(value_key, 0) or 0 for k, v in data.items() if v}
    if not values:
        return
    max_v  = max(abs(v) for v in values.values()) or 1
    if title:
        print(f"\n  {title}")
    for label, val in values.items():
        bar_len = int(width * abs(val) / max_v)
        bar     = "█" * bar_len
        sign    = "+" if val >= 0 else "-"
        print(f"  {label:<18} {sign}{abs(val):>6.2f}% │ {bar}")


def _print_table(rows: list, cols: list, title: str = ""):
    """Simple fixed-width table printer."""
    if title:
        print(f"\n  {title}")
    col_widths = [max(len(str(c)), max((len(str(r.get(c, ""))) for r in rows), default=0))
                  for c in cols]
    header = "  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, col_widths))
    print(header)
    print("  " + "-" * (sum(col_widths) + 2 * len(cols)))
    for row in rows:
        line = "  " + "  ".join(str(row.get(c, "")).ljust(w) for c, w in zip(cols, col_widths))
        print(line)


# ── Trade collector (inner scan loop, reusable) ───────────────────────────────

def _diagnostic_tags(row: dict) -> list:
    tags = []
    if row.get("vix_regime") in {"low_vol", "elevated"}:
        tags.append(f"vix_{row.get('vix_regime')}")
    if row.get("spy_regime") == "bull" and row.get("vix_regime") == "low_vol":
        tags.append("calm_bull_market")
    if row.get("sector_breadth") is not None and row.get("sector_breadth") <= 0.45:
        tags.append("weak_sector_breadth")
    if row.get("vix_ts") is not None and row.get("vix_ts") < 1.0:
        tags.append("vix_backwardation")
    if row.get("atr_pct") is not None and row.get("atr_pct") >= 0.04:
        tags.append("high_atr_pct")
    if row.get("day_range_pct") is not None and row.get("day_range_pct") >= 0.035:
        tags.append("wide_signal_day")
    if row.get("ret_1d") is not None and row.get("ret_1d") > 0.02:
        tags.append("already_up_1d")
    if row.get("ret_3d") is not None and row.get("ret_3d") > 0.07:
        tags.append("already_up_3d")
    if row.get("close_loc") is not None and row.get("close_loc") > 0.60:
        tags.append("closed_near_high")
    if row.get("pct_from_10d_high") is not None and row.get("pct_from_10d_high") >= -0.01:
        tags.append("near_10d_high")
    if row.get("vol_ratio_20d") is not None and row.get("vol_ratio_20d") > 1.2:
        tags.append("high_relative_volume")
    if row.get("coil_pts") is not None and row.get("coil_pts") < 8:
        tags.append("weak_coil")
    if row.get("trend_pts") is not None and row.get("trend_pts") < 8:
        tags.append("weak_trend")
    if row.get("brk_pts") is not None and row.get("brk_pts") < 8:
        tags.append("weak_pullback_position")
    if row.get("vol_pts") is not None and row.get("vol_pts") < 8:
        tags.append("weak_volume_setup")
    if row.get("regime_adj") is not None and row.get("regime_adj") < 0:
        tags.append("negative_regime_adjustment")
    return tags or ["no_obvious_tag"]


def _compact_trade_example(row: dict, hold: int, include_rejection=False) -> dict:
    ret_col = f"h{hold}_return"
    mae_col = f"h{hold}_mae"
    mfe_col = f"h{hold}_mfe"
    result = {
        "ticker": row.get("ticker"),
        "scan_date": row.get("scan_date"),
        "score": row.get("score"),
        "return_pct": round((row.get(ret_col, 0) or 0) * 100, 2),
        "outcome": row.get(f"h{hold}_outcome"),
        "tags": _diagnostic_tags(row),
        "score_parts": {
            "coil": row.get("coil_pts"),
            "position": row.get("brk_pts"),
            "trend": row.get("trend_pts"),
            "volume": row.get("vol_pts"),
            "regime_adj": row.get("regime_adj"),
        },
        "setup": {
            "vix_regime": row.get("vix_regime"),
            "spy_regime": row.get("spy_regime"),
            "atr_pct": row.get("atr_pct"),
            "vol_ratio_20d": row.get("vol_ratio_20d"),
            "rsi9": row.get("rsi9"),
            "ret_1d": row.get("ret_1d"),
            "ret_3d": row.get("ret_3d"),
            "close_loc": row.get("close_loc"),
            "sector_breadth": row.get("sector_breadth"),
        },
        "path": {
            "mae_pct": round((row.get(mae_col, 0) or 0) * 100, 2),
            "mfe_pct": round((row.get(mfe_col, 0) or 0) * 100, 2),
        },
    }
    if include_rejection:
        result["rejection_reasons"] = row.get("rejection_reasons", [])
    return result


def _summarize_reason_counts(rows: list) -> dict:
    counts = {}
    for row in rows:
        for tag in row.get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def _return_distribution_stats(trades_df: pd.DataFrame, hold: int) -> dict:
    ret_col = f"h{hold}_return"
    if ret_col not in trades_df.columns:
        return {}
    rets = pd.to_numeric(trades_df[ret_col], errors="coerce").dropna()
    if rets.empty:
        return {}
    return {
        "p01_return_pct": round(float(rets.quantile(0.01) * 100), 3),
        "p05_return_pct": round(float(rets.quantile(0.05) * 100), 3),
        "p25_return_pct": round(float(rets.quantile(0.25) * 100), 3),
        "p50_return_pct": round(float(rets.quantile(0.50) * 100), 3),
        "p75_return_pct": round(float(rets.quantile(0.75) * 100), 3),
        "p95_return_pct": round(float(rets.quantile(0.95) * 100), 3),
        "p99_return_pct": round(float(rets.quantile(0.99) * 100), 3),
        "pct_ge_3pct": round(float((rets >= 0.03).mean()), 4),
        "pct_ge_5pct": round(float((rets >= 0.05).mean()), 4),
        "pct_le_neg3pct": round(float((rets <= -0.03).mean()), 4),
        "pct_le_neg5pct": round(float((rets <= -0.05).mean()), 4),
    }


def _loss_diagnostics(trades_df: pd.DataFrame, hold: int,
                      bad_loss_pct: float = -0.03, max_examples: int = 25) -> dict:
    ret_col = f"h{hold}_return"
    if ret_col not in trades_df.columns:
        return {}
    df = trades_df.copy()
    df[ret_col] = pd.to_numeric(df[ret_col], errors="coerce")
    losses = df[df[ret_col] < 0].copy()
    bad_losses = df[df[ret_col] <= bad_loss_pct].copy()
    if losses.empty:
        return {}
    examples = [
        _compact_trade_example(row, hold)
        for row in bad_losses.sort_values(ret_col).head(max_examples).to_dict(orient="records")
    ]
    all_loss_examples = [
        _compact_trade_example(row, hold)
        for row in losses.to_dict(orient="records")
    ]
    feature_cols = [
        "score", "coil_pts", "brk_pts", "trend_pts", "vol_pts", "regime_adj",
        "atr_pct", "day_range_pct", "vol_ratio_20d", "vol_trend", "rsi9",
        "ret_1d", "ret_3d", "ret_5d", "close_loc", "sector_breadth", "vix_ts",
        "pct_from_10d_high", "pct_from_52w_high", "range_rank_52w",
    ]
    winner_mask = df[ret_col] > 0
    loser_mask = df[ret_col] < 0
    feature_deltas = {}
    for col in feature_cols:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.dropna().empty:
            continue
        feature_deltas[col] = {
            "winner_avg": round(float(s[winner_mask].mean()), 4) if winner_mask.any() else None,
            "loser_avg": round(float(s[loser_mask].mean()), 4) if loser_mask.any() else None,
            "bad_loss_avg": round(float(s[df[ret_col] <= bad_loss_pct].mean()), 4) if len(bad_losses) else None,
        }
    by_outcome = {}
    out_col = f"h{hold}_outcome"
    if out_col in df.columns:
        for outcome, grp in losses.groupby(out_col):
            by_outcome[str(outcome)] = {
                "losses": int(len(grp)),
                "avg_return_pct": round(float(grp[ret_col].mean() * 100), 3),
                "avg_mae_pct": round(float(pd.to_numeric(grp.get(f"h{hold}_mae"), errors="coerce").mean() * 100), 3)
                               if f"h{hold}_mae" in grp.columns else None,
                "avg_mfe_pct": round(float(pd.to_numeric(grp.get(f"h{hold}_mfe"), errors="coerce").mean() * 100), 3)
                               if f"h{hold}_mfe" in grp.columns else None,
            }
    return {
        "settings": {"bad_loss_pct": round(bad_loss_pct * 100, 2), "max_examples": max_examples},
        "summary": {
            "losses": int(len(losses)),
            "bad_losses": int(len(bad_losses)),
            "avg_loss_pct": round(float(losses[ret_col].mean() * 100), 3),
            "worst_loss_pct": round(float(losses[ret_col].min() * 100), 3),
        },
        "reason_counts": _summarize_reason_counts(all_loss_examples),
        "by_outcome": by_outcome,
        "feature_deltas": feature_deltas,
        "worst_examples": examples,
    }


def _missed_big_win_diagnostics(missed_rows: list, hold: int) -> dict:
    if not missed_rows:
        return {}
    rows = sorted(missed_rows, key=lambda r: r.get(f"h{hold}_return", 0), reverse=True)
    examples = [_compact_trade_example(row, hold, include_rejection=True) for row in rows]
    rejection_counts = {}
    for row in rows:
        for reason in row.get("rejection_reasons", []):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    return {
        "summary": {
            "missed_big_wins_kept": len(rows),
            "best_missed_return_pct": round(rows[0].get(f"h{hold}_return", 0) * 100, 2),
            "avg_missed_return_pct": round(float(np.mean([r.get(f"h{hold}_return", 0) for r in rows]) * 100), 2),
        },
        "rejection_counts": dict(sorted(rejection_counts.items(), key=lambda x: x[1], reverse=True)),
        "reason_counts": _summarize_reason_counts(examples),
        "top_examples": examples,
    }


ML_NUMERIC_FEATURES = [
    "score", "coil_pts", "brk_pts", "trend_pts", "vol_pts", "regime_adj",
    "rsi9", "rsi14", "macd_hist", "macd_bull", "cci14", "cci14_prev", "atr", "atr_pct",
    "vol_ratio_20d", "vol_ratio_10d", "vol_trend", "dollar_vol20",
    "body_pct", "upper_wick", "lower_wick", "close_loc", "day_range_pct",
    "pct_from_10d_high", "pct_from_52w_high", "pct_from_52w_low",
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    "rel_ret20_vs_spy", "spy_ret1", "spy_ret5", "spy_ret20", "sector_breadth", "vix_ts",
    "stoch_k", "stoch_d", "mfi14", "adx14", "cmf20", "range_rank_52w",
    "roc10", "roc20", "consec_up", "consec_down",
    # Bollinger / squeeze / gap / candle patterns
    "bb_pct", "squeeze", "gap_pct", "inside_day", "engulf_bull",
    # Trend slope (price of SMA is noise; slope direction is signal)
    "slope_sma20", "slope_sma50", "sma200_rising_20d",
    # Volume/price breadth confirmation
    "obv_above_sma", "pvt_above_sma", "dmi_bull",
    # Derived ratio features (computed in _ml_prepare_frame)
    "rsi_spread",   # rsi14 - rsi9: momentum divergence between time frames
    "vol_accel",    # vol_ratio_10d / vol_ratio_20d: short vs medium volume surge
    # Momentum slope and market microstructure features
    "rsi9_slope3",       # RSI9 change over 3 days: positive = recovering from oversold
    "macd_hist_slope3",  # MACD histogram slope: positive = building upward momentum
    "vix_1d_chg",        # VIX 1-day change: negative = fear subsiding (bullish for longs)
    # Trend quality (strongest predictors of bounce success — knowledge-based additions)
    "above_sma50",       # price > SMA50: buying pullback in uptrend vs breakdown (binary)
    "above_sma200",      # price > SMA200: long-term trend intact
    "rsi_recovering",    # rsi9_slope3 > 0: RSI already turning up (not still falling)
    "vol_dryup",         # vol_ratio_10d < 1.0: volume contracting = selling exhaustion
    # Volatility and momentum regime features (added to fix missing-from-sig-dict bug)
    "atr_expansion",     # today's ATR / rolling-20d ATR: >1 = expanding vol (breakout), <1 = compression
    "spy_momentum_accel", # SPY 5d return / SPY 20d return: >1 = market accelerating, <1 = decelerating
    "setup_rr",          # (target - entry) / (entry - stop): quality of risk/reward geometry
    # ── Market regime features (from MarketRegimeEngine) ─────────────────────
    # Included in sig dict by _add_regime_features_to_sig() called in _collect_trades
    "spy_ret60",         # SPY 60-day return: medium-term trend strength
    "spy_drawdown_20d",  # SPY distance below its 20-day high (0 = at high, negative = below)
    "spy_above_sma50",   # SPY price > SMA50 (binary)
    "spy_above_sma200",  # SPY price > SMA200 (binary)
    "spy_golden_cross",  # SPY SMA50 > SMA200 (binary, golden cross)
    "vix_20d_zscore",    # (VIX - 20d mean) / 20d std: >1.5 = vol expanding, < -1 = compressed
    "vol_expansion",     # 1 if vix_20d_zscore > 1.5 (unusual vol spike)
    "regime_score",      # MarketRegimeEngine.regime_score [0,1] continuous quality signal
    "crash_risk_score",  # MarketRegimeEngine.crash_risk_score [0,1] tail-risk proxy
    "risk_on_score",     # breadth × above_sma200 × (1 - vol_expansion)
    "risk_off_score",    # (1 - breadth) × vol_expansion × 0.5
]

ML_CATEGORICAL_FEATURES = [
    "day_of_week", "spy_regime", "vix_regime", "confirmed_pullback_gates",
    "candidate_status",
    "stock_regime",    # per-stock relative-to-market regime (new feature)
]

# ── Breakout-specific ML feature set ─────────────────────────────────────────
# Used when score_mode="breakout_v2". Superset of confirmed_pullback features
# plus breakout-specific features.
ML_NUMERIC_FEATURES_BREAKOUT = [
    # Core breakout scoring components
    "breakout_score", "compression_pts", "confirmation_pts",
    # Resistance proximity
    "pct_from_20d_high", "pct_from_50d_high", "pct_from_52w_high",
    # Volatility compression → expansion
    "bb_width", "atr_compression", "atr_expansion",
    "range_contraction_5_20", "keltner_squeeze",
    # Volume confirmation
    "vol_surge_3d", "vol_dryup_5d", "obv_slope_5d",
    # Trend alignment
    "above_sma20", "above_sma50", "above_sma200", "sma_alignment", "price_vs_ema9",
    # Relative strength
    "rel_strength_20d", "rel_strength_5d", "rs_momentum",
    # Failed breakout warning
    "upper_wick", "close_loc", "consec_failed_highs", "prev_breakout_failed",
    # All existing features that apply to breakouts too
    "score", "coil_pts", "brk_pts", "trend_pts", "vol_pts",
    "rsi9", "rsi14", "macd_hist", "macd_bull", "atr", "atr_pct",
    "vol_ratio_20d", "vol_ratio_10d", "dollar_vol20",
    "body_pct", "day_range_pct", "gap_pct",
    "pct_from_52w_low", "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    "rel_ret20_vs_spy", "spy_ret5", "spy_ret20", "vix_ts", "sector_breadth",
    "adx14", "roc10", "roc20", "bb_pct", "squeeze",
    "rsi9_slope3", "macd_hist_slope3",
    "slope_sma20", "slope_sma50",
    "atr_expansion", "spy_momentum_accel", "setup_rr",
]


def _ml_prepare_frame(rows_df: pd.DataFrame, hold: int) -> tuple:
    """Build a leakage-aware model frame from trade-time columns only."""
    if rows_df is None or rows_df.empty:
        return pd.DataFrame(), [], []
    df = rows_df.copy()
    ret_col = f"h{hold}_return"
    if ret_col not in df.columns:
        return pd.DataFrame(), [], []
    df["_scan_dt"] = pd.to_datetime(df.get("scan_date"), errors="coerce")
    df["_return"] = pd.to_numeric(df[ret_col], errors="coerce")
    # Quality win: must return > 0.5% (filters noise trades that barely beat 0)
    # Aligns training with real profitability — +0.001% "wins" hurt model calibration
    df["_win_label"] = (df["_return"] > 0.005).astype(int)
    df["_large_loss_label"] = (df["_return"] <= -0.03).astype(int)
    df["_missed_winner_label"] = (
        (df.get("candidate_status", "") == "rejected") & (df["_return"] >= 0.05)
    ).astype(int)
    out_col = f"h{hold}_outcome"
    outcomes = df[out_col].fillna("").astype(str) if out_col in df.columns else pd.Series("", index=df.index)
    df["_target_label"] = (outcomes == "TARGET_HIT").astype(int)
    df["_timeout_label"] = (outcomes == "TIMED_OUT").astype(int)

    # ── Breakout-specific labels (used when score_mode=breakout_v2) ───────────
    # Breakout win: h5_return > 1% AND not stopped out
    h5_ret_col = "h5_return"
    h5_out_col = "h5_outcome"
    if h5_ret_col in df.columns:
        h5_ret = pd.to_numeric(df[h5_ret_col], errors="coerce")
        h5_out = df[h5_out_col].fillna("").astype(str) if h5_out_col in df.columns else pd.Series("", index=df.index)
        df["_breakout_win_label"] = ((h5_ret > 0.01) & (h5_out != "STOP_HIT")).astype(int)
        # Failed breakout: gave back gains within h3 (closed below entry after being up)
        h3_ret_col = "h3_return"
        if h3_ret_col in df.columns:
            h3_ret = pd.to_numeric(df[h3_ret_col], errors="coerce")
            df["_failed_breakout_label"] = (h3_ret < 0.0).astype(int)
    # Big move: h10_return > 3%
    h10_ret_col = "h10_return"
    if h10_ret_col in df.columns:
        h10_ret = pd.to_numeric(df[h10_ret_col], errors="coerce")
        df["_big_move_label"] = (h10_ret > 0.03).astype(int)
    mae_col = f"h{hold}_mae"
    mfe_col = f"h{hold}_mfe"
    df["_mfe"] = pd.to_numeric(df[mfe_col], errors="coerce") if mfe_col in df.columns else np.nan
    df["_mae"] = pd.to_numeric(df[mae_col], errors="coerce") if mae_col in df.columns else np.nan

    # Derived features — computed from existing columns before feature selection
    if "rsi14" in df.columns and "rsi9" in df.columns:
        df["rsi_spread"] = pd.to_numeric(df["rsi14"], errors="coerce") - pd.to_numeric(df["rsi9"], errors="coerce")
    if "vol_ratio_10d" in df.columns and "vol_ratio_20d" in df.columns:
        v10 = pd.to_numeric(df["vol_ratio_10d"], errors="coerce")
        v20 = pd.to_numeric(df["vol_ratio_20d"], errors="coerce")
        df["vol_accel"] = v10 / v20.replace(0, np.nan)
    if "macd_hist" in df.columns and "macd_hist_prev2" in df.columns:
        df["macd_hist_slope3"] = pd.to_numeric(df["macd_hist"], errors="coerce") - pd.to_numeric(df["macd_hist_prev2"], errors="coerce")

    # Knowledge-based trend quality features
    entry_price = pd.to_numeric(df.get("entry") if "entry" in df.columns else df.get("h1_entry", None), errors="coerce")
    if "sma50" in df.columns and entry_price is not None:
        df["above_sma50"] = (entry_price > pd.to_numeric(df["sma50"], errors="coerce")).astype(float)
    if "sma200" in df.columns and entry_price is not None:
        df["above_sma200"] = (entry_price > pd.to_numeric(df["sma200"], errors="coerce")).astype(float)
    if "rsi9_slope3" in df.columns:
        slope = pd.to_numeric(df["rsi9_slope3"], errors="coerce")
        df["rsi_recovering"] = (slope > 0).astype(float)
    if "vol_ratio_10d" in df.columns:
        df["vol_dryup"] = (pd.to_numeric(df["vol_ratio_10d"], errors="coerce") < 1.0).astype(float)

    # SPY momentum acceleration: short vs long momentum ratio
    # > 1 = market accelerating (early bull phase); < 1 = decelerating (late bull, topping)
    # Computed here from existing columns to avoid adding to signal dict at scan time
    if "spy_ret5" in df.columns and "spy_ret20" in df.columns:
        s5 = pd.to_numeric(df["spy_ret5"], errors="coerce")
        s20 = pd.to_numeric(df["spy_ret20"], errors="coerce")
        df["spy_momentum_accel"] = (s5 / (s20.abs() + 0.001)).clip(-5.0, 5.0)

    # Setup R:R geometry quality from signal-level target/stop geometry
    # High R:R setups should predict better outcomes IF the model picks entries correctly
    if "target" in df.columns and "entry" in df.columns and "stop" in df.columns:
        _entry = pd.to_numeric(df["entry"], errors="coerce")
        _target = pd.to_numeric(df["target"], errors="coerce")
        _stop = pd.to_numeric(df["stop"], errors="coerce")
        _stop_dist = (_entry - _stop).clip(lower=0.001)
        df["setup_rr"] = ((_target - _entry) / _stop_dist).clip(-1.0, 10.0)

    numeric = [c for c in ML_NUMERIC_FEATURES if c in df.columns]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    categorical = [c for c in ML_CATEGORICAL_FEATURES if c in df.columns]
    for col in categorical:
        df[col] = df[col].fillna("unknown").astype(str)
    keep = ["ticker", "scan_date", "year", "month", "_scan_dt", "_return",
            "_win_label", "_large_loss_label", "_missed_winner_label",
            "_target_label", "_timeout_label", "_mfe", "_mae",
            "_breakout_win_label", "_failed_breakout_label", "_big_move_label",
            f"h{hold}_outcome", f"h{hold}_return", f"h{hold}_mae", f"h{hold}_mfe",
            "h3_return", "h5_return", "h5_outcome", "h10_return",
            "rejection_reasons"] + numeric + categorical
    keep = [c for c in keep if c in df.columns]
    df = df[keep].dropna(subset=["_scan_dt", "_return"])
    return df, numeric, categorical


def _ml_design_matrix(df: pd.DataFrame, numeric: list, categorical: list,
                      columns: list = None) -> tuple:
    x_num = df[numeric].copy() if numeric else pd.DataFrame(index=df.index)
    cat_frames = []
    for col in categorical:
        if col not in df.columns:
            continue
        values = df[col].fillna("unknown").astype(str)
        encoded = values.map(
            lambda v: int(hashlib.blake2b(v.encode("utf-8"), digest_size=4).hexdigest(), 16)
        ).astype("float64")
        cat_frames.append(encoded.rename(f"{col}_code"))
    x_cat = pd.concat(cat_frames, axis=1) if cat_frames else pd.DataFrame(index=df.index)
    x = pd.concat([x_num, x_cat], axis=1)
    x = x.replace([np.inf, -np.inf], np.nan)
    if columns is not None:
        x = x.reindex(columns=columns, fill_value=0)
    return x, list(x.columns)


def _ml_time_split(df: pd.DataFrame, embargo_days: int = 0) -> tuple:
    """Out-of-time split (train = earlier, test = last year).

    embargo_days > 0 drops training rows whose scan_date falls within
    `embargo_days` calendar days before the test boundary. Their forward-return
    label (which looks the hold horizon ahead) would otherwise overlap the test
    period and leak. Pass embargo_days >= the forward-return horizon to make the
    split honest. Default 0 preserves prior behavior for legacy callers.
    """
    df = df.sort_values("_scan_dt").reset_index(drop=True)
    dts = pd.to_datetime(df["_scan_dt"])
    years = sorted(dts.dt.year.dropna().unique().tolist())
    if len(years) >= 2:
        test_year = years[-1]
        test_mask = dts.dt.year == test_year
        train_mask = dts.dt.year < test_year
        if embargo_days and int(embargo_days) > 0:
            test_start = dts[test_mask].min()
            cutoff = test_start - pd.Timedelta(days=int(embargo_days))
            train_mask = train_mask & (dts <= cutoff)
        train_idx = df[train_mask].index
        test_idx = df[test_mask].index
        if len(train_idx) >= 50 and len(test_idx) >= 20:
            return train_idx, test_idx, int(test_year)
    split = int(len(df) * 0.75)
    if embargo_days and int(embargo_days) > 0 and len(df) > 1:
        # gap-purge the index split too
        gap = max(1, int(len(df) * 0.02))
        return df.index[:max(1, split - gap)], df.index[split:], "last_25pct"
    return df.index[:split], df.index[split:], "last_25pct"


def _ml_purged_walk_forward(frame: pd.DataFrame, numeric: list, categorical: list,
                            hold: int, min_train_rows: int = 400,
                            step_days: int = 21, embargo_days: int = None,
                            max_folds: int = 60) -> tuple:
    """Leak-free expanding-window walk-forward producing genuinely
    out-of-sample win/loss probabilities.

    Why this exists: training an ML gate on data that overlaps (or post-dates)
    the test window inflates every downstream profitability stat. Two leaks are
    closed here:
      1. Look-ahead: each fold trains ONLY on rows whose scan_date is on/before
         (test_start - embargo). No future rows ever enter training.
      2. Label overlap (purge/embargo): a training row's label looks `hold`
         trading days forward. embargo_days >= the forward horizon guarantees a
         train row's outcome window ends before the test window begins, so the
         label cannot peek into the test period.

    Returns (oos_df, win_prob, loss_prob, expected_return) over the concatenated
    out-of-sample rows (everything after the initial training fill), in
    chronological order.
    """
    from sklearn.ensemble import RandomForestClassifier as _RF
    from sklearn.ensemble import RandomForestRegressor as _RFR
    from sklearn.impute import SimpleImputer
    if embargo_days is None:
        embargo_days = int(np.ceil(hold * 1.5)) + 1  # hold trading days -> calendar + slack

    df = frame.copy()
    df["_d"] = pd.to_datetime(df["_scan_dt"], errors="coerce")
    df = df.dropna(subset=["_d"]).sort_values("_d").reset_index(drop=True)
    if len(df) < min_train_rows + 30:
        return df.iloc[0:0], np.array([]), np.array([]), np.array([])

    try:
        from xgboost import XGBClassifier as _XGB
        _have_xgb = True
    except Exception:
        _have_xgb = False

    def _mk(y):
        if _have_xgb:
            pos = int(np.sum(y)); neg = int(len(y) - pos)
            spw = (neg / pos) if pos > 0 else 1.0
            return _XGB(n_estimators=400, max_depth=6, learning_rate=0.05,
                        subsample=0.8, colsample_bytree=0.8, scale_pos_weight=spw,
                        tree_method="hist", eval_metric="logloss", verbosity=0,
                        n_jobs=-1, random_state=42)
        return _RF(n_estimators=150, max_depth=7, min_samples_leaf=25,
                   class_weight="balanced_subsample", random_state=42, n_jobs=-1)

    def _mk_reg():
        if _have_xgb:
            from xgboost import XGBRegressor as _XGBR
            return _XGBR(n_estimators=300, max_depth=6, learning_rate=0.05,
                         subsample=0.8, colsample_bytree=0.8,
                         tree_method="hist", verbosity=0, n_jobs=-1,
                         random_state=42)
        return _RFR(n_estimators=120, max_depth=7, min_samples_leaf=25,
                    random_state=42, n_jobs=-1)

    dser = df["_d"]
    start_d, end_d = dser.min(), dser.max()
    embargo = pd.Timedelta(days=int(embargo_days))
    step = pd.Timedelta(days=int(step_days))

    # First test window starts at the earliest date that leaves >= min_train_rows
    # of training data once the embargo gap is removed.
    ts = None
    probe = start_d + step
    while probe <= end_d:
        train_mask = dser <= (probe - embargo)
        if int(train_mask.sum()) >= min_train_rows:
            ts = probe
            break
        probe = probe + step
    if ts is None:
        return df.iloc[0:0], np.array([]), np.array([]), np.array([])

    oos_idx, win_parts, loss_parts, return_parts, _wf_errs = [], [], [], [], []
    folds = 0
    while ts <= end_d and folds < max_folds:
        te = ts + step
        train_mask = dser <= (ts - embargo)
        test_mask = (dser >= ts) & (dser < te)
        tr = df[train_mask]
        tef = df[test_mask]
        if len(tr) >= min_train_rows and len(tef) > 0:
            try:
                x_tr, feats = _ml_design_matrix(tr, numeric, categorical)
                x_te, _ = _ml_design_matrix(tef, numeric, categorical, feats)
                try:
                    imp = SimpleImputer(strategy="median", keep_empty_features=True)
                except TypeError:
                    imp = SimpleImputer(strategy="median")
                x_tr_i = imp.fit_transform(x_tr)
                x_te_i = imp.transform(x_te)
                y_w = tr["_win_label"].astype(int).to_numpy()
                wp = np.full(len(tef), np.nan)
                if len(set(y_w)) > 1:
                    wm = _mk(y_w); wm.fit(x_tr_i, y_w)
                    wp = wm.predict_proba(x_te_i)[:, 1]
                lp = np.zeros(len(tef))
                if "_large_loss_label" in tr.columns:
                    y_l = tr["_large_loss_label"].astype(int).to_numpy()
                    if len(set(y_l)) > 1:
                        lm = _mk(y_l); lm.fit(x_tr_i, y_l)
                        lp = lm.predict_proba(x_te_i)[:, 1]
                er = np.zeros(len(tef))
                y_r = pd.to_numeric(tr.get(f"h{hold}_return"), errors="coerce")
                rmask = y_r.notna()
                if int(rmask.sum()) >= max(50, min_train_rows // 4):
                    rm = _mk_reg()
                    rm.fit(x_tr_i[rmask.to_numpy()], y_r[rmask].to_numpy())
                    er = rm.predict(x_te_i)
                oos_idx.extend(tef.index.tolist())
                win_parts.append(wp)
                loss_parts.append(lp)
                return_parts.append(er)
                folds += 1
            except Exception as _e:
                _wf_errs.append(f"{type(_e).__name__}: {_e}")
        ts = te

    if not oos_idx:
        if _wf_errs:
            print(f"[wf] all folds failed; first errors: {_wf_errs[:3]}", flush=True)
        else:
            print("[wf] no folds met train/test size constraints", flush=True)
        return df.iloc[0:0], np.array([]), np.array([]), np.array([])
    oos_df = df.loc[oos_idx].copy()
    win_prob = np.concatenate(win_parts)
    loss_prob = np.concatenate(loss_parts)
    expected_return = np.concatenate(return_parts) if return_parts else np.zeros(len(oos_df))
    # Drop rows the win model could not score (single-class train fold)
    keep = ~np.isnan(win_prob)
    return (
        oos_df[keep].reset_index(drop=True),
        win_prob[keep],
        loss_prob[keep],
        expected_return[keep],
    )


def _round_float(v, nd=4):
    try:
        if v is None or pd.isna(v):
            return None
        if np.isinf(v):
            return "inf" if v > 0 else "-inf"
        return round(float(v), nd)
    except Exception:
        return None


def _classification_metrics(y_true, prob, threshold: float = 0.5) -> dict:
    try:
        from sklearn.metrics import (
            confusion_matrix, precision_score, recall_score, f1_score,
            roc_auc_score, average_precision_score, brier_score_loss,
        )
        pred = (prob >= threshold).astype(int)
        labels = [0, 1]
        cm = confusion_matrix(y_true, pred, labels=labels)
        out = {
            "threshold": threshold,
            "confusion_matrix": {
                "tn": int(cm[0, 0]), "fp": int(cm[0, 1]),
                "fn": int(cm[1, 0]), "tp": int(cm[1, 1]),
            },
            "precision": _round_float(precision_score(y_true, pred, zero_division=0)),
            "recall": _round_float(recall_score(y_true, pred, zero_division=0)),
            "f1": _round_float(f1_score(y_true, pred, zero_division=0)),
            "brier_score": _round_float(brier_score_loss(y_true, prob)),
        }
        if len(set(y_true)) > 1:
            out["roc_auc"] = _round_float(roc_auc_score(y_true, prob))
            out["average_precision"] = _round_float(average_precision_score(y_true, prob))
        return out
    except Exception as exc:
        return {"error": str(exc)}


def _regression_metrics(y_true, pred) -> dict:
    try:
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        return {
            "mae": _round_float(mean_absolute_error(y_true, pred)),
            "rmse": _round_float(mean_squared_error(y_true, pred) ** 0.5),
            "r2": _round_float(r2_score(y_true, pred)),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _calibration_bins(y_true, prob, bins: int = 10) -> list:
    frame = pd.DataFrame({"y": y_true, "p": prob}).dropna()
    if frame.empty:
        return []
    frame["bin"] = pd.cut(frame["p"], bins=np.linspace(0, 1, bins + 1),
                          include_lowest=True)
    rows = []
    for label, grp in frame.groupby("bin", observed=False):
        if grp.empty:
            continue
        rows.append({
            "bin": str(label),
            "n": int(len(grp)),
            "avg_predicted_prob": _round_float(grp["p"].mean()),
            "actual_rate": _round_float(grp["y"].mean()),
        })
    return rows


def _feature_importance(model, feature_names: list, limit: int = 30) -> list:
    raw = None
    if hasattr(model, "feature_importances_"):
        raw = model.feature_importances_
    elif hasattr(model, "coef_"):
        raw = model.coef_[0] if len(model.coef_.shape) > 1 else model.coef_
    if raw is None:
        return []
    rows = [
        {"feature": f, "importance": _round_float(v), "abs_importance": _round_float(abs(v))}
        for f, v in zip(feature_names, raw)
    ]
    return sorted(rows, key=lambda r: r["abs_importance"] or 0, reverse=True)[:limit]


def _optional_shap_summary(model, x_values, feature_names: list, limit: int = 20) -> dict:
    try:
        import shap
        sample = x_values[: min(len(x_values), 500)]
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(sample)
        values = shap_values[1] if isinstance(shap_values, list) and len(shap_values) > 1 else shap_values
        arr = np.asarray(values)
        if arr.ndim == 3:
            arr = arr[:, :, -1]
        mean_abs = np.abs(arr).mean(axis=0)
        rows = [
            {"feature": f, "mean_abs_shap": _round_float(v)}
            for f, v in zip(feature_names, mean_abs)
        ]
        return {
            "status": "available",
            "sample_rows": int(len(sample)),
            "top_features": sorted(rows, key=lambda r: r["mean_abs_shap"] or 0, reverse=True)[:limit],
        }
    except Exception as exc:
        return {"status": "unavailable_or_failed", "reason": str(exc)}


def _explain_linear_row(model, row_x: pd.Series, feature_names: list, limit: int = 8) -> list:
    if not hasattr(model, "coef_"):
        return []
    coef = model.coef_[0] if len(model.coef_.shape) > 1 else model.coef_
    vals = row_x.fillna(0).to_numpy(dtype=float)
    if coef.shape[0] != vals.shape[0]:
        return []
    contribs = coef * vals
    rows = [
        {"feature": f, "value": _round_float(v), "contribution": _round_float(c)}
        for f, v, c in zip(feature_names, vals, contribs)
    ]
    return sorted(rows, key=lambda r: abs(r["contribution"] or 0), reverse=True)[:limit]


def _model_error_examples(base_df: pd.DataFrame, prob, y_true, feature_names: list,
                          linear_model=None, x_frame: pd.DataFrame = None,
                          positive_name: str = "win", limit: int = 10) -> dict:
    tmp = base_df.copy()
    tmp["_prob"] = prob
    tmp["_actual"] = np.asarray(y_true)
    tmp["_pred"] = (tmp["_prob"] >= 0.5).astype(int)
    examples = {}
    cuts = {
        "false_positives": tmp[(tmp["_pred"] == 1) & (tmp["_actual"] == 0)].sort_values("_prob", ascending=False),
        "false_negatives": tmp[(tmp["_pred"] == 0) & (tmp["_actual"] == 1)].sort_values("_prob", ascending=True),
        "highest_risk_accepts": tmp.sort_values("_prob", ascending=False),
    }
    for name, grp in cuts.items():
        rows = []
        for idx, row in grp.head(limit).iterrows():
            item = {
                "ticker": row.get("ticker"),
                "scan_date": row.get("scan_date"),
                "probability": _round_float(row.get("_prob")),
                "actual": int(row.get("_actual")),
                "return_pct": _round_float(row.get("_return") * 100, 3),
                "label_meaning": positive_name,
                "rejection_reasons": row.get("rejection_reasons", []),
            }
            if linear_model is not None and x_frame is not None and idx in x_frame.index:
                item["top_contributions"] = _explain_linear_row(
                    linear_model, x_frame.loc[idx], feature_names
                )
            rows.append(item)
        examples[name] = rows
    return examples


def _segment_model_performance(df: pd.DataFrame, y, prob) -> dict:
    tmp = df.copy()
    tmp["_y"] = np.asarray(y)
    tmp["_prob"] = np.asarray(prob)
    out = {"by_year": {}, "by_market_regime": {}, "by_volatility_regime": {}}
    for yr, grp in tmp.groupby(pd.to_datetime(tmp["_scan_dt"]).dt.year):
        if len(grp) >= 10:
            out["by_year"][str(int(yr))] = _classification_metrics(grp["_y"], grp["_prob"])
    if "spy_regime" in tmp.columns:
        for reg, grp in tmp.groupby("spy_regime"):
            if len(grp) >= 10:
                out["by_market_regime"][str(reg)] = _classification_metrics(grp["_y"], grp["_prob"])
    if "vix_regime" in tmp.columns:
        for reg, grp in tmp.groupby("vix_regime"):
            if len(grp) >= 10:
                out["by_volatility_regime"][str(reg)] = _classification_metrics(grp["_y"], grp["_prob"])
    return out


def _gate_enhanced_stats(frame: pd.DataFrame, hold: int,
                         selected_col: str, winner_threshold: float = 0.05,
                         large_loss_threshold: float = -0.03) -> dict:
    ret_col = f"h{hold}_return"
    if frame.empty or selected_col not in frame.columns:
        return {}
    selected = frame[frame[selected_col]].copy()
    st = _stats(selected, hold) if len(selected) else {}
    out_col = f"h{hold}_outcome"
    actual_winner = pd.to_numeric(frame.get(ret_col), errors="coerce") >= winner_threshold
    actual_loss = pd.to_numeric(frame.get(ret_col), errors="coerce") <= 0
    actual_large_loss = pd.to_numeric(frame.get(ret_col), errors="coerce") <= large_loss_threshold
    selected_mask = frame[selected_col].fillna(False).astype(bool)
    st.update({
        "total_trades": int(selected_mask.sum()),
        "target_hit_rate": _round_float((selected.get(out_col) == "TARGET_HIT").mean()) if len(selected) and out_col in selected.columns else st.get("target_hit_rate"),
        "stop_hit_rate": _round_float((selected.get(out_col) == "STOP_HIT").mean()) if len(selected) and out_col in selected.columns else st.get("stopped_out_rate"),
        "large_loss_rate": _round_float(actual_large_loss[selected_mask].mean()) if selected_mask.any() else 0.0,
        "missed_winner_rate": _round_float((actual_winner & ~selected_mask).sum() / max(int(actual_winner.sum()), 1)),
        "false_positives": int((selected_mask & actual_loss).sum()),
        "false_negatives": int((~selected_mask & actual_winner).sum()),
        "correct_rejections": int((~selected_mask & ~actual_winner).sum()),
    })
    return st


def _gate_reason_list(row, ml_prob_threshold: float, ml_expected_return_min: float,
                      ml_large_loss_max: float) -> tuple:
    failed = []
    passed = []
    if row.get("ml_probability", 0) < ml_prob_threshold:
        failed.append(f"ml_probability_below_{ml_prob_threshold:.2f}")
    else:
        passed.append("ml_probability_pass")
    if row.get("expected_return", 0) <= ml_expected_return_min:
        failed.append("expected_return_not_positive")
    else:
        passed.append("expected_return_pass")
    if row.get("large_loss_probability", 1) > ml_large_loss_max:
        failed.append(f"large_loss_probability_above_{ml_large_loss_max:.2f}")
    else:
        passed.append("large_loss_probability_pass")
    return passed, failed


def _build_gate_analysis(test_df: pd.DataFrame, hold: int, win_prob, loss_prob,
                         expected_return, target_prob=None, timeout_prob=None,
                         ml_prob_threshold: float = 0.58,
                         ml_expected_return_min: float = 0.0,
                         ml_large_loss_max: float = 0.20,
                         diagnostics_limit: int = 250) -> dict:
    ret_col = f"h{hold}_return"
    if test_df.empty or ret_col not in test_df.columns:
        return {}
    df = test_df.copy()
    df["rule_pass"] = df.get("candidate_status", "executed").astype(str).eq("executed")
    df["rule_score"] = pd.to_numeric(df.get("score", 0), errors="coerce").fillna(0)
    df["ml_probability"] = np.asarray(win_prob, dtype=float)
    df["expected_return"] = np.asarray(expected_return, dtype=float)
    df["large_loss_probability"] = np.asarray(loss_prob, dtype=float)
    df["target_before_stop_probability"] = (
        np.asarray(target_prob, dtype=float) if target_prob is not None else df["ml_probability"].to_numpy()
    )
    df["timeout_probability"] = (
        np.asarray(timeout_prob, dtype=float) if timeout_prob is not None
        else np.clip(1 - df["ml_probability"] - df["large_loss_probability"], 0, 1).to_numpy()
    )
    df["model_confidence"] = np.maximum(df["ml_probability"], 1 - df["ml_probability"])
    ml_pass_values = []
    failed_ml = []
    passed_ml = []
    for _, row in df.iterrows():
        passed, failed = _gate_reason_list(
            row, ml_prob_threshold, ml_expected_return_min, ml_large_loss_max
        )
        passed_ml.append(passed)
        failed_ml.append(failed)
        ml_pass_values.append(len(failed) == 0)
    df["ml_pass"] = ml_pass_values
    df["failed_ml_reasons"] = failed_ml
    df["passed_ml_reasons"] = passed_ml
    df["final_pass"] = (
        df["rule_pass"] & df["ml_pass"]
        & (df["ml_probability"] >= ml_prob_threshold)
        & (df["expected_return"] > ml_expected_return_min)
        & (df["large_loss_probability"] <= ml_large_loss_max)
    )
    df["risk_filter_pass"] = df["large_loss_probability"] <= ml_large_loss_max

    def final_reason(row):
        if not row["rule_pass"]:
            return "rule_gate_failed"
        if not row["ml_pass"]:
            return "ml_gate_failed_after_rule_pass"
        if not row["risk_filter_pass"]:
            return "risk_filter_failed"
        return "rule_pass_and_ml_pass"

    df["final_decision_reason"] = df.apply(final_reason, axis=1)
    df["_actual_return"] = pd.to_numeric(df[ret_col], errors="coerce")
    df["_actual_winner"] = df["_actual_return"] >= 0.05
    df["missed_winner"] = (~df["final_pass"]) & df["_actual_winner"]
    df["false_positive"] = df["final_pass"] & (df["_actual_return"] <= 0)
    df["false_negative"] = (~df["final_pass"]) & df["_actual_winner"]
    df["correctly_rejected"] = (~df["final_pass"]) & (~df["_actual_winner"])

    df["strategy_rule_only"] = df["rule_pass"]
    df["strategy_ml_only"] = df["ml_pass"]
    df["strategy_rule_plus_ml"] = df["rule_pass"] & df["ml_pass"]
    df["strategy_rule_plus_ml_risk"] = df["final_pass"]

    diagnostics = []
    diag_order = pd.concat([
        df[df["final_pass"]].head(diagnostics_limit // 4),
        df[df["false_positive"]].head(diagnostics_limit // 4),
        df[df["missed_winner"]].head(diagnostics_limit // 4),
        df[~df["final_pass"]].head(diagnostics_limit // 4),
    ]).drop_duplicates(subset=["ticker", "scan_date"], keep="first")
    for _, row in diag_order.head(diagnostics_limit).iterrows():
        failed_rule_reasons = row.get("rejection_reasons", [])
        if not isinstance(failed_rule_reasons, list):
            failed_rule_reasons = [] if pd.isna(failed_rule_reasons) else [str(failed_rule_reasons)]
        passed_rule_reasons = ["confirmed_pullback_rule_pass"] if row["rule_pass"] else []
        diagnostics.append({
            "ticker": row.get("ticker"),
            "date": row.get("scan_date"),
            "rule_pass": bool(row["rule_pass"]),
            "ml_pass": bool(row["ml_pass"]),
            "final_pass": bool(row["final_pass"]),
            "rule_score": _round_float(row.get("rule_score"), 2),
            "ml_probability": _round_float(row.get("ml_probability")),
            "expected_return": _round_float(row.get("expected_return")),
            "large_loss_probability": _round_float(row.get("large_loss_probability")),
            "target_before_stop_probability": _round_float(row.get("target_before_stop_probability")),
            "timeout_probability": _round_float(row.get("timeout_probability")),
            "model_confidence": _round_float(row.get("model_confidence")),
            "failed_rule_reasons": failed_rule_reasons,
            "passed_rule_reasons": passed_rule_reasons,
            "failed_ml_reasons": row.get("failed_ml_reasons", []),
            "final_decision_reason": row.get("final_decision_reason"),
            "actual_forward_return": _round_float(row.get("_actual_return")),
            "actual_outcome": row.get(f"h{hold}_outcome"),
            "missed_winner": bool(row.get("missed_winner")),
            "false_positive": bool(row.get("false_positive")),
            "false_negative": bool(row.get("false_negative")),
            "correctly_rejected": bool(row.get("correctly_rejected")),
        })

    return {
        "settings": {
            "ml_probability_threshold": ml_prob_threshold,
            "ml_expected_return_min": ml_expected_return_min,
            "ml_large_loss_max": ml_large_loss_max,
            "primary_hold": hold,
            "diagnostics_are_test_period_sample": True,
        },
        "strategy_comparison": {
            "rule_only": _gate_enhanced_stats(df, hold, "strategy_rule_only"),
            "ml_only": _gate_enhanced_stats(df, hold, "strategy_ml_only"),
            "rule_plus_ml": _gate_enhanced_stats(df, hold, "strategy_rule_plus_ml"),
            "rule_plus_ml_risk_filter": _gate_enhanced_stats(df, hold, "strategy_rule_plus_ml_risk"),
        },
        "diagnostics": {
            "total_candidates": int(len(df)),
            "rule_passed": int(df["rule_pass"].sum()),
            "ml_passed": int(df["ml_pass"].sum()),
            "ml_passed_after_rule": int((df["rule_pass"] & df["ml_pass"]).sum()),
            "final_passed": int(df["final_pass"].sum()),
            "missed_winners": int(df["missed_winner"].sum()),
            "false_positives": int(df["false_positive"].sum()),
            "false_negatives": int(df["false_negative"].sum()),
            "correct_rejections": int(df["correctly_rejected"].sum()),
        },
        "candidate_diagnostics": diagnostics,
        "examples": {
            "accepted_trades": diagnostics[:20],
            "rule_rejected_missed_winners": [
                d for d in diagnostics if (not d["rule_pass"] and d["missed_winner"])
            ][:20],
            "ml_rejected_winners": [
                d for d in diagnostics if (d["rule_pass"] and not d["ml_pass"] and d["missed_winner"])
            ][:20],
            "ml_saved_bad_trades": [
                d for d in diagnostics if (d["rule_pass"] and not d["ml_pass"] and (d["actual_forward_return"] or 0) <= 0)
            ][:20],
            "false_positives": [d for d in diagnostics if d["false_positive"]][:20],
        },
    }


def _ml_strategy_comparison(test_df: pd.DataFrame, win_prob, loss_prob, hold: int,
                            expected_return=None,
                            ml_prob_threshold: float = 0.58,
                            ml_expected_return_min: float = 0.0,
                            ml_large_loss_max: float = 0.20,
                            account_size: float = 5000.0,
                            position_cap_pct: float = 20.0,
                            commission: float = 0.0,
                            sizing_mode: str = "fixed") -> dict:
    ret_col = f"h{hold}_return"
    if ret_col not in test_df.columns or len(test_df) == 0:
        return {}
    df = test_df.copy()
    df["_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    df["_win_prob"] = win_prob
    df["_loss_prob"] = loss_prob if loss_prob is not None else 0.0
    df["_expected_return"] = (
        np.asarray(expected_return, dtype=float)
        if expected_return is not None else np.zeros(len(df))
    )
    executed = df[df.get("candidate_status", "executed") == "executed"].copy()
    if executed.empty:
        executed = df.copy()

    def st(frame):
        return _stats(frame, hold) if len(frame) else {}

    rule = st(executed)
    ml_gate_mask = (
        (executed["_win_prob"] >= ml_prob_threshold)
        & (executed["_expected_return"] > ml_expected_return_min)
        & (executed["_loss_prob"] <= ml_large_loss_max)
    )
    ml_filter = st(executed[ml_gate_mask])
    ranked = executed.sort_values(["scan_date", "_win_prob"], ascending=[True, False])
    ranked = ranked.groupby("scan_date", group_keys=False).head(3)
    ml_rank = st(ranked)

    sized_rets = executed["_ret"] * np.clip(executed["_win_prob"] - executed["_loss_prob"], 0, 1)
    sizing_frame = executed.copy()
    sizing_frame[ret_col] = sized_rets
    ml_sizing = st(sizing_frame)

    adjusted = executed.copy()
    adjusted = adjusted[adjusted["_loss_prob"] <= ml_large_loss_max].copy()
    adj_rets = adjusted["_ret"].clip(lower=-0.02)
    adjusted[ret_col] = adj_rets
    ml_stop_target = st(adjusted)

    def answer(before, after, key):
        if not before or not after:
            return None
        return _round_float((after.get(key, 0) or 0) - (before.get(key, 0) or 0))

    # Honest portfolio $ for each strategy: run the SAME account engine
    # (concurrency + position cap + configured look-ahead-free sizing) on the
    # actual selected trades. Per-trade expectancy alone overstates results
    # because it ignores capital constraints; this gives the real number.
    exit_date_col = f"h{hold}_exit_date"

    def _acct(frame):
        try:
            if frame is None or len(frame) == 0 or "scan_date" not in frame.columns:
                return {}
            f = frame.copy()
            # ML frame may lack the columns the account engine needs; the sim
            # only uses entry for a >0 filter and exit_date for capital-release
            # ordering, so safe proxies preserve correctness.
            if "score" not in f.columns:
                f["score"] = f.get("_win_prob", 1.0)
            if "entry" not in f.columns:
                f["entry"] = 1.0
            else:
                f["entry"] = pd.to_numeric(f["entry"], errors="coerce").fillna(1.0)
                f.loc[f["entry"] <= 0, "entry"] = 1.0
            if exit_date_col not in f.columns:
                _sd = pd.to_datetime(f["scan_date"], errors="coerce")
                # hold trading days ≈ hold*7/5 calendar days
                f[exit_date_col] = (_sd + pd.to_timedelta(int(round(hold * 1.4)), unit="D")).dt.strftime("%Y-%m-%d")
            sim = _simulate_account(
                f, hold, _stats(f, hold),
                account_size=account_size,
                position_cap_pct=position_cap_pct,
                commission=commission,
                sizing_mode=sizing_mode,
            )
            s = (sim or {}).get("summary", {})
            return {
                "final_value": s.get("final_value"),
                "total_return_pct": s.get("total_return_pct"),
                "profit_dollars": (None if s.get("final_value") is None
                                   else round(s["final_value"] - account_size, 2)),
                "trades_taken": s.get("trades_taken"),
                "skipped_trades": s.get("skipped_trades"),
                "max_drawdown": s.get("max_drawdown"),
                "account_size": account_size,
            }
        except Exception:
            return {}

    rule["account_sim"] = _acct(executed)
    ml_filter["account_sim"] = _acct(executed[ml_gate_mask])
    ml_rank["account_sim"] = _acct(ranked)
    ml_stop_target["account_sim"] = _acct(adjusted)

    return {
        "rule_only_strategy": rule,
        "ml_filter_strategy": ml_filter,
        "ml_ranking_strategy_top3_per_day": ml_rank,
        "ml_position_sizing_strategy_probability_weighted": ml_sizing,
        "ml_stop_target_adjustment_strategy_loss_cap_proxy": ml_stop_target,
        "answers": {
            "did_ml_improve_win_rate": answer(rule, ml_filter, "win_rate"),
            "did_ml_improve_profit_factor": answer(rule, ml_filter, "profit_factor"),
            "did_ml_reduce_large_losers": answer(rule, ml_stop_target, "stopped_out_rate"),
            "filter_trades_kept": int(ml_filter.get("trades", 0) or 0),
            "rule_trades": int(rule.get("trades", 0) or 0),
        },
    }


def _run_ml_analysis(trades_df: pd.DataFrame, rejected_rows: list, hold: int,
                     max_rows: int = 0, min_train_rows: int = 200,
                     ml_prob_threshold: float = 0.58,
                     ml_expected_return_min: float = 0.0,
                     ml_large_loss_max: float = 0.20,
                     gate_diagnostics_limit: int = 250,
                     account_size: float = 5000.0,
                     position_cap_pct: float = 20.0,
                     commission: float = 0.0,
                     account_sizing_mode: str = "fixed",
                     ml_walk_forward: bool = True,
                     ml_wf_step_days: int = 21,
                     ml_wf_min_train: int = 400) -> dict:
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.tree import DecisionTreeClassifier
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.calibration import CalibratedClassifierCV
    except Exception as exc:
        return {"enabled": False, "error": f"scikit-learn unavailable: {exc}"}

    try:
        from xgboost import XGBClassifier as _XGBClassifier, XGBRegressor as _XGBRegressor
        _xgb_available = True
    except ImportError:
        _xgb_available = False

    def _make_clf(y_train_labels, **kwargs):
        """Return XGBClassifier if available, else RandomForest."""
        if _xgb_available:
            pos = int(y_train_labels.sum())
            neg = int(len(y_train_labels) - pos)
            spw = (neg / pos) if pos > 0 else 1.0
            return _XGBClassifier(
                n_estimators=500, max_depth=6, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=spw, tree_method="hist",
                eval_metric="logloss", verbosity=0, n_jobs=-1,
                random_state=42, **kwargs
            )
        return RandomForestClassifier(
            n_estimators=150, max_depth=7, min_samples_leaf=25,
            class_weight="balanced_subsample", random_state=42, n_jobs=-1,
        )

    def _make_reg(**kwargs):
        if _xgb_available:
            return _XGBRegressor(
                n_estimators=400, max_depth=6, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                tree_method="hist", verbosity=0, n_jobs=-1, random_state=42,
                **kwargs
            )
        return RandomForestRegressor(
            n_estimators=120, max_depth=7, min_samples_leaf=25,
            random_state=42, n_jobs=-1,
        )

    rows = []
    if trades_df is not None and not trades_df.empty:
        rows.extend(trades_df.to_dict(orient="records"))
    if rejected_rows:
        rows.extend(rejected_rows)
    candidate_df = pd.DataFrame(rows)
    frame, numeric, categorical = _ml_prepare_frame(candidate_df, hold)
    if len(frame) < min_train_rows:
        return {
            "enabled": True,
            "status": "not_enough_rows",
            "rows": int(len(frame)),
            "min_train_rows": int(min_train_rows),
        }
    if max_rows and max_rows > 0 and len(frame) > max_rows:
        frame = frame.sort_values("_scan_dt").sample(max_rows, random_state=42).sort_values("_scan_dt")
    frame = frame.sort_values("_scan_dt").reset_index(drop=True)

    if ml_walk_forward:
        # Leak-free path: out-of-sample win/loss probs via purged expanding
        # walk-forward, then the SAME account engine on the OOS trades.
        oos_df, wf_win, wf_loss, wf_expected_return = _ml_purged_walk_forward(
            frame, numeric, categorical, hold,
            min_train_rows=int(ml_wf_min_train),
            step_days=int(ml_wf_step_days),
        )
        if oos_df is None or len(oos_df) == 0:
            return {
                "enabled": True,
                "evaluation": "purged_walk_forward",
                "status": "insufficient_data_for_walk_forward",
                "rows": int(len(frame)),
                "needed_min_train": int(ml_wf_min_train),
            }
        sc = _ml_strategy_comparison(
            oos_df, wf_win, wf_loss, hold,
            expected_return=wf_expected_return,
            ml_prob_threshold=ml_prob_threshold,
            ml_expected_return_min=ml_expected_return_min,
            ml_large_loss_max=ml_large_loss_max,
            account_size=account_size,
            position_cap_pct=position_cap_pct,
            commission=commission,
            sizing_mode=account_sizing_mode,
        )
        return {
            "enabled": True,
            "evaluation": "purged_walk_forward",
            "leakage_controls": {
                "train_only_past": True,
                "embargo_days": int(np.ceil(hold * 1.5)) + 1,
                "embargo_reason": "purges forward-return label overlap (>= hold horizon)",
                "step_days": int(ml_wf_step_days),
                "min_train_rows": int(ml_wf_min_train),
            },
            "settings": {
                "hold_days": hold,
                "oos_rows": int(len(oos_df)),
                "total_rows": int(len(frame)),
                "ml_probability_threshold": ml_prob_threshold,
                "ml_expected_return_min": ml_expected_return_min,
                "ml_large_loss_max": ml_large_loss_max,
            },
            "strategy_comparison": sc,
        }

    train_idx, test_idx, test_period = _ml_time_split(frame)
    train_df = frame.loc[train_idx].copy()
    test_df = frame.loc[test_idx].copy()
    x_train, feature_names = _ml_design_matrix(train_df, numeric, categorical)
    x_test, _ = _ml_design_matrix(test_df, numeric, categorical, feature_names)

    # keep_empty_features=True: without it SimpleImputer silently drops
    # all-NaN columns, so x_*_imp loses columns while feature_names keeps
    # them -> downstream pd.DataFrame(..., columns=feature_names) shape error.
    try:
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    except TypeError:  # sklearn < 1.2 fallback
        imputer = SimpleImputer(strategy="median")
    x_train_imp = imputer.fit_transform(x_train)
    x_test_imp = imputer.transform(x_test)
    # Defensive: if the imputer (old sklearn) still dropped columns, align
    # feature_names to the surviving columns so all consumers stay consistent.
    if x_train_imp.shape[1] != len(feature_names):
        keep = getattr(imputer, "get_support", lambda: None)()
        if keep is not None and len(keep) == len(feature_names):
            feature_names = [f for f, k in zip(feature_names, keep) if k]
        else:
            feature_names = list(feature_names)[: x_train_imp.shape[1]]

    try:
        import importlib.util as _importlib_util
        optional_status = {
            "shap": "available" if _importlib_util.find_spec("shap") else "not_installed",
            "xgboost": "available" if _importlib_util.find_spec("xgboost") else "not_installed",
            "lightgbm": "available" if _importlib_util.find_spec("lightgbm") else "not_installed",
        }
    except Exception:
        optional_status = {"shap": "unknown", "xgboost": "unknown", "lightgbm": "unknown"}

    analysis = {
        "enabled": True,
        "settings": {
            "hold_days": hold,
            "rows_used": int(len(frame)),
            "train_rows": int(len(train_df)),
            "test_rows": int(len(test_df)),
            "test_period": test_period,
            "feature_count": int(len(feature_names)),
            "candidate_rows_include_rejected_sample": int((frame.get("candidate_status") == "rejected").sum())
                if "candidate_status" in frame.columns else 0,
            "optional_explainers": optional_status,
            "separate_gate_design": True,
            "ml_probability_threshold": ml_prob_threshold,
            "ml_expected_return_min": ml_expected_return_min,
            "ml_large_loss_max": ml_large_loss_max,
        },
        "features_used": feature_names,
    }

    y_train = train_df["_win_label"].astype(int).to_numpy()
    y_test = test_df["_win_label"].astype(int).to_numpy()
    classifiers = {}
    win_prob = np.zeros(len(test_df))
    if len(set(y_train)) > 1:
        logit = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(with_mean=False),
            LogisticRegression(max_iter=1000, class_weight="balanced"),
        )
        logit.fit(x_train, y_train)
        tree = make_pipeline(
            SimpleImputer(strategy="median"),
            DecisionTreeClassifier(max_depth=5, min_samples_leaf=30, class_weight="balanced", random_state=42),
        )
        tree.fit(x_train, y_train)
        rf = _make_clf(y_train)
        rf.fit(x_train_imp, y_train)
        calibrated_rf = CalibratedClassifierCV(rf, method="sigmoid", cv="prefit")
        try:
            calibrated_rf.fit(x_train_imp, y_train)
        except Exception:
            calibrated_rf = None
        classifiers = {
            "logistic_regression": logit,
            "decision_tree": tree,
            "random_forest": rf,
        }
        models_out = {}
        for name, model in classifiers.items():
            if name == "random_forest":
                prob = model.predict_proba(x_test_imp)[:, 1]
                imp_model = model
                linear = None
                x_for_exp = pd.DataFrame(x_test_imp, index=test_df.index, columns=feature_names)
            else:
                prob = model.predict_proba(x_test)[:, 1]
                imp_model = model.steps[-1][1]
                linear = imp_model if name == "logistic_regression" else None
                x_for_exp = x_test
            models_out[name] = {
                "task": "trade_win_loss_prediction",
                "metrics": _classification_metrics(y_test, prob),
                "calibration": _calibration_bins(y_test, prob),
                "feature_importance": _feature_importance(imp_model, feature_names),
                "performance_segments": _segment_model_performance(test_df, y_test, prob),
                "error_analysis": _model_error_examples(
                    test_df, prob, y_test, feature_names,
                    linear_model=linear, x_frame=x_for_exp,
                    positive_name="profitable_trade",
                ),
            }
            if name == "random_forest" and optional_status.get("shap") == "available":
                models_out[name]["shap_values"] = _optional_shap_summary(
                    model, x_test_imp, feature_names
                )
        if calibrated_rf is not None:
            cprob = calibrated_rf.predict_proba(x_test_imp)[:, 1]
            models_out["calibrated_random_forest"] = {
                "task": "calibrated_trade_win_probability",
                "metrics": _classification_metrics(y_test, cprob),
                "calibration": _calibration_bins(y_test, cprob),
            }
        analysis["trade_win_loss_prediction"] = models_out
        win_prob = classifiers["random_forest"].predict_proba(x_test_imp)[:, 1]
    else:
        analysis["trade_win_loss_prediction"] = {"status": "single_class_train_labels"}

    regression_targets = {
        "return_1d": "h1_return",
        "return_3d": "h3_return",
        "return_5d": "h5_return",
        "max_favorable_excursion": f"h{hold}_mfe",
        "max_adverse_excursion": f"h{hold}_mae",
    }
    reg_out = {}
    for label, col in regression_targets.items():
        if col not in frame.columns:
            continue
        ytr = pd.to_numeric(train_df[col], errors="coerce")
        yte = pd.to_numeric(test_df[col], errors="coerce")
        tr_mask = ytr.notna()
        te_mask = yte.notna()
        if tr_mask.sum() < min_train_rows // 2 or te_mask.sum() < 20:
            continue
        ridge = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(with_mean=False), Ridge(alpha=1.0))
        ridge.fit(x_train.loc[tr_mask], ytr[tr_mask])
        rf_reg = _make_reg()
        rf_reg.fit(imputer.transform(x_train.loc[tr_mask]), ytr[tr_mask])
        ridge_pred = ridge.predict(x_test.loc[te_mask])
        rf_pred = rf_reg.predict(imputer.transform(x_test.loc[te_mask]))
        reg_out[label] = {
            "linear_ridge": {
                "metrics": _regression_metrics(yte[te_mask], ridge_pred),
                "feature_importance": _feature_importance(ridge.steps[-1][1], feature_names),
            },
            "random_forest": {
                "metrics": _regression_metrics(yte[te_mask], rf_pred),
                "feature_importance": _feature_importance(rf_reg, feature_names),
            },
        }
    analysis["expected_return_prediction"] = reg_out

    miss_df = frame.copy()
    y_miss_train = train_df["_missed_winner_label"].astype(int).to_numpy()
    y_miss_test = test_df["_missed_winner_label"].astype(int).to_numpy()
    if len(set(y_miss_train)) > 1:
        miss_model = _make_clf(y_miss_train)
        miss_model.fit(x_train_imp, y_miss_train)
        miss_prob = miss_model.predict_proba(x_test_imp)[:, 1]
        analysis["missed_winner_detection"] = {
            "metrics": _classification_metrics(y_miss_test, miss_prob),
            "feature_importance": _feature_importance(miss_model, feature_names),
            "filter_miss_analysis": (
                test_df[test_df["_missed_winner_label"] == 1]
                .explode("rejection_reasons")["rejection_reasons"]
                .value_counts().head(25).to_dict()
                if "rejection_reasons" in test_df.columns else {}
            ),
            "examples": _model_error_examples(
                test_df, miss_prob, y_miss_test, feature_names,
                positive_name="missed_big_winner",
            ),
        }
    else:
        analysis["missed_winner_detection"] = {"status": "not_enough_positive_missed_winners"}

    y_loss_train = train_df["_large_loss_label"].astype(int).to_numpy()
    y_loss_test = test_df["_large_loss_label"].astype(int).to_numpy()
    loss_prob = np.zeros(len(test_df))
    if len(set(y_loss_train)) > 1:
        loss_model = _make_clf(y_loss_train)
        loss_model.fit(x_train_imp, y_loss_train)
        loss_prob = loss_model.predict_proba(x_test_imp)[:, 1]
        analysis["large_loss_risk_prediction"] = {
            "metrics": _classification_metrics(y_loss_test, loss_prob),
            "calibration": _calibration_bins(y_loss_test, loss_prob),
            "feature_importance": _feature_importance(loss_model, feature_names),
            "performance_segments": _segment_model_performance(test_df, y_loss_test, loss_prob),
            "examples": _model_error_examples(
                test_df, loss_prob, y_loss_test, feature_names,
                positive_name="large_loss_risk",
            ),
        }
    else:
        analysis["large_loss_risk_prediction"] = {"status": "not_enough_large_losses"}

    target_prob = None
    y_target_train = train_df["_target_label"].astype(int).to_numpy()
    y_target_test = test_df["_target_label"].astype(int).to_numpy()
    if len(set(y_target_train)) > 1:
        target_model = _make_clf(y_target_train)
        target_model.fit(x_train_imp, y_target_train)
        target_prob = target_model.predict_proba(x_test_imp)[:, 1]
        analysis["target_before_stop_prediction"] = {
            "metrics": _classification_metrics(y_target_test, target_prob),
            "feature_importance": _feature_importance(target_model, feature_names),
        }

    timeout_prob = None
    y_timeout_train = train_df["_timeout_label"].astype(int).to_numpy()
    y_timeout_test = test_df["_timeout_label"].astype(int).to_numpy()
    if len(set(y_timeout_train)) > 1:
        timeout_model = _make_clf(y_timeout_train)
        timeout_model.fit(x_train_imp, y_timeout_train)
        timeout_prob = timeout_model.predict_proba(x_test_imp)[:, 1]
        analysis["timeout_prediction"] = {
            "metrics": _classification_metrics(y_timeout_test, timeout_prob),
            "feature_importance": _feature_importance(timeout_model, feature_names),
        }

    expected_return = np.zeros(len(test_df))
    ret_col = f"h{hold}_return"
    if ret_col in train_df.columns:
        ytr_ret = pd.to_numeric(train_df[ret_col], errors="coerce")
        tr_mask = ytr_ret.notna()
        if tr_mask.sum() >= min_train_rows // 2:
            exp_model = _make_reg()
            exp_model.fit(imputer.transform(x_train.loc[tr_mask]), ytr_ret[tr_mask])
            expected_return = exp_model.predict(x_test_imp)
            analysis["selected_hold_expected_return_model"] = {
                "target": ret_col,
                "feature_importance": _feature_importance(exp_model, feature_names),
            }

    if "trade_win_loss_prediction" in analysis and "random_forest" in analysis["trade_win_loss_prediction"]:
        analysis["strategy_comparison"] = _ml_strategy_comparison(
            test_df, win_prob, loss_prob, hold,
            expected_return=expected_return,
            ml_prob_threshold=ml_prob_threshold,
            ml_expected_return_min=ml_expected_return_min,
            ml_large_loss_max=ml_large_loss_max,
            account_size=account_size,
            position_cap_pct=position_cap_pct,
            commission=commission,
            sizing_mode=account_sizing_mode,
        )
        analysis["gate_analysis"] = _build_gate_analysis(
            test_df, hold, win_prob, loss_prob, expected_return,
            target_prob=target_prob, timeout_prob=timeout_prob,
            ml_prob_threshold=ml_prob_threshold,
            ml_expected_return_min=ml_expected_return_min,
            ml_large_loss_max=ml_large_loss_max,
            diagnostics_limit=gate_diagnostics_limit,
        )

    top_imp = analysis.get("trade_win_loss_prediction", {}).get("random_forest", {}).get("feature_importance", [])
    analysis["plain_english_summary"] = {
        "features_that_mattered": top_imp[:12],
        "likely_noise_features": top_imp[-12:] if len(top_imp) > 12 else [],
        "did_ml_improve": analysis.get("strategy_comparison", {}).get("answers", {}),
        "notes": [
            "Models are trained with an out-of-time split, using the most recent year or final 25% as test data.",
            "SHAP/XGBoost/LightGBM are optional; this run used sklearn interpretable models unless those packages are later installed.",
            "ML stop/target adjustment is a proxy overlay unless full intraday remeasurement is added for adjusted stops and targets.",
        ],
    }
    return analysis


def _collect_trades(precomputed: dict, scan_dates, spy_df, spy_regime,
                    vix_regime, args,
                    vix_ts_series=None,
                    sector_breadth_series=None,
                    vix_raw_df=None,
                    collect_diagnostics: bool = True) -> list:
    """
    Core scan: for each ticker × scan_date, score and measure outcomes.
    Returns list of trade dicts.
    """
    hold_periods = args.hold_periods
    primary_h    = args.primary_hold
    min_price    = args.min_price
    max_price    = getattr(args, "max_price", None)
    max_atr_pct  = getattr(args, "max_atr_pct", None)
    min_adv      = getattr(args, "min_adv", None)
    allow_friday = getattr(args, "allow_friday", False)
    threshold    = args.threshold
    target_mult  = args.target_mult
    stop_mult    = args.stop_mult
    score_mode   = getattr(args, "score_mode", "breakout")
    entry_timing = getattr(args, "entry_timing", "trigger_break")
    no_gate_filter = getattr(args, "no_gate_filter", False)
    score_min    = getattr(args, "score_min", None)
    score_max    = getattr(args, "score_max", None)
    diagnostics_enabled = collect_diagnostics and getattr(args, "diagnostics", True)
    missed_big_win_pct = getattr(args, "missed_big_win_pct", 0.05)
    missed_max_examples = getattr(args, "missed_max_examples", 50)
    ml_candidate_sample = int(getattr(args, "ml_candidate_sample", 0) or 0)
    ml_rejected_candidates = []
    ml_reject_seen = 0

    # For grid search pre-collection we want the lowest threshold
    grid_thresholds = getattr(args, "grid_thresholds", [threshold])
    effective_thresh = min(grid_thresholds) if args.grid_search else threshold

    all_trades   = []
    missed_big_wins = []
    total_scored = 0
    total_passed = 0

    spy_idx_map: dict = {}
    if spy_df is not None:
        for i, dt in enumerate(spy_df.index):
            spy_idx_map[dt] = i

    # Precompute per-date regime scores from MarketRegimeEngine (once, not per ticker).
    # regime_score_map: date → {regime_score, crash_risk_score, risk_on_score, risk_off_score}
    _regime_score_map: dict = {}
    if spy_df is not None and len(spy_df) > 20:
        try:
            from tradingagents.screening.market_regime import MarketRegimeEngine
            _mr_engine = MarketRegimeEngine()
            for _d in scan_dates:
                _ds = str(_d.date())
                try:
                    _rs = _mr_engine.compute_from_dataframes(
                        spy_df=spy_df,
                        vix_df=vix_raw_df,
                        as_of_date=_ds,
                    )
                    _regime_score_map[_ds] = {
                        "regime_score":     round(_rs.regime_score, 4),
                        "crash_risk_score": round(_rs.crash_risk_score, 4),
                        "risk_on_score":    round(getattr(_rs, "prob_risk_on", 0.5), 4),
                        "risk_off_score":   round(getattr(_rs, "prob_risk_off", 0.0), 4),
                    }
                except Exception:
                    pass
        except ImportError:
            pass

    for ticker, (df, pc) in tqdm(precomputed.items(),
                                  desc="Scanning", unit="ticker"):
        df_idx = df.index

        for date_ts in scan_dates:
            pos = int(df_idx.searchsorted(date_ts, side="right")) - 1
            if pos < MIN_HISTORY or pos >= len(df) - 2:
                continue
            if abs((df_idx[pos] - date_ts).days) > 5:
                continue
            if not allow_friday and df_idx[pos].dayofweek == 4:
                continue

            total_scored += 1

            # Compute regime BEFORE scoring so it can influence the score
            pre_regime = "unknown"
            if len(spy_regime) > 0:
                ri = spy_regime.index.searchsorted(date_ts, side="right") - 1
                if 0 <= ri < len(spy_regime):
                    pre_regime = str(spy_regime.iloc[ri])

            pre_vix_reg = "unknown"
            if vix_regime is not None and len(vix_regime) > 0:
                vi = vix_regime.index.searchsorted(date_ts, side="right") - 1
                if 0 <= vi < len(vix_regime):
                    pre_vix_reg = str(vix_regime.iloc[vi])

            # VIX term structure: VIX3M/VIX ratio (from v5 — backwardation = skip)
            pre_vix_ts = None
            if vix_ts_series is not None and len(vix_ts_series) > 0:
                ti = vix_ts_series.index.searchsorted(date_ts, side="right") - 1
                if 0 <= ti < len(vix_ts_series):
                    v = vix_ts_series.iloc[ti]
                    pre_vix_ts = float(v) if pd.notna(v) else None

            # Sector breadth: fraction of 11 sectors with positive 20d return
            pre_sector_breadth = None
            if sector_breadth_series is not None and len(sector_breadth_series) > 0:
                si2 = sector_breadth_series.index.searchsorted(date_ts, side="right") - 1
                if 0 <= si2 < len(sector_breadth_series):
                    v = sector_breadth_series.iloc[si2]
                    pre_sector_breadth = float(v) if pd.notna(v) else None

            spy_close = spy_sma50 = spy_sma200 = spy_ret5 = spy_ret20 = None
            # Extra regime features for ML_NUMERIC_FEATURES
            _spy_ret60 = _spy_dd20 = _spy_abv50 = _spy_abv200 = _spy_golden = None
            if spy_df is not None:
                si_gate = spy_df.index.searchsorted(date_ts, side="right") - 1
                if si_gate >= 200:
                    spy_close = float(spy_df["Close"].iloc[si_gate])
                    spy_sma50 = float(spy_df["Close"].iloc[si_gate - 49:si_gate + 1].mean())
                    spy_sma200 = float(spy_df["Close"].iloc[si_gate - 199:si_gate + 1].mean())
                    spy_ret5  = float(spy_close / spy_df["Close"].iloc[si_gate - 5] - 1) if si_gate >= 5 else None
                    spy_ret20 = float(spy_close / spy_df["Close"].iloc[si_gate - 20] - 1) if si_gate >= 20 else None
                    # Extended regime features
                    _spy_ret60 = float(spy_close / spy_df["Close"].iloc[si_gate - 60] - 1) if si_gate >= 60 else None
                    _spy_high20 = float(spy_df["Close"].iloc[max(0, si_gate - 19):si_gate + 1].max())
                    _spy_dd20 = round((spy_close - _spy_high20) / _spy_high20, 4) if _spy_high20 > 0 else None
                    _spy_abv50  = 1.0 if spy_close > spy_sma50  else 0.0
                    _spy_abv200 = 1.0 if spy_close > spy_sma200 else 0.0
                    _spy_golden = 1.0 if spy_sma50 > spy_sma200  else 0.0

            # VIX-based regime features: vix_20d_zscore, vol_expansion
            _vix_20d_zscore = _vol_expansion = None
            if vix_raw_df is not None:
                try:
                    vi_gate = vix_raw_df.index.searchsorted(date_ts, side="right") - 1
                    if vi_gate >= 20:
                        _vix_close_now = float(vix_raw_df["Close"].iloc[vi_gate])
                        _vix_20d_slice = vix_raw_df["Close"].iloc[max(0, vi_gate - 19):vi_gate + 1]
                        _vix_20d_mean  = float(_vix_20d_slice.mean())
                        _vix_20d_std   = float(_vix_20d_slice.std(ddof=1))
                        if _vix_20d_std > 0:
                            _vix_20d_zscore = round((_vix_close_now - _vix_20d_mean) / _vix_20d_std, 4)
                            _vol_expansion  = 1.0 if _vix_20d_zscore > 1.5 else 0.0
                except Exception:
                    pass

            try:
                score, signals = score_at(pc, df, pos, target_mult, stop_mult,
                                          regime=pre_regime, vix_reg=pre_vix_reg,
                                          vix_ts=pre_vix_ts,
                                          sector_breadth=pre_sector_breadth,
                                          score_mode=score_mode,
                                          spy_close=spy_close,
                                          spy_sma50=spy_sma50,
                                          spy_sma200=spy_sma200,
                                          spy_ret5=spy_ret5,
                                          spy_ret20=spy_ret20)
            except Exception:
                continue

            if not signals:
                continue

            # Inject extended regime features into signals so they land in trade rows
            # and become ML training features via ML_NUMERIC_FEATURES.
            if _spy_ret60 is not None:    signals["spy_ret60"]          = _spy_ret60
            if _spy_dd20 is not None:     signals["spy_drawdown_20d"]   = _spy_dd20
            if _spy_abv50 is not None:    signals["spy_above_sma50"]    = _spy_abv50
            if _spy_abv200 is not None:   signals["spy_above_sma200"]   = _spy_abv200
            if _spy_golden is not None:   signals["spy_golden_cross"]   = _spy_golden
            if _vix_20d_zscore is not None: signals["vix_20d_zscore"]   = _vix_20d_zscore
            if _vol_expansion is not None:  signals["vol_expansion"]    = _vol_expansion
            # Regime engine scores (precomputed once per scan date)
            _rmap = _regime_score_map.get(str(date_ts.date()), {})
            if _rmap:
                signals["regime_score"]     = _rmap["regime_score"]
                signals["crash_risk_score"] = _rmap["crash_risk_score"]
                signals["risk_on_score"]    = _rmap["risk_on_score"]
                signals["risk_off_score"]   = _rmap["risk_off_score"]

            rejection_reasons = []
            if score < effective_thresh:
                rejection_reasons.append("below_threshold")

            gate_status = signals.get("confirmed_pullback_gates")
            if not no_gate_filter and gate_status and gate_status != "pass":
                rejection_reasons.extend(
                    reason for reason in str(gate_status).split(",") if reason
                )

            if score_min is not None and score < score_min:
                rejection_reasons.append("below_score_min")
            if score_max is not None and score > score_max:
                rejection_reasons.append("above_score_max")

            if max_atr_pct is not None:
                atr_pct = signals.get("atr_pct")
                if atr_pct is not None and atr_pct > max_atr_pct:
                    rejection_reasons.append("atr_pct_filter")

            if min_adv is not None:
                v50 = float(pc["vol50"].iloc[pos]) if pd.notna(pc["vol50"].iloc[pos]) else 0
                if v50 < min_adv:
                    rejection_reasons.append("min_adv_filter")

            if rejection_reasons:
                if diagnostics_enabled or ml_candidate_sample > 0:
                    out = measure_outcome(
                        df, pos, signals["entry"], signals["target"], signals["stop"], primary_h,
                        entry_timing=entry_timing,
                        target_mult=target_mult,
                        stop_mult=stop_mult,
                        atr=signals.get("atr"),
                    )
                    if diagnostics_enabled and out and out["actual_return"] >= missed_big_win_pct:
                        row_date = df_idx[pos]
                        missed = {
                            "ticker": ticker,
                            "scan_date": str(row_date.date()),
                            "day_of_week": int(row_date.dayofweek),
                            "month": str(row_date.to_period("M")),
                            "year": int(row_date.year),
                            "score": score,
                            "spy_regime": pre_regime,
                            "vix_regime": pre_vix_reg,
                            "rejection_reasons": rejection_reasons,
                            **signals,
                            f"h{primary_h}_outcome": out["outcome"],
                            f"h{primary_h}_entry": out["entry_price"],
                            f"h{primary_h}_target": out["target_price"],
                            f"h{primary_h}_stop": out["stop_price"],
                            f"h{primary_h}_return": out["actual_return"],
                            f"h{primary_h}_exit": out["exit_price"],
                            f"h{primary_h}_exit_date": out["exit_date"],
                            f"h{primary_h}_days": out["days_held"],
                            f"h{primary_h}_mae": out["mae"],
                            f"h{primary_h}_mfe": out["mfe"],
                            f"h{primary_h}_r_multiple": out["r_multiple"],
                        }
                        missed_big_wins.append(missed)
                        missed_big_wins = sorted(
                            missed_big_wins,
                            key=lambda r: r.get(f"h{primary_h}_return", 0),
                            reverse=True,
                        )[:missed_max_examples]
                    if out and ml_candidate_sample > 0:
                        ml_reject_seen += 1
                        row_date = df_idx[pos]
                        ml_row = {
                            "ticker": ticker,
                            "scan_date": str(row_date.date()),
                            "day_of_week": int(row_date.dayofweek),
                            "month": str(row_date.to_period("M")),
                            "year": int(row_date.year),
                            "score": score,
                            "spy_regime": pre_regime,
                            "vix_regime": pre_vix_reg,
                            "rejection_reasons": rejection_reasons,
                            "candidate_status": "rejected",
                            **signals,
                            f"h{primary_h}_outcome": out["outcome"],
                            f"h{primary_h}_entry": out["entry_price"],
                            f"h{primary_h}_target": out["target_price"],
                            f"h{primary_h}_stop": out["stop_price"],
                            f"h{primary_h}_return": out["actual_return"],
                            f"h{primary_h}_exit": out["exit_price"],
                            f"h{primary_h}_exit_date": out["exit_date"],
                            f"h{primary_h}_days": out["days_held"],
                            f"h{primary_h}_mae": out["mae"],
                            f"h{primary_h}_mfe": out["mfe"],
                            f"h{primary_h}_r_multiple": out["r_multiple"],
                        }
                        if len(ml_rejected_candidates) < ml_candidate_sample:
                            ml_rejected_candidates.append(ml_row)
                        else:
                            j = random.randint(0, ml_reject_seen - 1)
                            if j < ml_candidate_sample:
                                ml_rejected_candidates[j] = ml_row
                continue

            # Passed all filters.
            if min_adv is not None:
                v50 = float(pc["vol50"].iloc[pos]) if pd.notna(pc["vol50"].iloc[pos]) else 0
                if v50 < min_adv:
                    continue

            total_passed += 1
            entry    = signals["entry"]
            target   = signals["target"]
            stop     = signals["stop"]
            row_date = df_idx[pos]

            # Market regime on this date
            regime = "unknown"
            if len(spy_regime) > 0:
                ri = spy_regime.index.searchsorted(date_ts, side="right") - 1
                if 0 <= ri < len(spy_regime):
                    regime = str(spy_regime.iloc[ri])

            # VIX regime
            vix_reg = "unknown"
            if vix_regime is not None and len(vix_regime) > 0:
                vi = vix_regime.index.searchsorted(date_ts, side="right") - 1
                if 0 <= vi < len(vix_regime):
                    vix_reg = str(vix_regime.iloc[vi])

            # SPY position aligned to this date (for alpha)
            spy_pos = None
            if spy_df is not None:
                si = spy_df.index.searchsorted(date_ts, side="right") - 1
                if 0 <= si < len(spy_df):
                    spy_pos = si

            trade = {
                "ticker":      ticker,
                "scan_date":   str(row_date.date()),
                "day_of_week": int(row_date.dayofweek),
                "month":       str(row_date.to_period("M")),
                "year":        int(row_date.year),
                "score":       score,
                "spy_regime":  regime,
                "vix_regime":  vix_reg,
                "candidate_status": "executed",
                **signals,
            }

            # Measure outcomes for all hold periods
            any_valid = False
            for h in hold_periods:
                out = measure_outcome(
                    df, pos, entry, target, stop, h,
                    entry_timing=entry_timing,
                    target_mult=target_mult,
                    stop_mult=stop_mult,
                    atr=signals.get("atr"),
                )
                if out:
                    ret     = out["actual_return"]
                    outcome = out["outcome"]
                    if h == primary_h:
                        trade["entry"] = out["entry_price"]
                        trade["target"] = out["target_price"]
                        trade["stop"] = out["stop_price"]

                    trade[f"h{h}_outcome"]        = outcome
                    trade[f"h{h}_entry"]          = out["entry_price"]
                    trade[f"h{h}_target"]         = out["target_price"]
                    trade[f"h{h}_stop"]           = out["stop_price"]
                    trade[f"h{h}_return"]         = ret
                    trade[f"h{h}_exit"]           = out["exit_price"]
                    trade[f"h{h}_exit_date"]      = out["exit_date"]
                    trade[f"h{h}_days"]           = out["days_held"]
                    trade[f"h{h}_mae"]            = out["mae"]
                    trade[f"h{h}_mfe"]            = out["mfe"]
                    trade[f"h{h}_r_multiple"]     = out["r_multiple"]

                    trade[f"h{h}_direction_correct"] = ret > 0
                    trade[f"h{h}_target_hit"]        = outcome == "TARGET_HIT"
                    trade[f"h{h}_stopped_out"]       = outcome == "STOP_HIT"
                    trade[f"h{h}_timed_out_profit"]  = outcome == "TIMED_OUT" and ret > 0
                    trade[f"h{h}_timed_out_loss"]    = outcome == "TIMED_OUT" and ret <= 0
                    trade[f"h{h}_strong_win"]        = ret >= 0.01
                    trade[f"h{h}_bad_loss"]          = ret <= -0.01

                    if spy_pos is not None:
                        spy_ret = spy_return_over(spy_df, spy_pos, h)
                        trade[f"h{h}_spy_return"] = spy_ret
                        trade[f"h{h}_alpha"]      = round(ret - spy_ret, 4)
                        trade[f"h{h}_beat_spy"]   = ret > spy_ret

                    any_valid = True

            if any_valid:
                all_trades.append(trade)

    diagnostics = {
        "missed_big_wins": missed_big_wins,
        "ml_rejected_candidates": ml_rejected_candidates,
        "ml_rejected_seen": ml_reject_seen,
        "missed_big_win_pct": missed_big_win_pct,
        "missed_max_examples": missed_max_examples,
    }
    return all_trades, total_scored, total_passed, diagnostics


# ── Grid search (threshold-only, fast) ───────────────────────────────────────

def run_grid_search(trades_df: pd.DataFrame, args) -> list:
    """
    Fast grid search over threshold variations only.
    Uses already-collected trades DataFrame — just filter at different cutoffs.
    Returns sorted list of result dicts.
    """
    primary_h = args.primary_hold
    results   = []

    for thresh in args.grid_thresholds:
        subset = trades_df[trades_df["score"] >= thresh]
        if len(subset) == 0:
            continue
        st = _stats(subset, primary_h)
        if not st:
            continue
        st["threshold"] = thresh
        st["target_mult"] = args.target_mult
        st["stop_mult"] = args.stop_mult
        st["search_scope"] = "threshold_only"
        st["trades"]    = len(subset)
        results.append(st)

    return sorted(results, key=lambda x: x.get("profit_factor", 0), reverse=True)


# ── Walk-forward analysis ─────────────────────────────────────────────────────

def run_walk_forward(precomputed: dict, all_scan_dates, spy_df,
                     spy_regime, vix_regime, args,
                     vix_ts_series=None, sector_breadth_series=None,
                     vix_raw_df=None) -> list:
    """
    Rolling walk-forward: choose the best threshold on the train window,
    then apply that threshold to the next out-of-sample test window.
    """
    results   = []
    dates     = sorted(all_scan_dates)
    wf_window = args.wf_window
    wf_step   = args.wf_step
    i         = 0

    while i + wf_window + wf_step <= len(dates):
        train_dates = dates[i : i + wf_window]
        test_dates = dates[i + wf_window : i + wf_window + wf_step]

        train_trades, _, _, _ = _collect_trades(
            precomputed, train_dates, spy_df, spy_regime, vix_regime, args,
            vix_ts_series=vix_ts_series,
            sector_breadth_series=sector_breadth_series,
            vix_raw_df=vix_raw_df,
            collect_diagnostics=False,
        )
        if not train_trades:
            i += wf_step
            continue

        df_train = pd.DataFrame(train_trades)
        threshold_rows = []
        for thresh in args.grid_thresholds:
            subset = df_train[df_train["score"] >= thresh]
            if len(subset) < 20:
                continue
            st_train = _stats(subset, args.primary_hold)
            if st_train:
                st_train["threshold"] = thresh
                threshold_rows.append(st_train)
        if not threshold_rows:
            i += wf_step
            continue
        best_train = max(
            threshold_rows,
            key=lambda r: (r.get("profit_factor", 0) or 0, r.get("avg_return_pct", 0) or 0),
        )
        selected_threshold = best_train["threshold"]

        test_trades, _, _, _ = _collect_trades(
            precomputed, test_dates, spy_df, spy_regime, vix_regime, args,
            vix_ts_series=vix_ts_series,
            sector_breadth_series=sector_breadth_series,
            vix_raw_df=vix_raw_df,
            collect_diagnostics=False,
        )
        if test_trades:
            df_t = pd.DataFrame(test_trades)
            df_t = df_t[df_t["score"] >= selected_threshold]
            if df_t.empty:
                i += wf_step
                continue
            st   = _stats(df_t, args.primary_hold)
            if st:
                st["selected_threshold"] = selected_threshold
                st["train_period_start"] = str(train_dates[0].date())
                st["train_period_end"] = str(train_dates[-1].date())
                st["train_trades"] = int(best_train.get("trades", 0))
                st["train_profit_factor"] = best_train.get("profit_factor", 0)
                st["period_start"] = str(test_dates[0].date())
                st["period_end"]   = str(test_dates[-1].date())
                st["n_test_dates"] = len(test_dates)
                results.append(st)
        i += wf_step

    return results


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def run_monte_carlo(trades_df: pd.DataFrame, hold: int,
                    n_sims: int = 1000, n_sim_trades: int = 252) -> dict:
    """Bootstrap equity curves from trade list."""
    ret_col = f"h{hold}_return"
    if ret_col not in trades_df.columns:
        return {}
    rets = pd.to_numeric(trades_df[ret_col], errors="coerce").dropna().tolist()
    if len(rets) < 10:
        return {}

    final_equities = []
    max_drawdowns  = []

    for _ in range(n_sims):
        sample = random.choices(rets, k=min(n_sim_trades, len(rets)))
        equity = 1.0
        peak   = 1.0
        max_dd = 0.0
        for r in sample:
            equity *= (1 + r)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        final_equities.append(equity)
        max_drawdowns.append(max_dd)

    fe = sorted(final_equities)
    n  = len(fe)
    return {
        "n_sims":             n_sims,
        "n_trades":           len(rets),
        "n_sim_trades":       min(n_sim_trades, len(rets)),
        "equity_p05":         round(fe[int(0.05 * n)], 4),
        "equity_p25":         round(fe[int(0.25 * n)], 4),
        "equity_p50":         round(fe[int(0.50 * n)], 4),
        "equity_p75":         round(fe[int(0.75 * n)], 4),
        "equity_p95":         round(fe[min(int(0.95 * n), n - 1)], 4),
        "pct_profitable":     round(sum(e > 1 for e in fe) / n, 4),
        "pct_ruin":           round(sum(e < 0.7 for e in fe) / n, 4),
        "avg_max_drawdown":   round(float(np.mean(max_drawdowns)), 4),
        "worst_drawdown_p95": round(sorted(max_drawdowns)[min(int(0.95 * n), n - 1)], 4),
    }


# ── R-multiple distribution ───────────────────────────────────────────────────

def _r_multiple_distribution(trades_df: pd.DataFrame, hold: int) -> dict:
    """Histogram of R-multiples into standard buckets."""
    col = f"h{hold}_r_multiple"
    if col not in trades_df.columns:
        return {}
    rm = pd.to_numeric(trades_df[col], errors="coerce").dropna()
    if len(rm) == 0:
        return {}
    buckets = {
        "lt_neg2":       int((rm < -2).sum()),
        "neg2_to_neg1":  int(((rm >= -2) & (rm < -1)).sum()),
        "neg1_to_0":     int(((rm >= -1) & (rm < 0)).sum()),
        "0_to_1":        int(((rm >= 0)  & (rm < 1)).sum()),
        "1_to_2":        int(((rm >= 1)  & (rm < 2)).sum()),
        "gt_2":          int((rm >= 2).sum()),
        "avg_r":         round(float(rm.mean()), 3),
        "median_r":      round(float(rm.median()), 3),
        "pct_positive":  round(float((rm > 0).mean()), 4),
    }
    return buckets


# ── MAE / MFE analysis ────────────────────────────────────────────────────────

def _mae_mfe_analysis(trades_df: pd.DataFrame, hold: int) -> dict:
    """Summarize max adverse / favorable excursion by outcome."""
    mae_col = f"h{hold}_mae"
    mfe_col = f"h{hold}_mfe"
    ret_col = f"h{hold}_return"
    if mae_col not in trades_df.columns:
        return {}

    mae = pd.to_numeric(trades_df[mae_col], errors="coerce")
    mfe = pd.to_numeric(trades_df[mfe_col], errors="coerce")
    ret = pd.to_numeric(trades_df[ret_col], errors="coerce")

    mask_win  = ret > 0
    mask_lose = ret <= 0

    # Winners that went negative before recovering
    pct_negative_before_recovery = 0.0
    if mask_win.any():
        went_neg = (mae[mask_win] > 0.005).sum()
        pct_negative_before_recovery = round(
            float(went_neg / mask_win.sum()), 4
        )

    return {
        "avg_mae_all":       round(float(mae.mean()), 4),
        "avg_mfe_all":       round(float(mfe.mean()), 4),
        "avg_mae_winners":   round(float(mae[mask_win].mean()),  4) if mask_win.any()  else None,
        "avg_mfe_winners":   round(float(mfe[mask_win].mean()),  4) if mask_win.any()  else None,
        "avg_mae_losers":    round(float(mae[mask_lose].mean()), 4) if mask_lose.any() else None,
        "avg_mfe_losers":    round(float(mfe[mask_lose].mean()), 4) if mask_lose.any() else None,
        "pct_winners_went_negative_first": pct_negative_before_recovery,
        "mae_p90":           round(float(mae.quantile(0.90)), 4),
        "mfe_p90":           round(float(mfe.quantile(0.90)), 4),
    }


# ── ATR% bucket analysis ──────────────────────────────────────────────────────

def _atr_bucket_analysis(trades_df: pd.DataFrame, hold: int) -> dict:
    """Group trades by ATR% quartiles and report stats."""
    if "atr_pct" not in trades_df.columns:
        return {}
    df = trades_df.copy()
    def atr_label(v):
        if pd.isna(v):     return "unknown"
        if v < 0.015:      return "low (<1.5%)"
        if v < 0.030:      return "medium (1.5-3%)"
        if v < 0.050:      return "high (3-5%)"
        return "very_high (>5%)"
    df["_atr_bucket"] = df["atr_pct"].apply(atr_label)
    result = {}
    for bkt, grp in df.groupby("_atr_bucket"):
        if len(grp) < 2:
            continue
        st = _stats(grp, hold)
        st["avg_atr_pct"] = round(float(grp["atr_pct"].mean()), 4)
        result[str(bkt)]  = st
    return result


# ── Price bucket analysis ─────────────────────────────────────────────────────

def _price_bucket_analysis(trades_df: pd.DataFrame, hold: int) -> dict:
    """Group trades by price range."""
    if "entry" not in trades_df.columns:
        return {}
    df = trades_df.copy()
    def price_label(v):
        if pd.isna(v):  return "unknown"
        if v < 15:      return "$5-15"
        if v < 30:      return "$15-30"
        if v < 50:      return "$30-50"
        if v < 100:     return "$50-100"
        return "$100+"
    df["_price_bucket"] = df["entry"].apply(price_label)
    result = {}
    order  = ["$5-15", "$15-30", "$30-50", "$50-100", "$100+"]
    for bkt in order:
        grp = df[df["_price_bucket"] == bkt]
        if len(grp) < 2:
            continue
        st = _stats(grp, hold)
        st["avg_price"] = round(float(grp["entry"].mean()), 2)
        result[bkt]     = st
    return result


# ── VIX regime analysis ───────────────────────────────────────────────────────

def _vix_regime_analysis(trades_df: pd.DataFrame, hold: int) -> dict:
    """Stats broken down by VIX regime."""
    if "vix_regime" not in trades_df.columns:
        return {}
    result = {}
    for reg, grp in trades_df.groupby("vix_regime"):
        if len(grp) < 2:
            continue
        result[str(reg)] = _stats(grp, hold)
    return result


# ── Position sizing table ─────────────────────────────────────────────────────

def _position_sizing_table(hold_stats: dict, account_sizes=None) -> dict:
    """Kelly-based position sizing recommendations per hold period."""
    if account_sizes is None:
        account_sizes = [5_000, 10_000, 25_000, 50_000, 100_000, 250_000]
    result = {}
    for hold_key, st in hold_stats.items():
        if not st:
            continue
        k_pct  = st.get("kelly_pct", 0)
        hk_pct = st.get("half_kelly_pct", 0)
        sizing  = {
            "kelly_pct":      k_pct,
            "half_kelly_pct": hk_pct,
            "by_account_size": {}
        }
        for acct in account_sizes:
            sizing["by_account_size"][f"${acct:,}"] = {
                "kelly_dollars":      round(acct * k_pct / 100, 0),
                "half_kelly_dollars": round(acct * hk_pct / 100, 0),
            }
        result[hold_key] = sizing
    return result


def _simulate_account(trades_df: pd.DataFrame, hold: int, hold_stats: dict,
                      account_size: float = 5000.0,
                      position_cap_pct: float = 20.0,
                      commission: float = 1.0,
                      slippage_bps: float = 5.0,
                      regime_stats: dict = None,
                      sizing_mode: str = "fixed") -> dict:
    """Simulate a small account taking top-scored signals.

    sizing_mode:
      "fixed" (default): size every trade at position_cap_pct of current
          equity. No look-ahead at all. Use this for honest forward-style
          numbers.
      "kelly_static": size from the whole-sample Half-Kelly in hold_stats.
          NOTE: this uses the full period's win/payoff to size every trade,
          so the position size of early trades depends on later trades — a
          look-ahead in *sizing*. Use only for legacy comparisons.

    Reported max_drawdown is computed on a CONSERVATIVE equity track that
    marks each open position down to its realised worst (entry*(1-MAE))
    using the per-trade h{hold}_mae column when present, instead of marking
    open positions at cost — so drawdown is not optimistically understated.
    Final value / returns remain realised-cash based (unchanged).
    """
    ret_col = f"h{hold}_return"
    exit_date_col = f"h{hold}_exit_date"
    exit_col = f"h{hold}_exit"
    mae_col = f"h{hold}_mae"
    required_cols = {"scan_date", "score", "entry", ret_col, exit_date_col}
    if trades_df.empty or not required_cols.issubset(trades_df.columns):
        return {}

    half_kelly_pct = float(hold_stats.get("half_kelly_pct", 0) or 0)
    if sizing_mode == "fixed":
        sizing_pct = max(0.0, float(position_cap_pct or 0))
        half_kelly_pct = sizing_pct  # reported as effective
        regime_stats = None  # fixed sizing ignores per-regime Kelly
    else:
        sizing_pct = max(0.0, min(half_kelly_pct, float(position_cap_pct or 0)))
    regime_sizing = {}
    if regime_stats:
        cap = float(position_cap_pct or 0)
        for r, rst in regime_stats.items():
            rk = float(rst.get("half_kelly_pct", 0) or 0)
            regime_sizing[r] = max(0.0, min(rk, cap))
    cash = float(account_size)
    open_positions = []
    closed_trades = []
    skipped_trades = 0
    peak_equity = float(account_size)
    equity_curve = []

    df = trades_df.copy()
    df["_scan_dt"] = pd.to_datetime(df["scan_date"], errors="coerce")
    df["_exit_dt"] = pd.to_datetime(df[exit_date_col], errors="coerce")
    df["_entry"] = pd.to_numeric(df["entry"], errors="coerce")
    df["_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    df["_score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["_scan_dt", "_exit_dt", "_entry", "_ret", "_score"])
    df = df[df["_entry"] > 0].sort_values(["_scan_dt", "_score"], ascending=[True, False])
    # Apply round-trip slippage: entry slip (buy at ask) + exit slip (sell at bid)
    # Each side is `slippage_bps / 10000`; total round-trip deducted from return.
    if slippage_bps and float(slippage_bps) > 0:
        slip_rt = 2.0 * float(slippage_bps) / 10000.0
        df["_ret"] = df["_ret"] - slip_rt
    if df.empty:
        return {}

    if mae_col in df.columns:
        df["_mae"] = pd.to_numeric(df[mae_col], errors="coerce").clip(lower=0.0).fillna(0.0)
    else:
        df["_mae"] = 0.0

    trade_dates = set(df["_scan_dt"].dt.normalize())
    exit_dates = set(df["_exit_dt"].dt.normalize())
    event_dates = sorted(trade_dates | exit_dates)

    worst_peak = float(account_size)
    max_dd_conservative = 0.0

    def mark_equity():
        return float(cash + sum(p["notional"] for p in open_positions))

    def mark_equity_worst():
        # Open positions marked to their realised worst (entry*(1-MAE)):
        # a conservative lower bound on interim equity so reported drawdown
        # is not understated by cost-marking.
        return float(cash + sum(p["notional"] * (1.0 - p.get("mae", 0.0))
                                for p in open_positions))

    def record_curve(dt):
        nonlocal peak_equity, worst_peak, max_dd_conservative
        equity = mark_equity()
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        eq_worst = mark_equity_worst()
        worst_peak = max(worst_peak, eq_worst)
        if worst_peak > 0:
            max_dd_conservative = max(
                max_dd_conservative, (worst_peak - eq_worst) / worst_peak)
        equity_curve.append({
            "date": str(pd.Timestamp(dt).date()),
            "value": round(equity, 2),
            "cash": round(float(cash), 2),
            "open_positions": len(open_positions),
            "drawdown": round(float(drawdown), 4),
        })

    record_curve(event_dates[0])
    for current_dt in event_dates:
        due_positions = [p for p in open_positions if p["exit_dt"] <= current_dt]
        open_positions = [p for p in open_positions if p["exit_dt"] > current_dt]
        closed_batch = []
        for pos in due_positions:
            proceeds = max(0.0, pos["notional"] * (1 + pos["return_pct"]) - commission)
            cash += proceeds
            pnl = proceeds - pos["notional"] - pos["entry_commission"]
            closed_batch.append({
                "ticker": pos["ticker"],
                "entry_date": str(pos["entry_dt"].date()),
                "exit_date": str(pos["exit_dt"].date()),
                "score": round(pos["score"], 2),
                "entry_price": round(pos["entry_price"], 4),
                "exit_price": round(pos["exit_price"], 4) if pos["exit_price"] is not None else None,
                "shares": int(pos["shares"]),
                "notional": round(pos["notional"], 2),
                "return_pct": round(pos["return_pct"] * 100, 3),
                "pnl": round(pnl, 2),
            })
        batch_equity = round(mark_equity(), 2)
        for closed in closed_batch:
            closed["account_value_after_exit"] = batch_equity
            closed_trades.append(closed)

        day_trades = df[df["_scan_dt"].dt.normalize() == current_dt]
        for _, trade in day_trades.iterrows():
            equity = mark_equity()
            trade_regime = str(trade.get("spy_regime", "unknown"))
            eff_sizing = regime_sizing.get(trade_regime, sizing_pct)
            budget = equity * eff_sizing / 100
            entry = float(trade["_entry"])
            spendable = min(budget, max(0.0, cash - commission))
            shares = int(spendable // entry)
            total_cost = shares * entry + commission
            if shares < 1 or total_cost > cash or sizing_pct <= 0:
                skipped_trades += 1
                continue
            cash -= total_cost
            exit_price = trade.get(exit_col)
            open_positions.append({
                "ticker": str(trade.get("ticker", "")),
                "entry_dt": pd.Timestamp(trade["_scan_dt"]),
                "exit_dt": pd.Timestamp(trade["_exit_dt"]).normalize(),
                "entry_price": entry,
                "exit_price": float(exit_price) if pd.notna(exit_price) else None,
                "shares": shares,
                "notional": float(shares * entry),
                "return_pct": float(trade["_ret"]),
                "score": float(trade["_score"]),
                "entry_commission": float(commission),
                "mae": float(trade.get("_mae", 0.0) or 0.0),
            })

        record_curve(current_dt)

    final_exit_dates = sorted({p["exit_dt"] for p in open_positions})
    for current_dt in final_exit_dates:
        due_positions = [p for p in open_positions if p["exit_dt"] <= current_dt]
        open_positions = [p for p in open_positions if p["exit_dt"] > current_dt]
        closed_batch = []
        for pos in due_positions:
            proceeds = max(0.0, pos["notional"] * (1 + pos["return_pct"]) - commission)
            cash += proceeds
            pnl = proceeds - pos["notional"] - pos["entry_commission"]
            closed_batch.append({
                "ticker": pos["ticker"],
                "entry_date": str(pos["entry_dt"].date()),
                "exit_date": str(pos["exit_dt"].date()),
                "score": round(pos["score"], 2),
                "entry_price": round(pos["entry_price"], 4),
                "exit_price": round(pos["exit_price"], 4) if pos["exit_price"] is not None else None,
                "shares": int(pos["shares"]),
                "notional": round(pos["notional"], 2),
                "return_pct": round(pos["return_pct"] * 100, 3),
                "pnl": round(pnl, 2),
            })
        batch_equity = round(mark_equity(), 2)
        for closed in closed_batch:
            closed["account_value_after_exit"] = batch_equity
            closed_trades.append(closed)
        record_curve(current_dt)

    curve_df = pd.DataFrame(equity_curve).drop_duplicates(subset=["date"], keep="last")
    curve_df["date_dt"] = pd.to_datetime(curve_df["date"])
    curve_df = curve_df.sort_values("date_dt")
    period_values = curve_df.set_index("date_dt")["value"]

    yearly_returns = {}
    prev_value = float(account_size)
    for year, value in period_values.groupby(period_values.index.year).last().items():
        ret = (float(value) / prev_value - 1) if prev_value else 0.0
        yearly_returns[str(year)] = round(ret * 100, 2)
        prev_value = float(value)

    monthly_returns = {}
    prev_value = float(account_size)
    for period, value in period_values.groupby(period_values.index.to_period("M")).last().items():
        ret = (float(value) / prev_value - 1) if prev_value else 0.0
        monthly_returns[str(period)] = round(ret * 100, 2)
        prev_value = float(value)

    final_value = float(curve_df["value"].iloc[-1]) if len(curve_df) else float(account_size)
    total_return = final_value / float(account_size) - 1
    start_dt = pd.Timestamp(curve_df["date_dt"].iloc[0]) if len(curve_df) else None
    end_dt = pd.Timestamp(curve_df["date_dt"].iloc[-1]) if len(curve_df) else None
    years = max((end_dt - start_dt).days / 365.25, 0) if start_dt is not None else 0
    annualized = ((final_value / account_size) ** (1 / years) - 1) if years > 0 else total_return
    wins = [t for t in closed_trades if t["pnl"] > 0]
    avg_trade_return = float(np.mean([t["return_pct"] for t in closed_trades])) if closed_trades else 0.0
    best_month = max(monthly_returns.items(), key=lambda x: x[1]) if monthly_returns else (None, None)
    worst_month = min(monthly_returns.items(), key=lambda x: x[1]) if monthly_returns else (None, None)

    return {
        "settings": {
            "account_size": round(float(account_size), 2),
            "sizing_mode": sizing_mode,
            "sizing_method": ("fixed_pct" if sizing_mode == "fixed"
                              else ("half_kelly_regime_aware" if regime_sizing
                                    else "half_kelly_capped_top_score")),
            "sizing_lookahead_free": sizing_mode == "fixed",
            "half_kelly_pct": round(half_kelly_pct, 2),
            "position_cap_pct": round(float(position_cap_pct), 2),
            "effective_position_pct": round(sizing_pct, 2),
            "commission": round(float(commission), 2),
            "primary_hold": hold,
        },
        "summary": {
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return * 100, 2),
            "annualized_return_pct": round(annualized * 100, 2),
            "max_drawdown": round(float(max_dd_conservative), 4),
            "max_drawdown_cost_marked": round(float(curve_df["drawdown"].max()) if len(curve_df) else 0.0, 4),
            "trades_taken": len(closed_trades),
            "skipped_trades": int(skipped_trades),
            "win_rate": round(len(wins) / len(closed_trades), 4) if closed_trades else 0.0,
            "avg_trade_return_pct": round(avg_trade_return, 3),
            "best_month": {"month": best_month[0], "return_pct": best_month[1]},
            "worst_month": {"month": worst_month[0], "return_pct": worst_month[1]},
        },
        "equity_curve": curve_df.drop(columns=["date_dt"]).to_dict(orient="records"),
        "yearly_returns": yearly_returns,
        "monthly_returns": monthly_returns,
        "closed_trades": closed_trades,
    }


def _generate_backtest_charts(account_sim: dict, hold_stats: dict, yearly_stats: dict,
                              wf_results: list, mc_results: dict,
                              chart_dir: Path, account_size: float) -> dict:
    """Generate PNG charts. Missing matplotlib is reported without failing the backtest."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return {"warning": f"Chart generation skipped: matplotlib is not available ({exc})"}

    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_files = {}

    def save(fig, name):
        path = chart_dir / name
        fig.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        chart_files[name.removesuffix(".png")] = str(path.resolve())

    curve = pd.DataFrame(account_sim.get("equity_curve", []))
    if not curve.empty:
        curve["date"] = pd.to_datetime(curve["date"])
        fig, ax1 = plt.subplots(figsize=(11, 5))
        ax1.plot(curve["date"], curve["value"], color="#1f77b4", linewidth=2)
        ax1.set_title(f"${account_size:,.0f} Account Equity Curve")
        ax1.set_ylabel("Account value ($)")
        ax1.grid(True, alpha=0.25)
        ax2 = ax1.twinx()
        ax2.fill_between(curve["date"], 0, curve["drawdown"] * 100,
                         color="#d62728", alpha=0.18)
        ax2.set_ylabel("Drawdown (%)")
        ax2.invert_yaxis()
        save(fig, "account_equity_curve.png")

    yr_returns = account_sim.get("yearly_returns", {})
    if yr_returns:
        labels = list(yr_returns.keys())
        vals = [yr_returns[k] for k in labels]
        colors = ["#2ca02c" if v >= 0 else "#d62728" for v in vals]
        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.bar(labels, vals, color=colors)
        ax.axhline(0, color="#333333", linewidth=0.8)
        ax.set_title("Yearly Account Returns")
        ax.set_ylabel("Return (%)")
        ax.grid(axis="y", alpha=0.25)
        save(fig, "yearly_returns.png")

    monthly = account_sim.get("monthly_returns", {})
    if monthly:
        month_df = pd.DataFrame(
            [{"year": int(k[:4]), "month": int(k[5:7]), "return": v}
             for k, v in monthly.items()]
        )
        pivot = month_df.pivot(index="year", columns="month", values="return").sort_index()
        pivot = pivot.reindex(columns=range(1, 13))
        fig, ax = plt.subplots(figsize=(11, max(3.5, 0.45 * len(pivot))))
        im = ax.imshow(pivot.fillna(0), cmap="RdYlGn", aspect="auto")
        ax.set_title("Monthly Account Returns")
        ax.set_xticks(range(12))
        ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([str(y) for y in pivot.index])
        for y_i, year in enumerate(pivot.index):
            for m_i, month in enumerate(pivot.columns):
                val = pivot.loc[year, month]
                if pd.notna(val):
                    ax.text(m_i, y_i, f"{val:.1f}", ha="center", va="center", fontsize=7)
        fig.colorbar(im, ax=ax, label="Return (%)")
        save(fig, "monthly_heatmap.png")

    if wf_results:
        labels = [f"{r.get('period_start', '')}\n{r.get('period_end', '')}" for r in wf_results]
        win_rates = [(r.get("win_rate", 0) or 0) * 100 for r in wf_results]
        avg_rets = [r.get("avg_return_pct", 0) or 0 for r in wf_results]
        profit_factors = [r.get("profit_factor", 0) or 0 for r in wf_results]
        x = np.arange(len(labels))
        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
        axes[0].bar(x, win_rates, color="#1f77b4")
        axes[0].set_ylabel("Win rate %")
        axes[1].bar(x, avg_rets, color=["#2ca02c" if v >= 0 else "#d62728" for v in avg_rets])
        axes[1].axhline(0, color="#333333", linewidth=0.8)
        axes[1].set_ylabel("Avg return %")
        axes[2].bar(x, profit_factors, color="#9467bd")
        axes[2].axhline(1, color="#333333", linewidth=0.8)
        axes[2].set_ylabel("Profit factor")
        axes[2].set_xticks(x)
        axes[2].set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
        fig.suptitle("Walk-Forward Windows")
        save(fig, "walk_forward.png")

    if mc_results:
        labels = ["5th", "25th", "50th", "75th", "95th"]
        keys = ["equity_p05", "equity_p25", "equity_p50", "equity_p75", "equity_p95"]
        vals = [float(mc_results.get(k, 0) or 0) * account_size for k in keys]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar(labels, vals, color="#17becf")
        ax.axhline(account_size, color="#333333", linewidth=0.9, linestyle="--")
        ax.set_title("Monte Carlo Final Account Value")
        ax.set_ylabel("Final value ($)")
        ax.grid(axis="y", alpha=0.25)
        save(fig, "monte_carlo.png")

    primary = next(iter(hold_stats.values()), {})
    summary = account_sim.get("summary", {})
    metrics = [
        ("Trades", summary.get("trades_taken", 0)),
        ("Final $", summary.get("final_value", 0)),
        ("Acct Ret %", summary.get("total_return_pct", 0)),
        ("Win Rate %", (summary.get("win_rate", 0) or 0) * 100),
        ("Avg Ret %", primary.get("avg_return_pct", 0) or 0),
        ("Profit Factor", primary.get("profit_factor", 0) or 0),
        ("Max DD %", (summary.get("max_drawdown", 0) or 0) * 100),
    ]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.axis("off")
    ax.set_title("Strategy Summary", fontsize=18, pad=18)
    for i, (label, value) in enumerate(metrics):
        x = 0.08 + (i % 4) * 0.24
        y = 0.68 - (i // 4) * 0.35
        text = f"${value:,.0f}" if label == "Final $" else (
            f"{value:,.0f}" if label == "Trades" else f"{value:.2f}"
        )
        ax.text(x, y, label, fontsize=10, color="#555555", transform=ax.transAxes)
        ax.text(x, y - 0.12, text, fontsize=18, weight="bold", transform=ax.transAxes)
    save(fig, "strategy_summary.png")

    return chart_files


# ── Main backtest ─────────────────────────────────────────────────────────────

ARG_DEFAULTS = {
    "tickers": "all_tickers.txt",
    "start": "2020-01-01",
    "end": "2024-12-31",
    "threshold": 100.0,
    "score_mode": "confirmed_pullback",
    "entry_timing": "next_open",
    "freq": 1,
    "no_cache": False,
    "min_price": 5.0,
    "max_price": None,
    "max_atr_pct": 0.08,
    "min_adv": None,
    "target_mult": 0.75,
    "stop_mult": 1.0,
    "hold_periods": [3, 5, 10],
    "primary_hold": 10,
    "allow_friday": False,
    "regime_filter": "all",
    "benchmark": "SPY",
    "score_min": None,
    "score_max": None,
    "grid_search": False,
    "grid_thresholds": [100.0],
    "grid_targets": [0.8, 1.0, 1.2, 1.5, 2.0],
    "grid_stops": [0.4, 0.5, 0.7, 1.0],
    "walk_forward": False,
    "wf_window": 252,
    "wf_step": 63,
    "monte_carlo": False,
    "mc_sims": 1000,
    "mc_sim_trades": 252,
    "account_size": 5000.0,
    "generate_charts": True,
    "charts_dir": None,
    "account_position_cap_pct": 20.0,
    "account_commission": 1.0,
    "account_slippage_bps": 5.0,
    "account_sizing_mode": "fixed",
    "diagnostics": True,
    "ml_analysis": True,
    "ml_walk_forward": True,
    "ml_max_rows": 0,
    "ml_candidate_sample": 100000,
    "ml_min_train_rows": 200,
    "ml_probability_threshold": 0.50,
    "ml_expected_return_min": -0.01,
    "ml_large_loss_max": 0.35,
    "ml_wf_step_days": 21,
    "ml_wf_min_train": 400,
    "gate_diagnostics_limit": 250,
    "missed_big_win_pct": 0.05,
    "bad_loss_pct": -0.03,
    "diagnostic_max_examples": 25,
    "missed_max_examples": 50,
    "export_csv": None,
    "no_trades_json": False,
}


def apply_arg_defaults(args):
    for name, value in ARG_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value.copy() if isinstance(value, list) else value)

    if args.primary_hold not in args.hold_periods:
        args.hold_periods = sorted(set(args.hold_periods + [args.primary_hold]))

    if args.grid_search and args.threshold not in args.grid_thresholds:
        args.grid_thresholds = sorted(set(args.grid_thresholds + [args.threshold]))


def run_backtest(args):
    apply_arg_defaults(args)
    t_start = datetime.datetime.now()

    # Apply global MIN_PRICE override from args
    global MIN_PRICE
    MIN_PRICE = args.min_price

    hold_periods = args.hold_periods
    primary_h    = args.primary_hold

    print("\n" + "=" * 70)
    print("  TradingAgents Backtest v3")
    print("=" * 70)
    print(f"  Tickers        : {args.tickers}")
    print(f"  Period         : {args.start} → {args.end}")
    print(f"  Threshold      : {args.threshold}")
    print(f"  Score mode     : {args.score_mode}")
    print(f"  Entry timing   : {args.entry_timing}")
    print(f"  Hold periods   : {hold_periods} days  (primary={primary_h}d)")
    print(f"  Scan freq      : every {args.freq} trading day(s)")
    print(f"  Price filter   : ${args.min_price:.2f}"
          + (f" – ${args.max_price:.2f}" if args.max_price else "+"))
    if args.max_atr_pct:
        print(f"  ATR% filter    : <= {args.max_atr_pct:.1%}")
    if args.min_adv:
        print(f"  Min ADV        : {args.min_adv:,} shares")
    print(f"  Target mult    : {args.target_mult}x ATR")
    print(f"  Stop mult      : {args.stop_mult}x ATR")
    print(f"  Allow Friday   : {args.allow_friday}")
    print(f"  Benchmark      : {args.benchmark}")
    if args.grid_search:
        print(f"  Grid search    : thresholds={args.grid_thresholds}")
    if args.walk_forward:
        print(f"  Walk-forward   : window={args.wf_window}d, step={args.wf_step}d")
    if args.monte_carlo:
        print(f"  Monte Carlo    : {args.mc_sims} simulations")
    print("=" * 70)

    tickers = load_tickers(args.tickers)
    print(f"\nLoaded {len(tickers)} tickers from {args.tickers}")

    lookback_start = (
        datetime.datetime.strptime(args.start, "%Y-%m-%d")
        - datetime.timedelta(days=420)
    ).strftime("%Y-%m-%d")
    forward_end = (
        datetime.datetime.strptime(args.end, "%Y-%m-%d")
        + datetime.timedelta(days=max(hold_periods) + 10)
    ).strftime("%Y-%m-%d")

    # Download tickers + benchmark in batch; VIX separately (batch mode misses ^VIX)
    all_dl   = list(dict.fromkeys(tickers + [args.benchmark]))
    raw_data = download_all(all_dl, lookback_start, forward_end, args.no_cache,
                            batch_size=getattr(args, "batch_size", BATCH_SIZE))

    spy_df  = raw_data.pop(args.benchmark, None)

    # Fallback: batch extraction sometimes misses the benchmark — download it solo
    if spy_df is None or len(spy_df) < 10:
        _spy_cache = CACHE_DIR / f"spy_{args.benchmark}_{lookback_start}_{forward_end}.pkl"
        if not args.no_cache and _spy_cache.exists():
            try:
                with open(_spy_cache, "rb") as f:
                    spy_df = pickle.load(f)
            except Exception:
                spy_df = None
        if spy_df is None or len(spy_df) < 10:
            try:
                _spy_raw = yf.download(args.benchmark, start=lookback_start, end=forward_end,
                                       progress=False, auto_adjust=True)
                if _spy_raw is not None and len(_spy_raw) > 10:
                    if isinstance(_spy_raw.columns, pd.MultiIndex):
                        _spy_raw.columns = _spy_raw.columns.get_level_values(0)
                    spy_df = _spy_raw[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                    with open(_spy_cache, "wb") as f:
                        pickle.dump(spy_df, f)
            except Exception as exc:
                print(f"  WARNING: Could not download {args.benchmark}: {exc}")

    # VIX: direct single download (not batched — ^ prefix breaks batch extraction)
    vix_raw = None
    try:
        vix_cache = CACHE_DIR / f"vix_{lookback_start}_{forward_end}.pkl"
        if not args.no_cache and vix_cache.exists():
            with open(vix_cache, "rb") as f:
                vix_raw = pickle.load(f)
        else:
            _vix = yf.download("^VIX", start=lookback_start, end=forward_end,
                               progress=False, auto_adjust=True)
            if _vix is not None and len(_vix) > 10:
                if isinstance(_vix.columns, pd.MultiIndex):
                    _vix.columns = _vix.columns.get_level_values(0)
                vix_raw = _vix[["Close"]].dropna()
                with open(vix_cache, "wb") as f:
                    pickle.dump(vix_raw, f)
    except Exception as e:
        print(f"  VIX download failed ({e}) — regime analysis will be skipped")

    # VIX3M: needed for term-structure filter (backwardation = fear spike)
    vix3m_raw = None
    vix_ts_series = None
    try:
        vix3m_cache = CACHE_DIR / f"vix3m_{lookback_start}_{forward_end}.pkl"
        if not args.no_cache and vix3m_cache.exists():
            with open(vix3m_cache, "rb") as f:
                vix3m_raw = pickle.load(f)
        else:
            _v3m = yf.download("^VIX3M", start=lookback_start, end=forward_end,
                               progress=False, auto_adjust=True)
            if _v3m is not None and len(_v3m) > 10:
                if isinstance(_v3m.columns, pd.MultiIndex):
                    _v3m.columns = _v3m.columns.get_level_values(0)
                vix3m_raw = _v3m[["Close"]].dropna()
                with open(vix3m_cache, "wb") as f:
                    pickle.dump(vix3m_raw, f)
        if vix_raw is not None and vix3m_raw is not None:
            vix_ts_series = build_vix_term_structure(vix_raw, vix3m_raw)
            print(f"  VIX term structure: {len(vix_ts_series)} days loaded")
    except Exception as e:
        print(f"  VIX3M download failed ({e}) — term-structure filter skipped")

    # Sector ETFs: 20d breadth score across all 11 SPDR sectors
    sector_raw_dfs = {}
    sector_breadth_series = None
    try:
        sector_cache = CACHE_DIR / f"sectors_{lookback_start}_{forward_end}.pkl"
        if not args.no_cache and sector_cache.exists():
            with open(sector_cache, "rb") as f:
                sector_raw_dfs = pickle.load(f)
        else:
            for etf in SECTOR_ETFS:
                try:
                    _s = yf.download(etf, start=lookback_start, end=forward_end,
                                     progress=False, auto_adjust=True)
                    if _s is not None and len(_s) > 10:
                        if isinstance(_s.columns, pd.MultiIndex):
                            _s.columns = _s.columns.get_level_values(0)
                        sector_raw_dfs[etf] = _s[["Close"]].dropna()
                except Exception:
                    pass
            with open(sector_cache, "wb") as f:
                pickle.dump(sector_raw_dfs, f)
        if sector_raw_dfs:
            sector_breadth_series = build_sector_breadth(sector_raw_dfs)
            print(f"  Sector breadth: {len(sector_raw_dfs)}/{len(SECTOR_ETFS)} ETFs, "
                  f"{len(sector_breadth_series)} days")
    except Exception as e:
        print(f"  Sector ETF download failed ({e}) — sector breadth skipped")

    raw_data = filter_by_price(raw_data, args.min_price, args.max_price)
    print(f"Valid tickers after filter: {len(raw_data)}")

    spy_regime = (build_spy_regime(spy_df)
                  if spy_df is not None else pd.Series(dtype=str))
    vix_regime = (build_vix_regime(vix_raw)
                  if vix_raw is not None else None)

    # ── Precompute rolling stats ──────────────────────────────────────────
    print("\nPrecomputing rolling indicators...")
    precomputed = {}
    for ticker, df in tqdm(raw_data.items(), desc="Precompute", unit="ticker"):
        try:
            precomputed[ticker] = (df, precompute(df))
        except Exception:
            pass

    # ── Build scan dates ──────────────────────────────────────────────────
    scan_dates = pd.bdate_range(args.start, args.end, freq=f"{args.freq}B")
    print(f"\nScan dates: {len(scan_dates)}  "
          f"({scan_dates[0].date()} → {scan_dates[-1].date()})")

    # ── Main scan loop ────────────────────────────────────────────────────
    all_trades, total_scored, total_passed, scan_diagnostics = _collect_trades(
        precomputed, scan_dates, spy_df, spy_regime, vix_regime, args,
        vix_ts_series=vix_ts_series,
        sector_breadth_series=sector_breadth_series,
        vix_raw_df=vix_raw,
    )

    # ── Analysis ──────────────────────────────────────────────────────────
    print(f"\nTotal ticker-dates scored : {total_scored:,}")
    print(f"Total signals passed      : {total_passed:,}")
    print(f"Trades with outcome data  : {len(all_trades):,}")

    if not all_trades:
        print("\nNo trades generated. Try lowering --threshold.")
        return

    df = pd.DataFrame(all_trades)

    if args.regime_filter != "all":
        df = df[df["spy_regime"] == args.regime_filter]
        if df.empty:
            print(f"\nNo trades in regime '{args.regime_filter}'. Check --regime-filter.")
            return

    # Grid search needs the wider score range (down to min grid threshold).
    # Main analysis must be filtered to args.threshold only.
    df_grid = df.copy()
    if args.grid_search:
        df = df[df["score"] >= args.threshold].copy()
        if df.empty:
            print("\nNo trades at threshold after grid pre-collection. Try lowering --threshold.")
            return
        print(f"Trades at selected threshold ({args.threshold:g}) : {len(df):,}")

    # Per hold-period stats
    hold_stats = {f"{h}d": _stats(df, h) for h in hold_periods}

    # Score buckets — explicit boolean filtering avoids pd.cut edge-case gaps
    lo = int(args.threshold)
    bucket_edges = [
        (lo,      lo + 5,  f"{lo}-{lo+5}"),
        (lo + 5,  lo + 10, f"{lo+5}-{lo+10}"),
        (lo + 10, lo + 15, f"{lo+10}-{lo+15}"),
        (lo + 15, 200,     f"{lo+15}+"),
    ]
    score_buckets = {}
    for low, high, label in bucket_edges:
        grp = df[(df["score"] >= low) & (df["score"] < high)]
        if len(grp) == 0:
            continue
        st = _stats(grp, primary_h)
        st["avg_score"] = round(float(grp["score"].mean()), 1)
        score_buckets[label] = st

    # Market regime
    regime_stats = {r: _stats(g, primary_h)
                    for r, g in df.groupby("spy_regime") if len(g) >= 2}

    # Monthly
    monthly_stats = {}
    for month, grp in df.groupby("month"):
        if len(grp) >= 5:
            monthly_stats[str(month)] = _stats(grp, primary_h)

    # Day of week
    dow_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday",
                 3: "Thursday", 4: "Friday"}
    dow_stats = {}
    for dow, grp in df.groupby("day_of_week"):
        if len(grp) >= 5:
            dow_stats[dow_names.get(int(dow), str(dow))] = _stats(grp, primary_h)

    # Yearly
    yearly_stats = {
        str(yr): _stats(g, primary_h)
        for yr, g in df.groupby("year") if len(g) >= 5
    }

    # Signal correlation
    ret_col     = f"h{primary_h}_return"
    signal_cols = [
        "score", "coil_pts", "brk_pts", "trend_pts", "vol_pts",
        "contraction", "vol_dryup", "vol_ratio_20d", "vol_trend",
        "rsi9", "rsi14", "macd_hist", "bb_pct",
        "pct_from_10d_high", "pct_from_52w_high", "pct_from_52w_low",
        "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
        "atr", "atr_pct", "day_range_pct",
        "gap_pct", "body_pct", "upper_wick", "lower_wick",
        "inside_day", "engulf_bull", "vol_ratio_10d",
        # New indicators
        "stoch_k", "stoch_d", "mfi14", "cci14", "adx14",
        "cmf20", "close_loc", "squeeze", "range_rank_52w",
        "roc10", "roc20", "consec_up", "consec_down",
    ]
    signal_analysis = {}
    if ret_col in df.columns:
        ret_s = pd.to_numeric(df[ret_col], errors="coerce")
        for col in signal_cols:
            if col not in df.columns:
                continue
            s = pd.to_numeric(df[col], errors="coerce")
            if s.isna().all() or s.std() == 0:
                continue
            mask_w = ret_s > 0
            mask_l = ret_s <= 0
            signal_analysis[col] = {
                "corr_with_return": round(float(s.corr(ret_s)), 4),
                "mean_winners":     round(float(s[mask_w].mean()), 4) if mask_w.any() else None,
                "mean_losers":      round(float(s[mask_l].mean()), 4) if mask_l.any() else None,
                "overall_mean":     round(float(s.mean()), 4),
                "overall_std":      round(float(s.std()),  4),
            }

    sorted_sigs = sorted(
        signal_analysis.items(),
        key=lambda x: abs(x[1]["corr_with_return"]),
        reverse=True,
    )

    # Per-ticker
    ticker_stats = {}
    for ticker, grp in df.groupby("ticker"):
        if len(grp) < 2:
            continue
        st = _stats(grp, primary_h)
        st["avg_score"] = round(float(grp["score"].mean()), 1)
        ticker_stats[str(ticker)] = st

    # ── New analysis sections ─────────────────────────────────────────────

    atr_bucket_analysis   = _atr_bucket_analysis(df, primary_h)
    price_bucket_analysis = _price_bucket_analysis(df, primary_h)
    r_mult_analysis       = _r_multiple_distribution(df, primary_h)
    mae_mfe_analysis      = _mae_mfe_analysis(df, primary_h)
    vix_regime_analysis   = _vix_regime_analysis(df, primary_h)
    position_sizing       = _position_sizing_table(hold_stats)
    return_distribution   = _return_distribution_stats(df, primary_h)
    loss_diagnostics      = (
        _loss_diagnostics(
            df,
            primary_h,
            bad_loss_pct=args.bad_loss_pct,
            max_examples=args.diagnostic_max_examples,
        )
        if args.diagnostics else {}
    )
    missed_big_win_analysis = (
        _missed_big_win_diagnostics(
            scan_diagnostics.get("missed_big_wins", []),
            primary_h,
        )
        if args.diagnostics else {}
    )
    ml_analysis = (
        _run_ml_analysis(
            df,
            scan_diagnostics.get("ml_rejected_candidates", []),
            primary_h,
            max_rows=getattr(args, "ml_max_rows", 0),
            min_train_rows=getattr(args, "ml_min_train_rows", 200),
            ml_prob_threshold=getattr(args, "ml_probability_threshold", 0.50),
            ml_expected_return_min=getattr(args, "ml_expected_return_min", -0.01),
            ml_large_loss_max=getattr(args, "ml_large_loss_max", 0.35),
            gate_diagnostics_limit=getattr(args, "gate_diagnostics_limit", 250),
            account_size=args.account_size,
            position_cap_pct=args.account_position_cap_pct,
            commission=args.account_commission,
            account_sizing_mode=getattr(args, "account_sizing_mode", "fixed"),
            ml_walk_forward=getattr(args, "ml_walk_forward", True),
            ml_wf_step_days=getattr(args, "ml_wf_step_days", 21),
            ml_wf_min_train=getattr(args, "ml_wf_min_train", 400),
        )
        if getattr(args, "ml_analysis", True) and getattr(args, "score_mode", "") != "oversold_bounce" else {}
    )
    account_simulation    = _simulate_account(
        df,
        primary_h,
        hold_stats.get(f"{primary_h}d", {}),
        account_size=args.account_size,
        position_cap_pct=args.account_position_cap_pct,
        commission=args.account_commission,
        slippage_bps=getattr(args, "account_slippage_bps", 5.0),
        regime_stats=regime_stats,
        sizing_mode=getattr(args, "account_sizing_mode", "fixed"),
    )

    # ── Optional: grid search ─────────────────────────────────────────────
    grid_results = {}
    if args.grid_search:
        print("\nRunning grid search over thresholds...")
        grid_list = run_grid_search(df_grid, args)
        grid_results = {
            "search_scope": "threshold_only",
            "note": "target_mult/grid_targets and stop_mult/grid_stops are not optimized by this fast search",
            "top_results": grid_list[:10],
            "all_results": grid_list,
        }

    # ── Optional: walk-forward ────────────────────────────────────────────
    wf_results = []
    if args.walk_forward:
        print("\nRunning walk-forward analysis...")
        wf_results = run_walk_forward(
            precomputed, scan_dates, spy_df, spy_regime, vix_regime, args,
            vix_ts_series=vix_ts_series,
            sector_breadth_series=sector_breadth_series,
            vix_raw_df=vix_raw,
        )

    # ── Optional: Monte Carlo ─────────────────────────────────────────────
    mc_results = {}
    if args.monte_carlo:
        print(f"\nRunning Monte Carlo ({args.mc_sims} sims)...")
        mc_results = run_monte_carlo(df, primary_h, args.mc_sims, args.mc_sim_trades)

    elapsed = (datetime.datetime.now() - t_start).seconds
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    charts_dir = Path(args.charts_dir) if args.charts_dir else Path(f"backtest_charts_{ts}")
    chart_files = {}
    if args.generate_charts:
        chart_files = _generate_backtest_charts(
            account_simulation,
            {f"{primary_h}d": hold_stats.get(f"{primary_h}d", {})},
            yearly_stats,
            wf_results,
            mc_results,
            charts_dir,
            args.account_size,
        )

    # ── Console summary ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    print("\n  By hold period:")
    hdr = (f"  {'Hold':>5}  {'Trades':>7}  {'Direction':>10}  {'TargetHit':>10}  "
           f"{'StoppedOut':>11}  {'AvgRet%':>8}  {'ProfFact':>9}  "
           f"{'Sortino':>8}  {'Kelly%':>7}  {'BeatSPY':>8}")
    print(hdr)
    print("  " + "-" * 95)
    for hd, st in hold_stats.items():
        if not st:
            continue
        dc  = f"{st['direction_correct_rate']:.1%}" if st.get("direction_correct_rate") is not None else "  n/a"
        th  = f"{st['target_hit_rate']:.1%}"        if st.get("target_hit_rate")        is not None else "  n/a"
        so  = f"{st['stopped_out_rate']:.1%}"       if st.get("stopped_out_rate")       is not None else "  n/a"
        bs  = f"{st['beat_spy_rate']:.1%}"          if st.get("beat_spy_rate")          is not None else "  n/a"
        so_r = st.get("sortino_ratio", 0) or 0
        kl   = st.get("kelly_pct", 0) or 0
        print(f"  {hd:>5}  {st['trades']:>7,}  {dc:>10}  {th:>10}  "
              f"{so:>11}  {st['avg_return_pct']:>+8.3f}  {st['profit_factor']:>9.3f}  "
              f"{so_r:>8.3f}  {kl:>6.1f}%  {bs:>8}")

    print(f"\n  Extended stats (hold={primary_h}d):")
    ps = hold_stats.get(f"{primary_h}d", {})
    if ps:
        print(f"    Calmar ratio     : {ps.get('calmar_ratio', 0):>8.3f}")
        print(f"    Max drawdown     : {ps.get('max_drawdown', 0):>8.2%}")
        print(f"    Half-Kelly %     : {ps.get('half_kelly_pct', 0):>8.2f}%")
        print(f"    Expectancy/trade : {ps.get('expectancy_per_trade_pct', 0):>+8.3f}%")

    print(f"\n  Score buckets (hold={primary_h}d):")
    for bkt, st in score_buckets.items():
        if not st:
            continue
        print(f"    {bkt:>12}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%  pf={st['profit_factor']:.3f}")

    print(f"\n  Market regime (hold={primary_h}d):")
    for reg, st in regime_stats.items():
        if not st:
            continue
        print(f"    {reg:<6}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%")

    print(f"\n  VIX regime (hold={primary_h}d):")
    for reg, st in vix_regime_analysis.items():
        if not st:
            continue
        print(f"    {reg:<12}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%  pf={st['profit_factor']:.3f}")

    print(f"\n  ATR% bucket analysis (hold={primary_h}d):")
    for bkt, st in atr_bucket_analysis.items():
        if not st:
            continue
        print(f"    {bkt:<22}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%")

    print(f"\n  Price bucket analysis (hold={primary_h}d):")
    for bkt, st in price_bucket_analysis.items():
        if not st:
            continue
        print(f"    {bkt:<12}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%")

    print(f"\n  Day of week (hold={primary_h}d):")
    for dow, st in dow_stats.items():
        if not st:
            continue
        print(f"    {dow:<10}  n={st['trades']:>5,}  win={st['win_rate']:.1%}  "
              f"avg={st['avg_return_pct']:+.3f}%")

    print(f"\n  Top signal correlations with {primary_h}d return:")
    for name, stat in sorted_sigs[:20]:
        c  = stat["corr_with_return"]
        mw = f"{stat['mean_winners']:.4f}" if stat["mean_winners"] is not None else " n/a"
        ml = f"{stat['mean_losers']:.4f}"  if stat["mean_losers"]  is not None else " n/a"
        print(f"    {name:<28}  corr={c:+.4f}  win_avg={mw}  loss_avg={ml}")

    if return_distribution:
        print(f"\n  Return distribution (hold={primary_h}d):")
        print(f"    p05 / median / p95 : "
              f"{return_distribution.get('p05_return_pct', 0):+.2f}% / "
              f"{return_distribution.get('p50_return_pct', 0):+.2f}% / "
              f"{return_distribution.get('p95_return_pct', 0):+.2f}%")
        print(f"    Big wins >=5%      : {return_distribution.get('pct_ge_5pct', 0):.2%}")
        print(f"    Big losses <=-5%   : {return_distribution.get('pct_le_neg5pct', 0):.2%}")

    if loss_diagnostics:
        ld_sum = loss_diagnostics.get("summary", {})
        print(f"\n  Loss diagnostics (hold={primary_h}d):")
        print(f"    Losses / bad losses : {ld_sum.get('losses', 0):,} / {ld_sum.get('bad_losses', 0):,}")
        print(f"    Avg / worst loss    : {ld_sum.get('avg_loss_pct', 0):+.2f}% / "
              f"{ld_sum.get('worst_loss_pct', 0):+.2f}%")
        print("    Top loss tags       :")
        for tag, count in list(loss_diagnostics.get("reason_counts", {}).items())[:8]:
            print(f"      {tag:<26} {count:>6,}")

    if missed_big_win_analysis:
        mbw_sum = missed_big_win_analysis.get("summary", {})
        print(f"\n  Missed big-win diagnostics (hold={primary_h}d):")
        print(f"    Missed examples kept : {mbw_sum.get('missed_big_wins_kept', 0):,}")
        print(f"    Best / avg missed    : {mbw_sum.get('best_missed_return_pct', 0):+.2f}% / "
              f"{mbw_sum.get('avg_missed_return_pct', 0):+.2f}%")
        print("    Rejection reasons    :")
        for reason, count in list(missed_big_win_analysis.get("rejection_counts", {}).items())[:8]:
            print(f"      {reason:<26} {count:>6,}")

    if ml_analysis:
        ml_settings = ml_analysis.get("settings", {})
        print("\n  ML analysis:")
        if ml_analysis.get("evaluation") == "purged_walk_forward":
            leak = ml_analysis.get("leakage_controls", {})
            print("    Evaluation       : purged walk-forward (honest OOS)")
            print(f"    OOS / total rows : {ml_settings.get('oos_rows', 0):,} / "
                  f"{ml_settings.get('total_rows', 0):,}")
            print(f"    Embargo / step   : {leak.get('embargo_days', 'n/a')}d / "
                  f"{leak.get('step_days', 'n/a')}d")
        else:
            print("    Evaluation       : last-period split (legacy diagnostic)")
            print(f"    Rows used        : {ml_settings.get('rows_used', 0):,} "
                  f"(train={ml_settings.get('train_rows', 0):,}, test={ml_settings.get('test_rows', 0):,})")
            win_rf = ml_analysis.get("trade_win_loss_prediction", {}).get("random_forest", {})
            win_metrics = win_rf.get("metrics", {})
            if win_metrics:
                print(f"    Win model AUC    : {win_metrics.get('roc_auc', 'n/a')}")
                print(f"    Win precision/rec: {win_metrics.get('precision', 'n/a')} / "
                      f"{win_metrics.get('recall', 'n/a')}")
        strat_ans = ml_analysis.get("strategy_comparison", {}).get("answers", {})
        if strat_ans:
            print(f"    ML filter win-rate delta : {strat_ans.get('did_ml_improve_win_rate')}")
            print(f"    ML filter PF delta       : {strat_ans.get('did_ml_improve_profit_factor')}")
        gate = ml_analysis.get("gate_analysis", {})
        gate_diag = gate.get("diagnostics", {})
        if gate_diag:
            print(f"    Gate final passed        : {gate_diag.get('final_passed', 0):,}")
            print(f"    Gate false positives     : {gate_diag.get('false_positives', 0):,}")
            print(f"    Gate missed winners      : {gate_diag.get('missed_winners', 0):,}")

    # R-multiple distribution
    if r_mult_analysis:
        print(f"\n  R-multiple distribution (hold={primary_h}d):")
        bkt_keys = ["lt_neg2", "neg2_to_neg1", "neg1_to_0",
                    "0_to_1", "1_to_2", "gt_2"]
        bkt_labels = ["< -2R", "-2 to -1R", "-1 to 0R",
                      "0 to 1R", "1 to 2R", "> 2R"]
        r_col = f"h{primary_h}_r_multiple"
        if r_col in df.columns:
            rm_vals = pd.to_numeric(df[r_col], errors="coerce").dropna().tolist()
            _ascii_histogram(rm_vals, bins=12, width=35,
                             title=f"R-multiple histogram (h={primary_h}d)")
        for k, lbl in zip(bkt_keys, bkt_labels):
            print(f"    {lbl:<14} : {r_mult_analysis.get(k, 0):>6,}")
        print(f"    avg R        : {r_mult_analysis.get('avg_r', 0):>+.3f}")
        print(f"    median R     : {r_mult_analysis.get('median_r', 0):>+.3f}")

    # MAE / MFE
    if mae_mfe_analysis:
        print(f"\n  MAE / MFE analysis (hold={primary_h}d):")
        for k, v in mae_mfe_analysis.items():
            if v is None:
                continue
            if isinstance(v, float):
                print(f"    {k:<40} : {v:>+.4f}")
            else:
                print(f"    {k:<40} : {v}")

    # Position sizing
    if position_sizing:
        print("\n  Position sizing (Kelly / Half-Kelly):")
        ps_primary = position_sizing.get(f"{primary_h}d", {})
        if ps_primary:
            print(f"    Kelly% (h={primary_h}d)      : "
                  f"{ps_primary.get('kelly_pct', 0):.2f}%")
            print(f"    Half-Kelly% (h={primary_h}d) : "
                  f"{ps_primary.get('half_kelly_pct', 0):.2f}%")
            by_acct = ps_primary.get("by_account_size", {})
            print(f"    {'Account':>12}  {'Half-Kelly $':>14}  {'Kelly $':>10}")
            for acct_lbl, v in by_acct.items():
                print(f"    {acct_lbl:>12}  "
                      f"${v['half_kelly_dollars']:>12,.0f}  "
                      f"${v['kelly_dollars']:>9,.0f}")

    if account_simulation:
        acct_summary = account_simulation.get("summary", {})
        acct_settings = account_simulation.get("settings", {})
        print(f"\n  ${args.account_size:,.0f} account simulation "
              f"(Half-Kelly capped at {acct_settings.get('position_cap_pct', 0):.1f}%):")
        print(f"    Final value       : ${acct_summary.get('final_value', 0):,.2f}")
        print(f"    Total return      : {acct_summary.get('total_return_pct', 0):>+.2f}%")
        print(f"    Annualized return : {acct_summary.get('annualized_return_pct', 0):>+.2f}%")
        print(f"    Max drawdown      : {acct_summary.get('max_drawdown', 0):>8.2%}")
        print(f"    Trades taken      : {acct_summary.get('trades_taken', 0):,}")
        print(f"    Skipped signals   : {acct_summary.get('skipped_trades', 0):,}")

    if chart_files:
        if "warning" in chart_files:
            print(f"\n  {chart_files['warning']}")
        else:
            print(f"\n  Charts saved â†’ {charts_dir.resolve()}")

    # Grid search output
    if args.grid_search and grid_results.get("top_results"):
        print(f"\n  Grid search results (threshold sweep, hold={primary_h}d):")
        hdr2 = (f"  {'Threshold':>10}  {'Trades':>7}  {'WinRate':>8}  "
                f"{'AvgRet%':>8}  {'ProfFact':>9}  {'Sharpe':>7}  {'Sortino':>8}")
        print(hdr2)
        print("  " + "-" * 70)
        for r in grid_results["top_results"]:
            wr  = f"{r.get('win_rate', 0):.1%}"
            ar  = r.get("avg_return_pct", 0) or 0
            pf  = r.get("profit_factor", 0)  or 0
            sh  = r.get("sharpe_ratio", 0)   or 0
            so  = r.get("sortino_ratio", 0)  or 0
            print(f"  {r['threshold']:>10.1f}  {r['trades']:>7,}  {wr:>8}  "
                  f"{ar:>+8.3f}  {pf:>9.3f}  {sh:>7.3f}  {so:>8.3f}")

    # Walk-forward output
    if args.walk_forward and wf_results:
        print(f"\n  Walk-forward results ({len(wf_results)} windows):")
        hdr3 = (f"  {'Period':>22}  {'Trades':>7}  {'WinRate':>8}  "
                f"{'AvgRet%':>8}  {'ProfFact':>9}")
        print(hdr3)
        print("  " + "-" * 60)
        for r in wf_results:
            period = f"{r.get('period_start','')} → {r.get('period_end','')}"
            wr     = f"{r.get('win_rate', 0):.1%}"
            ar     = r.get("avg_return_pct", 0) or 0
            pf     = r.get("profit_factor", 0)  or 0
            print(f"  {period:>22}  {r.get('trades', 0):>7,}  {wr:>8}  "
                  f"{ar:>+8.3f}  {pf:>9.3f}")

    # Monte Carlo output
    if args.monte_carlo and mc_results:
        print(f"\n  Monte Carlo results ({mc_results['n_sims']:,} simulations, "
              f"hold={primary_h}d, n_trades={mc_results['n_trades']:,}):")
        for pct_label, key in [("5th",  "equity_p05"), ("25th", "equity_p25"),
                                ("50th", "equity_p50"), ("75th", "equity_p75"),
                                ("95th", "equity_p95")]:
            v = mc_results.get(key, 0)
            print(f"    Equity percentile {pct_label}  : {v:.4f}x  "
                  f"({'▲' if v >= 1 else '▼'} {abs(v-1):.2%})")
        print(f"    Probability profitable   : {mc_results.get('pct_profitable', 0):.1%}")
        print(f"    Probability of ruin      : {mc_results.get('pct_ruin', 0):.2%}")
        print(f"    Avg max drawdown         : {mc_results.get('avg_max_drawdown', 0):.2%}")
        print(f"    Worst drawdown (p95)     : {mc_results.get('worst_drawdown_p95', 0):.2%}")

    # ── Export CSV ────────────────────────────────────────────────────────
    if args.export_csv:
        export_df = df.drop(columns=["score_bucket"], errors="ignore")
        export_df.to_csv(args.export_csv, index=False)
        print(f"\n  CSV exported → {args.export_csv}")

    # ── Save JSON output ──────────────────────────────────────────────────
    output = {
        "meta": {
            "version":              "backtest_v3",
            "tickers_file":         args.tickers,
            "start":                args.start,
            "end":                  args.end,
            "threshold":            args.threshold,
            "score_mode":           args.score_mode,
            "entry_timing":         args.entry_timing,
            "hold_periods_tested":  hold_periods,
            "primary_hold":         primary_h,
            "scan_freq_days":       args.freq,
            "min_price":            args.min_price,
            "max_price":            args.max_price,
            "max_atr_pct":          args.max_atr_pct,
            "min_adv":              args.min_adv,
            "target_mult":          args.target_mult,
            "stop_mult":            args.stop_mult,
            "allow_friday":         args.allow_friday,
            "regime_filter":        args.regime_filter,
            "benchmark":            args.benchmark,
            "score_min":            args.score_min,
            "score_max":            args.score_max,
            "grid_search":          args.grid_search,
            "grid_thresholds":      args.grid_thresholds if args.grid_search else None,
            "walk_forward":         args.walk_forward,
            "wf_window":            args.wf_window if args.walk_forward else None,
            "wf_step":              args.wf_step   if args.walk_forward else None,
            "monte_carlo":          args.monte_carlo,
            "mc_sims":              args.mc_sims if args.monte_carlo else None,
            "mc_sim_trades":        args.mc_sim_trades if args.monte_carlo else None,
            "account_size":         args.account_size,
            "generate_charts":      args.generate_charts,
            "charts_dir":           str(charts_dir),
            "account_position_cap_pct": args.account_position_cap_pct,
            "account_commission":   args.account_commission,
            "account_sizing_mode":  getattr(args, "account_sizing_mode", "fixed"),
            "diagnostics":          args.diagnostics,
            "ml_analysis":          getattr(args, "ml_analysis", True),
            "ml_walk_forward":      getattr(args, "ml_walk_forward", True),
            "ml_wf_step_days":      getattr(args, "ml_wf_step_days", 21),
            "ml_wf_min_train":      getattr(args, "ml_wf_min_train", 400),
            "ml_max_rows":          getattr(args, "ml_max_rows", 0),
            "ml_candidate_sample":   getattr(args, "ml_candidate_sample", 100000),
            "ml_min_train_rows":     getattr(args, "ml_min_train_rows", 200),
            "ml_probability_threshold": getattr(args, "ml_probability_threshold", 0.50),
            "ml_expected_return_min": getattr(args, "ml_expected_return_min", -0.01),
            "ml_large_loss_max":     getattr(args, "ml_large_loss_max", 0.35),
            "gate_diagnostics_limit": getattr(args, "gate_diagnostics_limit", 250),
            "missed_big_win_pct":    args.missed_big_win_pct,
            "bad_loss_pct":          args.bad_loss_pct,
            "diagnostic_max_examples": args.diagnostic_max_examples,
            "missed_max_examples":   args.missed_max_examples,
            "measurement_warnings": [
                "Universe uses the provided ticker file for all historical dates; survivorship bias remains unless the file is point-in-time.",
                "This validates the technical screener only; it does not validate downstream LLM overrides in the live AI pipeline.",
                "Fundamental/news providers outside this backtest may not be point-in-time unless explicitly guarded.",
                "Fast grid_search is threshold-only; target/stop arrays are informational unless a full remeasurement grid is added.",
            ],
            "tickers_loaded":       len(tickers),
            "tickers_after_filter": len(raw_data),
            "run_at":               datetime.datetime.now().isoformat(),
            "elapsed_seconds":      elapsed,
        },
        "summary": {
            "total_ticker_dates_scored": total_scored,
            "total_signals_passed":      total_passed,
            "precollection_trades_with_outcome": len(all_trades),
            "total_trades_with_outcome": len(df),
            "by_hold_period":            hold_stats,
        },
        "score_bucket_analysis":  score_buckets,
        "market_regime_analysis": regime_stats,
        "monthly_analysis":       monthly_stats,
        "day_of_week_analysis":   dow_stats,
        "yearly_analysis":        yearly_stats,
        "signal_analysis":        signal_analysis,
        "per_ticker_stats":       ticker_stats,
        "atr_bucket_analysis":    atr_bucket_analysis,
        "price_bucket_analysis":  price_bucket_analysis,
        "r_multiple_analysis":    r_mult_analysis,
        "mae_mfe_analysis":       mae_mfe_analysis,
        "vix_regime_analysis":    vix_regime_analysis,
        "return_distribution":    return_distribution,
        "loss_diagnostics":       loss_diagnostics,
        "missed_big_win_analysis": missed_big_win_analysis,
        "ml_analysis":             ml_analysis,
        "gate_analysis":           ml_analysis.get("gate_analysis", {}) if isinstance(ml_analysis, dict) else {},
        "grid_search_results":    grid_results if args.grid_search else {},
        "walk_forward_results":   wf_results   if args.walk_forward else [],
        "monte_carlo_results":    mc_results   if args.monte_carlo  else {},
        "position_sizing":        position_sizing,
        "account_simulation":     account_simulation,
        "chart_files":            chart_files,
    }

    if not args.no_trades_json:
        output["all_trades"] = (
            df
              .to_dict(orient="records")
        )

    out_path = Path(f"backtest_results_{ts}.json")
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n  Elapsed: {elapsed}s")
    print(f"  Results saved → {out_path.resolve()}")
    print("  Send this file back for analysis.\n")

    # Append to backtest index DB
    try:
        import sqlite3 as _sqlite3
        primary_h = args.primary_hold if hasattr(args, "primary_hold") else 3
        h_stats = output.get("summary", {}).get("by_hold_period", {}).get(f"{primary_h}d", {})
        idx_db = Path("backtest_index.db")
        with closing(_sqlite3.connect(str(idx_db))) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT, result_file TEXT,
                    start_date TEXT, end_date TEXT,
                    tickers_count INTEGER, n_trades INTEGER,
                    win_rate REAL, total_return REAL, sharpe REAL, hold_period INTEGER
                )
            """)
            conn.execute(
                "INSERT INTO runs (run_at,result_file,start_date,end_date,tickers_count,n_trades,win_rate,total_return,sharpe,hold_period) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    output.get("settings", {}).get("run_at", ts),
                    str(out_path.resolve()),
                    str(getattr(args, "start", "")),
                    str(getattr(args, "end", "")),
                    int(output.get("settings", {}).get("tickers_loaded", 0)),
                    int(h_stats.get("n_trades", 0)),
                    float(h_stats.get("win_rate", 0)),
                    float(h_stats.get("avg_return_pct", 0)),
                    float(h_stats.get("sharpe", 0) or 0),
                    int(primary_h),
                ),
            )
            conn.commit()
    except Exception as _idx_exc:
        print(f"  (backtest index write failed: {_idx_exc})")

    return out_path


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TradingAgents Backtest v3 — full universe, accurate outcomes, extended analytics"
    )

    # ── Core arguments (unchanged from v2) ───────────────────────────────
    parser.add_argument(
        "--tickers", default="all_tickers.txt",
        help="Ticker file, one per line (default: all_tickers.txt)"
    )
    parser.add_argument(
        "--start", default="2020-01-01",
        help="Start date YYYY-MM-DD (default: 2020-01-01)"
    )
    parser.add_argument(
        "--end", default="2024-12-31",
        help="End date YYYY-MM-DD (default: 2024-12-31)"
    )
    parser.add_argument(
        "--threshold", type=float, default=100.0,
        help="Min score to record a signal (default: 100; confirmed gate pass)"
    )
    parser.add_argument(
        "--score-mode", choices=["confirmed_pullback", "breakout", "breakout_v2", "mean_reversion", "oversold_bounce"], default="confirmed_pullback",
        help="Scoring model to backtest (default: confirmed_pullback)"
    )
    parser.add_argument(
        "--entry-timing", choices=["trigger_break", "next_open", "current_close"], default="next_open",
        help="Execution assumption for fills (default: trigger_break)"
    )
    parser.add_argument(
        "--no-gate-filter", action="store_true", default=False,
        help="Skip confirmed_pullback_gates rejection — collect ALL scored candidates (use with --threshold 0 for ML-only mode)"
    )
    parser.add_argument(
        "--freq", type=int, default=1,
        help="Scan every N trading days (default: 1 = daily)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-download even if cache exists"
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Tickers per download batch (default: {BATCH_SIZE}). Lower = more reliable"
    )

    # ── New price / volume filters ────────────────────────────────────────
    parser.add_argument(
        "--min-price", type=float, default=5.0,
        help="Minimum stock price to include (default: 5.0)"
    )
    parser.add_argument(
        "--max-price", type=float, default=None,
        help="Maximum stock price to include (default: None = no upper limit)"
    )
    parser.add_argument(
        "--max-atr-pct", type=float, default=0.08,
        help="Exclude stocks with ATR > this fraction of price (default: 0.08 = 8%%)"
    )
    parser.add_argument(
        "--min-adv", type=int, default=None,
        help="Minimum average daily volume in shares (default: no limit)"
    )

    # ── Target / stop multipliers ─────────────────────────────────────────
    parser.add_argument(
        "--target-mult", type=float, default=0.75,
        help="Target = entry + mult * ATR (default: 0.9)"
    )
    parser.add_argument(
        "--stop-mult", type=float, default=1.0,
        help="Stop = tighter of signal low - 0.2 ATR or entry - mult * ATR (default: 1.1)"
    )

    # ── Hold periods ──────────────────────────────────────────────────────
    parser.add_argument(
        "--hold-periods", type=int, nargs="+", default=[3, 5, 10],
        help="Hold periods to test in days (default: 1 2 3)"
    )
    parser.add_argument(
        "--primary-hold", type=int, default=10,
        help="Primary hold period for analysis (default: 3)"
    )

    # ── Friday / benchmark ────────────────────────────────────────────────
    parser.add_argument(
        "--allow-friday", action="store_true",
        help="Include Friday signals (off by default — backtested as underperforming)"
    )
    parser.add_argument(
        "--regime-filter", choices=["all", "bear", "bull"], default="all",
        help="Restrict analysis to signals in given SPY 200-SMA regime (default: all)"
    )
    parser.add_argument(
        "--benchmark", default="SPY",
        help="Benchmark ticker for alpha calculation (default: SPY)"
    )

    # ── Score range filter ────────────────────────────────────────────────
    parser.add_argument(
        "--score-min", type=float, default=None,
        help="Only record signals with score >= this value"
    )
    parser.add_argument(
        "--score-max", type=float, default=None,
        help="Only record signals with score <= this value"
    )

    # ── Grid search ───────────────────────────────────────────────────────
    parser.add_argument(
        "--grid-search", action="store_true",
        help="Run grid search over threshold values (uses existing trades, fast)"
    )
    parser.add_argument(
        "--grid-thresholds", type=float, nargs="+",
        default=[100.0],
        help="Threshold values to test in grid search (default: 100)"
    )
    parser.add_argument(
        "--grid-targets", type=float, nargs="+",
        default=[0.8, 1.0, 1.2, 1.5, 2.0],
        help="Target multipliers for reference (default: 0.8 1.0 1.2 1.5 2.0)"
    )
    parser.add_argument(
        "--grid-stops", type=float, nargs="+",
        default=[0.4, 0.5, 0.7, 1.0],
        help="Stop multipliers for reference (default: 0.4 0.5 0.7 1.0)"
    )

    # ── Walk-forward ──────────────────────────────────────────────────────
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Run rolling walk-forward out-of-sample analysis"
    )
    parser.add_argument(
        "--wf-window", type=int, default=252,
        help="Walk-forward training window in trading days (default: 252)"
    )
    parser.add_argument(
        "--wf-step", type=int, default=63,
        help="Walk-forward step size in trading days (default: 63)"
    )

    # ── Monte Carlo ───────────────────────────────────────────────────────
    parser.add_argument(
        "--monte-carlo", action="store_true",
        help="Run Monte Carlo bootstrap simulation on trade returns"
    )
    parser.add_argument(
        "--mc-sims", type=int, default=1000,
        help="Number of Monte Carlo simulations (default: 1000)"
    )
    parser.add_argument(
        "--mc-sim-trades", type=int, default=252,
        help="Trades per Monte Carlo simulation run (default: 252 = ~1 trading year)"
    )

    # ── Output ────────────────────────────────────────────────────────────
    parser.add_argument(
        "--account-size", type=float, default=5000.0,
        help="Starting account size for practical account simulation (default: 5000)"
    )
    parser.add_argument(
        "--account-position-cap-pct", type=float, default=20.0,
        help="Maximum account percent allocated per simulated trade (default: 20)"
    )
    parser.add_argument(
        "--account-commission", type=float, default=1.0,
        help="Flat commission dollars per entry and exit (default: 1.0 = $1/side)"
    )
    parser.add_argument(
        "--account-slippage-bps", type=float, default=5.0,
        help="Round-trip slippage in basis points applied to every trade return "
             "(default: 5 bps = 0.05%% per side, 0.10%% total). "
             "Models bid/ask spread and imperfect fill on entry and exit."
    )
    parser.add_argument(
        "--account-sizing-mode", choices=["kelly_static", "fixed"],
        default="fixed",
        help="Position sizing. 'fixed' (default) sizes every trade at "
             "--account-position-cap-pct of current equity with NO look-ahead. "
             "'kelly_static' sizes from whole-sample Half-Kelly and has a sizing "
             "look-ahead; use only for legacy comparisons."
    )
    parser.add_argument(
        "--generate-charts", action=argparse.BooleanOptionalAction, default=True,
        help="Generate PNG chart files (default: true; use --no-generate-charts to skip)"
    )
    parser.add_argument(
        "--charts-dir", type=str, default=None,
        help="Directory for PNG charts (default: backtest_charts_<timestamp>)"
    )
    parser.add_argument(
        "--diagnostics", action=argparse.BooleanOptionalAction, default=True,
        help="Generate loss and missed-big-win diagnostics (default: true)"
    )
    parser.add_argument(
        "--ml-walk-forward", action=argparse.BooleanOptionalAction, default=True,
        help="Leak-free ML profitability eval: purged expanding walk-forward "
             "(default: true; use --no-ml-walk-forward only for legacy diagnostics).")
    parser.add_argument("--ml-wf-step-days", type=int, default=21,
        help="Walk-forward test step size in calendar days (default: 21).")
    parser.add_argument("--ml-wf-min-train", type=int, default=400,
        help="Minimum training rows before the first walk-forward fold (default: 400).")
    parser.add_argument(
        "--ml-analysis", action=argparse.BooleanOptionalAction, default=True,
        help="Train interpretable ML models and add ml_analysis to JSON (default: true)"
    )
    parser.add_argument(
        "--ml-max-rows", type=int, default=0,
        help="Maximum rows used for ML training/evaluation after sampling (default: 0 = all rows)"
    )
    parser.add_argument(
        "--ml-candidate-sample", type=int, default=100000,
        help="Reservoir sample size for rejected candidates with outcomes (default: 100000)"
    )
    parser.add_argument(
        "--ml-min-train-rows", type=int, default=200,
        help="Minimum usable rows before ML analysis runs (default: 200)"
    )
    parser.add_argument(
        "--ml-probability-threshold", type=float, default=0.50,
        help="Minimum ML win probability for the ML gate to pass (default: 0.50)"
    )
    parser.add_argument(
        "--ml-expected-return-min", type=float, default=-0.01,
        help="Minimum expected return for the ML gate to pass (default: -0.01)"
    )
    parser.add_argument(
        "--ml-large-loss-max", type=float, default=0.35,
        help="Maximum large-loss probability for the ML/risk gate to pass (default: 0.35)"
    )
    parser.add_argument(
        "--gate-diagnostics-limit", type=int, default=250,
        help="Maximum candidate gate diagnostics stored in JSON (default: 250)"
    )
    parser.add_argument(
        "--missed-big-win-pct", type=float, default=0.05,
        help="Rejected setup return threshold counted as a missed big win (default: 0.05)"
    )
    parser.add_argument(
        "--bad-loss-pct", type=float, default=-0.03,
        help="Taken-trade loss threshold for detailed loss examples (default: -0.03)"
    )
    parser.add_argument(
        "--diagnostic-max-examples", type=int, default=25,
        help="Max taken-loss examples stored in JSON diagnostics (default: 25)"
    )
    parser.add_argument(
        "--missed-max-examples", type=int, default=50,
        help="Max missed-big-win examples stored in JSON diagnostics (default: 50)"
    )
    parser.add_argument(
        "--export-csv", type=str, default=None,
        help="Export all_trades to this CSV file path"
    )
    parser.add_argument(
        "--no-trades-json", action="store_true",
        help="Skip writing all_trades array to JSON (saves disk space)"
    )

    args = parser.parse_args()

    # Ensure primary hold is in hold_periods
    if args.primary_hold not in args.hold_periods:
        args.hold_periods = sorted(set(args.hold_periods + [args.primary_hold]))

    # Ensure grid_thresholds includes main threshold when grid_search is on
    if args.grid_search and args.threshold not in args.grid_thresholds:
        args.grid_thresholds = sorted(set(args.grid_thresholds + [args.threshold]))

    run_backtest(args)
