"""Sweep target/stop/hold + quality filters on cached signal table.

Uses the EXACT measure_outcome simulator (real bars, real same-bar tiebreak).
No inflation. Goal: WR>=80%, trades>=150, profit>=$1000 over Nov14-May14.
"""
import pickle
import numpy as np
import pandas as pd
from resim import run_config, portfolio

sigtab = pd.read_pickle("/tmp/sigtab.pkl")
px = pickle.load(open("/tmp/px.pkl", "rb"))
print(f"signals: {len(sigtab)}", flush=True)

S = sigtab
F = {
    "all": np.ones(len(S), bool),
    "rsi44_50": ((S.rsi9 >= 44) & (S.rsi9 <= 50)).values,
    "rsi46_54": ((S.rsi9 >= 46) & (S.rsi9 <= 54)).values,
    "adx>18": (S.adx14 > 18).values,
    "adx>22": (S.adx14 > 22).values,
    "closeloc>0.5": (S.close_loc > 0.5).values,
    "closeloc>0.6": (S.close_loc > 0.6).values,
    "body>0": (S.body_pct > 0).values,
    "cmf>0": (S.cmf20 > 0).values,
    "mfi>50": (S.mfi14 > 50).values,
    "relrs>=0": (S.rel_ret20_vs_spy >= 0).values,
    "relrs>=-.02": (S.rel_ret20_vs_spy >= -0.02).values,
    "spy5>-.01": (S.spy_ret5 > -0.01).values,
    "vol<.85": (S.vol_ratio_20d < 0.85).values,
    "atr1_4": ((S.atr_pct >= 0.01) & (S.atr_pct <= 0.04)).values,
    "stoch30_60": ((S.stoch_k >= 30) & (S.stoch_k <= 60)).values,
    "macdslope+": ((S.macd_hist - S.macd_hist_prev2) > 0).values,
    "rr52w>.55": (S.range_rank_52w > 0.55).values,
}

TS = [(0.30,1.0),(0.40,1.0),(0.50,1.0),(0.30,0.7),(0.40,0.7),(0.50,0.7),
      (0.25,0.5),(0.35,0.5),(0.50,0.5),(0.60,0.7),(0.75,1.0),(0.50,0.8)]
HOLDS = [1, 2, 3, 5]

hits = []
print(f"\n{'cfg':<46}{'WR%':>6}{'n':>5}{'profit$':>9}", flush=True)
for fname, fmask in F.items():
    for hold in HOLDS:
        for tgt, stop in TS:
            tr = run_config(S, px, tgt, stop, hold, mask=fmask)
            if tr.empty:
                continue
            wr = tr["win"].mean() * 100
            n = len(tr)
            if n < 100:
                continue
            pf = portfolio(tr, start=10000.0, pos_pct=0.25, max_pos=4)
            tag = f"{fname} t{tgt} s{stop} h{hold}"
            ok = wr >= 80 and pf["n"] >= 150 and pf["profit"] >= 1000
            if wr >= 70 or ok:
                print(f"{tag:<46}{wr:>5.1f}{n:>5}{pf['profit']:>+9.0f}"
                      f"{'  <== GOAL' if ok else ''}", flush=True)
            if ok:
                hits.append((tag, wr, pf["n"], pf["profit"], pf.get("monthly")))

print("\n=== GOAL-MEETING CONFIGS ===", flush=True)
for tag, wr, n, profit, monthly in hits:
    print(f"{tag}: WR={wr:.1f}% trades={n} profit=${profit:+.0f} monthly={monthly}", flush=True)
if not hits:
    print("none yet — widen sweep", flush=True)
