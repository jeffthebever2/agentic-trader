"""Honest rule search on the liquid feature panel.

Searches entry families x exit (target/stop/hold) x portfolio shape x
same-day ranking, all leak-free (measure_outcome on post-signal bars only),
realistic account sim, plus an untouched time-split honesty check.

  python scripts/panel_run.py 2>&1 | tee tmp/panel_sweep.log
"""
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel as P  # noqa: E402

TRAIN_END = pd.Timestamp("2023-06-30")
TEST_START = pd.Timestamp("2023-07-01")


def rules(p):
    r = {}
    ex = p["candidate_status"] == "executed"
    rsi14 = p["rsi14"]; mfi14 = p["mfi14"]; rsi9 = p["rsi9"]
    sma50 = p["sma50_dist"]; sma200 = p["sma200_dist"]
    reg = p["spy_regime"].astype(str); vix = p["vix_regime"].astype(str)
    relrs = p["rel_ret20_vs_spy"]; spy5 = p["spy_ret5"]
    adx = p["adx14"]
    r["cp_executed"] = ex
    r["oversold_basic"] = (rsi14 < 35) & (mfi14 < 35)
    r["oversold_deep"] = (rsi14 < 28) & (mfi14 < 28)
    r["oversold_up200"] = (rsi14 < 35) & (mfi14 < 35) & (sma200 > 0)
    r["oversold_bull"] = (rsi14 < 35) & (mfi14 < 35) & (reg == "bull")
    r["oversold_bull_up200"] = ((rsi14 < 35) & (mfi14 < 35) & (reg == "bull")
                                & (sma200 > 0))
    r["oversold_notbear"] = (rsi14 < 35) & (mfi14 < 35) & (reg != "bear")
    r["oversold_up200_notbear"] = ((rsi14 < 35) & (mfi14 < 35) & (sma200 > 0)
                                   & (reg != "bear"))
    r["oversold_up50_200"] = ((rsi14 < 35) & (mfi14 < 35) & (sma200 > 0)
                              & (sma50 > -0.05))
    r["oversold_relrs"] = (rsi14 < 35) & (mfi14 < 35) & (relrs > -0.05)
    r["oversold_spyok"] = (rsi14 < 35) & (mfi14 < 35) & (spy5 > -0.03)
    r["oversold_liqtrend"] = ((rsi14 < 35) & (mfi14 < 35) & (sma200 > 0)
                              & (reg != "bear") & (spy5 > -0.03))
    return {k: v.fillna(False) for k, v in r.items()}


def main():
    px = pickle.load(open(P.PX_PKL, "rb"))
    p = P.build_panel()
    print(f"panel rows={len(p)} "
          f"{p['scan_date'].min().date()}->{p['scan_date'].max().date()} "
          f"tickers={p['ticker'].nunique()}", flush=True)
    R0 = rules(p)
    union = pd.Series(False, index=p.index)
    for v in R0.values():
        union = union | v
    p = p[union].reset_index(drop=True)
    print(f"reduced to candidate rows={len(p)} (union of all rules)",
          flush=True)
    print("locating bars + building fast windows (one-time)...", flush=True)
    P.build_sig(p, px)  # warm pos cache + fast replay windows
    R = rules(p)

    # STAGE A: each rule, default exit/shape, count + headline.
    print("\n=== STAGE A: entry rules (t0.75/s1.0/h10, pp0.20/mp6, "
          "rank score:desc) ===", flush=True)
    base = {}
    for name, m in R.items():
        res, tr = P.evaluate(p, px, m, 0.75, 1.0, 10,
                             pos_pct=0.20, max_pos=6, rank="score:desc")
        base[name] = (res, int(m.sum()))
        print(P.fmt(f"{name}(cand={int(m.sum())})", res), flush=True)

    top = sorted(base, key=lambda k: -base[k][0]["ann"])[:3]
    print(f"\nTOP rules: {top}", flush=True)

    # STAGE B: exit grid on the best rule.
    best_rule = top[0]
    m = R[best_rule]
    print(f"\n=== STAGE B: target/stop/hold on {best_rule} "
          f"(pp0.20/mp6) ===", flush=True)
    grid = []
    for tm in (0.5, 0.75, 1.0, 1.5, 2.0):
        for sm in (0.5, 0.75, 1.0, 1.5):
            for hold in (2, 3, 5, 8, 10, 15):
                res, _ = P.evaluate(p, px, m, tm, sm, hold,
                                    pos_pct=0.20, max_pos=6)
                grid.append((f"t{tm}/s{sm}/h{hold}", tm, sm, hold, res))
    for tag, tm, sm, h, res in sorted(grid, key=lambda x: -x[4]["ann"])[:12]:
        print(P.fmt(tag, res), flush=True)
    btag, btm, bsm, bh, _ = max(grid, key=lambda x: x[4]["ann"])
    print(f"\nBEST exit: {btag}", flush=True)

    # STAGE C: shape x rank on best rule+exit.
    print(f"\n=== STAGE C: shape x rank ({best_rule} {btag}) ===", flush=True)
    sc = []
    for pp in (0.10, 0.20, 0.33, 0.50, 1.0):
        for mp in (1, 2, 3, 4, 6, 10):
            for rk in ("score:desc", "rsi14:asc", "mfi14:asc",
                       "rsi9:asc", "random"):
                res, _ = P.evaluate(p, px, m, btm, bsm, bh,
                                    pos_pct=pp, max_pos=mp, rank=rk)
                sc.append((f"pp{pp}/mp{mp}/{rk}", pp, mp, rk, res))
    for tag, pp, mp, rk, res in sorted(sc, key=lambda x: -x[4]["ann"])[:15]:
        print(P.fmt(tag, res), flush=True)
    btag2, bpp, bmp, brk, _ = max(sc, key=lambda x: x[4]["ann"])
    print(f"\nBEST shape+rank: {btag2}", flush=True)

    # STAGE D: time-split honesty on the final config.
    print(f"\n=== STAGE D: time split ({best_rule} {btag} {btag2}) ===",
          flush=True)
    for lbl, span in (("TRAIN", p["scan_date"] <= TRAIN_END),
                      ("TEST", p["scan_date"] >= TEST_START),
                      ("ALL", pd.Series(True, index=p.index))):
        res, _ = P.evaluate(p, px, (m & span).fillna(False), btm, bsm, bh,
                            pos_pct=bpp, max_pos=bmp, rank=brk)
        print(P.fmt(f"{lbl}", res), flush=True)


if __name__ == "__main__":
    main()
