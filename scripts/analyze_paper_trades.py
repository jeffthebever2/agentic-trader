#!/usr/bin/env python3
"""
Paper trading telemetry analysis.

Reads events.jsonl (SELL records) from all strategy subdirs and
candidates_history.jsonl (pre-trade signal data) to produce:
  - WR / PF / avg_return by alpha_tier
  - WR by ll_prob bucket
  - Win vs loss breakdown (TARGET / STOP / TIMEOUT)
  - Regime context analysis

Usage:
    python scripts/analyze_paper_trades.py [--output-dir tmp/paper_trading_today]
"""
import argparse
import json
from pathlib import Path
import sys


def load_events(output_dir: Path) -> list[dict]:
    """Load all SELL events from every strategy subdir."""
    sells = []
    for events_file in output_dir.rglob("events.jsonl"):
        with events_file.open() as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    if ev.get("type") == "SELL":
                        ev["_strategy"] = events_file.parent.name
                        ev["_events_file"] = str(events_file)
                        sells.append(ev)
                except Exception:
                    pass
    return sells


def load_candidates_history(output_dir: Path) -> list[dict]:
    """Load all candidates_history.jsonl records (one level above day dirs)."""
    records = []
    for hist_file in output_dir.parent.glob("*_candidates_history.jsonl"):
        with hist_file.open() as f:
            for line in f:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    # Also check output_dir itself
    for hist_file in output_dir.glob("*_candidates_history.jsonl"):
        with hist_file.open() as f:
            for line in f:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def stats(trades: list[dict], label: str = "") -> None:
    if not trades:
        print(f"  {label}: n=0")
        return
    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    wr = len(wins) / len(trades)
    avg_ret = sum(pnl_pcts) / len(pnl_pcts)
    pf = abs(sum(t["pnl_pct"] for t in wins) / sum(t["pnl_pct"] for t in losses)) if losses else float("inf")
    print(f"  {label}: n={len(trades)} WR={wr:.1%} E={avg_ret:.3%} avg_win={avg_win:.3%} avg_loss={avg_loss:.3%} PF={pf:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="tmp/paper_trading_today",
                        help="Paper trading output dir (contains day subdirs)")
    args = parser.parse_args()

    base = Path(args.output_dir)
    if not base.exists():
        print(f"ERROR: {base} not found")
        sys.exit(1)

    # Load data
    sells = load_events(base)
    candidates = load_candidates_history(base)

    print(f"Loaded {len(sells)} SELL events, {len(candidates)} candidate records")

    if not sells:
        print("\nNo completed trades yet. Run paper trading and wait for exits.")
        # Show candidate summary instead
        if candidates:
            executed = [c for c in candidates if c.get("candidate_status") == "executed"
                        or c.get("decision_reason", "").startswith("rule_pass")]
            print(f"\nExecuted candidates in history: {len(executed)}")
            ll_vals = [c["large_loss_probability"] for c in executed
                       if c.get("large_loss_probability") is not None]
            if ll_vals:
                print(f"ll_prob: min={min(ll_vals):.3f} median={sorted(ll_vals)[len(ll_vals)//2]:.3f} max={max(ll_vals):.3f}")
            tiers = {}
            for c in executed:
                t = c.get("alpha_tier", "?")
                tiers[t] = tiers.get(t, 0) + 1
            print(f"Alpha tiers: {tiers}")
        return

    print(f"\n=== OVERALL ===")
    stats(sells, "All trades")

    # By exit reason
    print(f"\n=== BY EXIT REASON ===")
    for reason in ["TARGET", "STOP", "BREAKEVEN_STOP", "TIMEOUT", "EARLY_STOP_EXIT"]:
        subset = [t for t in sells if t.get("exit_reason") == reason]
        if subset:
            stats(subset, reason)

    # By alpha tier (from SELL events directly after telemetry fix)
    tiers_in_sells = set(t.get("alpha_tier") for t in sells if t.get("alpha_tier"))
    if tiers_in_sells:
        print(f"\n=== BY ALPHA TIER (from SELL events) ===")
        for tier in sorted(tiers_in_sells):
            stats([t for t in sells if t.get("alpha_tier") == tier], f"Tier {tier}")
    else:
        # Fallback: join with candidates_history
        print(f"\n=== BY ALPHA TIER (joined from candidates_history) ===")
        cand_map = {(c["ticker"], c.get("scan_date", c.get("signal_date", ""))): c
                    for c in candidates}
        enriched = []
        for sell in sells:
            ticker = sell.get("ticker")
            entry_date = (sell.get("entry_time") or "")[:10]
            key = (ticker, entry_date)
            cand = cand_map.get(key) or {}
            sell["alpha_tier"] = cand.get("alpha_tier", "?")
            sell["large_loss_probability"] = cand.get("large_loss_probability")
            enriched.append(sell)
        for tier in sorted(set(t.get("alpha_tier") for t in enriched)):
            stats([t for t in enriched if t.get("alpha_tier") == tier], f"Tier {tier}")
        sells = enriched

    # By ll_prob bucket
    ll_sells = [t for t in sells if t.get("large_loss_probability") is not None]
    if ll_sells:
        print(f"\n=== BY LARGE_LOSS_PROB BUCKET ===")
        buckets = [(0.0, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.50), (0.50, 1.0)]
        for lo, hi in buckets:
            subset = [t for t in ll_sells
                      if lo <= t["large_loss_probability"] < hi]
            if subset:
                stats(subset, f"ll [{lo:.2f},{hi:.2f})")

    # By regime
    regimes = set(t.get("regime_at_entry") for t in sells if t.get("regime_at_entry"))
    if regimes:
        print(f"\n=== BY REGIME AT ENTRY ===")
        for r in sorted(regimes):
            stats([t for t in sells if t.get("regime_at_entry") == r], r)

    # By ml_probability bucket
    ml_sells = [t for t in sells if t.get("ml_probability") is not None]
    if ml_sells:
        print(f"\n=== BY WIN_PROB BUCKET ===")
        for lo, hi in [(0.0, 0.55), (0.55, 0.60), (0.60, 0.66), (0.66, 1.0)]:
            subset = [t for t in ml_sells
                      if lo <= t["ml_probability"] < hi]
            if subset:
                stats(subset, f"wp [{lo:.2f},{hi:.2f})")


if __name__ == "__main__":
    main()
