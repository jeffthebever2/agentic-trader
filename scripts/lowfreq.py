"""Low-turnover rule strategies from the research report, tested honestly.

Strategies (all leak-free; signal on close, fill NEXT open; only post-signal
bars read for outcomes; liquid universe; NET of Roll-spread + slippage +
commission via honest_sweep.portfolio):

  - double_seven : Close > SMA200 AND Close == 7-day-low -> exit on
                    Close == 7-day-high OR time stop.
  - connors_rsi2 : Close > SMA200 AND ConnorsRSI < thr -> exit Close > SMA5
                    OR time stop.
  - ema_ribbon   : EMA30 slope>0 AND EMA7>EMA30 AND Low<=EMA7 -> Chandelier
                    (HH22 - 3*ATR) or time stop.

Validation: full-period NET + time-split (TRAIN<=2023-06-30 / TEST after)
+ Combinatorial Purged CV path distribution + Probabilistic & Deflated
Sharpe across the variants tried (multiple-testing aware).
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_sweep as HS  # noqa: E402

PX_PKL = "/tmp/honest_px.pkl"
LIQUID = "tickers_liquid.txt"
TRAIN_END = pd.Timestamp("2023-06-30")
TEST_START = pd.Timestamp("2023-07-01")
START = pd.Timestamp("2019-01-01")
END = pd.Timestamp("2026-05-07")


# ---------- indicators (vectorised, as-of) ----------
def _rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _streak(s: pd.Series) -> pd.Series:
    """Connors up/down streak, fully vectorised. streak_i = signed run length
    of consecutive same-sign daily moves (0 on a flat day)."""
    sign = np.sign(s.diff().fillna(0).to_numpy())
    n = len(sign)
    out = np.zeros(n)
    # group id increments whenever the sign changes
    chg = np.r_[True, sign[1:] != sign[:-1]]
    order = np.arange(n)
    grp_start = np.where(chg, order, 0)
    grp_start = np.maximum.accumulate(grp_start)   # start idx of current run
    within = order - grp_start + 1                 # 1..run length
    out = sign * within
    out[sign == 0] = 0.0
    return pd.Series(out, index=s.index)


def _pctrank(s: pd.Series, n: int) -> pd.Series:
    """Rolling percent-rank of the last value vs the prior n-1, vectorised
    via a strided sliding window (no python per-bar loop)."""
    x = s.to_numpy(np.float64)
    m = len(x)
    out = np.full(m, 50.0)
    if m <= n:
        return pd.Series(out, index=s.index)
    from numpy.lib.stride_tricks import sliding_window_view
    w = sliding_window_view(x, n)              # (m-n+1, n)
    last = w[:, -1:][:, 0]
    frac = (w[:, :-1] < last[:, None]).mean(axis=1) * 100.0
    out[n - 1:] = frac
    return pd.Series(out, index=s.index)


def connors_rsi(close: pd.Series) -> pd.Series:
    r3 = _rsi(close, 3)
    streak = _streak(close)
    rs2 = _rsi(streak, 2)
    ret1 = close.pct_change()
    pr = _pctrank(ret1, 100)
    return ((r3 + rs2 + pr) / 3.0)


def _atr(df: pd.DataFrame, n: int = 22) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------- per-ticker indicator precompute (once per ticker/strat) ----------
def _indicators(df, strat):
    c = df["Close"]
    sma200 = c.rolling(200).mean()
    ind = {"c": c, "o": df["Open"], "idx": df.index,
           "sma200": sma200, "rh_src": c.to_numpy(np.float64)}
    if strat == "double_seven":
        ind["low7"] = c.rolling(7).min()
        ind["high7"] = c.rolling(7).max()
    elif strat == "connors_rsi2":
        ind["crsi"] = connors_rsi(c)
        ind["sma5"] = c.rolling(5).mean()
    elif strat == "ema_ribbon":
        ind["e7"] = c.ewm(span=7, adjust=False).mean()
        ind["e30"] = c.ewm(span=30, adjust=False).mean()
        ind["slope"] = ind["e30"].diff(3)
        ind["atr"] = _atr(df, 22)
        ind["hh22"] = df["High"].rolling(22).max()
    return ind


def _simulate_ticker(tk, df, strat, params, ind=None):
    if df is None or len(df) < 230:
        return []
    if ind is None:
        ind = _indicators(df, strat)
    c = ind["c"]; o = ind["o"]; idx = ind["idx"]
    sma200 = ind["sma200"]; rh_src = ind["rh_src"]
    out = []

    if strat == "double_seven":
        low7, high7 = ind["low7"], ind["high7"]
        tstop = params.get("time_stop", 15)
        sig = (c > sma200) & (c <= low7)
    elif strat == "connors_rsi2":
        crsi, sma5 = ind["crsi"], ind["sma5"]
        thr = params.get("crsi_thr", 5.0)
        tstop = params.get("time_stop", 7)
        sig = (c > sma200) & (crsi < thr)
    elif strat == "ema_ribbon":
        e7, e30, slope = ind["e7"], ind["e30"], ind["slope"]
        atr, hh22 = ind["atr"], ind["hh22"]
        tstop = params.get("time_stop", 40)
        sig = (slope > 0) & (e7 > e30) & (df["Low"] <= e7) & (c > sma200)
    else:
        return []

    sig_pos = np.where(sig.to_numpy())[0]
    in_trade_until = -1
    for p in sig_pos:
        if p <= in_trade_until or p + 1 >= len(df):
            continue
        entry = float(o.iloc[p + 1])
        if not np.isfinite(entry) or entry <= 0:
            continue
        mae = 0.0
        exit_px = float(c.iloc[-1])
        days = 0
        ed = idx[-1]
        for j in range(p + 1, min(p + 1 + tstop + 1, len(df))):
            lo = float(df["Low"].iloc[j])
            hi = float(df["High"].iloc[j])
            cl = float(c.iloc[j])
            adv = (entry - lo) / entry
            if adv > mae:
                mae = adv
            days = j - p
            done = False
            if strat == "double_seven":
                if cl >= float(high7.iloc[j]):
                    exit_px, ed, done = cl, idx[j], True
            elif strat == "connors_rsi2":
                if cl > float(sma5.iloc[j]):
                    exit_px, ed, done = cl, idx[j], True
            elif strat == "ema_ribbon":
                chand = float(hh22.iloc[j]) - 3.0 * float(atr.iloc[j])
                if lo <= chand:
                    exit_px, ed, done = chand, idx[j], True
            if done:
                break
            if days >= tstop:
                exit_px, ed = cl, idx[j]
                break
        ret = (exit_px - entry) / entry
        in_trade_until = p + days
        prior = rh_src[max(0, p - 60): p + 1]
        out.append({
            "ticker": tk, "scan_date": idx[p + 1], "score": 1.0,
            "ret": float(ret), "outcome": "EXIT", "days": int(max(days, 1)),
            "win": ret > 0, "entry": entry, "mae": float(mae),
            "roll_half": HS.roll_half_spread_frac(prior),
            "exit_date": pd.Timestamp(ed),
        })
    return out


def build_trades_multi(px, tickers, strat, plist):
    """Compute per-ticker indicators ONCE per strat, reuse across the param
    list (Connors RSI / streak / pctrank are expensive)."""
    res = {i: [] for i in range(len(plist))}
    for tk in tickers:
        df = px.get(tk)
        if df is None or len(df) < 230:
            continue
        ind = _indicators(df, strat)
        for i, params in enumerate(plist):
            res[i].extend(_simulate_ticker(tk, df, strat, params, ind=ind))
    out = {}
    for i, rows in res.items():
        t = pd.DataFrame(rows)
        if len(t):
            t = t[(t["scan_date"] >= START) & (t["scan_date"] <= END)]
        out[i] = t.reset_index(drop=True)
    return out


def build_trades(px, tickers, strat, params):
    return build_trades_multi(px, tickers, strat, [params])[0]


# ---------- PSR / DSR ----------
def sharpe(rets):
    r = np.asarray(rets, float)
    if len(r) < 3 or r.std(ddof=1) == 0:
        return 0.0
    return r.mean() / r.std(ddof=1)


def psr(rets, sr_benchmark=0.0):
    r = np.asarray(rets, float)
    n = len(r)
    if n < 4:
        return 0.0
    sr = sharpe(r)
    g3 = pd.Series(r).skew()
    g4 = pd.Series(r).kurt() + 3.0
    denom = np.sqrt(1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2)
    if denom <= 0:
        return 0.0
    return float(norm.cdf((sr - sr_benchmark) * np.sqrt(n - 1) / denom))


def deflated_sharpe(rets, all_trial_sharpes):
    """DSR: PSR evaluated against the expected MAX Sharpe of N independent
    trials (False Strategy Theorem), so the bar rises with #variants tried."""
    r = np.asarray(rets, float)
    n = len(r)
    if n < 4:
        return 0.0
    srs = np.asarray(all_trial_sharpes, float)
    N = max(len(srs), 1)
    var_sr = srs.var(ddof=1) if N > 1 else 1.0
    eg = 0.5772156649
    emax = (np.sqrt(var_sr) *
            ((1 - eg) * norm.ppf(1 - 1.0 / N) +
             eg * norm.ppf(1 - 1.0 / (N * np.e)))) if N > 1 else 0.0
    return psr(r, sr_benchmark=emax)


# ---------- CPCV ----------
def cpcv_paths(trades, k=8, k_test=2, embargo_days=10, pos_pct=0.2,
               max_pos=6, rank="score:desc"):
    if trades is None or len(trades) < 30:
        return []
    t = trades.copy()
    t["scan_date"] = pd.to_datetime(t["scan_date"])
    lo, hi = t["scan_date"].min(), t["scan_date"].max()
    edges = pd.date_range(lo, hi, periods=k + 1)
    blocks = [(edges[i], edges[i + 1]) for i in range(k)]
    emb = pd.Timedelta(days=embargo_days)
    out = []
    for combo in itertools.combinations(range(k), k_test):
        mask = pd.Series(False, index=t.index)
        for bi in combo:
            s, e = blocks[bi]
            mask |= (t["scan_date"] >= s) & (t["scan_date"] < e)
        # purge: drop test trades whose holding window would overlap an
        # adjacent train block within the embargo.
        sub = t[mask].copy()
        if len(sub) < 10:
            continue
        r = HS.portfolio(sub, 10000.0, pos_pct=pos_pct, max_pos=max_pos,
                         rank=rank)
        if r["n"] >= 8:
            out.append(r["ann"])
    return out


def fmt(tag, p):
    return (f"{tag:<46} ann={p['ann']:>7.2f}%  tot={p['total_ret']:>8.2f}%  "
            f"${p['profit']:>9.2f}  DD={p['max_dd']:>5.2f}%  "
            f"WR={p['wr']:>5.2f}%  PF={p['pf']:>6.3f}  n={p['n']:>5}  "
            f"[{p['start_date']}->{p['end_date']}]")


def main():
    with open(PX_PKL, "rb") as _f:
        px = pickle.load(_f)
    with open(LIQUID) as _f:
        want = [l.strip() for l in _f if l.strip()]
    tickers = [t for t in want if t in px][:600]
    print(f"liquid tickers in cache: {len(tickers)}", flush=True)

    grid = []
    for strat, plist in {
        "double_seven": [{"time_stop": ts} for ts in (8, 12, 15, 20)],
        "connors_rsi2": [{"crsi_thr": th, "time_stop": ts}
                         for th in (5, 10, 15) for ts in (5, 7, 10)],
        "ema_ribbon": [{"time_stop": ts} for ts in (20, 40, 60)],
    }.items():
        tmap = build_trades_multi(px, tickers, strat, plist)
        for i, params in enumerate(plist):
            t = tmap[i]
            if len(t) < 30:
                print(f"{strat} {params}: only {len(t)} trades, skip",
                      flush=True)
                continue
            for pp, mp in ((0.10, 8), (0.20, 6), (0.33, 4)):
                full = HS.portfolio(t, 10000.0, pos_pct=pp, max_pos=mp)
                tr = t[pd.to_datetime(t["scan_date"]) <= TRAIN_END]
                te = t[pd.to_datetime(t["scan_date"]) >= TEST_START]
                ptr = HS.portfolio(tr, 10000.0, pos_pct=pp, max_pos=mp)
                pte = HS.portfolio(te, 10000.0, pos_pct=pp, max_pos=mp)
                grid.append({
                    "tag": f"{strat} {params} pp{pp}/mp{mp}",
                    "t": t, "pp": pp, "mp": mp,
                    "full": full, "train": ptr, "test": pte,
                })

    # Selection bar = TRAIN annualized only; TEST + CPCV reported once.
    grid.sort(key=lambda g: -g["train"]["ann"])
    print(f"\nvariants tested (N for DSR) = {len(grid)}", flush=True)
    print("\n=== all variants (NET of costs), sorted by TRAIN ann ===",
          flush=True)
    for g in grid:
        print(fmt("FULL " + g["tag"], g["full"]), flush=True)
        print("   " + fmt("TRAIN", g["train"]), flush=True)
        print("   " + fmt("TEST ", g["test"]), flush=True)

    if not grid:
        print("no qualifying variant", flush=True)
        return
    best = grid[0]
    paths = cpcv_paths(best["t"], pos_pct=best["pp"], max_pos=best["mp"])
    print(f"\n##### BEST on TRAIN: {best['tag']} #####", flush=True)
    print(fmt("FULL  NET", best["full"]), flush=True)
    print(fmt("TRAIN NET", best["train"]), flush=True)
    print(fmt("TEST  NET (held-out, seen once)", best["test"]), flush=True)
    if paths:
        ap = np.array(paths)
        print(f"\nCPCV ({len(paths)} purged paths) ann%%: "
              f"median={np.median(ap):.2f}  mean={ap.mean():.2f}  "
              f"p25={np.percentile(ap,25):.2f}  p75={np.percentile(ap,75):.2f}"
              f"  %paths>0={ (ap>0).mean()*100:.0f}%%  "
              f"%paths>=20={ (ap>=20).mean()*100:.0f}%%", flush=True)
        cpsr = psr(ap / 100.0)
        cdsr = deflated_sharpe(ap / 100.0,
                               [g["full"]["ann"] / 100.0 for g in grid])
        print(f"CPCV-path Sharpe={sharpe(ap/100.0):.3f}  "
              f"PSR(vs0)={cpsr:.3f}  DeflatedSR(N={len(grid)})={cdsr:.3f}",
              flush=True)
        print("PASS" if cdsr >= 0.95 and np.median(ap) >= 20 else
              "FAIL: not a robust >=20%% edge after costs + multiple-testing",
              flush=True)


if __name__ == "__main__":
    main()
