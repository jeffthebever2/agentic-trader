"""
Thematic Signal Backtest / Validation

Reads tmp/thematic_score_history.jsonl and tmp/thematic_exit_log.jsonl,
then validates whether high-score signals / insider+social combos / theme picks
were predictive.

Usage:
    python3 scripts/backtest_thematic_signals.py [--days 30] [--min-score 20]

Output:
    - Per-signal return (entry → close after hold_days)
    - Win rate by score bucket (low / mid / high)
    - Win rate: insider+social combo vs. no combo
    - Win rate by theme
    - Top and worst performing tickers from history
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

SCORE_HISTORY  = ROOT / "tmp" / "thematic_score_history.jsonl"
EXIT_LOG       = ROOT / "tmp" / "thematic_exit_log.jsonl"
SIGNALS_FILE   = ROOT / "tmp" / "thematic_signals.json"
TRADES_FILE    = ROOT / "tmp" / "thematic_trades.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    results = []
    for line in path.read_text().splitlines():
        try:
            results.append(json.loads(line))
        except Exception:
            pass
    return results


def fetch_returns(ticker: str, entry_date: str, hold_days: int) -> float | None:
    """Fetch actual return from entry_date to entry_date + hold_days using yfinance."""
    try:
        import yfinance as yf
        import datetime as dt
        start = dt.date.fromisoformat(entry_date[:10])
        end   = start + dt.timedelta(days=hold_days + 5)  # buffer for weekends
        data  = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                            auto_adjust=True, progress=False)
        closes = data["Close"] if hasattr(data["Close"], "iloc") else None
        if closes is None or len(closes) < 2:
            return None
        entry_price = float(closes.iloc[0])
        exit_price  = float(closes.iloc[min(hold_days, len(closes) - 1)])
        if entry_price <= 0:
            return None
        return round((exit_price - entry_price) / entry_price * 100, 2)
    except Exception as e:
        print(f"  [warn] {ticker} fetch failed: {e}")
        return None


def run_backtest(days: int = 90, min_score: float = 0.0) -> None:
    print(f"\n{'='*60}")
    print(f"Thematic Signal Backtest  (last {days} days, min_score={min_score})")
    print(f"{'='*60}")

    # ── Load score history ────────────────────────────────────────────────
    history = load_jsonl(SCORE_HISTORY)
    if not history:
        print("No score history found at", SCORE_HISTORY)
        print("Run at least one auto-scan first.")
        return

    import datetime as dt
    cutoff = dt.datetime.now() - dt.timedelta(days=days)

    # ── Load executed trades for actual entry prices / exit ───────────────
    trades = load_jsonl(TRADES_FILE)
    trade_map: dict[str, dict] = {}
    for t in trades:
        ticker = t.get("ticker", "")
        if ticker and ticker not in trade_map:
            trade_map[ticker] = t

    # ── Load exit log for actual outcomes ─────────────────────────────────
    exits = load_jsonl(EXIT_LOG)
    exit_map: dict[str, dict] = {}
    for ex in exits:
        ticker = ex.get("ticker", "")
        if ticker:
            exit_map[ticker] = ex  # last exit per ticker

    # ── Build analysis rows from score history ────────────────────────────
    rows: list[dict] = []
    for rec in history:
        try:
            ts = dt.datetime.fromisoformat(rec["ts"])
        except Exception:
            continue
        if ts < cutoff:
            continue
        breakdown = rec.get("breakdown", {})
        for ticker, score in rec.get("ranked", []):
            if score < min_score:
                continue
            bd = breakdown.get(ticker, {})
            has_insider = bd.get("insider", 0) > 0
            has_social  = bd.get("trusted_twitter", 0) > 0 or bd.get("reddit", 0) > 0
            combo       = has_insider and has_social
            rows.append({
                "ticker":  ticker,
                "score":   score,
                "ts":      ts,
                "date":    ts.date().isoformat(),
                "combo":   combo,
                "insider": has_insider,
                "social":  has_social,
                "n_sources": len([k for k, v in bd.items() if v > 0 and "bonus" not in k and "combo" not in k]),
            })

    if not rows:
        print(f"No score history entries after cutoff (last {days} days) with score >= {min_score}")
        return

    print(f"\nAnalyzing {len(rows)} ticker-scan entries from {len(history)} scans...")
    print("Fetching returns from yfinance (may take 30-60s)...\n")

    # Deduplicate: one analysis per ticker (use first appearance)
    seen: set[str] = set()
    unique_rows = []
    for r in sorted(rows, key=lambda x: x["ts"]):
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique_rows.append(r)

    # Fetch 5-day returns for each unique ticker
    results = []
    for row in unique_rows[:50]:  # cap at 50 to avoid hammering yfinance
        ret = fetch_returns(row["ticker"], row["date"], hold_days=5)
        if ret is not None:
            row["return_5d"] = ret
            row["win"] = ret > 0
            results.append(row)
            print(f"  {row['ticker']:6s} score={row['score']:6.1f} combo={row['combo']} → {'+' if ret > 0 else ''}{ret:.2f}%")

    if not results:
        print("Could not fetch returns — check yfinance connectivity.")
        return

    wins   = [r for r in results if r["win"]]
    losses = [r for r in results if not r["win"]]
    avg    = sum(r["return_5d"] for r in results) / len(results)
    wr     = len(wins) / len(results) * 100

    print(f"\n{'─'*50}")
    print(f"Overall:  {len(results)} tickers | WR={wr:.1f}% | AvgRet={avg:+.2f}%")

    # ── Win rate by score bucket ──────────────────────────────────────────
    def bucket(s: float) -> str:
        if s >= 50: return "high(≥50)"
        if s >= 20: return "mid(20-50)"
        return "low(<20)"

    buckets: dict[str, list] = {}
    for r in results:
        b = bucket(r["score"])
        buckets.setdefault(b, []).append(r)

    print(f"\nBy score bucket:")
    for b in ["high(≥50)", "mid(20-50)", "low(<20)"]:
        rs = buckets.get(b, [])
        if not rs:
            continue
        bwr  = sum(1 for r in rs if r["win"]) / len(rs) * 100
        bavg = sum(r["return_5d"] for r in rs) / len(rs)
        print(f"  {b:12s}  n={len(rs):3d}  WR={bwr:.1f}%  avg={bavg:+.2f}%")

    # ── Insider + social combo ────────────────────────────────────────────
    combos   = [r for r in results if r["combo"]]
    nocombos = [r for r in results if not r["combo"]]
    if combos:
        cwr  = sum(1 for r in combos if r["win"]) / len(combos) * 100
        cavg = sum(r["return_5d"] for r in combos) / len(combos)
        print(f"\nInsider+Social combo:  n={len(combos)}  WR={cwr:.1f}%  avg={cavg:+.2f}%")
    if nocombos:
        nwr  = sum(1 for r in nocombos if r["win"]) / len(nocombos) * 100
        navg = sum(r["return_5d"] for r in nocombos) / len(nocombos)
        print(f"No combo:              n={len(nocombos)}  WR={nwr:.1f}%  avg={navg:+.2f}%")

    # ── By source count ───────────────────────────────────────────────────
    multi = [r for r in results if r["n_sources"] >= 3]
    single = [r for r in results if r["n_sources"] < 3]
    if multi:
        mwr  = sum(1 for r in multi if r["win"]) / len(multi) * 100
        mavg = sum(r["return_5d"] for r in multi) / len(multi)
        print(f"\nMulti-source (≥3):     n={len(multi)}  WR={mwr:.1f}%  avg={mavg:+.2f}%")
    if single:
        swr  = sum(1 for r in single if r["win"]) / len(single) * 100
        savg = sum(r["return_5d"] for r in single) / len(single)
        print(f"Single-source (<3):    n={len(single)}  WR={swr:.1f}%  avg={savg:+.2f}%")

    # ── Top / worst ───────────────────────────────────────────────────────
    sorted_results = sorted(results, key=lambda r: r["return_5d"], reverse=True)
    print(f"\nTop 5:")
    for r in sorted_results[:5]:
        print(f"  {r['ticker']:6s} {r['return_5d']:+.2f}%  score={r['score']:.0f}  combo={r['combo']}")
    print(f"Worst 5:")
    for r in sorted_results[-5:]:
        print(f"  {r['ticker']:6s} {r['return_5d']:+.2f}%  score={r['score']:.0f}  combo={r['combo']}")

    # ── Exit log outcomes ─────────────────────────────────────────────────
    if exits:
        print(f"\nActual exits from exit log ({len(exits)} total):")
        by_reason: dict[str, list] = {}
        for ex in exits:
            by_reason.setdefault(ex.get("reason", "unknown"), []).append(ex.get("pnl_pct", 0))
        for reason, pnls in by_reason.items():
            avg_pnl = sum(pnls) / len(pnls)
            wr_ex = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            print(f"  {reason:20s}  n={len(pnls):3d}  WR={wr_ex:.1f}%  avg={avg_pnl:+.2f}%")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest thematic auto-pick signals")
    parser.add_argument("--days",      type=int,   default=90,  help="Look-back window in days")
    parser.add_argument("--min-score", type=float, default=0.0, help="Min raw buzz score to include")
    args = parser.parse_args()
    run_backtest(days=args.days, min_score=args.min_score)
