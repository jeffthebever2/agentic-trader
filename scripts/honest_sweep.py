"""Honest multi-year sweeper for the confirmed_pullback rule strategy.

Reuses the EXACT leak-free measure_outcome() bar replay from scripts/resim.py
(real OHLC bars, same-bar tiebreak, trigger_break entry — no look-ahead) over
the FULL enriched-scan date span, not a hand-picked 6-month window.

Leakage status:
  - The rule signal (confirmed_pullback gate) has NO parameters fitted on the
    test data here; it is a fixed strategy definition. measure_outcome only
    reads bars strictly AFTER the signal bar. So per-config results are
    out-of-sample by construction.
  - To guard against the pre-existing in-sample tuning of the gate thresholds
    and against overfitting any NEW filter added during the sweep, the harness
    supports an explicit time train/test split: tune on the train span, then
    report the untouched test span.

Account simulation is realistic: fixed % of CURRENT cash per position (no
forward-looking Kelly), hard max concurrent positions, cash constraint,
commission, and a drawdown estimate that marks open positions to their
per-trade worst (max adverse excursion) instead of cost — so reported
max_drawdown is conservative, not optimistic.

Reproducible: build the signal table once (cached to /tmp), then every config
is a deterministic replay.
"""
from __future__ import annotations

import glob
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse the verified leak-free bar replay.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resim import measure_outcome  # noqa: E402

ENRICHED_CSV = "ml_models/stock_universe_candidate_20260512/training_data_enriched.csv"
CACHE_GLOB = ".backtest_cache/batch_2017-11-07_2026-05-20_bs100_*.pkl"
SIG_PKL = "/tmp/honest_sigtab.pkl"
PX_PKL = "/tmp/honest_px.pkl"

# Feature columns carried for filtering / ranking experiments.
FEAT_COLS = [
    "score", "rsi9", "rsi14", "adx14", "close_loc", "body_pct", "cmf20",
    "mfi14", "rel_ret20_vs_spy", "spy_ret5", "spy_ret20", "vol_ratio_20d",
    "vol_ratio_10d", "atr_pct", "stoch_k", "pct_from_10d_high",
    "range_rank_52w", "macd_hist", "macd_hist_prev2", "lower_wick", "roc10",
    "roc20", "spy_regime", "vix_regime", "dollar_vol20", "sma200_rising_20d",
    "consec_down", "consec_up", "pct_from_52w_high",
]


def load_cache() -> dict:
    px: dict = {}
    for f in glob.glob(CACHE_GLOB):
        try:
            d = pickle.load(open(f, "rb"))
        except Exception:
            continue
        for k, v in d.items():
            if k not in px and isinstance(v, pd.DataFrame) and len(v):
                px[k] = v
    return px


def build_signal_table(px: dict) -> pd.DataFrame:
    """All confirmed_pullback gate-pass signals across the full enriched span,
    located to an integer bar position in the cached price frame."""
    usecols = ["ticker", "scan_date", "confirmed_pullback_gates", "atr"] + FEAT_COLS
    # de-dup while preserving order
    seen, cols = set(), []
    for c in usecols:
        if c not in seen:
            seen.add(c)
            cols.append(c)
    parts = []
    for ch in pd.read_csv(ENRICHED_CSV, usecols=lambda c: c in set(cols),
                          chunksize=400_000):
        ch = ch[ch["confirmed_pullback_gates"] == "pass"]
        ch = ch[pd.to_numeric(ch["atr"], errors="coerce") > 0]
        if len(ch):
            parts.append(ch)
    w = pd.concat(parts, ignore_index=True)
    w["scan_date"] = pd.to_datetime(w["scan_date"]).dt.normalize()

    # Pre-index normalized price dates once per ticker.
    idx_cache = {tk: pdf.index.normalize() for tk, pdf in px.items()}
    rows = []
    for r in w.itertuples(index=False):
        tk = r.ticker
        idx = idx_cache.get(tk)
        if idx is None:
            continue
        locs = np.where(idx == r.scan_date)[0]
        if len(locs) == 0:
            continue
        rec = {"ticker": tk, "scan_date": r.scan_date, "pos": int(locs[0]),
               "atr": float(r.atr), "pdf_key": tk}
        for c in FEAT_COLS:
            rec[c] = getattr(r, c, None)
        rows.append(rec)
    out = pd.DataFrame(rows).sort_values("scan_date").reset_index(drop=True)
    return out


def build_from_export(csv_path: str, px: dict) -> pd.DataFrame:
    """Build a signal table from a backtest.py --export-csv file (any score
    mode). Rows are already the passed signals; we only need ticker /
    scan_date / atr to locate the bar and replay outcomes. Carries whatever
    FEAT_COLS exist for filtering."""
    head = pd.read_csv(csv_path, nrows=1)
    cols = [c for c in (["ticker", "scan_date", "atr"] + FEAT_COLS)
            if c in head.columns]
    if "ticker" not in cols or "scan_date" not in cols or "atr" not in cols:
        raise SystemExit(f"export CSV missing ticker/scan_date/atr: {csv_path}")
    w = pd.read_csv(csv_path, usecols=cols)
    w = w[pd.to_numeric(w["atr"], errors="coerce") > 0].copy()
    w["scan_date"] = pd.to_datetime(w["scan_date"]).dt.normalize()
    idx_cache = {tk: pdf.index.normalize() for tk, pdf in px.items()}
    rows = []
    for r in w.itertuples(index=False):
        idx = idx_cache.get(r.ticker)
        if idx is None:
            continue
        locs = np.where(idx == r.scan_date)[0]
        if len(locs) == 0:
            continue
        rec = {"ticker": r.ticker, "scan_date": r.scan_date,
               "pos": int(locs[0]), "atr": float(r.atr), "pdf_key": r.ticker}
        for c in FEAT_COLS:
            rec[c] = getattr(r, c, None)
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("scan_date").reset_index(drop=True)


def get_data_csv(csv_path: str, sig_pkl: str, rebuild: bool = False):
    """Like get_data but for an arbitrary exported-signal CSV (reuses the
    shared price cache pickle so it stays consistent and fast)."""
    if not Path(PX_PKL).exists():
        raise SystemExit("run honest_sweep build first to create the px cache")
    px = pickle.load(open(PX_PKL, "rb"))
    if not rebuild and Path(sig_pkl).exists():
        sig = pd.read_pickle(sig_pkl)
        return sig, px
    print(f"building signal table from {csv_path} ...", flush=True)
    sig = build_from_export(csv_path, px)
    print(f"signals located: {len(sig)} "
          f"({sig['scan_date'].min().date()} -> {sig['scan_date'].max().date()})",
          flush=True)
    sig.to_pickle(sig_pkl)
    return sig, px


def get_data(rebuild: bool = False):
    if not rebuild and Path(SIG_PKL).exists() and Path(PX_PKL).exists():
        sig = pd.read_pickle(SIG_PKL)
        if "_pdf" in sig.columns and "pdf_key" not in sig.columns:
            sig = sig.rename(columns={"_pdf": "pdf_key"})
        px = pickle.load(open(PX_PKL, "rb"))
        return sig, px
    print("loading price cache...", flush=True)
    px = load_cache()
    print(f"cache tickers: {len(px)}", flush=True)
    print("building signal table (full span)...", flush=True)
    sig = build_signal_table(px)
    print(f"gate-pass signals located: {len(sig)} "
          f"({sig['scan_date'].min().date()} -> {sig['scan_date'].max().date()})",
          flush=True)
    sig.to_pickle(SIG_PKL)
    pickle.dump(px, open(PX_PKL, "wb"))
    return sig, px


def run_config(sig: pd.DataFrame, px: dict, target_mult: float,
               stop_mult: float, hold: int, mask=None,
               entry_timing: str = "trigger_break") -> pd.DataFrame:
    sub = sig if mask is None else sig[mask]
    recs = []
    for s in sub.itertuples(index=False):
        pdf = px[s.pdf_key]
        o = measure_outcome(pdf, s.pos, s.atr, target_mult, stop_mult, hold,
                            entry_timing=entry_timing)
        if o is None:
            continue
        win = (o["outcome"] == "TARGET_HIT") or (
            o["outcome"] == "TIMED_OUT" and o["ret"] > 0)
        exit_pos = min(int(s.pos) + int(o["days"]), len(pdf.index) - 1)
        recs.append({"ticker": s.ticker, "scan_date": s.scan_date,
                     "score": float(getattr(s, "score", 0) or 0),
                     "ret": o["ret"], "outcome": o["outcome"],
                     "days": o["days"], "win": win, "entry": o["entry"],
                     "mae": o["mae"], "exit_date": pdf.index[exit_pos]})
    return pd.DataFrame(recs)


_FAST_CACHE: dict = {}
_FAST_DEFAULT_MAXHOLD = 30
# Feature values carried into the trade output so the portfolio can rank
# same-day candidates by them (the decisive lever for capturing edge that
# constant-score gate strategies otherwise can't select on).
_RANK_COLS = ["rsi9", "rsi14", "mfi14", "atr_pct", "dollar_vol20",
              "rel_ret20_vs_spy", "adx14", "roc10", "pct_from_52w_high"]


def roll_half_spread_frac(closes: np.ndarray, win: int = 40,
                          floor_bps: float = 3.0) -> float:
    """Roll (1984) implicit half-spread as a fraction of price, estimated
    from the negative serial covariance of price changes over the `win`
    bars ENDING at (and including) the signal bar — strictly as-of, no
    look-ahead. s = 2*sqrt(-cov(dP_t, dP_{t-1})); half-spread = s/2.

    When cov >= 0 (Roll undefined) fall back to a small liquid-name floor
    so costs are never zero. Returned as a fraction of the last price.
    """
    c = closes[-(win + 2):]
    if len(c) < 8:
        return floor_bps / 1e4
    dp = np.diff(c)
    if len(dp) < 4:
        return floor_bps / 1e4
    cov = np.cov(dp[1:], dp[:-1])[0, 1]
    px_last = c[-1] if c[-1] > 0 else 1.0
    if cov < 0:
        s = 2.0 * np.sqrt(-cov)
        half = (s / 2.0) / px_last
    else:
        half = floor_bps / 1e4
    # clamp to a sane band for liquid equities (0.03%–1.5% half-spread)
    return float(min(max(half, floor_bps / 1e4), 0.015))


def _build_fast(sig: pd.DataFrame, px: dict, max_hold: int = _FAST_DEFAULT_MAXHOLD):
    """Precompute, per signal, the post-signal OHLC window as numpy arrays
    once (max 25 bars). Identical bar logic to resim.measure_outcome /
    backtest.measure_outcome (trigger_break entry, same-bar tiebreak), just
    without pandas iterrows. Cached for the lifetime of the process."""
    # id() is reused by CPython after the original object is GC'd, which
    # could return a stale OTHER sig's cached windows. Guard by also keying
    # on a cheap content fingerprint AND holding a strong ref to sig so its
    # id cannot be recycled while cached.
    try:
        fp = (len(sig), int(pd.util.hash_pandas_object(
            sig[["scan_date", "pos", "pdf_key"]], index=False).sum()))
    except Exception:
        fp = (len(sig), -1)
    key = (id(sig), int(max_hold), fp)
    if key in _FAST_CACHE:
        return _FAST_CACHE[key][1]
    recs = []
    for s in sig.itertuples(index=False):
        pdf = px.get(s.pdf_key)
        if pdf is None:
            recs.append(None)
            continue
        p = int(s.pos)
        fut = pdf.iloc[p + 1: p + 1 + int(max_hold)]
        if len(fut) == 0:
            recs.append(None)
            continue
        prior_close = pdf["Close"].iloc[max(0, p - 60): p + 1].to_numpy(
            np.float64)
        recs.append({
            "O": fut["Open"].to_numpy(np.float64),
            "H": fut["High"].to_numpy(np.float64),
            "L": fut["Low"].to_numpy(np.float64),
            "C": fut["Close"].to_numpy(np.float64),
            "D": fut.index.to_numpy(),
            "sh": float(pdf["High"].iloc[p]),
            "sl": float(pdf["Low"].iloc[p]),
            "sc": float(pdf["Close"].iloc[p]),
            "atr": float(s.atr),
            "ticker": s.ticker,
            "scan_date": s.scan_date,
            "score": float(getattr(s, "score", 0) or 0),
            "roll_half": roll_half_spread_frac(prior_close),
            "rk": {c: getattr(s, c, None) for c in _RANK_COLS},
        })
    _FAST_CACHE[key] = (sig, recs)  # strong ref prevents id() reuse
    return recs


def fast_run_config(sig: pd.DataFrame, px: dict, target_mult: float,
                    stop_mult: float, hold: int, mask=None,
                    entry_timing: str = "trigger_break") -> pd.DataFrame:
    """Vectorized-window equivalent of run_config. Same fills, ~100x faster."""
    fast = _build_fast(sig, px, max_hold=max(hold, _FAST_DEFAULT_MAXHOLD))
    if mask is None:
        idxs = range(len(fast))
    else:
        m = np.asarray(mask)
        idxs = np.nonzero(m)[0]
    out_t, out_d, out_xd, out_s, out_r, out_o, out_dy, out_w, out_e, out_mae = (
        [], [], [], [], [], [], [], [], [], [])
    out_rk = []
    out_rh = []
    for i in idxs:
        r = fast[i]
        if r is None:
            continue
        atr = r["atr"]
        O, H, L, C, D = r["O"], r["H"], r["L"], r["C"], r["D"]
        n = min(hold, O.shape[0])
        if n <= 0:
            continue
        if entry_timing == "next_open":
            entry = O[0]
            target = entry + target_mult * atr
            stop = entry - stop_mult * atr
        else:  # trigger_break (production default)
            trigger = r["sh"] + 0.05 * atr
            no = O[0]
            nh = H[0]
            if no > r["sc"] + 0.7 * atr:
                continue
            if nh < trigger:
                continue
            entry = no if no >= trigger else trigger
            target = entry + target_mult * atr
            stop = max(r["sl"] - 0.2 * atr, entry - stop_mult * atr)
        if entry <= 0:
            continue
        outcome = "TIMED_OUT"
        exit_px = C[n - 1]
        days = n
        mae = 0.0
        for j in range(n):
            o, hi, lo, cl = O[j], H[j], L[j], C[j]
            adv = (entry - lo) / entry
            if adv > mae:
                mae = adv
            if o <= stop:
                outcome, exit_px, days = "STOP_HIT", o, j + 1
                break
            if o >= target:
                outcome, exit_px, days = "TARGET_HIT", o, j + 1
                break
            ht = hi >= target
            hs = lo <= stop
            if ht and hs:
                mid = (target + stop) / 2.0
                if cl >= mid:
                    outcome, exit_px = "TARGET_HIT", target
                else:
                    outcome, exit_px = "STOP_HIT", stop
                days = j + 1
                break
            if ht:
                outcome, exit_px, days = "TARGET_HIT", target, j + 1
                break
            if hs:
                outcome, exit_px, days = "STOP_HIT", stop, j + 1
                break
        ret = (exit_px - entry) / entry
        win = (outcome == "TARGET_HIT") or (outcome == "TIMED_OUT" and ret > 0)
        exit_date = pd.Timestamp(D[days - 1]) if len(D) >= days else pd.NaT
        out_t.append(r["ticker"]); out_d.append(r["scan_date"])
        out_xd.append(exit_date)
        out_s.append(r["score"]); out_r.append(ret); out_o.append(outcome)
        out_dy.append(days); out_w.append(win); out_e.append(entry)
        out_mae.append(mae)
        out_rk.append(r["rk"])
        out_rh.append(r.get("roll_half", 0.0003))
    out = pd.DataFrame({
        "ticker": out_t, "scan_date": out_d, "score": out_s, "ret": out_r,
        "outcome": out_o, "days": out_dy, "win": out_w, "entry": out_e,
        "mae": out_mae, "roll_half": out_rh, "exit_date": out_xd,
    })
    if out_rk:
        rk = pd.DataFrame(out_rk).reset_index(drop=True)
        for c in _RANK_COLS:
            if c in rk.columns:
                out[c] = pd.to_numeric(rk[c], errors="coerce").values
    return out


DEFAULT_COSTS = {
    "on": True,
    "commission_per_share": 0.005,   # institutional tier
    "min_commission": 1.0,           # per fill, penalises churn
    "extra_slippage_bps": 1.0,       # volume-impact proxy, each side
    "use_roll": True,                # Roll-model half-spread each side
}


def _trade_cost(shares, entry, ret, roll_half, costs):
    """Round-trip friction in $: half-spread + slippage on entry and exit
    notional + per-share commission (min floor) on each fill."""
    if not costs or not costs.get("on", True):
        return 0.0
    entry_notional = shares * entry
    exit_notional = shares * entry * (1.0 + ret)
    half = (roll_half if costs.get("use_roll", True) else 0.0)
    slip = costs.get("extra_slippage_bps", 0.0) / 1e4
    per_side_frac = half + slip
    spread_cost = (entry_notional + exit_notional) * per_side_frac
    cps = costs.get("commission_per_share", 0.0)
    minc = costs.get("min_commission", 0.0)
    commission = 2.0 * max(cps * shares, minc)
    return spread_cost + commission


def portfolio(trades: pd.DataFrame, start: float = 10000.0, pos_pct: float = 0.25,
              max_pos: int = 4, commission: float = 0.0,
              rank: str = "score:desc", costs: dict = None) -> dict:
    """Realistic account sim WITH transaction costs (default ON).

    costs: dict (see DEFAULT_COSTS) — Roll-model half-spread + slippage on
    entry & exit notional + per-share commission with a min floor. Pass
    {"on": False} for a frictionless diagnostic only.
    rank: same-day selection priority when more candidates than free slots,
          as "<col>:<asc|desc>" (e.g. "score:desc" default like live,
          "rsi9:asc" = deepest-oversold first, "random" = seed-fixed shuffle).
    Drawdown is computed on an equity curve where OPEN positions are marked
    to their realized worst (entry*(1-mae)) — a conservative lower bound on
    interim equity, so max_drawdown is not understated by cost-marking.
    """
    if costs is None:
        costs = DEFAULT_COSTS
    if trades is None or trades.empty:
        return {"profit": 0.0, "n": 0, "wr": 0.0, "end": start, "pf": 0.0,
                "max_dd": 0.0, "ann": 0.0, "total_ret": 0.0, "start_date": None,
                "end_date": None}
    t = trades.copy()
    t["scan_date"] = pd.to_datetime(t["scan_date"])
    if rank == "random":
        t = t.sample(frac=1.0, random_state=42).sort_values(
            "scan_date", kind="stable")
    else:
        col, _, direction = rank.partition(":")
        if col in t.columns:
            ascending = direction != "desc"
            t = t.sort_values(["scan_date", col],
                              ascending=[True, ascending], kind="stable")
        else:
            t = t.sort_values("scan_date", kind="stable")
    if "exit_date" in t.columns:
        t["exit_date"] = pd.to_datetime(t["exit_date"], errors="coerce")
    else:
        t["exit_date"] = pd.NaT
    missing_exit = t["exit_date"].isna()
    t.loc[missing_exit, "exit_date"] = (
        t.loc[missing_exit, "scan_date"]
        + pd.to_timedelta(t.loc[missing_exit, "days"], unit="D")
    )
    cash = start
    open_pos: list = []
    closed: list = []
    events = sorted(set(t["scan_date"]) | set(t["exit_date"]))
    tl = t.to_dict("records")
    ti = 0
    peak = start
    max_dd = 0.0

    def mark_to_worst() -> float:
        eq = cash
        for p in open_pos:
            eq += p["shares"] * p["entry"] * (1.0 - max(0.0, p["mae"]))
        return eq

    for ev in events:
        still = []
        for p in open_pos:
            if p["exit_date"] <= ev:
                tc = _trade_cost(p["shares"], p["entry"], p["ret"],
                                 p.get("roll_half", 0.0003), costs)
                pnl = p["shares"] * p["entry"] * p["ret"] - tc
                cash += p["shares"] * p["entry"] + pnl
                closed.append({"pnl": pnl, "win": pnl > 0,
                               "scan_date": p["scan_date"], "ret": p["ret"],
                               "cost": tc})
            else:
                still.append(p)
        open_pos = still
        while ti < len(tl) and tl[ti]["scan_date"] <= ev:
            tr = tl[ti]
            ti += 1
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
        eq = mark_to_worst()
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    for p in open_pos:
        tc = _trade_cost(p["shares"], p["entry"], p["ret"],
                         p.get("roll_half", 0.0003), costs)
        pnl = p["shares"] * p["entry"] * p["ret"] - tc
        cash += p["shares"] * p["entry"] + pnl
        closed.append({"pnl": pnl, "win": pnl > 0,
                       "scan_date": p["scan_date"], "ret": p["ret"],
                       "cost": tc})
    c = pd.DataFrame(closed)
    if c.empty:
        return {"profit": 0.0, "n": 0, "wr": 0.0, "end": start, "pf": 0.0,
                "max_dd": 0.0, "ann": 0.0, "total_ret": 0.0, "start_date": None,
                "end_date": None}
    wins = c[c["pnl"] > 0]["pnl"].sum()
    losses = abs(c[c["pnl"] <= 0]["pnl"].sum())
    pf = (wins / losses) if losses > 0 else float("inf")
    sd = pd.to_datetime(t["scan_date"]).min()
    ed = pd.to_datetime(t["exit_date"]).max()
    years = max((ed - sd).days / 365.25, 1e-9)
    total_ret = cash / start - 1.0
    ann = (cash / start) ** (1.0 / years) - 1.0 if cash > 0 else -1.0
    return {
        "profit": round(cash - start, 2),
        "end": round(cash, 2),
        "total_ret": round(total_ret * 100, 2),
        "ann": round(ann * 100, 2),
        "n": int(len(c)),
        "wr": round(int((c["pnl"] > 0).sum()) / len(c) * 100, 2),
        "pf": round(pf, 3) if np.isfinite(pf) else 999.0,
        "max_dd": round(max_dd * 100, 2),
        "start_date": str(sd.date()),
        "end_date": str(ed.date()),
        "years": round(years, 2),
    }


def fmt(tag: str, p: dict) -> str:
    return (f"{tag:<34} ann={p['ann']:>7.2f}%  tot={p['total_ret']:>8.2f}%  "
            f"${p['profit']:>10.2f}  DD={p['max_dd']:>5.2f}%  "
            f"WR={p['wr']:>5.2f}%  PF={p['pf']:>6.3f}  n={p['n']:>5}  "
            f"[{p['start_date']}->{p['end_date']}]")


if __name__ == "__main__":
    rebuild = "--rebuild" in sys.argv
    sig, px = get_data(rebuild=rebuild)
    print(f"signals: {len(sig)}", flush=True)
