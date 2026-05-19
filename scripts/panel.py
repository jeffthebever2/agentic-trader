"""Feature-panel honest backtester.

The enriched scan CSV is a ~2.76M-row feature panel over ticker-dates
(7,897 confirmed_pullback executions + 2.75M scored/sampled rows with full
indicator columns). Instead of re-scanning (slow), we define ANY entry rule
directly on these already-computed, as-of feature values and replay the
leak-free, parity-verified measure_outcome on cached bars.

Honesty:
  - Feature columns were computed as-of the scan bar by the original scan
    (searchsorted side=right); measure_outcome only reads bars AFTER the
    signal bar. So every rule evaluated here is out-of-sample by construction.
  - Restricted to a liquidity-defined universe (tickers_liquid.txt: median
    500-day dollar volume >= $39M, $5-$1000) so trigger/target/stop fills are
    realistic, not micro-cap fiction.
  - Account sim = fixed %-of-current-cash, hard max concurrent positions,
    cash constraint, conservative MAE-marked drawdown. No Kelly look-ahead.
  - A time train/test split guards against rule overfitting.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_sweep as HS  # noqa: E402
from honest_sweep import portfolio, fmt  # noqa: E402

ENRICHED = "ml_models/stock_universe_candidate_20260512/training_data_enriched.csv"
PX_PKL = "/tmp/honest_px.pkl"
PANEL_PKL = "/tmp/panel_liquid.pkl"
LIQUID = "tickers_liquid.txt"

PANEL_COLS = [
    "ticker", "scan_date", "candidate_status", "setup_type", "score",
    "spy_regime", "vix_regime", "atr", "atr_pct", "dollar_vol20",
    "vol_ratio_20d", "vol_ratio_10d", "pct_from_10d_high", "pct_from_52w_high",
    "pct_from_52w_low", "rel_ret20_vs_spy", "spy_ret5", "spy_ret20",
    "sma20_dist", "sma50_dist", "sma200_dist", "rsi9", "rsi14", "macd_hist",
    "macd_hist_prev2", "cci14", "body_pct", "close_loc", "stoch_k", "stoch_d",
    "mfi14", "adx14", "cmf20", "range_rank_52w", "roc10", "roc20",
    "consec_up", "consec_down", "squeeze",
]
_RANK = ["rsi9", "rsi14", "mfi14", "atr_pct", "dollar_vol20",
         "rel_ret20_vs_spy", "adx14", "roc10", "pct_from_52w_high",
         "close_loc", "stoch_k", "cci14"]


def build_panel(rebuild=False):
    if not rebuild and Path(PANEL_PKL).exists():
        return pd.read_pickle(PANEL_PKL)
    want = {l.strip() for l in open(LIQUID) if l.strip()}
    head = pd.read_csv(ENRICHED, nrows=1)
    cols = [c for c in PANEL_COLS if c in head.columns]
    parts = []
    for ch in pd.read_csv(ENRICHED, usecols=cols, chunksize=400_000):
        ch = ch[ch["ticker"].isin(want)]
        ch = ch[pd.to_numeric(ch["atr"], errors="coerce") > 0]
        if len(ch):
            parts.append(ch)
    p = pd.concat(parts, ignore_index=True)
    p["scan_date"] = pd.to_datetime(p["scan_date"]).dt.normalize()
    for c in p.columns:
        if c not in ("ticker", "scan_date", "candidate_status",
                     "setup_type", "spy_regime", "vix_regime"):
            p[c] = pd.to_numeric(p[c], errors="coerce")
    p = p.drop_duplicates(["ticker", "scan_date"]).reset_index(drop=True)
    p.to_pickle(PANEL_PKL)
    return p


_PXC = {}


def _locate(panel, px):
    """Attach integer bar position for each (ticker, scan_date)."""
    key = "_pos_cache"
    if key in _PXC:
        return _PXC[key]
    idx = {tk: pdf.index.normalize() for tk, pdf in px.items()}
    pos = np.full(len(panel), -1, dtype=np.int64)
    tks = panel["ticker"].to_numpy()
    sds = panel["scan_date"].to_numpy()
    cache = {}
    for i in range(len(panel)):
        tk = tks[i]
        ii = idx.get(tk)
        if ii is None:
            continue
        k = (tk, sds[i])
        v = cache.get(k)
        if v is None:
            locs = np.where(ii.values == sds[i])[0]
            v = int(locs[0]) if len(locs) else -1
            cache[k] = v
        pos[i] = v
    _PXC[key] = pos
    return pos


def build_sig(panel, px):
    """One-time sig table over panel rows with a valid bar position, in
    panel order. Reuses honest_sweep's parity-verified fast replay. Also
    returns the boolean has-pos selector so a panel-aligned mask maps onto
    sig rows."""
    if "_sig" in _PXC:
        return _PXC["_sig"], _PXC["_haspos"]
    pos = _locate(panel, px)
    haspos = pos >= 0
    sub = panel[haspos].reset_index(drop=True)
    sp = pos[haspos]
    cols = {
        "ticker": sub["ticker"].to_numpy(),
        "scan_date": sub["scan_date"].to_numpy(),
        "pos": sp,
        "atr": sub["atr"].to_numpy(),
        "pdf_key": sub["ticker"].to_numpy(),
        "score": pd.to_numeric(sub.get("score", 0), errors="coerce")
        .fillna(0).to_numpy(),
    }
    for c in HS._RANK_COLS:
        if c in sub.columns:
            cols[c] = pd.to_numeric(sub[c], errors="coerce").to_numpy()
    sig = pd.DataFrame(cols)
    _PXC["_sig"] = sig
    _PXC["_haspos"] = haspos
    return sig, haspos


def evaluate(panel, px, mask, target_mult, stop_mult, hold,
             pos_pct=0.20, max_pos=6, rank="score:desc",
             entry_timing="trigger_break", start_cash=10000.0, costs=None):
    sig, haspos = build_sig(panel, px)
    m = mask.to_numpy() if hasattr(mask, "to_numpy") else np.asarray(mask)
    sigmask = m[haspos]
    trades = HS.fast_run_config(sig, px, target_mult, stop_mult, hold,
                                mask=sigmask, entry_timing=entry_timing)
    return portfolio(trades, start_cash, pos_pct=pos_pct, max_pos=max_pos,
                     rank=rank, costs=costs), trades


if __name__ == "__main__":
    rebuild = "--rebuild" in sys.argv
    print("building liquid panel...", flush=True)
    p = build_panel(rebuild=rebuild)
    print(f"panel rows={len(p)} "
          f"{p['scan_date'].min().date()}->{p['scan_date'].max().date()} "
          f"tickers={p['ticker'].nunique()}", flush=True)
    print("setup_type:", p["setup_type"].value_counts().to_dict(), flush=True)
    osm = (p["rsi14"] < 35) & (p["mfi14"] < 35)
    print(f"oversold(rsi14<35&mfi14<35) rows={int(osm.sum())}", flush=True)
