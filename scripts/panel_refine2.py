"""Tight, fast refinement of the OOS-robust oversold-uptrend family.

Fixes the winning shape/rank from panel_run (concentrated, deepest-oversold
first) and re-optimises only the exit + a few entry-threshold variants.

Strict single hold-out: select the SINGLE best config on TRAIN only, then
evaluate that one config on the UNTOUCHED TEST span exactly once. ALL shown
for context. No ranking against TEST -> no multiple-testing contamination.
"""
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel as P  # noqa: E402

TRAIN_END = pd.Timestamp("2023-06-30")
TEST_START = pd.Timestamp("2023-07-01")


def main():
    with open(P.PX_PKL, "rb") as _f:
        px = pickle.load(_f)
    p = P.build_panel()
    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    env = (rsi14 < 40) & (mfi14 < 45) & (sma200 > -0.02)
    p = p[env.fillna(False)].reset_index(drop=True)
    print(f"envelope rows={len(p)} "
          f"{p['scan_date'].min().date()}->{p['scan_date'].max().date()}",
          flush=True)
    P.build_sig(p, px)

    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma50 = pd.to_numeric(p["sma50_dist"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    train = p["scan_date"] <= TRAIN_END
    test = p["scan_date"] >= TEST_START

    entries = {
        "r14<35&m14<35&sma200>0&sma50>-.05":
            (rsi14 < 35) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
        "r14<33&m14<35&sma200>0&sma50>-.05":
            (rsi14 < 33) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
        "r14<35&m14<40&sma200>0&sma50>-.10":
            (rsi14 < 35) & (mfi14 < 40) & (sma200 > 0) & (sma50 > -0.10),
        "r14<30&m14<30&sma200>0&sma50>-.05":
            (rsi14 < 30) & (mfi14 < 30) & (sma200 > 0) & (sma50 > -0.05),
        "r14<38&m14<40&sma200>0&sma50>0":
            (rsi14 < 38) & (mfi14 < 40) & (sma200 > 0) & (sma50 > 0),
    }
    shapes = [(1.0, 1), (1.0, 2), (0.5, 2)]
    trials = []
    for ename, emask in entries.items():
        emask = emask.fillna(False)
        for tm in (1.5, 2.0, 2.5, 3.0, 4.0):
            for sm in (0.75, 1.0, 1.5, 2.0):
                for hold in (8, 10, 15, 20, 25, 30):
                    for pp, mp in shapes:
                        a, _ = P.evaluate(p, px,
                                          (emask & train).fillna(False),
                                          tm, sm, hold, pos_pct=pp,
                                          max_pos=mp, rank="rsi9:asc")
                        if a["n"] < 50:
                            continue
                        trials.append({
                            "emask": emask, "ename": ename,
                            "tm": tm, "sm": sm, "hold": hold,
                            "pp": pp, "mp": mp,
                            "exit": f"t{tm}/s{sm}/h{hold}",
                            "shape": f"pp{pp}/mp{mp}/rsi9:asc",
                            "train": a,
                        })
    trials.sort(key=lambda x: -x["train"]["ann"])
    print(f"qualifying TRAIN configs={len(trials)}", flush=True)
    print("\nTop 12 by TRAIN (selection only):", flush=True)
    for r in trials[:12]:
        print(f"  {r['ename']} {r['exit']} {r['shape']}: "
              + P.fmt("TRAIN", r["train"]), flush=True)
    if not trials:
        print("no qualifying config", flush=True)
        return
    best = trials[0]
    tb, _ = P.evaluate(p, px, (best["emask"] & test).fillna(False),
                       best["tm"], best["sm"], best["hold"],
                       pos_pct=best["pp"], max_pos=best["mp"],
                       rank="rsi9:asc")
    ab, _ = P.evaluate(p, px, best["emask"], best["tm"], best["sm"],
                       best["hold"], pos_pct=best["pp"], max_pos=best["mp"],
                       rank="rsi9:asc")
    print("\n##### SINGLE HELD-OUT VERDICT (best-on-TRAIN, TEST seen once) "
          "#####", flush=True)
    print(f"rule={best['ename']} exit={best['exit']} shape={best['shape']}",
          flush=True)
    print("TRAIN " + P.fmt("", best["train"]), flush=True)
    print("TEST  " + P.fmt("", tb), flush=True)
    print("ALL   " + P.fmt("", ab), flush=True)


if __name__ == "__main__":
    main()
