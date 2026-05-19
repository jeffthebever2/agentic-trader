"""Disciplined refinement of the one OOS-robust family found by panel_run:
oversold + uptrend (above SMA200, near/above SMA50), liquid universe.

Anti-overfit protocol (strict, single hold-out):
  - The grid is selected ENTIRELY on the TRAIN span (<= 2023-06-30).
  - The single best TRAIN config (by annualized return, with a trade-count
    floor) is then run ONCE on the UNTOUCHED TEST span (>= 2023-07-01).
  - TEST is looked at exactly once, for one config — no ranking against
    TEST, so the reported out-of-sample number is not multiple-testing
    contaminated. ALL-period is shown for context only.
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
    px = pickle.load(open(P.PX_PKL, "rb"))
    p = P.build_panel()
    # Reduce to the oversold-uptrend candidate envelope so build_sig stays
    # light, then rebuild masks on the reduced frame.
    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    env = (rsi14 < 40) & (mfi14 < 45) & (sma200 > -0.02)
    p = p[env.fillna(False)].reset_index(drop=True)
    print(f"oversold-uptrend envelope rows={len(p)} "
          f"{p['scan_date'].min().date()}->{p['scan_date'].max().date()}",
          flush=True)
    P.build_sig(p, px)

    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    rsi9 = pd.to_numeric(p["rsi9"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma50 = pd.to_numeric(p["sma50_dist"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    macd = pd.to_numeric(p["macd_hist"], errors="coerce")
    macd2 = pd.to_numeric(p["macd_hist_prev2"], errors="coerce")
    voldry = pd.to_numeric(p["vol_ratio_20d"], errors="coerce")
    relrs = pd.to_numeric(p["rel_ret20_vs_spy"], errors="coerce")
    train = p["scan_date"] <= TRAIN_END
    test = p["scan_date"] >= TEST_START

    train_results = []
    n = 0
    for r14 in (30, 33, 35, 38):
        for m14 in (30, 35, 40):
            for s50 in (-0.10, -0.05, 0.0):
                for macd_up in (False, True):
                    for dry in (False, True):
                        base = ((rsi14 < r14) & (mfi14 < m14)
                                & (sma200 > 0) & (sma50 > s50))
                        if macd_up:
                            base = base & (macd > macd2)
                        if dry:
                            base = base & (voldry < 0.95)
                        base = base.fillna(False)
                        if int(base.sum()) < 120:
                            continue
                        for tm in (1.5, 2.0, 2.5, 3.0):
                            for sm in (1.0, 1.5, 2.0):
                                for hold in (10, 15, 20, 25):
                                    for pp, mp in ((1.0, 1), (1.0, 4),
                                                   (0.5, 2), (0.5, 4)):
                                        n += 1
                                        a, _ = P.evaluate(
                                            p, px,
                                            (base & train).fillna(False),
                                            tm, sm, hold, pos_pct=pp,
                                            max_pos=mp, rank="rsi9:asc")
                                        # selection uses TRAIN only.
                                        if a["n"] < 50:
                                            continue
                                        train_results.append({
                                            "base": base,
                                            "rule": f"r14<{r14}&m14<{m14}&"
                                            f"sma200>0&sma50>{s50}"
                                            f"{'&macd_up' if macd_up else ''}"
                                            f"{'&dry' if dry else ''}",
                                            "tm": tm, "sm": sm, "hold": hold,
                                            "pp": pp, "mp": mp,
                                            "exit": f"t{tm}/s{sm}/h{hold}",
                                            "shape": f"pp{pp}/mp{mp}/rsi9:asc",
                                            "train": a,
                                        })
    train_results.sort(key=lambda x: -x["train"]["ann"])
    print(f"configs evaluated on TRAIN={n}  "
          f"with>=50 train trades={len(train_results)}", flush=True)
    print("\nTop 10 by TRAIN annualized (selection only — TEST not peeked):",
          flush=True)
    for r in train_results[:10]:
        print(f"  {r['rule']} {r['exit']} {r['shape']}: "
              + P.fmt("TRAIN", r["train"]), flush=True)
    if not train_results:
        print("no qualifying config", flush=True)
        return
    # ONE config: best on TRAIN. Look at TEST exactly once.
    best = train_results[0]
    tb, _ = P.evaluate(p, px,
                       (best["base"] & test).fillna(False),
                       best["tm"], best["sm"], best["hold"],
                       pos_pct=best["pp"], max_pos=best["mp"],
                       rank="rsi9:asc")
    ab, _ = P.evaluate(p, px, best["base"], best["tm"], best["sm"],
                       best["hold"], pos_pct=best["pp"], max_pos=best["mp"],
                       rank="rsi9:asc")
    print("\n##### SINGLE HELD-OUT VERDICT (best-on-TRAIN, TEST seen once) "
          "#####", flush=True)
    print(f"rule={best['rule']} exit={best['exit']} shape={best['shape']}",
          flush=True)
    print("TRAIN " + P.fmt("", best["train"]), flush=True)
    print("TEST  " + P.fmt("", tb), flush=True)
    print("ALL   " + P.fmt("", ab), flush=True)


if __name__ == "__main__":
    main()
