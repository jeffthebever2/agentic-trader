"""Sweep 2: tight-target / longer-hold region + 2-3 filter combos.

Breakeven WR for (tgt,stop): WR* = stop/(tgt+stop). Profit needs WR>WR*.
Focus where WR* is reachable AND high WR is plausible (tiny tgt, long hold).
"""
import pickle
import itertools
import numpy as np
import pandas as pd
from resim import run_config, portfolio

S = pd.read_pickle("/tmp/sigtab.pkl")
with open("/tmp/px.pkl", "rb") as _f:
    px = pickle.load(_f)
print(f"signals: {len(S)}", flush=True)

base = {
 "rsi44_50": ((S.rsi9>=44)&(S.rsi9<=50)).values,
 "adx>18":   (S.adx14>18).values,
 "closeloc>0.55": (S.close_loc>0.55).values,
 "body>0":   (S.body_pct>0).values,
 "cmf>0":    (S.cmf20>0).values,
 "relrs>=-.02": (S.rel_ret20_vs_spy>=-0.02).values,
 "mfi>50":   (S.mfi14>50).values,
 "spy5>-.01":(S.spy_ret5>-0.01).values,
 "macdslope+": ((S.macd_hist-S.macd_hist_prev2)>0).values,
 "stoch30_60": ((S.stoch_k>=30)&(S.stoch_k<=60)).values,
 "vol<.9":   (S.vol_ratio_20d<0.9).values,
 "rr52w>.5": (S.range_rank_52w>0.5).values,
}
keys = list(base)

TS = [(0.20,1.0),(0.25,1.0),(0.30,1.0),(0.35,1.0),
      (0.25,1.25),(0.30,1.25),(0.40,1.25),
      (0.30,1.5),(0.40,1.5),(0.50,1.5)]
HOLDS = [5, 7, 10, 15, 20]

def wrstar(t,s): return s/(t+s)*100

best = []
goal = []
# single + pair + triple filter combos
combos = [()]
combos += [(k,) for k in keys]
combos += list(itertools.combinations(keys,2))
combos += list(itertools.combinations(keys,3))
print(f"combos={len(combos)}  configs/combo={len(TS)*len(HOLDS)}", flush=True)

for combo in combos:
    m = np.ones(len(S),bool)
    for k in combo: m &= base[k]
    if m.sum() < 150:   # need volume headroom (portfolio drops some)
        continue
    for hold in HOLDS:
        for tgt,stop in TS:
            tr = run_config(S, px, tgt, stop, hold, mask=m)
            if tr.empty or len(tr) < 150:
                continue
            wr = tr["win"].mean()*100
            if wr < 78:
                continue
            pf = portfolio(tr, start=10000.0, pos_pct=0.25, max_pos=5)
            tag = f"{'+'.join(combo) or 'all'} | t{tgt} s{stop} h{hold}"
            rec = (wr, len(tr), pf["n"], pf["profit"], wrstar(tgt,stop), tag, pf.get("monthly"))
            best.append(rec)
            if wr>=80 and pf["n"]>=150 and pf["profit"]>=1000:
                goal.append(rec)

best.sort(key=lambda r:(-r[0],-r[3]))
print("\nTop 30 by WR (raw n>=150, WR>=78):", flush=True)
print(f"{'WR%':>5}{'rawN':>6}{'pfN':>5}{'prof$':>8}{'WR*':>6}  cfg", flush=True)
for wr,rn,pn,pr,ws,tag,mo in best[:30]:
    flag = '  <==GOAL' if (wr>=80 and pn>=150 and pr>=1000) else ''
    print(f"{wr:>5.1f}{rn:>6}{pn:>5}{pr:>+8.0f}{ws:>6.1f}  {tag}{flag}", flush=True)

print("\n=== GOAL CONFIGS ===", flush=True)
for wr,rn,pn,pr,ws,tag,mo in goal:
    print(f"{tag}  WR={wr:.1f}% pfN={pn} profit=${pr:+.0f} monthly={mo}", flush=True)
if not goal:
    print("none — analyze top WR rows for next step", flush=True)
