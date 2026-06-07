#!/usr/bin/env python3
"""
Qlib Research Stage — standalone alpha factor IC/ICIR analysis and model tournament.

Runs independently of production training.  Saves a JSON report per run to
qlib_reports/<timestamp>_qlib_research.json.

Qlib's role in this project
----------------------------
CURRENT (research only):
  - Computes alpha factor IC/ICIR on historical price data.
  - Runs a walk-forward model tournament using qlib-derived features.
  - Outputs a JSON report for manual review.
  - Does NOT feed into production training by default.

OPTIONAL (gated, requires --include-qlib-features in training scripts):
  - QlibFeatureMerger merges lagged qlib_* columns into the training dataset.
  - All features are 1-trading-day lagged (feature[T] uses prices through T-1).
  - Leakage tests run automatically before merge.
  - Does NOT weaken existing leakage, PSI, or calibration gates.

NOT YET (pending forward/paper evidence):
  - Qlib Portfolio signals as UnifiedBrain input.
  - Qlib LightGBM model replacing or supplementing the current XGBoost model.
  - Mark PRODUCTION_READY only when live paper-trade results support it.

Usage::

    python scripts/qlib_research.py --tickers AAPL MSFT NVDA TSLA --start 2020-01-01
    python scripts/qlib_research.py --tickers-file all_tickers.txt --max-tickers 50
    python scripts/qlib_research.py --json                           # stdout JSON only
    python scripts/qlib_research.py --report-dir qlib_reports/

The script exits 0 on success, 1 on error, 2 when qlib is unavailable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _check_qlib() -> tuple[bool, str]:
    try:
        import qlib
        return True, getattr(qlib, "__version__", "unknown")
    except ImportError as exc:
        return False, str(exc)


def _load_tickers(args) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers if t.strip()]
    if args.tickers_file:
        p = Path(args.tickers_file)
        if not p.exists():
            print(f"[qlib_research] ERROR: tickers file not found: {p}", file=sys.stderr)
            sys.exit(1)
        tickers = [line.strip().upper() for line in p.read_text().splitlines() if line.strip()]
        if args.max_tickers:
            tickers = tickers[: args.max_tickers]
        return tickers
    return ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA", "JPM", "V"]


def _run_factor_ic(
    tickers: list[str],
    start: str,
    end: str,
    forward_days: int = 5,
) -> dict:
    """Run IC/ICIR analysis for the standard qlib feature set."""
    from tradingagents.qlib_integration.adapter import QlibDataAdapter
    from tradingagents.qlib_integration.feature_merger import (
        compute_qlib_features,
        QLIB_FEATURE_COLS,
    )
    from tradingagents.qlib_integration.factor_ic import factor_summary

    adapter = QlibDataAdapter()
    ic_results: dict = {}
    errors: list[str] = []

    for ticker in tickers:
        try:
            df = adapter.ohlcv_from_yfinance([ticker], start=start, end=end)
            if df.empty:
                errors.append(f"{ticker}: empty price data")
                continue

            # Normalize to flat close series
            if isinstance(df.index, pd.MultiIndex):
                try:
                    close = df.xs(ticker, level=1)["close"].dropna()
                    high_s = df.xs(ticker, level=1)["high"].dropna()
                    low_s = df.xs(ticker, level=1)["low"].dropna()
                except Exception:
                    continue
            else:
                close = df["close"].dropna()
                high_s = df.get("high")
                low_s = df.get("low")

            if len(close) < 300:
                errors.append(f"{ticker}: insufficient history ({len(close)} days)")
                continue

            feats = compute_qlib_features(close, high_s, low_s, lag_days=1)
            returns_df = close.pct_change(1).rename(ticker).to_frame()

            ticker_ic: dict = {}
            for col in QLIB_FEATURE_COLS:
                if col not in feats.columns:
                    continue
                factor_df = feats[[col]].rename(columns={col: ticker})
                summary = factor_summary(factor_df, returns_df, forward_days=forward_days)
                ticker_ic[col] = summary

            if ticker_ic:
                ic_results[ticker] = ticker_ic

        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    # Aggregate IC mean across tickers per factor
    aggregated: dict = {}
    for col in QLIB_FEATURE_COLS:
        ic_vals = [
            ic_results[t][col]["ic_mean"]
            for t in ic_results
            if col in ic_results.get(t, {})
            and ic_results[t][col].get("ic_mean") is not None
            and ic_results[t][col]["ic_mean"] == ic_results[t][col]["ic_mean"]  # not nan
        ]
        icir_vals = [
            ic_results[t][col]["icir"]
            for t in ic_results
            if col in ic_results.get(t, {})
            and ic_results[t][col].get("icir") is not None
            and ic_results[t][col]["icir"] == ic_results[t][col]["icir"]
        ]
        if ic_vals:
            aggregated[col] = {
                "mean_ic_across_tickers": round(float(sum(ic_vals) / len(ic_vals)), 6),
                "mean_icir_across_tickers": round(float(sum(icir_vals) / len(icir_vals)), 4)
                if icir_vals
                else None,
                "n_tickers": len(ic_vals),
            }

    return {
        "per_ticker": ic_results,
        "aggregated": aggregated,
        "forward_days": forward_days,
        "errors": errors,
    }


def _run_tournament(
    tickers: list[str],
    start: str,
    end: str,
) -> dict:
    """Run walk-forward model tournament using qlib features."""
    from tradingagents.qlib_integration.engine import QlibResearchEngine

    engine = QlibResearchEngine(wf_roc_gate=0.49, n_splits=5)
    result = engine.run_tournament(tickers=tickers, start=start, end=end)
    return {
        "run_at": result.run_at,
        "best_model": result.best_model,
        "best_wf_roc": result.best_wf_roc,
        "results": [
            {
                "model_name": r.model_name,
                "wf_roc": r.wf_roc,
                "accuracy": r.accuracy,
                "n_oos_samples": r.n_oos_samples,
                "top_features": dict(
                    sorted(r.feature_importances.items(), key=lambda x: -x[1])[:10]
                ),
            }
            for r in result.results
        ],
        "notes": result.notes,
        "gate_passes": result.best_wf_roc >= 0.49 if result.results else False,
    }


def build_report(args) -> dict:
    import pandas as pd  # noqa: F401 — needed for IC functions

    qlib_ok, qlib_ver = _check_qlib()
    tickers = _load_tickers(args)

    report: dict = {
        "run_at": dt.datetime.now().isoformat(),
        "qlib_version": qlib_ver,
        "qlib_available": qlib_ok,
        "tickers": tickers,
        "start": args.start,
        "end": args.end,
        "production_ready": False,
        "role": "research_stage",
        "feature_merge_gated": True,
        "feature_merge_default": "OFF",
        "leakage_safe": True,
        "lag_days": 1,
        "features_available": [
            "qlib_mom_252_21",
            "qlib_vol_ratio",
            "qlib_atr_z",
            "qlib_close_rank",
        ],
        "status": "BLOCKED" if not qlib_ok else "OK",
    }

    if not qlib_ok:
        report["status"] = "BLOCKED"
        report["blocker"] = f"qlib import failed: {qlib_ver}"
        report["fix"] = (
            "pip install 'pyqlib @ git+https://github.com/microsoft/qlib.git'"
        )
        return report

    # Factor IC analysis
    if not args.skip_ic:
        try:
            import pandas as pd  # noqa: F811
            report["factor_ic"] = _run_factor_ic(
                tickers=tickers[:args.max_tickers_ic] if args.max_tickers_ic else tickers,
                start=args.start,
                end=args.end,
                forward_days=args.forward_days,
            )
        except Exception as exc:
            report["factor_ic"] = {"error": str(exc)}

    # Walk-forward model tournament
    if not args.skip_tournament:
        try:
            report["tournament"] = _run_tournament(
                tickers=tickers[:args.max_tickers_tournament]
                if args.max_tickers_tournament
                else tickers,
                start=args.start,
                end=args.end,
            )
        except Exception as exc:
            report["tournament"] = {"error": str(exc)}

    # Leakage self-check
    if not args.skip_leakage_check:
        try:
            import numpy as np
            import pandas as pd
            from tradingagents.qlib_integration.feature_merger import (
                compute_qlib_features,
                assert_no_leakage,
            )

            n = 400
            idx = pd.bdate_range("2018-01-01", periods=n)
            rng = np.random.default_rng(99)
            c = pd.Series(100.0 + rng.standard_normal(n).cumsum() + 10.0, index=idx)
            feats = compute_qlib_features(c, lag_days=1)
            assert_no_leakage(feats, c)
            report["leakage_check"] = {"status": "clean", "method": "perturbation+truncation"}
        except Exception as exc:
            report["leakage_check"] = {"status": "FAILED", "error": str(exc)}
            report["leakage_safe"] = False

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Qlib research stage: IC/ICIR analysis and walk-forward tournament."
    )
    parser.add_argument("--tickers", nargs="*", default=None, help="Space-separated list of tickers.")
    parser.add_argument("--tickers-file", default=None, help="File with one ticker per line.")
    parser.add_argument("--max-tickers", type=int, default=None, help="Limit ticker count.")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=dt.date.today().isoformat())
    parser.add_argument("--forward-days", type=int, default=5, help="IC forward-return horizon.")
    parser.add_argument("--report-dir", default="qlib_reports", help="Directory for JSON reports.")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout only.")
    parser.add_argument("--skip-ic", action="store_true", help="Skip IC/ICIR analysis.")
    parser.add_argument("--skip-tournament", action="store_true", help="Skip model tournament.")
    parser.add_argument("--skip-leakage-check", action="store_true", help="Skip leakage self-check.")
    parser.add_argument(
        "--max-tickers-ic", type=int, default=20,
        help="Max tickers for IC analysis (default 20; set 0 for all).",
    )
    parser.add_argument(
        "--max-tickers-tournament", type=int, default=10,
        help="Max tickers for tournament (default 10; set 0 for all).",
    )
    args = parser.parse_args()

    report = build_report(args)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        sys.exit(0 if report.get("status") != "BLOCKED" else 2)

    # Save report
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{stamp}_qlib_research.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\n[qlib_research] Report saved: {report_path}")
    print(f"  Qlib version : {report.get('qlib_version', 'N/A')}")
    print(f"  Status       : {report.get('status', 'N/A')}")
    print(f"  Tickers      : {len(report.get('tickers', []))}")
    print(f"  Leakage-safe : {report.get('leakage_safe', 'N/A')}")
    print(f"  Prod-ready   : {report.get('production_ready', False)}")

    if report.get("tournament"):
        t = report["tournament"]
        print(f"  Best model   : {t.get('best_model', 'N/A')} WF-ROC={t.get('best_wf_roc', 0):.4f}")
        print(f"  Gate passes  : {t.get('gate_passes', False)}")

    if report.get("factor_ic", {}).get("aggregated"):
        print("\n  IC summary (mean across tickers):")
        for col, v in report["factor_ic"]["aggregated"].items():
            print(
                f"    {col:<28} IC={v.get('mean_ic_across_tickers', float('nan')):.4f}  "
                f"ICIR={v.get('mean_icir_across_tickers') or float('nan'):.3f}  "
                f"n={v.get('n_tickers', 0)}"
            )

    if report.get("status") == "BLOCKED":
        print(f"\n  BLOCKED: {report.get('blocker', '')}")
        print(f"  Fix: {report.get('fix', '')}")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    import pandas as pd  # noqa: F401
    main()
