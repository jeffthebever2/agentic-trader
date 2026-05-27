#!/usr/bin/env python3
"""
scan_breakouts.py — CLI for live/historical breakout scanning.

Scans a universe of tickers for breakout setups using BreakoutScanner,
ranks by breakout_score, and outputs results to JSON + terminal table.

Usage:
    # Scan all tickers in all_tickers.txt for today's breakouts
    python scripts/scan_breakouts.py

    # Scan specific tickers
    python scripts/scan_breakouts.py --tickers AAPL NVDA MSFT

    # Use a ticker file
    python scripts/scan_breakouts.py --ticker-file all_tickers.txt

    # Filter by minimum score
    python scripts/scan_breakouts.py --min-score 60

    # Filter by breakout type
    python scripts/scan_breakouts.py --type range_breakout volume_breakout

    # Top N results
    python scripts/scan_breakouts.py --top 20

    # Include ML probability predictions (requires trained breakout model)
    python scripts/scan_breakouts.py --ml-model ml_models/breakout/model_bundle_breakout.joblib

    # Historical scan (requires backtest CSV with breakout_v2 features)
    python scripts/scan_breakouts.py --date 2026-01-15

Output:
    breakout_scan_YYYYMMDD.json — full results
    Terminal table with top candidates

WARNING: This scanner is for signal generation only.
  - Never use scan results to tune ML thresholds or select features.
  - Holdout window (2026-05-08 → present) must not be used for model tuning.
  - All scanner outputs are leakage-free (no future data used).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.screening.breakout_scanner import BreakoutResult, BreakoutScanner
from tradingagents.screening.tickers import get_tickers


# ── Result formatting ─────────────────────────────────────────────────────────

def _fmt_pct(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "    –"
    return f"{v * 100:+.{digits}f}%"


def _fmt_prob(v: Optional[float]) -> str:
    if v is None:
        return "  –  "
    return f"{v * 100:.0f}%"


def print_results(results: List[BreakoutResult], top_n: int = 30) -> None:
    """Print top breakout candidates to terminal."""
    shown = results[:top_n]
    if not shown:
        print("\n  No breakout candidates found.\n")
        return

    print(f"\n{'='*105}")
    print(f"  BREAKOUT SCAN — {dt.date.today()}  ({len(results)} candidates found, showing top {len(shown)})")
    print(f"{'='*105}")
    print(
        f"  {'Ticker':<7} {'Score':>5} {'Type':<25} {'20dH':>7} {'VolSrg':>6} "
        f"{'SqzA':>4} {'SqzC':>4} {'BrkWin':>6} {'FlBrk':>6} {'Entry':>7} {'Stop':>7} {'Tgt':>7} {'Conf':<6}"
    )
    print(f"  {'-'*100}")

    for r in shown:
        f = r.features
        ticker = r.ticker
        score  = f"{r.score:.0f}"
        btype  = r.breakout_type[:24]
        h20d   = _fmt_pct(f.get("pct_from_20d_high"), 1)
        vol    = f"{f.get('vol_surge_1d', 0):.1f}x" if f.get("vol_surge_1d") else "   –"
        sqz_a  = "✓" if f.get("keltner_squeeze") else "·"   # squeeze active
        sqz_c  = "✓" if f.get("range_contraction_5_20") and f["range_contraction_5_20"] < 0.50 else "·"
        bw     = _fmt_prob(r.breakout_success_probability)
        fb     = _fmt_prob(r.failed_breakout_probability)
        entry  = f"${r.entry:.2f}"  if r.entry else "    –"
        stop   = f"${r.stop:.2f}"   if r.stop  else "    –"
        tgt    = f"${r.take_profit:.2f}" if r.take_profit else "    –"
        conf   = r.confidence

        print(
            f"  {ticker:<7} {score:>5} {btype:<25} {h20d:>7} {vol:>6} "
            f"{sqz_a:>4} {sqz_c:>4} {bw:>6} {fb:>6} {entry:>7} {stop:>7} {tgt:>7} {conf:<6}"
        )

    print(f"\n  SqzA=Keltner squeeze active, SqzC=5d/20d range compressed")
    print(f"  BrkWin=breakout_success_prob, FlBrk=failed_breakout_prob")
    print(f"{'='*105}\n")


def print_detail(r: BreakoutResult) -> None:
    """Print detailed breakdown for a single result."""
    f = r.features
    print(f"\n  ── {r.ticker} ──")
    print(f"  Score:         {r.score:.1f}/100  ({r.breakout_type})")
    print(f"  Confidence:    {r.confidence}")
    sc = r.score_components
    if sc:
        print(f"  Components:    compression={sc.compression_pts:.0f}  confirmation={sc.confirmation_pts:.0f}  trend={sc.trend_pts:.0f}  volume={sc.volume_pts:.0f}")
    print(f"  Entry/Stop/Tgt: ${r.entry:.2f} / ${r.stop:.2f} / ${r.take_profit:.2f}")
    if r.invalidation_level:
        print(f"  Invalidation:  ${r.invalidation_level:.2f}")
    if r.breakout_success_probability is not None:
        print(f"  Probabilities: success={r.breakout_success_probability:.0%}  fail={r.failed_breakout_probability:.0%}  large_loss={r.large_loss_probability:.0%}")
    if r.expected_move_5d is not None:
        print(f"  Expected move: 5d={r.expected_move_5d:+.1%}  10d={r.expected_move_10d:+.1%}")
    if r.signal_reasons:
        print(f"  Signals:       {', '.join(r.signal_reasons)}")
    if r.warning_flags:
        print(f"  ⚠ Warnings:    {', '.join(r.warning_flags)}")


# ── ML probability enrichment ─────────────────────────────────────────────────

def _enrich_with_ml(results: List[BreakoutResult], model_path: Path) -> List[BreakoutResult]:
    """Load breakout ML model and add probability predictions to results."""
    try:
        import joblib
        import numpy as np
        bundle = joblib.load(model_path)
        print(f"  Loaded ML model: {model_path.name}")
    except Exception as e:
        print(f"  Warning: could not load ML model ({e}), skipping enrichment")
        return results

    try:
        from backtest import ML_NUMERIC_FEATURES_BREAKOUT, ML_CATEGORICAL_FEATURES
        import pandas as pd

        win_model    = bundle.get("win_probability")
        exp_model    = bundle.get("expected_return")
        loss_model   = bundle.get("large_loss")
        feature_cols = bundle.get("feature_columns", ML_NUMERIC_FEATURES_BREAKOUT)

        if win_model is None:
            print("  Warning: win_probability model not in bundle, skipping ML enrichment")
            return results

        rows = [r.features for r in results]
        df = pd.DataFrame(rows)

        for col in feature_cols:
            if col not in df.columns:
                df[col] = np.nan

        X = df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0).values

        win_prob  = win_model.predict_proba(X)[:, 1]
        loss_prob = loss_model.predict_proba(X)[:, 1] if loss_model else np.zeros(len(X))
        exp_ret   = exp_model.predict(X) if exp_model else np.zeros(len(X))

        for i, r in enumerate(results):
            r.breakout_success_probability = round(float(win_prob[i]), 3)
            r.large_loss_probability       = round(float(loss_prob[i]), 3)
            r.failed_breakout_probability  = round(1.0 - float(win_prob[i]), 3)
            r.expected_move_5d             = round(float(exp_ret[i]), 4)

    except Exception as e:
        print(f"  Warning: ML enrichment failed ({e})")

    return results


# ── Saving ────────────────────────────────────────────────────────────────────

def _result_to_dict(r: BreakoutResult) -> Dict[str, Any]:
    sc = r.score_components
    return {
        "ticker": r.ticker,
        "scan_date": r.scan_date,
        "breakout_score": r.score,
        "breakout_type": r.breakout_type,
        "confidence": r.confidence,
        "breakout_success_probability": r.breakout_success_probability,
        "failed_breakout_probability": r.failed_breakout_probability,
        "large_loss_probability": r.large_loss_probability,
        "expected_move_5d": r.expected_move_5d,
        "expected_move_10d": r.expected_move_10d,
        "entry": r.entry,
        "stop": r.stop,
        "take_profit": r.take_profit,
        "invalidation_level": r.invalidation_level,
        "score_components": {
            "compression_pts": sc.compression_pts if sc else None,
            "confirmation_pts": sc.confirmation_pts if sc else None,
            "trend_pts": sc.trend_pts if sc else None,
            "volume_pts": sc.volume_pts if sc else None,
        },
        "signal_reasons": r.signal_reasons,
        "warning_flags": r.warning_flags,
        "features": {k: v for k, v in r.features.items() if v is not None},
    }


def save_results(results: List[BreakoutResult], output_path: Path) -> None:
    data = {
        "scan_date": str(dt.date.today()),
        "generated_at": dt.datetime.now().isoformat(),
        "total_scanned": None,  # filled by caller
        "candidates_found": len(results),
        "results": [_result_to_dict(r) for r in results],
    }
    output_path.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved: {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Breakout scanner — rank tickers by breakout quality score.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="Specific tickers to scan")
    parser.add_argument("--ticker-file", default=None,
                        help="File with one ticker per line (default: auto-detect all_tickers.txt)")
    parser.add_argument("--min-score", type=float, default=40.0,
                        help="Minimum breakout_score to report (default: 40)")
    parser.add_argument("--type", nargs="+", metavar="TYPE",
                        choices=["range_breakout", "volume_breakout", "gap_continuation",
                                 "trend_continuation", "failed_breakout_risk", "consolidation_setup"],
                        help="Filter by breakout_type")
    parser.add_argument("--top", type=int, default=30,
                        help="Show top N results in terminal (default: 30)")
    parser.add_argument("--detail", nargs="*", metavar="TICKER",
                        help="Print detailed breakdown for these tickers")
    parser.add_argument("--ml-model", default=None,
                        help="Path to breakout model bundle .joblib for ML probability enrichment")
    parser.add_argument("--output", default=None,
                        help="Output JSON path (default: breakout_scan_YYYYMMDD.json)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Tickers to process per yfinance batch (default: 50)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel worker threads (default: 4)")
    parser.add_argument("--no-save", action="store_true",
                        help="Skip saving JSON output")
    parser.add_argument("--exclude-failed-breakout-risk", action="store_true",
                        help="Exclude failed_breakout_risk type from output")
    args = parser.parse_args()

    # ── Load tickers ──────────────────────────────────────────────────────
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    elif args.ticker_file:
        tickers = [l.strip().upper() for l in Path(args.ticker_file).read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
    else:
        default_file = ROOT / "all_tickers.txt"
        if default_file.exists():
            tickers = [l.strip().upper() for l in default_file.read_text().splitlines()
                       if l.strip() and not l.startswith("#")]
        else:
            tickers = get_tickers()

    print(f"\n  Scanning {len(tickers)} tickers for breakout setups...")
    print(f"  Min score: {args.min_score}  Workers: {args.workers}\n")

    # ── Run scanner ───────────────────────────────────────────────────────
    scan_date = str(dt.date.today())
    scanner = BreakoutScanner(threshold=args.min_score)

    results = scanner.scan_batch(
        tickers=tickers,
        as_of_date=scan_date,
    )

    total_scanned = len(tickers)

    # ── Apply filters ─────────────────────────────────────────────────────
    if args.type:
        results = [r for r in results if r.breakout_type in args.type]
    if args.exclude_failed_breakout_risk:
        results = [r for r in results if r.breakout_type != "failed_breakout_risk"]

    # ── ML enrichment ─────────────────────────────────────────────────────
    if args.ml_model:
        results = _enrich_with_ml(results, Path(args.ml_model))

    # ── Sort by score ─────────────────────────────────────────────────────
    results.sort(key=lambda r: r.score, reverse=True)

    # ── Print table ───────────────────────────────────────────────────────
    print_results(results, top_n=args.top)

    # ── Detail view ───────────────────────────────────────────────────────
    if args.detail is not None:
        detail_tickers = args.detail if args.detail else [r.ticker for r in results[:5]]
        detail_map = {r.ticker: r for r in results}
        for ticker in detail_tickers:
            if ticker in detail_map:
                print_detail(detail_map[ticker])
            else:
                print(f"  {ticker}: not found in results (below min-score or filtered)")

    # ── Save ──────────────────────────────────────────────────────────────
    if not args.no_save:
        date_str = dt.date.today().strftime("%Y%m%d")
        out_path = Path(args.output) if args.output else ROOT / f"breakout_scan_{date_str}.json"
        data = {
            "scan_date": str(dt.date.today()),
            "generated_at": dt.datetime.now().isoformat(),
            "total_scanned": total_scanned,
            "candidates_found": len(results),
            "results": [_result_to_dict(r) for r in results],
        }
        out_path.write_text(json.dumps(data, indent=2, default=str))
        print(f"  Saved: {out_path}")

    print(f"  Done. {len(results)} breakout candidates from {total_scanned} tickers scanned.\n")


if __name__ == "__main__":
    main()
