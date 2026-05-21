#!/usr/bin/env python3
"""
Rule-based portfolio simulation with confidence sizing and market violation tracking.

Usage:
    python scripts/simulate_rule_based.py --input /tmp/sixmo_v2.csv --capital 10000
"""

import argparse
from collections import deque
from datetime import timedelta

import pandas as pd

MAX_POSITIONS_PER_DAY = 20
MAX_SINGLE_POSITION_PCT = 0.20   # 20% of portfolio per position
PDT_DAY_TRADE_LIMIT = 3          # max day trades in 5-day rolling window for <$25k
PDT_THRESHOLD = 25_000           # PDT applies below this account value
WASH_SALE_DAYS = 30


def confidence_position_size(capital: float, confidence: float) -> float:
    """Scale position size by confidence. Returns dollar amount to allocate."""
    if confidence >= 0.75:
        base_pct = 0.12   # 12% of capital
    elif confidence >= 0.55:
        base_pct = 0.08
    elif confidence >= 0.35:
        base_pct = 0.05
    else:
        base_pct = 0.03
    return capital * base_pct


def simulate(df: pd.DataFrame, initial_capital: float) -> dict:
    df = df.copy()
    df["ret"] = pd.to_numeric(df["h3_return"], errors="coerce")
    df["win"] = df["ret"] > 0

    if "confidence" not in df.columns:
        # Fallback: compute from rsi14 and mfi14
        rsi_conf = ((50 - df["rsi14"].clip(upper=50)) / 50).clip(0, 1)
        mfi_conf = ((50 - df["mfi14"].clip(upper=50)) / 50).clip(0, 1)
        df["confidence"] = (rsi_conf * 0.5 + mfi_conf * 0.5).round(3)

    violations = []
    capital = initial_capital
    pnl_log = []
    open_positions = []   # list of (ticker, entry_date, exit_date, cost, return)
    recent_losses = {}    # ticker -> date of last loss sale (for wash sale)

    # PDT tracking: deque of (date, ticker) for same-day buys+sells
    day_trades_window = deque()  # rolling 5-business-day window

    scan_dates = sorted(df["scan_date"].unique())

    for scan_date in scan_dates:
        dt = pd.to_datetime(scan_date)

        # Close positions that have matured (h3 = 3-day hold)
        still_open = []
        for pos in open_positions:
            exit_dt = pd.to_datetime(pos["exit_date"]) if pd.notna(pos.get("exit_date")) else dt + timedelta(days=4)
            if exit_dt <= dt:
                pnl = pos["cost"] * pos["ret"]
                capital += pos["cost"] + pnl
                pnl_log.append({
                    "ticker": pos["ticker"],
                    "entry_date": pos["entry_date"],
                    "exit_date": str(exit_dt.date()),
                    "cost": pos["cost"],
                    "ret": pos["ret"],
                    "pnl": pnl,
                })
                # Track losses for wash sale rule
                if pnl < 0:
                    recent_losses[pos["ticker"]] = dt
            else:
                still_open.append(pos)
        open_positions = still_open

        # Day's candidates ranked by confidence descending
        day = df[df["scan_date"] == scan_date].copy()
        day = day.dropna(subset=["ret"])
        day = day.sort_values("confidence", ascending=False)

        slots_available = MAX_POSITIONS_PER_DAY - len(open_positions)
        if slots_available <= 0:
            continue

        taken = 0
        for _, row in day.iterrows():
            if taken >= slots_available:
                break

            ticker = row["ticker"]
            confidence = float(row.get("confidence", 0.3))
            ret = float(row["ret"])

            # --- Market violation checks ---

            # 1. Wash sale: buying within 30 days of a loss on same ticker
            if ticker in recent_losses:
                days_since_loss = (dt - recent_losses[ticker]).days
                if days_since_loss <= WASH_SALE_DAYS:
                    violations.append({
                        "date": scan_date, "ticker": ticker,
                        "type": "WASH_SALE",
                        "detail": f"Loss sold {days_since_loss}d ago, repurchase blocked"
                    })
                    continue  # skip this trade

            # 2. Position sizing vs concentration limit
            pos_size = confidence_position_size(capital, confidence)
            max_allowed = capital * MAX_SINGLE_POSITION_PCT
            pos_size = min(pos_size, max_allowed)

            if pos_size > capital:
                violations.append({
                    "date": scan_date, "ticker": ticker,
                    "type": "INSUFFICIENT_CAPITAL",
                    "detail": f"Need ${pos_size:.0f}, have ${capital:.0f}"
                })
                continue

            # 3. PDT check (only applies to accounts < $25k)
            # With 3-day hold we are NOT day trading (buy and sell same day).
            # Entry at next_open, exit 3 days later → not a day trade.
            # We record this for completeness but won't block here.
            if capital < PDT_THRESHOLD:
                recent_dt_count = sum(
                    1 for d, t in day_trades_window
                    if (dt - d).days <= 5
                )
                if recent_dt_count >= PDT_DAY_TRADE_LIMIT:
                    violations.append({
                        "date": scan_date, "ticker": ticker,
                        "type": "PDT_WARNING",
                        "detail": f"{recent_dt_count} day trades in past 5 days (limit {PDT_DAY_TRADE_LIMIT})"
                    })
                    # Note: this strategy uses 3-day holds so not actually day trades
                    # Warning only, not blocking

            # Execute trade
            capital -= pos_size
            exit_date = row.get("h3_exit_date", None)
            open_positions.append({
                "ticker": ticker,
                "entry_date": scan_date,
                "exit_date": exit_date,
                "cost": pos_size,
                "ret": ret,
            })
            taken += 1

    # Close any remaining open positions at last known return
    for pos in open_positions:
        pnl = pos["cost"] * pos["ret"]
        capital += pos["cost"] + pnl
        pnl_log.append({
            "ticker": pos["ticker"],
            "entry_date": pos["entry_date"],
            "exit_date": "final",
            "cost": pos["cost"],
            "ret": pos["ret"],
            "pnl": pnl,
        })

    pnl_df = pd.DataFrame(pnl_log)

    return {
        "initial_capital": initial_capital,
        "final_capital": capital,
        "total_pnl": capital - initial_capital,
        "total_trades": len(pnl_df),
        "win_rate": (pnl_df["pnl"] > 0).mean() if len(pnl_df) > 0 else 0.0,
        "avg_ret": pnl_df["ret"].mean() if len(pnl_df) > 0 else 0.0,
        "violations": violations,
        "pnl_df": pnl_df,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--capital", type=float, default=10_000)
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False)
    print(f"Loaded {len(df):,} candidates from {args.input}")
    print(f"Date range: {df['scan_date'].min()} -> {df['scan_date'].max()}")

    # Tier breakdown
    if "ob_tier" in df.columns:
        print("\nTier breakdown:")
        for tier, grp in df.groupby("ob_tier"):
            w = (pd.to_numeric(grp["h3_return"], errors="coerce") > 0).mean()
            print(f"  Tier {tier}: {len(grp):,} trades  win={w:.1%}")

    print(f"\nActive trading days: {df['scan_date'].nunique()}")
    print(f"Trades per active day: {len(df) / df['scan_date'].nunique():.1f}")

    result = simulate(df, args.capital)

    print(f"\n{'='*50}")
    print(f"SIMULATION RESULTS (max {MAX_POSITIONS_PER_DAY} positions/day, confidence sizing)")
    print(f"{'='*50}")
    print(f"Initial capital : ${result['initial_capital']:>10,.2f}")
    print(f"Final capital   : ${result['final_capital']:>10,.2f}")
    print(f"Total P&L       : ${result['total_pnl']:>+10,.2f}")
    print(f"Total trades    : {result['total_trades']}")
    print(f"Win rate        : {result['win_rate']:.1%}")
    print(f"Avg return/trade: {result['avg_ret']:.3%}")

    # Monthly P&L
    pnl_df = result["pnl_df"]
    if len(pnl_df) > 0:
        pnl_df["month"] = pd.to_datetime(pnl_df["entry_date"]).dt.to_period("M")
        print("\nMonthly P&L:")
        for m, g in pnl_df.groupby("month"):
            wr = (g["pnl"] > 0).mean()
            print(f"  {m}: trades={len(g):3d}  win={wr:.1%}  pnl=${g['pnl'].sum():+,.0f}")

    # Violations summary
    viols = result["violations"]
    if viols:
        vdf = pd.DataFrame(viols)
        print(f"\nMarket violations detected: {len(viols)}")
        for vtype, grp in vdf.groupby("type"):
            print(f"  {vtype}: {len(grp)} occurrences")
            for _, v in grp.head(3).iterrows():
                print(f"    {v['date']} {v['ticker']}: {v['detail']}")
    else:
        print("\nNo market violations detected.")

    # Goal check
    pnl = result["total_pnl"]
    wr = result["win_rate"]
    trades = result["total_trades"]
    print(f"\n{'='*50}")
    print("GOAL CHECK")
    print(f"{'='*50}")
    print(f"Win rate >= 85%  : {'PASS' if wr >= 0.85 else 'FAIL'}  ({wr:.1%})")
    print(f"Profit >= $2,500 : {'PASS' if pnl >= 2500 else 'FAIL'}  (${pnl:+,.0f})")
    print(f"Trades >= 150    : {'PASS' if trades >= 150 else 'FAIL'}  ({trades})")
    print(f"Max positions/day: {MAX_POSITIONS_PER_DAY} (enforced)")


if __name__ == "__main__":
    main()
