"""Find n>=150 & WR>=80 & profit>=1000. Test next_open entry (fills every
signal -> ~2x trades) + filter loosening around the rsi44_50+cmf winning region."""
import pickle, itertools
import numpy as np, pandas as pd
from resim import run_config, portfolio

S = pd.read_pickle("/tmp/sigtab.pkl")
px = pickle.load(open("/tmp/px.pkl","rb"))
print(f"signals: {len(S)}", flush=True)

# Quality filters known to lift WR; test loosened variants for volume
fil = {
 "rsi42_52": ((S.rsi9>=42)&(S.rsi9<=52)).values,
 "rsi40_54": ((S.rsi9>=40)&(S.rsi9<=54)).values,
 "rsi44_50": ((S.rsi9>=44)&(S.rsi9<=50)).values,
 "cmf>0":    (S.cmf20>0).values,
 "cmf>-.05": (S.cmf20>-0.05).values,
 "mfi>45":   (S.mfi14>45).values,
 "body>-.1": (S.body_pct>-0.1).values,
 "relrs>=-.03": (S.rel_ret20_vs_spy>=-0.03).values,
}
combos = [
 ("rsi44_50+cmf>0", fil["rsi44_50"]&fil["cmf>0"]),
 ("rsi42_52+cmf>0", fil["rsi42_52"]&fil["cmf>0"]),
 ("rsi40_54+cmf>0", fil["rsi40_54"]&fil["cmf>0"]),
 ("rsi42_52+cmf>-.05", fil["rsi42_52"]&fil["cmf>-.05"]),
 ("rsi40_54+cmf>-.05", fil["rsi40_54"]&fil["cmf>-.05"]),
 ("rsi42_52", fil["rsi42_52"]),
 ("rsi40_54", fil["rsi40_54"]),
 ("rsi42_52+mfi>45", fil["rsi42_52"]&fil["mfi>45"]),
 ("rsi40_54+relrs>=-.03", fil["rsi40_54"]&fil["relrs>=-.03"]),
 ("ALL", np.ones(len(S),bool)),
]
TS = [(0.20,1.5),(0.20,2.0),(0.25,1.5),(0.25,2.0),(0.25,3.0),(0.30,2.0),(0.30,3.0),(0.15,2.0)]
HOLD = [10,15,20]

rows=[]
for ename in ("trigger_break","next_open"):
    for cname,cm in combos:
        base_n=cm.sum()
        for h in HOLD:
            for t,s in TS:
                tr=run_config(S,px,t,s,h,mask=cm,entry_timing=ename)
                if tr.empty or len(tr)<120: continue
                wr=tr.win.mean()*100
                pf=portfolio(tr,10000,0.20,6)
                if wr>=79:
                    goal = wr>=80 and pf['n']>=150 and pf['profit']>=1000
                    rows.append((wr,len(tr),pf['n'],pf['profit'],ename,cname,t,s,h,goal,pf.get('monthly')))

rows.sort(key=lambda r:(-(r[9]),-r[0],-r[3]))
print(f"{'WR%':>5}{'rawN':>6}{'pfN':>5}{'prof$':>8}  entry  filter  cfg", flush=True)
for wr,rn,pn,pr,en,cn,t,s,h,goal,mo in rows[:40]:
    print(f"{wr:>5.1f}{rn:>6}{pn:>5}{pr:>+8.0f}  {en[:4]}  {cn} t{t} s{s} h{h}{'  <==GOAL '+str(mo) if goal else ''}", flush=True)
print("\nGOAL hits:", sum(1 for r in rows if r[9]), flush=True)
