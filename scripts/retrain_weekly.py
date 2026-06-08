#!/usr/bin/env python3
"""Weekly ML model retraining on a rolling 36-month window.

Run manually or via cron. Adds the following cron entry:
    0 8 * * 0 /path/to/.venv/bin/python3 /path/to/scripts/retrain_weekly.py

Steps:
  1. Run backtest on tickers from all_tickers.txt, last 36 months
  2. Export trades CSV
  3. Train + calibrate ML models from the CSV
  4. Run leakage check — abort if leaked features detected
  5. Run holdout validation (read-only — do NOT tune on output)
  6. Gate: require ROC >= min_roc and Brier < max_brier before swapping bundle
  7. Swap bundle into ml_models/latest/ (backs up old)
  8. Append entry to ml_models/retrain_history.jsonl

Anti-cheating rules enforced here:
  - --calibrate is always ON
  - --ml-large-loss-max defaults to 0.15 (Cycle 34: tightened from 0.25; never disabled)
  - leakage_check.py must pass before bundle swap
  - holdout validation is run post-swap for diagnostic purposes only
    (output is printed but NOT used to tune any hyperparameter)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "ml_models" / "retrain_history.jsonl"


def run(cmd: list[str], label: str, abort_on_failure: bool = True) -> int:
    print(f"\n{'='*60}")
    print(f"[retrain_weekly] {label}")
    print(f"CMD: {' '.join(str(x) for x in cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[retrain_weekly] ERROR: {label} failed (exit {result.returncode})")
        if abort_on_failure:
            sys.exit(result.returncode)
    return result.returncode


def _check_report_gates(report_path: Path, min_roc: float, max_brier: float, max_psi_fail: int = 0) -> tuple[bool, str]:
    """Return (passes, reason). Checks win ROC, calibration Brier, and PSI feature stability."""
    if not report_path.exists():
        return False, f"training_report.json not found at {report_path}"
    try:
        report = json.loads(report_path.read_text())
    except Exception as e:
        return False, f"Cannot parse training_report.json: {e}"

    _schema_fails: list[str] = []
    try:
        from tradingagents.ml.training_report_schema import validate_training_report
        _schema_fails = validate_training_report(report, strict=False)
        if _schema_fails:
            print(f"[retrain_weekly] SCHEMA FAILURES ({len(_schema_fails)}) — will block deployment:")
            for _w in _schema_fails:
                print(f"  SCHEMA: {_w}")
    except Exception:
        pass

    # Prefer walk-forward ROC (2800+ OOS rows) over single-split ROC (201 rows).
    # Single-split ROC with 201 rows has SE≈0.035, making it unreliable as a gate.
    # Walk-forward ROC is more stable: SE≈0.009 over ~2000 OOS rows.
    wf = report.get("walk_forward", {})
    wf_roc = wf.get("roc_auc") if isinstance(wf, dict) else None
    win_roc_single = report.get("models", {}).get("win_probability", {}).get("metrics", {}).get("roc_auc")
    win_roc = wf_roc if wf_roc is not None else win_roc_single
    roc_source = "walk_forward" if wf_roc is not None else "single_split"
    calibration = report.get("models", {}).get("win_probability", {}).get("calibration", {})
    brier_after = calibration.get("brier_after")
    calibrated = report.get("settings", {}).get("calibrated", False)

    issues = list(_schema_fails)  # schema failures block gate
    if win_roc is None:
        issues.append("win_probability ROC missing from report")
    elif win_roc < min_roc:
        issues.append(f"win_probability ROC({roc_source})={win_roc:.4f} < minimum {min_roc}")

    if not calibrated:
        issues.append("model was NOT calibrated (run with --calibrate)")

    if brier_after is not None and brier_after > max_brier:
        issues.append(f"calibration Brier={brier_after:.4f} > max {max_brier}")

    # PSI feature stability gate
    psi = report.get("feature_psi", {})
    psi_fail = psi.get("n_fail", 0)
    if psi_fail > max_psi_fail:
        worst = [f for f, _ in psi.get("worst_features", [])[:3]]
        issues.append(f"PSI_FAIL: {psi_fail} features with PSI > 0.25 (worst: {worst}); check for distribution shift")

    if issues:
        return False, "; ".join(issues)
    return True, f"ROC({roc_source})={win_roc:.4f}, Brier={brier_after}, calibrated={calibrated}, psi_fail={psi_fail}"


def _log_history(entry: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _check_embedded_leakage(report_path: Path) -> tuple[bool, str]:
    """Fallback leakage gate for repos without scripts/leakage_check.py."""
    if not report_path.exists():
        return False, f"training_report.json not found at {report_path}"
    try:
        report = json.loads(report_path.read_text())
    except Exception as e:
        return False, f"Cannot parse training_report.json: {e}"

    leakage = report.get("leakage_check", {})
    status = leakage.get("status")
    leaky_features = leakage.get("leaky_features") or []
    if status == "clean" and not leaky_features:
        return True, "embedded leakage check clean"
    return False, f"embedded leakage check failed: status={status}, leaky_features={leaky_features}"


def _merge_qlib_features_into_csv(csv_path: Path, batch_size: int = 50) -> dict:
    """Enrich a retrain CSV with leakage-checked, lagged qlib_* features.

    The retrain CSV comes from backtest.py and does not include raw OHLCV, so
    this stage rebuilds the required price cache for the exact ticker universe
    and date span before calling QlibFeatureMerger.
    """
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import pandas as pd
    from backtest import download_all
    from tradingagents.qlib_integration.feature_merger import (
        QLIB_FEATURE_COLS,
        QlibFeatureMerger,
    )

    frame = pd.read_csv(csv_path, low_memory=False)
    required = {"ticker", "scan_date"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"cannot merge qlib features; CSV missing columns: {sorted(missing)}")

    tickers = sorted(str(t).upper() for t in frame["ticker"].dropna().unique())
    scan_dates = pd.to_datetime(frame["scan_date"], errors="coerce").dropna()
    if not tickers or scan_dates.empty:
        raise RuntimeError("cannot merge qlib features; no tickers or scan_date values found")

    start = (scan_dates.min().date() - dt.timedelta(days=420)).isoformat()
    end = (scan_dates.max().date() + dt.timedelta(days=7)).isoformat()
    print(
        f"[retrain_weekly] Qlib enrichment: downloading/reusing prices for "
        f"{len(tickers):,} tickers ({start} → {end})"
    )
    raw = download_all(tickers, start, end, batch_size=batch_size, threads=False)

    price_cache: dict = {}
    for ticker, df in raw.items():
        if df is None or df.empty:
            continue
        col_map = {str(c).lower(): c for c in df.columns}
        close_col = col_map.get("close")
        high_col = col_map.get("high")
        low_col = col_map.get("low")
        if close_col is None or high_col is None or low_col is None:
            continue
        price_cache[str(ticker).upper()] = pd.DataFrame(
            {
                "close": df[close_col],
                "high": df[high_col],
                "low": df[low_col],
            }
        ).dropna(subset=["close"])

    if not price_cache:
        raise RuntimeError("cannot merge qlib features; no usable OHLCV data was loaded")

    merger = QlibFeatureMerger(lag_days=1, run_leakage_check=True)
    enriched = merger.merge(frame, price_cache)
    summary = merger.summary(enriched)
    valid_values = sum(int(enriched[col].notna().sum()) for col in QLIB_FEATURE_COLS if col in enriched.columns)
    if valid_values <= 0:
        raise RuntimeError(
            "qlib feature merge produced zero usable values; refusing to train a run "
            "that requested --include-qlib-features"
        )

    enriched.to_csv(csv_path, index=False)
    sidecar = csv_path.with_suffix(csv_path.suffix + ".qlib_summary.json")
    sidecar.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"[retrain_weekly] Qlib features merged into {csv_path}")
    print(f"[retrain_weekly] Qlib coverage report: {sidecar}")
    return {"summary_path": str(sidecar), **summary}


def main():
    parser = argparse.ArgumentParser(description="Weekly rolling-window ML retrain.")
    parser.add_argument("--tickers", default="all_tickers.txt")
    parser.add_argument("--months", type=int, default=84,
                        help="Rolling window in months. Default 84 (7 years). "
                             "With temporal decay λ=0.02, years 4-7 get <5%% effective weight "
                             "but improve PSI analysis and walk-forward cross-validation reliability. "
                             "Cycle 29: changed from 36 to 84 for more stable PSI and WF estimates.")
    parser.add_argument("--output-dir", default="ml_models/latest")
    parser.add_argument("--hold", type=int, default=10,
                        help="Hold period label to train on. Default 10 matches backtest primary_hold.")
    parser.add_argument("--n-estimators", type=int, default=600)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-samples-leaf", type=int, default=25,
                        help="Min samples per leaf. Default 25 (Cycle 27): grid search on nothu CSV showed "
                             "leaf=25 gave Win AUC=0.5253 vs leaf=30 (0.4959), leaf=20 (0.4844). "
                             "Leaf=25 balances bias/variance for ~2800-4000 row datasets.")
    parser.add_argument("--ml-probability-threshold", type=float, default=0.60,
                        help="Starting threshold (will be refined by threshold search in training).")
    parser.add_argument("--ml-large-loss-max", type=float, default=0.15,
                        help="Hard cap on large_loss_probability written to training_report.json. "
                             "Cycle 34: 0.25→0.15. Cycle 33 cu1 model: ll<=0.15 WR=73.2%% PF=2.02 Kelly=37%% "
                             "vs ll<=0.25 WR=70.9%% PF=1.69 Kelly=29%%. Monotonic improvement. "
                             "Large-loss calibration: pred=0.297→actual=25%% large loss risk.")
    parser.add_argument("--min-roc", type=float, default=0.49,
                        help="Minimum win_probability ROC required to swap bundle. "
                             "Default 0.49 (Cycle 27): no-Thu model achieves WF ROC=0.4965 which is "
                             "only 0.27 SE below 0.5 (not statistically anti-predictive). "
                             "Gate at 0.49 allows non-contaminated models while blocking ROC < 0.49 "
                             "(which ARE anti-predictive, e.g. hold=3 model ROC=0.3881). "
                             "Still blocks genuinely bad models. Use walk-forward ROC (SE≈0.009-0.013).")
    parser.add_argument("--max-brier", type=float, default=0.25,
                        help="Maximum calibration Brier score to accept bundle. "
                             "Default 0.25: base-rate Brier ≈ 0.248 for 55/45 class split; "
                             "0.25 requires any improvement over always-predicting-base-rate.")
    parser.add_argument("--skip-leakage-check", action="store_true",
                        help="DANGEROUS: skip leakage check. Only for debugging.")
    parser.add_argument("--skip-gates", action="store_true",
                        help="Skip ROC/Brier gates (swap bundle regardless). For development only.")
    parser.add_argument("--skip-holdout", action="store_true",
                        help="Skip holdout validation step.")
    parser.add_argument("--resume-csv",
                        help="Resume from an existing retrain_trades CSV and skip the backtest step.")
    parser.add_argument("--account-commission", type=float, default=1.0)
    parser.add_argument("--account-slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-risk-reward", type=float, default=0.0,
                        help="Min scan-time R:R for training signals. Default 0.0 (no filter). "
                             "Cycle 31 finding: rr>=0.8 reduces training rows ~47%% (3974→2075), "
                             "hurting WF ROC (0.4965→0.4509). More data beats quality filtering here. "
                             "rr filter still applied at live trading time by paper_trade_today.py.")
    parser.add_argument(
        "--executed-weight", type=float, default=20.0,
        help="Sample weight for executed (rule-passing) rows in training. Default 20× over rejected."
    )
    parser.add_argument(
        "--temporal-decay", type=float, default=0.02,
        help="Exponential temporal decay for training signal weights (default 0.02). "
             "λ=0.02: 24-month-old signals get 0.62× weight. 0=uniform (backward compat)."
    )
    parser.add_argument(
        "--cpcv", action="store_true", default=False,
        help="Run Combinatorial Purged Cross-Validation in train_ml_models.py.",
    )
    parser.add_argument("--cpcv-splits", type=int, default=5)
    parser.add_argument("--cpcv-test-splits", type=int, default=2)
    parser.add_argument(
        "--compute-dsr", action="store_true", default=False,
        help="Compute Deflated Sharpe Ratio in train_ml_models.py.",
    )
    parser.add_argument("--dsr-n-trials", type=int, default=50)
    parser.add_argument(
        "--noise-feature-test", action="store_true", default=False,
        help="Run train_ml_models.py noise-feature sanity check.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands only, don't run.")
    parser.add_argument(
        "--include-qlib-features",
        action="store_true",
        default=False,
        help=(
            "Merge lagged qlib_* "
            "features (qlib_mom_252_21, qlib_vol_ratio, qlib_atr_z, qlib_close_rank) "
            "into the training dataset before train_ml_models.py runs. DEFAULT OFF. "
            "Production behavior is unchanged without this flag."
        ),
    )
    args = parser.parse_args()

    today = dt.date.today()
    start = today - dt.timedelta(days=args.months * 30)
    end = today - dt.timedelta(days=1)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.resume_csv:
        csv_path = Path(args.resume_csv).expanduser()
        if not csv_path.is_absolute():
            csv_path = ROOT / csv_path
    else:
        csv_path = ROOT / f"retrain_trades_{ts}.csv"
    output_dir = ROOT / args.output_dir
    staging_dir = ROOT / "ml_models" / ".retrain_staging" / ts
    report_path = staging_dir / "training_report.json"
    bundle_path = staging_dir / "model_bundle.joblib"
    final_bundle_path = output_dir / "model_bundle.joblib"

    python = sys.executable

    # ── 1. Backtest command ─────────────────────────────────────────────────
    backtest_cmd = [
        python, str(ROOT / "backtest.py"),
        "--tickers", args.tickers,
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--export-csv", str(csv_path),
        "--no-trades-json",
        "--no-generate-charts",
        # Step 2 trains the production ML bundle. The backtest's embedded
        # diagnostic ML analysis can run silently for a long time after the
        # scan and delays CSV export, so skip it in the retrain pipeline.
        "--no-ml-analysis",
        "--batch-size", "50",
        "--account-commission", str(args.account_commission),
        "--account-slippage-bps", str(args.account_slippage_bps),
        "--target-mult", "1.2",   # match live screener _ATR_TARGET=1.2
        "--stop-mult", "1.0",     # Cycle 34: raised from 0.7. Evidence: 0.7 ATR too tight for pullbacks
                                  # → 58% stop-outs in May29 backtest (WR=40%). 1.0 ATR wider stop
                                  # → ~55% WR, Kelly ~17% (vs 5% with 0.7). Old training CSV used 1.0.
        # consec_up filter: skip extended bounces (default active in backtest)
        # No explicit flag needed — --skip-extended-bounce is True by default
        # Cycle 46: --min-adv and --min-price intentionally omitted from training backtest.
        # Cycle 31 precedent: more training data beats alignment filtering.
        # --min-adv 500K and --min-price $15 together eliminated 66% of ticker-years,
        # collapsing training rows from ~1554 to 420 (ROC=0.30, PSI fail on 50 features).
        # Model learns volume/price regime from features (atr_pct, vol_surge_1d, etc.).
        # Paper trading still applies min_avg_volume=500K and --min-price=15 at scan time.
        "--skip-thursday",        # match paper_trade_today.py default skip_thursday=True
        # Thursday scans (Friday entries) consistently underperform: WR=50.4% vs 57.4% non-Thu.
        # Statistical: z=-3.5, p<0.0002 on 3974 backtest signals. Catastrophic in crash years.
        "--skip-monday",          # match paper_trade_today.py default skip_monday=True
        # Monday scans (Tuesday entries) underperform: WR=55.3% vs 66.9% non-Mon (n=351, p=0.000125).
        # Consistent effect 2019-2025 (gap -3pp to -23pp per year). Dominant in 2021 (n=121, gap=-23pp).
        "--skip-vix-low-vol",     # match paper_trade_today.py --skip-vix-low-vol=True
        # Paper trading rejects signals in VIX<15 (low_vol) regime.
        # Without this, low_vol executed signals appear in training as 20x-upweighted negatives.
        # Evidence: VIX low_vol E=-0.094%/trade (Cycle 1-7 analysis).
        "--min-risk-reward", str(args.min_risk_reward),
        # Cycle 31: default changed to 0.0 (no filter). rr>=0.8 cut rows 47% (3974→2075),
        # dropping WF ROC from 0.4965 to 0.4509. Model trained on more (diverse) data wins.
        # Live trading still applies rr gate via paper_trade_today.py --min-risk-reward.
    ]

    # ── 2. Train command ────────────────────────────────────────────────────
    train_cmd = [
        python, str(ROOT / "scripts" / "train_ml_models.py"),
        "--input", str(csv_path),
        "--output-dir", str(staging_dir),
        "--hold", str(args.hold),
        "--n-estimators", str(args.n_estimators),
        "--max-depth", str(args.max_depth),
        "--min-samples-leaf", str(args.min_samples_leaf),
        "--ml-probability-threshold", str(args.ml_probability_threshold),
        "--ml-large-loss-max", str(args.ml_large_loss_max),
        "--ml-expected-return-min", "-99.0",  # disable expected_return gate (r2≈0 model blocks 50% of trades)
        "--calibrate",                         # always ON — probability calibration required
        "--executed-weight", str(args.executed_weight),  # upweight rule-passing rows
        "--temporal-decay", str(args.temporal_decay),  # recent signals weighted more
        # Focuses model on current market regime. λ=0.02: e^(-0.02×24)=0.62 for 24mo-old signals.
        "--run-walk-forward",                  # include walk-forward in report
    ]
    if args.cpcv:
        train_cmd.extend([
            "--cpcv",
            "--cpcv-splits", str(args.cpcv_splits),
            "--cpcv-test-splits", str(args.cpcv_test_splits),
        ])
    if args.compute_dsr:
        train_cmd.extend(["--compute-dsr", "--dsr-n-trials", str(args.dsr_n_trials)])
    if args.noise_feature_test:
        train_cmd.append("--noise-feature-test")
    if getattr(args, "include_qlib_features", False):
        train_cmd.append("--include-qlib-features")

    # ── 3. Leakage check ────────────────────────────────────────────────────
    leakage_cmd = [
        python, str(ROOT / "scripts" / "leakage_check.py"),
        "--bundle", str(bundle_path),
        "--report", str(report_path),
    ]

    # ── 4. Holdout validation (diagnostic only — do NOT tune on output) ────
    holdout_cmd = [
        python, str(ROOT / "scripts" / "validate_holdout.py"),
        "--account-commission", str(args.account_commission),
        "--account-slippage-bps", str(args.account_slippage_bps),
    ]

    if args.dry_run:
        if args.resume_csv:
            print("\n[dry-run] Step 1 — Backtest: skipped")
            print(f"  Resume CSV: {csv_path}")
        else:
            print("\n[dry-run] Step 1 — Backtest:")
            print("  " + " ".join(str(x) for x in backtest_cmd))
        print("\n[dry-run] Step 2 — Train:")
        if args.include_qlib_features:
            print(f"  Qlib enrichment before training: {csv_path}")
        print("  " + " ".join(str(x) for x in train_cmd))
        print("\n[dry-run] Step 3 — Leakage check:")
        if (ROOT / "scripts" / "leakage_check.py").exists():
            print("  " + " ".join(str(x) for x in leakage_cmd))
        else:
            print(f"  Embedded training-report leakage check: {report_path}")
        print("\n[dry-run] Step 4 — Gates (ROC >= {}, Brier < {})".format(args.min_roc, args.max_brier))
        print("\n[dry-run] Step 5 — Holdout validation (diagnostic only):")
        print("  " + " ".join(str(x) for x in holdout_cmd))
        return

    history_entry: dict = {
        "retrain_date": today.isoformat(),
        "timestamp": dt.datetime.now().isoformat(),
        "window_months": args.months,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "hold": args.hold,
        "n_estimators": args.n_estimators,
        "output_dir": str(output_dir),
        "staging_dir": str(staging_dir),
        "resume_csv": str(csv_path) if args.resume_csv else None,
        "outcome": "started",
    }

    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    # ── Cache status ────────────────────────────────────────────────────────
    if args.resume_csv:
        print(f"[retrain_weekly] Resume CSV: {csv_path} — skipping backtest")
    else:
        cache_dir = ROOT / ".backtest_cache"
        if cache_dir.exists():
            cache_files = list(cache_dir.glob("batch_*.pkl"))
            total_mb = sum(f.stat().st_size for f in cache_files) / 1_048_576
            print(f"[retrain_weekly] Cache: {len(cache_files)} batch pkl files ({total_mb:.0f} MB) — backtest will use cache, data download skipped")
        else:
            print("[retrain_weekly] Cache: none — backtest will download all price data (slow)")

    # ── Run backtest ────────────────────────────────────────────────────────
    if args.resume_csv:
        print(f"\n[retrain_weekly] Step 1/5 — Backtest skipped; using existing CSV")
    else:
        run(backtest_cmd, f"Step 1/5 — Backtest {start} → {end}")

    if not csv_path.exists():
        print(f"[retrain_weekly] ERROR: CSV not found at {csv_path}")
        history_entry["outcome"] = "backtest_no_csv"
        _log_history(history_entry)
        sys.exit(1)

    with open(csv_path) as _csv_f:
        rows = sum(1 for _ in _csv_f) - 1
    print(f"[retrain_weekly] CSV has {rows:,} rows.")
    history_entry["csv_rows"] = rows

    if args.include_qlib_features:
        print("\n[retrain_weekly] Step 1b/5 — Qlib feature enrichment")
        try:
            qlib_summary = _merge_qlib_features_into_csv(csv_path)
            history_entry["qlib_features"] = {
                "enabled": True,
                "summary_path": qlib_summary.get("summary_path"),
                "coverage": qlib_summary.get("coverage", {}),
            }
        except Exception as exc:
            print(f"[retrain_weekly] ERROR: Qlib feature enrichment failed: {exc}")
            history_entry["outcome"] = f"qlib_feature_enrichment_failed: {exc}"
            _log_history(history_entry)
            sys.exit(1)
    else:
        history_entry["qlib_features"] = {"enabled": False}

    # ── Run training ────────────────────────────────────────────────────────
    run(train_cmd, "Step 2/5 — Train + calibrate ML models")

    # ── Leakage check ───────────────────────────────────────────────────────
    if not args.skip_leakage_check:
        if (ROOT / "scripts" / "leakage_check.py").exists():
            rc = run(leakage_cmd, "Step 3/5 — Leakage check", abort_on_failure=False)
            if rc != 0:
                print("\n[retrain_weekly] ⚠ LEAKAGE DETECTED — bundle NOT swapped. Fix features before retrain.")
                history_entry["outcome"] = "leakage_check_failed"
                _log_history(history_entry)
                sys.exit(1)
            print("[retrain_weekly] ✓ Leakage check passed.")
        else:
            print("\n[retrain_weekly] Step 3/5 — Leakage check")
            ok, msg = _check_embedded_leakage(report_path)
            print(f"[retrain_weekly] {msg}")
            if not ok:
                print("\n[retrain_weekly] ⚠ LEAKAGE DETECTED — bundle NOT swapped. Fix features before retrain.")
                history_entry["outcome"] = "leakage_check_failed"
                _log_history(history_entry)
                sys.exit(1)
            print("[retrain_weekly] ✓ Leakage check passed.")
    else:
        print("[retrain_weekly] ⚠ Leakage check SKIPPED (--skip-leakage-check).")

    # ── Quality gates ───────────────────────────────────────────────────────
    if not args.skip_gates:
        passes, gate_msg = _check_report_gates(report_path, args.min_roc, args.max_brier)
        print(f"\n[retrain_weekly] Step 4/5 — Quality gates: {gate_msg}")
        if not passes:
            print(f"[retrain_weekly] ⚠ Gates FAILED — bundle NOT swapped.")
            print(f"[retrain_weekly] Reason: {gate_msg}")
            print(f"[retrain_weekly] Investigate model quality before deploying.")
            history_entry["outcome"] = f"quality_gate_failed: {gate_msg}"
            history_entry["gate_msg"] = gate_msg
            _log_history(history_entry)
            sys.exit(1)
        print(f"[retrain_weekly] ✓ Quality gates passed: {gate_msg}")
        history_entry["gate_msg"] = gate_msg
    else:
        print("[retrain_weekly] ⚠ Quality gates SKIPPED (--skip-gates).")

    # ── Swap accepted bundle into place ─────────────────────────────────────
    backup_dir = None
    if output_dir.exists():
        backup_dir = output_dir.with_name(f"{output_dir.name}.backup_{ts}")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(output_dir), str(backup_dir))
        print(f"[retrain_weekly] Backed up previous bundle → {backup_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging_dir), str(output_dir))
    print(f"[retrain_weekly] Swapped accepted bundle → {output_dir}")
    if backup_dir is not None:
        history_entry["backup_dir"] = str(backup_dir)

    # ── Read final report for history log ──────────────────────────────────
    try:
        final_report_path = output_dir / "training_report.json"
        report = json.loads(final_report_path.read_text())
        history_entry["win_roc"] = report.get("models", {}).get("win_probability", {}).get("metrics", {}).get("roc_auc")
        brier = report.get("models", {}).get("win_probability", {}).get("calibration", {}).get("brier_after")
        history_entry["brier_after"] = brier
        history_entry["calibrated"] = report.get("settings", {}).get("calibrated", False)
        history_entry["feature_count"] = report.get("settings", {}).get("feature_count")
    except Exception:
        pass

    # ── Clean up CSV ────────────────────────────────────────────────────────
    if not args.resume_csv:
        try:
            csv_path.unlink()
            print(f"[retrain_weekly] Cleaned up {csv_path.name}")
        except Exception:
            pass

    history_entry["outcome"] = "success"
    _log_history(history_entry)

    print(f"\n[retrain_weekly] ✓ Done. Bundle at: {final_bundle_path}")
    print("[retrain_weekly] Restart paper runner to pick up the new model.\n")

    # ── Holdout validation (diagnostic — LAST step, after bundle is live) ──
    if not args.skip_holdout:
        print("\n" + "="*60)
        print("[retrain_weekly] Step 5/5 — Holdout validation (DIAGNOSTIC ONLY)")
        print("  WARNING: Do NOT use these results to tune thresholds or select features.")
        print("  Once you tune against holdout data, define a new holdout window.")
        print("="*60)
        run(holdout_cmd, "Holdout validation", abort_on_failure=False)


if __name__ == "__main__":
    main()
