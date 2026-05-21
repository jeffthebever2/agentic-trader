"""Map the true WR/profit frontier with the real simulator. No filters except
optional rsi44_50. Print full grid so we see the ceiling."""
import pickle
import numpy as np
import pandas as pd
from resim import run_config, portfolio

S = pd.read_pickle("/tmp/sigtab.pkl")
px = pickle.load(open("/tmp/px.pkl","rb"))

masks = {
 "ALL": np.ones(len(S),bool),
 "rsi44_50": ((S.rsi9>=44)&(S.rsi9<=50)).values,
 "rsi44_50+cmf>0": ((S.rsi9>=44)&(S.rsi9<=50)&(S.cmf20>0)).values,
}
TGT  = [0.10,0.15,0.20,0.25,0.30,0.40,0.50]
STOP = [0.5,0.75,1.0,1.5,2.0,3.0]
HOLD = [3,5,10,20]

for mn,mm in masks.items():
    print(f"\n###### {mn}  (base n={mm.sum()}) ######", flush=True)
    print(f"{'h':>3}{'tgt':>6}{'stop':>6}{'WR%':>7}{'n':>5}{'avgR%':>8}{'prof$':>9}{'WR*':>6}", flush=True)
    for h in HOLD:
        for t in TGT:
            for s in STOP:
                tr = run_config(S,px,t,s,h,mask=mm)
                if tr.empty: continue
                wr = tr.win.mean()*100
                n = len(tr)
                avg = tr.ret.mean()*100
                pf = portfolio(tr,10000,0.25,5)
                ws = s/(t+s)*100
                if wr>=75 or (n>=150 and pf['profit']>=500):
                    print(f"{h:>3}{t:>6.2f}{s:>6.2f}{wr:>6.1f}{n:>5}{avg:>+8.3f}{pf['profit']:>+9.0f}{ws:>6.1f}"
                          f"{'  WR>=80' if wr>=80 else ''}{' GOAL' if (wr>=80 and pf['n']>=150 and pf['profit']>=1000) else ''}", flush=True)
