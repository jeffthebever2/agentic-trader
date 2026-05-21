"""
Quick win-rate / PF analysis across score thresholds.
Reads the latest backtest JSON that includes all_trades, OR uses
grid_search_results + score_bucket_analysis if trades aren't stored.

Usage:  python analyze_scores.py
        python analyze_scores.py backtest_results_20260505_230429.json
"""
import sys
import json
import glob
from pathlib import Path

# ── Find results file ─────────────────────────────────────────────────
if len(sys.argv) > 1:
    path = Path(sys.argv[1])
else:
    files = sorted(glob.glob("backtest_results_*.json"))
    if not files:
        print("No backtest_results_*.json found."); sys.exit(1)
    path = Path(files[-1])

print(f"Reading: {path.name}\n")
data = json.loads(path.read_text())
hold = data["meta"].get("primary_hold", 3)

# ── Method 1: individual trades (fastest, most flexible) ──────────────
trades = data.get("all_trades", [])
if trades:
    print(f"Found {len(trades):,} individual trades — computing from raw data.\n")
    import pandas as pd
    df = pd.DataFrame(trades)
    ret_col = f"h{hold}_return"
    if ret_col not in df.columns:
        print(f"Column {ret_col} missing."); sys.exit(1)

    df["ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    df = df.dropna(subset=["ret", "score"])

    thresholds = list(range(72, 99, 1))
    print(f"{'Threshold':>10} {'Trades':>8} {'WinRate':>9} {'AvgRet%':>9} {'PF':>7} {'Sortino':>8}")
    print("  " + "-" * 60)
    for t in thresholds:
        sub = df[df["score"] >= t]
        if len(sub) < 30:
            break
        wins   = sub["ret"][sub["ret"] > 0]
        losses = sub["ret"][sub["ret"] <= 0]
        wr     = (sub["ret"] > 0).mean() * 100
        avg    = sub["ret"].mean() * 100
        pf     = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 0
        down   = sub["ret"][sub["ret"] < 0].std()
        sort   = (sub["ret"].mean() / down) if down > 0 else 0
        print(f"  {t:>8}+  {len(sub):>8,}  {wr:>8.1f}%  {avg:>+8.3f}%  {pf:>7.3f}  {sort:>8.3f}")

# ── Method 2: grid search results (when all_trades not stored) ────────
else:
    print("No individual trades in file — using grid_search_results + score_bucket_analysis.\n")

    grid = data.get("grid_search_results", {}).get("top_results", [])
    if grid:
        grid_sorted = sorted(grid, key=lambda x: x.get("threshold", 0))
        print(f"  Grid search  (hold={hold}d)")
        print(f"  {'Threshold':>10} {'Trades':>8} {'WinRate':>9} {'AvgRet%':>9} {'PF':>7} {'Sortino':>8}")
        print("  " + "-" * 60)
        for r in grid_sorted:
            t   = r.get("threshold", "?")
            n   = r.get("trades", 0)
            wr  = (r.get("win_rate", 0) or 0) * 100
            avg = r.get("avg_return_pct", 0) or 0
            pf  = r.get("profit_factor", 0) or 0
            so  = r.get("sortino_ratio", 0) or 0
            print(f"  {t:>9}+  {n:>8,}  {wr:>8.1f}%  {avg:>+8.3f}%  {pf:>7.3f}  {so:>8.3f}")

    buckets = data.get("score_bucket_analysis", {})
    if buckets:
        print(f"\n  Score buckets  (hold={hold}d)")
        print(f"  {'Bucket':>10} {'Trades':>8} {'WinRate':>9} {'AvgRet%':>9} {'PF':>7} {'Sortino':>8} {'AvgScore':>9}")
        print("  " + "-" * 68)
        for bkt, r in buckets.items():
            if not r:
                continue
            wr  = (r.get("win_rate", 0) or 0) * 100
            avg = r.get("avg_return_pct", 0) or 0
            pf  = r.get("profit_factor", 0) or 0
            so  = r.get("sortino_ratio", 0) or 0
            sc  = r.get("avg_score", "?")
            n   = r.get("trades", 0)
            print(f"  {bkt:>10}  {n:>8,}  {wr:>8.1f}%  {avg:>+8.3f}%  {pf:>7.3f}  {so:>8.3f}  {sc:>9}")

    # VIX regime breakdown
    vix = data.get("vix_regime_analysis", {})
    if vix:
        print(f"\n  VIX regime  (hold={hold}d)")
        print(f"  {'Regime':>12} {'Trades':>8} {'WinRate':>9} {'AvgRet%':>9} {'PF':>7}")
        print("  " + "-" * 52)
        for reg, r in vix.items():
            if not r: continue
            wr  = (r.get("win_rate", 0) or 0) * 100
            avg = r.get("avg_return_pct", 0) or 0
            pf  = r.get("profit_factor", 0) or 0
            print(f"  {reg:>12}  {r.get('trades',0):>8,}  {wr:>8.1f}%  {avg:>+8.3f}%  {pf:>7.3f}")

    # Bear vs bull
    regime = data.get("market_regime_analysis", {})
    if regime:
        print(f"\n  Market regime  (hold={hold}d)")
        print(f"  {'Regime':>8} {'Trades':>8} {'WinRate':>9} {'AvgRet%':>9} {'PF':>7} {'Sharpe':>8}")
        print("  " + "-" * 56)
        for reg, r in regime.items():
            if not r: continue
            wr  = (r.get("win_rate", 0) or 0) * 100
            avg = r.get("avg_return_pct", 0) or 0
            pf  = r.get("profit_factor", 0) or 0
            sh  = r.get("sharpe_ratio", 0) or 0
            print(f"  {reg:>8}  {r.get('trades',0):>8,}  {wr:>8.1f}%  {avg:>+8.3f}%  {pf:>7.3f}  {sh:>8.3f}")
