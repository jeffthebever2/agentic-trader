#!/usr/bin/env python3
"""CLI portfolio competition report — shows current leaderboard from state files.

Usage:
    python3 scripts/portfolio_report.py
    python3 scripts/portfolio_report.py --json
    python3 scripts/portfolio_report.py --group risk
    python3 scripts/portfolio_report.py --min-trades 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _base_dir() -> Path:
    base = os.environ.get("PAPER_OUTPUT_DIR", "tmp/paper_trading_today")
    base_path = ROOT / base if not Path(base).is_absolute() else Path(base)
    if base_path.exists():
        dated = sorted(
            [d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()],
            reverse=True,
        )
        if dated:
            return dated[0]
    return base_path


def main() -> None:
    p = argparse.ArgumentParser(description="Print portfolio competition leaderboard.")
    p.add_argument("--json", action="store_true", help="Output raw JSON.")
    p.add_argument("--group", default="", help="Filter to group: signal|risk|hold|filter")
    p.add_argument("--min-trades", type=int, default=0, help="Only show portfolios with >= N trades.")
    p.add_argument("--top", type=int, default=0, help="Show only top N results (0 = all).")
    p.add_argument("--sort-by", default="rank",
                   choices=["rank", "total_return_pct", "win_rate", "sharpe", "profit_factor", "trade_count"],
                   help="Sort column.")
    args = p.parse_args()

    sys.path.insert(0, str(ROOT))
    try:
        from tradingagents.portfolios.comparison import leaderboard_summary
    except ImportError as e:
        print(f"ERROR: cannot import comparison module: {e}", file=sys.stderr)
        sys.exit(1)

    base_dir = _base_dir()
    if not base_dir.exists():
        print(f"No portfolio data found at: {base_dir}", file=sys.stderr)
        print("Start paper trading first: ./start.sh paper", file=sys.stderr)
        sys.exit(1)

    summary = leaderboard_summary(base_dir)
    portfolios = summary["portfolios"]

    if args.group:
        portfolios = [p for p in portfolios if p["group"] == args.group]
    if args.min_trades > 0:
        portfolios = [p for p in portfolios if p["trade_count"] >= args.min_trades]
    if args.sort_by != "rank":
        portfolios = sorted(portfolios, key=lambda p: -(p.get(args.sort_by) or 0))
    if args.top > 0:
        portfolios = portfolios[:args.top]

    if args.json:
        print(json.dumps({"portfolios": portfolios, "as_of": summary["as_of"]}, indent=2, default=str))
        return

    print(f"\nPortfolio Competition Leaderboard")
    print(f"Data dir : {base_dir}")
    print(f"As of    : {summary['as_of'][:19]}")
    print(f"Active   : {summary['active_count']} / {summary['portfolio_count']}")
    if args.group:
        print(f"Group    : {args.group}")
    print()

    # Header
    print(f"{'#':<4} {'Portfolio':<24} {'Group':<8} {'Return':>8} {'WR':>7} {'PF':>6} {'Sharpe':>7} "
          f"{'MaxDD':>7} {'Trades':>7} {'AvgHold':>8}")
    print("-" * 95)

    for port in portfolios:
        ret = port.get("total_return_pct", 0)
        ret_str = f"{ret:+.2f}%" if port["status"] == "active" else "—"
        wr = port.get("win_rate")
        wr_str = f"{wr*100:.1f}%" if wr is not None else "—"
        pf = port.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None else "—"
        sh = port.get("sharpe")
        sh_str = f"{sh:.2f}" if sh is not None else "—"
        dd = port.get("max_drawdown", 0)
        dd_str = f"{dd*100:.1f}%" if dd > 0 else "—"
        hold = port.get("avg_hold_days")
        hold_str = f"{hold:.1f}d" if hold is not None else "—"
        rank = port.get("rank") or "?"

        print(f"{rank:<4} {port['label']:<24} {port['group']:<8} {ret_str:>8} {wr_str:>7} "
              f"{pf_str:>6} {sh_str:>7} {dd_str:>7} {port['trade_count']:>7} {hold_str:>8}")

    # Winner callout
    active = [p for p in portfolios if p["status"] == "active"]
    if active:
        best = active[0] if args.sort_by == "rank" else max(active, key=lambda p: p.get(args.sort_by) or 0)
        print()
        print(f"  Winner: {best['emoji']} {best['label']}  ({best['total_return_pct']:+.2f}% return, "
              f"{best['trade_count']} trades)")
    print()


if __name__ == "__main__":
    main()
