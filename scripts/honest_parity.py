"""Parity check: fast_run_config must produce IDENTICAL fills to the
authoritative slow run_config (verbatim resim.measure_outcome port).

If this fails, the fast path is not trustworthy and must not be used for
reported numbers.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from honest_sweep import get_data, run_config, fast_run_config

sig, px = get_data()
sample = sig.iloc[:1500].reset_index(drop=True)

fails = 0
for tm, sm, hold, et in [
    (0.75, 1.0, 10, "trigger_break"),
    (2.0, 0.5, 5, "trigger_break"),
    (1.5, 1.0, 20, "next_open"),
    (3.0, 1.5, 3, "trigger_break"),
]:
    a = run_config(sample, px, tm, sm, hold, entry_timing=et)
    b = fast_run_config(sample, px, tm, sm, hold, entry_timing=et)
    a = a.sort_values(["ticker", "scan_date"]).reset_index(drop=True)
    b = b.sort_values(["ticker", "scan_date"]).reset_index(drop=True)
    same_n = len(a) == len(b)
    if same_n and len(a):
        dr = np.abs(a["ret"].to_numpy() - b["ret"].to_numpy()).max()
        do = (a["outcome"].to_numpy() != b["outcome"].to_numpy()).sum()
        dd = (a["days"].to_numpy() != b["days"].to_numpy()).sum()
        de = np.abs(a["entry"].to_numpy() - b["entry"].to_numpy()).max()
        ok = dr < 1e-9 and do == 0 and dd == 0 and de < 1e-9
    else:
        dr = do = dd = de = "N/A"
        ok = False
    if not ok:
        fails += 1
    print(f"t{tm}/s{sm}/h{hold}/{et}: n_slow={len(a)} n_fast={len(b)} "
          f"max|dret|={dr} outcome_diff={do} days_diff={dd} max|dentry|={de} "
          f"-> {'OK' if ok else 'MISMATCH'}", flush=True)

print(f"\nPARITY {'PASS' if fails == 0 else 'FAIL (' + str(fails) + ')'}",
      flush=True)
sys.exit(0 if fails == 0 else 1)
