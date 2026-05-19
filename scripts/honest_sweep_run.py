"""Run the honest rule-strategy sweep over the full OOS span.

Every printed line is a realistic account simulation (concurrency, cash cap,
fixed %-of-cash sizing, commission, conservative MAE-marked drawdown) on
leak-free measure_outcome replays (parity-verified vs the authoritative
resim port). No ML, no parameters fitted on the test data.

Default: confirmed_pullback signal table (honest_sweep.get_data).
  python scripts/honest_sweep_run.py
Arbitrary exported signals (any score mode):
  python scripts/honest_sweep_run.py --sig-csv tmp/ob_signals.csv \
      --sig-pkl /tmp/ob_sig.pkl --label oversold_bounce

Stages: (1) target/stop/hold grid  (2) pos_pct x max_pos
        (3) single filters  (4) time-split honesty (tune TRAIN, report TEST).
"""
import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from honest_sweep import (get_data, get_data_csv, fast_run_config as run_config,
                          portfolio, fmt)  # noqa: E402

TRAIN_END = "2023-06-30"
TEST_START = "2023-07-01"
START_CASH = 10000.0


def run_sweep(sig, px, label):
    print(f"\n##### SWEEP: {label}  signals={len(sig)}  "
          f"{sig['scan_date'].min().date()} -> {sig['scan_date'].max().date()} #####",
          flush=True)

    print("\n=== STAGE 1: target/stop/hold (pos_pct=0.25 max_pos=4) ===",
          flush=True)
    best = None
    grid = []
    for tm in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        for sm in (0.5, 0.75, 1.0, 1.5):
            for hold in (2, 3, 5, 8, 10, 15, 20):
                tr = run_config(sig, px, tm, sm, hold)
                p = portfolio(tr, START_CASH, pos_pct=0.25, max_pos=4)
                tag = f"t{tm}/s{sm}/h{hold}"
                grid.append((tag, tm, sm, hold, p))
                if best is None or p["ann"] > best[4]["ann"]:
                    best = (tag, tm, sm, hold, p)
    for tag, tm, sm, hold, p in sorted(grid, key=lambda x: -x[4]["ann"])[:12]:
        print(fmt(tag, p), flush=True)
    btag, btm, bsm, bhold, bp = best
    print(f"\nBEST stage1: {btag}\n{fmt(btag, bp)}", flush=True)

    print("\n=== STAGE 2: pos_pct x max_pos on best exit ===", flush=True)
    base_tr = run_config(sig, px, btm, bsm, bhold)
    shape = []
    for pp in (0.10, 0.15, 0.20, 0.25, 0.33, 0.50, 1.0):
        for mp in (1, 2, 3, 4, 5, 6, 8, 10):
            p = portfolio(base_tr, START_CASH, pos_pct=pp, max_pos=mp)
            shape.append((f"pp{pp}/mp{mp}", pp, mp, p))
    for tag, pp, mp, p in sorted(shape, key=lambda x: -x[3]["ann"])[:12]:
        print(fmt(tag, p), flush=True)
    s_tag, s_pp, s_mp, s_p = max(shape, key=lambda x: x[3]["ann"])
    print(f"\nBEST stage2: {s_tag}\n{fmt(s_tag, s_p)}", flush=True)

    print("\n=== STAGE 2R: same-day candidate ranking (best exit+shape) ===",
          flush=True)
    ranks = ["score:desc", "random", "rsi9:asc", "rsi9:desc", "rsi14:asc",
             "mfi14:asc", "atr_pct:asc", "atr_pct:desc", "dollar_vol20:desc",
             "rel_ret20_vs_spy:desc", "rel_ret20_vs_spy:asc", "adx14:desc",
             "roc10:asc", "pct_from_52w_high:asc"]
    rres = []
    for rk in ranks:
        p = portfolio(base_tr, START_CASH, pos_pct=s_pp, max_pos=s_mp, rank=rk)
        rres.append((rk, p))
    for rk, p in sorted(rres, key=lambda x: -x[1]["ann"]):
        print(fmt(rk, p), flush=True)
    best_rank = max(rres, key=lambda x: x[1]["ann"])[0]
    print(f"\nBEST rank: {best_rank}", flush=True)

    print("\n=== STAGE 3: filters (best exit + shape + rank) ===", flush=True)
    s = sig

    def num(col):
        return pd.to_numeric(s[col], errors="coerce") if col in s.columns \
            else pd.Series(float("nan"), index=s.index)

    F = {
        "none": pd.Series(True, index=s.index),
        "spy_bull": (s["spy_regime"].astype(str) == "bull")
        if "spy_regime" in s.columns else pd.Series(False, index=s.index),
        "spy_not_bear": (s["spy_regime"].astype(str) != "bear")
        if "spy_regime" in s.columns else pd.Series(True, index=s.index),
        "relrs>=0": num("rel_ret20_vs_spy") >= 0,
        "relrs>=-.02": num("rel_ret20_vs_spy") >= -0.02,
        "spy5>-.01": num("spy_ret5") > -0.01,
        "voldry<.85": num("vol_ratio_20d") < 0.85,
        "adx>20": num("adx14") > 20,
        "near52w": num("pct_from_52w_high") > -0.15,
        "atrpct<.04": num("atr_pct") < 0.04,
        "dvol>2m": num("dollar_vol20") > 2_000_000,
        "rsi9<35": num("rsi9") < 35,
    }
    filt = []
    for name, m in F.items():
        m = m.fillna(False)
        tr = run_config(sig, px, btm, bsm, bhold, mask=m.values)
        p = portfolio(tr, START_CASH, pos_pct=s_pp, max_pos=s_mp, rank=best_rank)
        filt.append((name, m, p))
    for name, m, p in sorted(filt, key=lambda x: -x[2]["ann"]):
        print(fmt(name, p), flush=True)

    print("\n=== STAGE 4: time-split honesty (tune TRAIN, report TEST) ===",
          flush=True)
    print(f"TRAIN <= {TRAIN_END}   TEST >= {TEST_START}", flush=True)
    train_m = sig["scan_date"] <= TRAIN_END
    test_m = sig["scan_date"] >= TEST_START
    ranked = [n for n, _, _ in sorted(filt, key=lambda x: -x[2]["ann"])
              if n != "none"][:3]
    combos = [("none", pd.Series(True, index=sig.index))]
    for r in range(1, len(ranked) + 1):
        for cc in itertools.combinations(ranked, r):
            mm = pd.Series(True, index=sig.index)
            for nm in cc:
                mm = mm & F[nm].fillna(False)
            combos.append(("+".join(cc), mm))
    for name, mm in combos:
        for lbl, span in (("TRAIN", train_m), ("TEST", test_m)):
            tr = run_config(sig, px, btm, bsm, bhold, mask=(mm & span).values)
            p = portfolio(tr, START_CASH, pos_pct=s_pp, max_pos=s_mp, rank=best_rank)
            print(fmt(f"{lbl} {name}", p), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sig-csv", default=None)
    ap.add_argument("--sig-pkl", default=None)
    ap.add_argument("--label", default="confirmed_pullback")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.sig_csv:
        sig, px = get_data_csv(a.sig_csv, a.sig_pkl or "/tmp/honest_sig_alt.pkl",
                               rebuild=a.rebuild)
    else:
        sig, px = get_data(rebuild=a.rebuild)
    run_sweep(sig, px, a.label)
