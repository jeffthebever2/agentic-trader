"""Targeted re-simulator.

Reuses the already-completed full-universe scan (enriched CSV) plus the cached
daily OHLC bars, and replays the EXACT measure_outcome() logic from backtest.py
for arbitrary target_mult / stop_mult / hold. No re-scan, no inflation: identical
bar-by-bar same-bar tiebreak rule as the production backtester.
"""
import glob
import pickle
import numpy as np
import pandas as pd

WIN_START = "2025-11-14"
WIN_END   = "2026-05-14"
CSV = "ml_models/stock_universe_candidate_20260512/training_data_enriched.csv"

def load_cache():
    px = {}
    for f in glob.glob(".backtest_cache/batch_2017-11-07_2026-05-20_bs100_*.pkl"):
        try:
            with open(f, "rb") as _f:
                d = pickle.load(_f)
        except Exception:
            continue
        for k, v in d.items():
            if k not in px and isinstance(v, pd.DataFrame) and len(v):
                px[k] = v
    return px

def measure_outcome(df, signal_pos, atr, target_mult, stop_mult, hold_days,
                    entry_timing="trigger_break"):
    """Verbatim port of backtest.py measure_outcome."""
    future = df.iloc[signal_pos + 1 : signal_pos + 1 + hold_days]
    if len(future) == 0:
        return None
    if entry_timing == "next_open":
        entry  = float(future["Open"].iloc[0])
        target = entry + target_mult * atr
        stop   = entry - stop_mult * atr
    else:
        signal_high  = float(df["High"].iloc[signal_pos])
        signal_low   = float(df["Low"].iloc[signal_pos])
        signal_close = float(df["Close"].iloc[signal_pos])
        trigger   = signal_high + 0.05 * atr
        next_open = float(future["Open"].iloc[0])
        next_high = float(future["High"].iloc[0])
        if next_open > signal_close + 0.7 * atr:
            return None
        if next_high < trigger:
            return None
        entry  = next_open if next_open >= trigger else trigger
        target = entry + target_mult * atr
        stop   = max(signal_low - 0.2 * atr, entry - stop_mult * atr)

    outcome   = "TIMED_OUT"
    exit_px   = float(future["Close"].iloc[-1])
    days_held = len(future)
    mae = 0.0
    mfe = 0.0
    for i, (_, row) in enumerate(future.iterrows(), 1):
        o  = float(row.get("Open",  entry))
        hi = float(row.get("High",  entry))
        lo = float(row.get("Low",   entry))
        cl = float(row.get("Close", entry))
        if entry > 0:
            adverse   = (entry - lo) / entry
            favorable = (hi - entry) / entry
            if adverse   > mae: mae = adverse
            if favorable > mfe: mfe = favorable
        if o <= stop:
            outcome, exit_px, days_held = "STOP_HIT", o, i; break
        if o >= target:
            outcome, exit_px, days_held = "TARGET_HIT", o, i; break
        hit_t = hi >= target
        hit_s = lo <= stop
        if hit_t and hit_s:
            mid = (target + stop) / 2.0
            if cl >= mid:
                outcome, exit_px = "TARGET_HIT", target
            else:
                outcome, exit_px = "STOP_HIT", stop
            days_held = i; break
        elif hit_t:
            outcome, exit_px, days_held = "TARGET_HIT", target, i; break
        elif hit_s:
            outcome, exit_px, days_held = "STOP_HIT", stop, i; break
    actual_ret = (exit_px - entry) / entry if entry > 0 else 0.0
    return {"outcome": outcome, "entry": entry, "exit": exit_px,
            "ret": actual_ret, "days": days_held, "mae": mae, "mfe": mfe}

def build_signal_table(px):
    df = pd.read_csv(CSV)
    df["scan_date"] = pd.to_datetime(df["scan_date"])
    w = df[(df["scan_date"] >= WIN_START) & (df["scan_date"] <= WIN_END) &
           (df["confirmed_pullback_gates"] == "pass")].copy()
    w = w[(w["atr"] > 0)].reset_index(drop=True)
    rows = []
    for _, r in w.iterrows():
        tk = r["ticker"]
        pdf = px.get(tk)
        if pdf is None:
            continue
        sd = pd.Timestamp(r["scan_date"]).normalize()
        idx = pdf.index.normalize()
        locs = np.where(idx == sd)[0]
        if len(locs) == 0:
            continue
        rows.append({"ticker": tk, "scan_date": sd, "pos": int(locs[0]),
                     "atr": float(r["atr"]), "score": float(r.get("score", 0) or 0),
                     # carry feature columns for filtering
                     "rsi9": r.get("rsi9"), "adx14": r.get("adx14"),
                     "close_loc": r.get("close_loc"), "body_pct": r.get("body_pct"),
                     "cmf20": r.get("cmf20"), "mfi14": r.get("mfi14"),
                     "rel_ret20_vs_spy": r.get("rel_ret20_vs_spy"),
                     "spy_ret5": r.get("spy_ret5"), "vol_ratio_20d": r.get("vol_ratio_20d"),
                     "atr_pct": r.get("atr_pct"), "stoch_k": r.get("stoch_k"),
                     "pct_from_10d_high": r.get("pct_from_10d_high"),
                     "range_rank_52w": r.get("range_rank_52w"),
                     "macd_hist": r.get("macd_hist"), "macd_hist_prev2": r.get("macd_hist_prev2"),
                     "lower_wick": r.get("lower_wick"), "roc10": r.get("roc10"),
                     "_pdf": tk})
    return pd.DataFrame(rows), px

def run_config(sigtab, px, target_mult, stop_mult, hold, mask=None,
               entry_timing="trigger_break"):
    sub = sigtab if mask is None else sigtab[mask]
    recs = []
    for _, s in sub.iterrows():
        pdf = px[s["_pdf"]]
        o = measure_outcome(pdf, s["pos"], s["atr"], target_mult, stop_mult, hold,
                            entry_timing=entry_timing)
        if o is None:
            continue
        win = (o["outcome"] == "TARGET_HIT") or (o["outcome"] == "TIMED_OUT" and o["ret"] > 0)
        recs.append({"ticker": s["ticker"], "scan_date": s["scan_date"],
                     "score": s["score"], "ret": o["ret"], "outcome": o["outcome"],
                     "days": o["days"], "win": win, "entry": o["entry"]})
    return pd.DataFrame(recs)

def portfolio(trades, start=10000.0, pos_pct=0.25, max_pos=4, commission=0.0):
    if trades.empty:
        return {"profit": 0, "n": 0, "wr": 0, "end": start}
    t = trades.sort_values(["scan_date", "score"], ascending=[True, False]).copy()
    t["exit_date"] = t["scan_date"] + pd.to_timedelta(t["days"], unit="D")
    cash = start
    open_pos = []
    closed = []
    events = sorted(set(t["scan_date"]) | set(t["exit_date"]))
    ti = 0
    tl = t.to_dict("records")
    for ev in events:
        still = []
        for p in open_pos:
            if p["exit_date"] <= ev:
                pnl = p["shares"] * p["entry"] * p["ret"] - commission
                cash += p["shares"] * p["entry"] + pnl
                closed.append({"pnl": pnl, "win": p["win"], "scan_date": p["scan_date"]})
            else:
                still.append(p)
        open_pos = still
        while ti < len(tl) and tl[ti]["scan_date"] <= ev:
            tr = tl[ti]; ti += 1
            if tr["scan_date"] != ev:
                continue
            if len(open_pos) >= max_pos:
                continue
            alloc = cash * pos_pct
            if alloc < tr["entry"] or tr["entry"] <= 0:
                continue
            sh = int(alloc / tr["entry"])
            if sh <= 0:
                continue
            cash -= sh * tr["entry"]
            open_pos.append({**tr, "shares": sh})
    for p in open_pos:
        pnl = p["shares"] * p["entry"] * p["ret"] - commission
        cash += p["shares"] * p["entry"] + pnl
        closed.append({"pnl": pnl, "win": p["win"], "scan_date": p["scan_date"]})
    c = pd.DataFrame(closed)
    if c.empty:
        return {"profit": 0, "n": 0, "wr": 0, "end": start}
    wins = int(c["win"].sum())
    return {"profit": cash - start, "n": len(c), "wr": wins / len(c) * 100,
            "end": cash, "monthly": c.assign(m=c["scan_date"].dt.to_period("M"))
                          .groupby("m")["pnl"].sum().round(0).to_dict()}

if __name__ == "__main__":
    print("loading cache...", flush=True)
    px = load_cache()
    print(f"cache tickers: {len(px)}", flush=True)
    sigtab, px = build_signal_table(px)
    print(f"window gate-pass signals located in cache: {len(sigtab)}", flush=True)
    sigtab.to_pickle("/tmp/sigtab.pkl")
    import pickle as _p
    with open("/tmp/px.pkl", "wb") as _f:
        _p.dump(px, _f)
    print("saved /tmp/sigtab.pkl /tmp/px.pkl", flush=True)
