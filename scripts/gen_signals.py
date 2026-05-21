"""Parallel signal generator — fidelity-exact, fast.

Reuses backtest.precompute + backtest.score_at and replicates the EXACT
as-of context block from backtest._collect_trades (regime / VIX / VIX term
structure / sector breadth / SPY gate values, all via searchsorted side=right
-> strictly as-of, no look-ahead). Only the pre-score half is reproduced;
outcome replay is done leak-free later by honest_sweep.fast_run_config.

Parallelised per ticker over the cached price frames so a full-universe
multi-year scan that takes ~3h single-threaded in backtest.py finishes in
minutes. Output CSV is consumed by honest_sweep_run.py --sig-csv.

Reproducible:
  python scripts/gen_signals.py --mode oversold_bounce --threshold 70 \
      --start 2019-01-01 --end 2026-05-07 --out tmp/ob_signals.csv
"""
import argparse
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backtest as bt  # noqa: E402

PX_PKL = "/tmp/honest_px.pkl"
WIN = "2017-11-07_2026-05-20"  # cached aux-series window covering the span

_G = {}


def _load_aux():
    cd = Path(".backtest_cache")
    def _pk(name):
        p = cd / f"{name}_{WIN}.pkl"
        return pickle.load(open(p, "rb")) if p.exists() else None
    vix = _pk("vix")
    vix3m = _pk("vix3m")
    sectors = _pk("sectors")
    return vix, vix3m, sectors


def _init():
    # With the 'fork' start method the child inherits the parent's already
    # populated _G (including the single px load) via copy-on-write memory —
    # no per-worker re-import of backtest, no 6x 407MB unpickle.
    pass


def _score_ticker(ticker):
    px = _G["px"]
    df = px.get(ticker)
    if df is None or len(df) < _G["min_hist"] + 5:
        return []
    try:
        pc = bt.precompute(df)
    except Exception:
        return []
    df_idx = df.index
    spy_df = _G["spy_df"]
    spy_regime = _G["spy_regime"]
    vix_regime = _G["vix_regime"]
    vix_ts_series = _G["vix_ts"]
    sb_series = _G["sector_breadth"]
    mode = _G["mode"]
    thr = _G["thr"]
    tmult = _G["tmult"]
    smult = _G["smult"]
    out = []
    for date_ts in _G["scan_dates"]:
        pos = int(df_idx.searchsorted(date_ts, side="right")) - 1
        if pos < _G["min_hist"] or pos >= len(df) - 2:
            continue
        if abs((df_idx[pos] - date_ts).days) > 5:
            continue
        if df_idx[pos].dayofweek == 4:  # Friday excluded (backtest default)
            continue

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
        pre_vix_ts = None
        if vix_ts_series is not None and len(vix_ts_series) > 0:
            ti = vix_ts_series.index.searchsorted(date_ts, side="right") - 1
            if 0 <= ti < len(vix_ts_series):
                v = vix_ts_series.iloc[ti]
                pre_vix_ts = float(v) if pd.notna(v) else None
        pre_sb = None
        if sb_series is not None and len(sb_series) > 0:
            si2 = sb_series.index.searchsorted(date_ts, side="right") - 1
            if 0 <= si2 < len(sb_series):
                v = sb_series.iloc[si2]
                pre_sb = float(v) if pd.notna(v) else None

        spy_close = spy_sma50 = spy_sma200 = spy_ret5 = spy_ret20 = None
        if spy_df is not None:
            sg = spy_df.index.searchsorted(date_ts, side="right") - 1
            if sg >= 200:
                spy_close = float(spy_df["Close"].iloc[sg])
                spy_sma50 = float(spy_df["Close"].iloc[sg - 49:sg + 1].mean())
                spy_sma200 = float(spy_df["Close"].iloc[sg - 199:sg + 1].mean())
                spy_ret5 = float(spy_close / spy_df["Close"].iloc[sg - 5] - 1) if sg >= 5 else None
                spy_ret20 = float(spy_close / spy_df["Close"].iloc[sg - 20] - 1) if sg >= 20 else None

        try:
            score, sig = bt.score_at(
                pc, df, pos, tmult, smult,
                regime=pre_regime, vix_reg=pre_vix_reg, vix_ts=pre_vix_ts,
                sector_breadth=pre_sb, score_mode=mode,
                spy_close=spy_close, spy_sma50=spy_sma50,
                spy_sma200=spy_sma200, spy_ret5=spy_ret5, spy_ret20=spy_ret20)
        except Exception:
            continue
        if not sig or score < thr:
            continue
        gate = sig.get("confirmed_pullback_gates")
        if gate is not None and gate != "pass":
            continue
        rd = df_idx[pos]
        row = {"ticker": ticker, "scan_date": str(rd.date()),
               "score": score, "spy_regime": pre_regime,
               "vix_regime": pre_vix_reg}
        row.update(sig)
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--threshold", type=float, default=70.0)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2026-05-07")
    ap.add_argument("--target-mult", type=float, default=0.75)
    ap.add_argument("--stop-mult", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tickers-file", default=None,
                    help="Restrict scoring to tickers listed here (e.g. a "
                         "liquidity-filtered universe). SPY context kept.")
    ap.add_argument("--shard", default="0/1",
                    help="i/N: process only ticker shard i of N (independent "
                         "process parallelism, robust vs Pool COW thrash).")
    a = ap.parse_args()
    si, sn = (int(x) for x in a.shard.split("/"))

    print("loading px (once, shared via fork)...", flush=True)
    px = pickle.load(open(PX_PKL, "rb"))
    all_tickers = sorted(px.keys())
    spy_df = px.get("SPY")
    if spy_df is None:
        sys.exit("SPY not in px cache")
    if a.tickers_file:
        want = {ln.strip() for ln in open(a.tickers_file) if ln.strip()}
        universe = [t for t in all_tickers if t in want]
    else:
        universe = [t for t in all_tickers if t != "SPY"]
    # Keep SPY available for the gate context but only score this shard.
    tickers = [t for i, t in enumerate(universe) if i % sn == si]

    vix, vix3m, sectors = _load_aux()
    _G["px"] = px
    _G["spy_df"] = spy_df
    _G["spy_regime"] = bt.build_spy_regime(spy_df)
    _G["vix_regime"] = bt.build_vix_regime(vix) if vix is not None else None
    _G["vix_ts"] = (bt.build_vix_term_structure(vix, vix3m)
                    if vix is not None and vix3m is not None else None)
    _G["sector_breadth"] = bt.build_sector_breadth(sectors) if sectors else None
    _G["scan_dates"] = pd.bdate_range(a.start, a.end)
    _G["mode"] = a.mode
    _G["thr"] = a.threshold
    _G["tmult"] = a.target_mult
    _G["smult"] = a.stop_mult
    _G["min_hist"] = bt.MIN_HISTORY
    print(f"shard {si}/{sn}: tickers={len(tickers)} "
          f"scan_dates={len(_G['scan_dates'])} mode={a.mode} "
          f"thr={a.threshold}", flush=True)

    # Single-threaded over this shard's tickers. Cross-ticker parallelism is
    # achieved by launching N independent --shard processes (no Pool / no COW
    # page-fault thrash on the shared 400MB price dict).
    import time
    rows = []
    t0 = time.time()
    for k, tk in enumerate(tickers, 1):
        r = _score_ticker(tk)
        if r:
            rows.extend(r)
        if k % 200 == 0:
            dt = time.time() - t0
            print(f"  shard{si}: {k}/{len(tickers)} "
                  f"({dt/k:.2f}s/tk) signals={len(rows)}", flush=True)
            pd.DataFrame(rows).to_csv(a.out, index=False)  # partial flush
    df = pd.DataFrame(rows)
    df.to_csv(a.out, index=False)
    sdr = (f"{df['scan_date'].min()} -> {df['scan_date'].max()}"
           if len(df) else "none")
    print(f"DONE shard{si}: {len(df)} signals -> {a.out}  [{sdr}]", flush=True)


if __name__ == "__main__":
    main()
