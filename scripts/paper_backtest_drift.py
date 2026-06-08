#!/usr/bin/env python3
"""Paper vs. Backtest Fill-Price Drift Report — BT-3.

Loads paper trade BUY events from the paper trade log and matches them to
backtest signal prices for the same (ticker, signal_date) pairs. Computes
fill slippage (basis points) between the two.

Output JSON: mean_slip_bps, std_slip_bps, p95_slip_bps, n_trades (+ breakdown)

Usage:
    python3 scripts/paper_backtest_drift.py \\
        --paper-log tmp/paper_trading_today/account_log.jsonl \\
        --backtest-csv backtest_results_latest.csv \\
        --output docs/paper_backtest_drift_<date>.json

    python3 scripts/paper_backtest_drift.py --dry-run   # print stats, no write
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(description="Paper vs. Backtest fill-price drift report.")
    parser.add_argument(
        "--paper-log",
        default="tmp/paper_trading_today/account_log.jsonl",
        help="Path to paper trade JSONL log. Each line must be a JSON object.",
    )
    parser.add_argument(
        "--backtest-csv",
        default=None,
        help="Path to backtest results CSV with columns: ticker, signal_date, entry_price.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Defaults to docs/paper_backtest_drift_<YYYYMMDD>.json",
    )
    parser.add_argument(
        "--min-trades", type=int, default=5,
        help="Abort with warning if fewer than this many matched trades. Default: 5.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print stats but do not write output file.",
    )
    return parser.parse_args()


def _load_paper_buys(log_path: Path) -> list:
    """Load BUY events from paper trade log. Returns list of dicts."""
    if not log_path.exists():
        return []
    buys = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") == "BUY":
                    buys.append(obj)
            except json.JSONDecodeError:
                continue
    return buys


def _load_backtest_prices(csv_path: Path) -> dict:
    """Load backtest signal prices. Returns {(ticker, date_str): price}."""
    if csv_path is None or not csv_path.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_csv(str(csv_path))
        required = {"ticker", "signal_date", "entry_price"}
        if not required.issubset(set(df.columns)):
            print(f"  WARNING: backtest CSV missing columns {required - set(df.columns)}")
            return {}
        prices = {}
        for _, row in df.iterrows():
            ticker = str(row["ticker"])
            date = str(row["signal_date"])[:10]
            price = float(row["entry_price"])
            prices[(ticker, date)] = price
        return prices
    except Exception as e:
        print(f"  WARNING: Could not load backtest CSV: {e}")
        return {}


def _compute_drift(paper_buys: list, backtest_prices: dict) -> dict:
    """Compute per-trade slip and aggregate stats.

    Returns dict with: matches, slip_bps_list, by_setup_type, by_day_of_week
    """
    matches = []
    for buy in paper_buys:
        ticker = buy.get("ticker") or buy.get("symbol")
        fill_price = buy.get("price") or buy.get("fill_price") or buy.get("entry_price")
        signal_date = buy.get("signal_date") or (buy.get("timestamp", "")[:10])
        if not (ticker and fill_price and signal_date):
            continue
        bt_price = backtest_prices.get((ticker, signal_date))
        if bt_price is None or bt_price <= 0:
            continue
        fill = float(fill_price)
        slip_bps = (fill - bt_price) / bt_price * 10000.0
        matches.append({
            "ticker": ticker,
            "signal_date": signal_date,
            "paper_fill": fill,
            "backtest_price": bt_price,
            "slip_bps": round(slip_bps, 2),
            "setup_type": buy.get("setup_type", "unknown"),
        })
    return matches


def _aggregate(matches: list) -> dict:
    import statistics
    if not matches:
        return {
            "n_trades": 0,
            "mean_slip_bps": None,
            "std_slip_bps": None,
            "p95_slip_bps": None,
            "by_setup_type": {},
            "by_day_of_week": {},
        }

    slips = [m["slip_bps"] for m in matches]
    slips_sorted = sorted(slips)
    p95_idx = max(0, int(len(slips_sorted) * 0.95) - 1)

    by_setup: dict = {}
    for m in matches:
        st = m["setup_type"]
        by_setup.setdefault(st, []).append(m["slip_bps"])

    by_dow: dict = {}
    for m in matches:
        try:
            dt = datetime.strptime(m["signal_date"], "%Y-%m-%d")
            dow = dt.strftime("%A")
        except ValueError:
            dow = "unknown"
        by_dow.setdefault(dow, []).append(m["slip_bps"])

    return {
        "n_trades": len(matches),
        "mean_slip_bps": round(statistics.mean(slips), 2),
        "std_slip_bps": round(statistics.stdev(slips) if len(slips) > 1 else 0.0, 2),
        "p95_slip_bps": round(slips_sorted[p95_idx], 2),
        "by_setup_type": {
            k: {"n": len(v), "mean_bps": round(statistics.mean(v), 2)}
            for k, v in by_setup.items()
        },
        "by_day_of_week": {
            k: {"n": len(v), "mean_bps": round(statistics.mean(v), 2)}
            for k, v in by_dow.items()
        },
    }


def main():
    args = parse_args()

    paper_log = Path(args.paper_log)
    backtest_csv = Path(args.backtest_csv) if args.backtest_csv else None
    today = datetime.today().strftime("%Y%m%d")
    output_path = Path(args.output) if args.output else Path(f"docs/paper_backtest_drift_{today}.json")

    print(f"Loading paper trades from: {paper_log}")
    paper_buys = _load_paper_buys(paper_log)
    print(f"  Found {len(paper_buys)} BUY events")

    print(f"Loading backtest prices from: {backtest_csv}")
    bt_prices = _load_backtest_prices(backtest_csv)
    print(f"  Found {len(bt_prices)} backtest price entries")

    matches = _compute_drift(paper_buys, bt_prices)
    print(f"  Matched {len(matches)} trades")

    if len(matches) < args.min_trades:
        print(
            f"\n  WARNING: Only {len(matches)} matched trades < --min-trades {args.min_trades}. "
            "Stats may not be reliable. Pass --min-trades 0 to suppress."
        )
        if not args.dry_run and len(matches) == 0:
            print("  No output written (no matched trades).")
            return

    report = _aggregate(matches)
    report["generated_at"] = datetime.now().isoformat()[:19]
    report["paper_log"] = str(paper_log)
    report["backtest_csv"] = str(backtest_csv)
    report["trades"] = matches[:500]  # cap sample size in JSON

    print(f"\n  mean_slip_bps = {report.get('mean_slip_bps')} bps")
    print(f"  std_slip_bps  = {report.get('std_slip_bps')} bps")
    print(f"  p95_slip_bps  = {report.get('p95_slip_bps')} bps")
    print(f"  n_trades      = {report.get('n_trades')}")

    if args.dry_run:
        print("\nDry-run: report NOT saved.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
