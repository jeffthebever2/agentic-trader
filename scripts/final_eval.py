"""Net-of-cost re-evaluation of the prior best configs.

panel.evaluate -> honest_sweep.portfolio now applies Roll-spread + slippage
+ commission by default. This re-prices the configs that looked best GROSS
so the reported numbers are honest NET figures.
"""
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel as P  # noqa: E402
from honest_sweep import fmt  # noqa: E402

TRAIN_END = pd.Timestamp("2023-06-30")
TEST_START = pd.Timestamp("2023-07-01")
FRICTIONLESS = {"on": False}


def main():
    px = pickle.load(open(P.PX_PKL, "rb"))
    p = P.build_panel()
    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")
    env = (rsi14 < 40) & (mfi14 < 45) & (sma200 > -0.02)
    p = p[env.fillna(False)].reset_index(drop=True)
    P.build_sig(p, px)
    rsi14 = pd.to_numeric(p["rsi14"], errors="coerce")
    mfi14 = pd.to_numeric(p["mfi14"], errors="coerce")
    sma50 = pd.to_numeric(p["sma50_dist"], errors="coerce")
    sma200 = pd.to_numeric(p["sma200_dist"], errors="coerce")

    configs = {
        "panel_run_best oversold_up50_200 t2.0/s1.5/h15 pp1.0/mp1 rsi9:asc": (
            (rsi14 < 35) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
            2.0, 1.5, 15, 1.0, 1),
        "refine2_TRAINbest r14<35&m14<35 t4.0/s2.0/h25 pp1.0/mp1 rsi9:asc": (
            (rsi14 < 35) & (mfi14 < 35) & (sma200 > 0) & (sma50 > -0.05),
            4.0, 2.0, 25, 1.0, 1),
    }
    for name, (mask, tm, sm, hold, pp, mp) in configs.items():
        mask = mask.fillna(False)
        print(f"\n##### {name} #####", flush=True)
        for lbl, span, costs in (
            ("ALL  gross", pd.Series(True, index=p.index), FRICTIONLESS),
            ("ALL  NET  ", pd.Series(True, index=p.index), None),
            ("TRAIN NET ", p["scan_date"] <= TRAIN_END, None),
            ("TEST  NET ", p["scan_date"] >= TEST_START, None),
        ):
            res, _ = P.evaluate(p, px, (mask & span).fillna(False),
                                tm, sm, hold, pos_pct=pp, max_pos=mp,
                                rank="rsi9:asc", costs=costs)
            print("  " + fmt(lbl, res), flush=True)


if __name__ == "__main__":
    main()
