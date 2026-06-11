#!/usr/bin/env python3
"""Summarize the 2026-06-10 hold/stop sweep (P3 audit follow-up).

Reads the three sweep result JSONs (stop 1.0 / 1.25 / 1.5) and prints a
stop x hold matrix of the metrics that decide profitability. Hypothesis under
test: widening the stop / extending the hold shifts trades out of the stop
bucket (38% of test trades, avg -4.3%) into the timeout bucket (WR 98.7% per
the 2026-05-30 audit), raising expectancy per trade.

Usage: python3 scripts/analyze_sweep_20260610.py sweep_logs_<ts>/
"""
import json
import sys
from pathlib import Path

RUNS = [
    ("A", "1.0", "results_A_stop1.0.json"),
    ("B", "1.25", "results_B_stop1.25.json"),
    ("C", "1.5", "results_C_stop1.5.json"),
]
HOLDS = ["10d", "15d", "20d"]

COLS = [
    ("trades", "trades", "{:>7,}"),
    ("win_rate", "WR", "{:>6.1%}"),
    ("avg_return_pct", "avg%", "{:>+7.3f}"),
    ("profit_factor", "PF", "{:>6.3f}"),
    ("expectancy_per_trade_pct", "exp%", "{:>+7.3f}"),
    ("target_hit_rate", "tgt", "{:>6.1%}"),
    ("stopped_out_rate", "stop", "{:>6.1%}"),
    ("sharpe_ratio", "sharpe", "{:>7.3f}"),
    ("max_drawdown", "maxDD", "{:>6.1%}"),
    ("kelly_pct", "kelly", "{:>6.1f}"),
]


def fmt(stats: dict, key: str, spec: str) -> str:
    v = stats.get(key)
    if v is None:
        return f"{'—':>7}"
    try:
        return spec.format(v)
    except (ValueError, TypeError):
        return f"{v!s:>7}"


def main() -> None:
    log_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    header = f"{'stop':>5} {'hold':>5} " + " ".join(h for _, h, _ in COLS)
    print(header)
    print("-" * len(header))
    best = None
    for run_id, stop, fname in RUNS:
        path = log_dir / fname
        if not path.exists():
            print(f"{stop:>5}  — missing: {path}")
            continue
        data = json.loads(path.read_text())
        by_hold = data.get("summary", {}).get("by_hold_period", {})
        for hold in HOLDS:
            stats = by_hold.get(hold, {})
            if not stats:
                continue
            row = " ".join(fmt(stats, k, spec) for k, _, spec in COLS)
            print(f"{stop:>5} {hold:>5} {row}")
            exp = stats.get("expectancy_per_trade_pct")
            pf = stats.get("profit_factor")
            n = stats.get("trades") or 0
            if exp is not None and n >= 200:
                score = exp  # rank on expectancy; PF tiebreak
                if best is None or (score, pf or 0) > (best[0], best[1]):
                    best = (score, pf or 0, stop, hold, n)
        print()
    if best:
        print(f"BEST (n>=200): stop={best[2]} hold={best[3]} "
              f"expectancy={best[0]:+.3f}%/trade PF={best[1]:.3f} n={best[4]:,}")


if __name__ == "__main__":
    main()
