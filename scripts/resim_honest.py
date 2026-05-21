"""HONEST evaluation. Rules to kill inflated stats:
  - target_mult >= 0.75 ATR  (a win must beat spread+commission, be real money)
  - stop_mult   <= 1.5 ATR   (no noise-scalp giant stops)
  - apply per-trade cost: commission $1 + slippage 0.05% each side
  - win = realized PnL after costs > 0  (not 'touched a tick above entry')
Report true frontier: max honest WR, and best honest profit, with n.
"""
import pickle
import numpy as np
import pandas as pd
from resim import run_config, portfolio

S = pd.read_pickle("/tmp/sigtab.pkl")
px = pickle.load(open("/tmp/px.pkl","rb"))
COST_PCT = 0.0010  # 0.05% slippage each side, round trip ~0.10%

fil = {
 "ALL": np.ones(len(S),bool),
 "rsi42_52": ((S.rsi9>=42)&(S.rsi9<=52)).values,
 "rsi44_50": ((S.rsi9>=44)&(S.rsi9<=50)).values,
 "rsi44_50+cmf>0": ((S.rsi9>=44)&(S.rsi9<=50)&(S.cmf20>0)).values,
 "rsi44_50+cmf>0+adx>18": ((S.rsi9>=44)&(S.rsi9<=50)&(S.cmf20>0)&(S.adx14>18)).values,
 "rsi44_50+relrs>=0": ((S.rsi9>=44)&(S.rsi9<=50)&(S.rel_ret20_vs_spy>=0)).values,
 "rsi44_50+cmf>0+relrs>=0": ((S.rsi9>=44)&(S.rsi9<=50)&(S.cmf20>0)&(S.rel_ret20_vs_spy>=0)).values,
}
# economically real targets/stops only
TS = [(0.75,1.0),(1.0,1.0),(1.0,1.5),(1.25,1.0),(1.5,1.0),(1.5,1.5),
      (0.75,0.75),(1.0,0.75),(1.5,0.75),(2.0,1.0),(2.0,1.5)]
HOLD = [3,5,10]

def honest(tr):
    """recompute win on cost-adjusted realized return."""
    r = tr["ret"].values - 2*COST_PCT
    w = r > 0
    return w, r

rows=[]
for ename in ("trigger_break","next_open"):
    for cn,cm in fil.items():
        for h in HOLD:
            for t,s in TS:
                tr=run_config(S,px,t,s,h,mask=cm,entry_timing=ename)
                if tr.empty or len(tr)<100: continue
                w,r = honest(tr)
                wr=w.mean()*100
                n=len(tr)
                # portfolio with costs baked into ret
                tr2=tr.copy(); tr2["ret"]=r; tr2["win"]=w
                pf=portfolio(tr2,10000,0.20,6)
                rows.append((wr,n,pf['n'],pf['profit'],r.mean()*100,ename,cn,t,s,h,pf.get('monthly')))

# best honest WR (n>=120) and best honest profit (n>=150)
rows.sort(key=lambda x:-x[0])
print("=== TOP honest WR (cost-adjusted, real targets), n>=120 ===", flush=True)
print(f"{'WR%':>5}{'rawN':>6}{'pfN':>5}{'prof$':>8}{'avgR%':>7}  entry filter t/s/h", flush=True)
for wr,n,pn,pr,ar,en,cn,t,s,h,mo in [r for r in rows if r[1]>=120][:15]:
    print(f"{wr:>5.1f}{n:>6}{pn:>5}{pr:>+8.0f}{ar:>+7.2f}  {en[:4]} {cn} t{t} s{s} h{h}", flush=True)

rows.sort(key=lambda x:-x[3])
print("\n=== TOP honest PROFIT, n(pf)>=150 ===", flush=True)
for wr,n,pn,pr,ar,en,cn,t,s,h,mo in [r for r in rows if r[2]>=150][:15]:
    print(f"{wr:>5.1f}{n:>6}{pn:>5}{pr:>+8.0f}{ar:>+7.2f}  {en[:4]} {cn} t{t} s{s} h{h}  {mo}", flush=True)

g=[r for r in rows if r[0]>=80 and r[2]>=150 and r[3]>=1000]
print(f"\nHONEST goal hits (WR>=80 & pfN>=150 & profit>=1000): {len(g)}", flush=True)
for wr,n,pn,pr,ar,en,cn,t,s,h,mo in g[:10]:
    print(f"  {wr:.1f}% n={pn} ${pr:+.0f} avgR={ar:+.2f}% {en} {cn} t{t} s{s} h{h} {mo}", flush=True)
