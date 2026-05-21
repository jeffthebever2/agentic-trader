#!/usr/bin/env python3
"""Honest per-strategy report from a backtest_results JSON.

Reads the leak-free walk-forward `ml_analysis.strategy_comparison`, maps each
archetype to the named portfolios, annualizes the out-of-sample return over
the actual OOS span, and prints a PASS/FAIL vs a target annual return.

No fabrication: every number comes straight from the JSON's account_sim
(real account engine: concurrency + position cap + Half-Kelly). If a field
is missing it prints '—', never a guess.

Usage:
  python3 scripts/analyze_strategy_results.py [results.json] [--target 0.35]
"""
from __future__ import annotations
import json
import glob
import argparse
import datetime as dt

# archetype -> named portfolios it represents
MAP = {
    "rule_only_strategy": ["algorithm", "pure_ai"],
    "ml_filter_strategy": ["machine_learning", "ml_new", "combined"],
    "ml_ranking_strategy_top3_per_day": ["(ML-rank overlay)"],
    "ml_stop_target_adjustment_strategy_loss_cap_proxy": ["long_hold (loss-cap)"],
}


def _annualize(total_return_pct, days):
    if total_return_pct is None or not days or days <= 0:
        return None
    r = total_return_pct / 100.0
    try:
        return ((1.0 + r) ** (365.0 / days) - 1.0) * 100.0
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--target", type=float, default=0.35,
                    help="Annual return target (fraction, default 0.35 = 35%%)")
    a = ap.parse_args()

    path = a.path or sorted(glob.glob("backtest_results_*.json"))[-1]
    d = json.load(open(path))
    ml = d.get("ml_analysis", {})
    meta = d.get("meta", {})
    print(f"file: {path}")
    print(f"window: {meta.get('start')} -> {meta.get('end')} | "
          f"account ${meta.get('account_size')} | score_mode {meta.get('score_mode')}")
    print(f"evaluation: {ml.get('evaluation')}  status: {ml.get('status','ok')}")
    lc = ml.get("leakage_controls")
    if lc:
        print(f"leakage_controls: embargo={lc.get('embargo_days')}d "
              f"step={lc.get('step_days')}d min_train={lc.get('min_train_rows')} "
              f"(train_only_past={lc.get('train_only_past')})")
    sc = ml.get("strategy_comparison", {})
    if not sc:
        print("\nNo strategy_comparison (run not finished or not leak-free).")
        return

    # OOS span in days
    oos_days = None
    try:
        s, e = meta.get("start"), meta.get("end")
        if ml.get("settings", {}).get("oos_rows"):
            # approximate OOS span: full window minus the initial train fill
            d0 = dt.date.fromisoformat(s); d1 = dt.date.fromisoformat(e)
            oos_days = (d1 - d0).days  # conservative upper bound; annualized is then a floor
    except Exception:
        pass

    tgt = a.target * 100.0
    print(f"\nTarget: {tgt:.0f}% / year  (PASS if annualized OOS return >= target)\n")
    hdr = f"{'archetype -> portfolios':52} {'n':>4} {'WR':>5} {'PF':>5} {'$profit':>9} {'ret%':>7} {'ann%':>7}  verdict"
    print(hdr); print("-" * len(hdr))
    for arch, names in MAP.items():
        s = sc.get(arch, {})
        acc = s.get("account_sim", {}) or {}
        n = s.get("trades")
        wr = s.get("win_rate")
        pf = s.get("profit_factor")
        prof = acc.get("profit_dollars")
        ret = acc.get("total_return_pct")
        ann = _annualize(ret, oos_days)
        verdict = "—"
        if ann is not None:
            verdict = "PASS" if ann >= tgt else "FAIL"
        def f(x, p=2):
            return "—" if x is None else f"{x:.{p}f}"
        label = f"{arch.replace('_strategy','')} -> {','.join(names)}"
        print(f"{label[:52]:52} {str(n or '—'):>4} "
              f"{f(wr,2):>5} {f(pf,2):>5} {('$'+f(prof)) if prof is not None else '—':>9} "
              f"{f(ret):>7} {f(ann):>7}  {verdict}")
    print("\nNotes:")
    print("- annualized uses the full window span as OOS-day denominator (conservative;")
    print("  true OOS span is shorter after train-fill, so real annualized >= shown).")
    print("- $0 profit with PF<1 means the account engine correctly refused to size a")
    print("  negative-edge series (Half-Kelly=0). That is honest, not a bug.")


if __name__ == "__main__":
    main()
