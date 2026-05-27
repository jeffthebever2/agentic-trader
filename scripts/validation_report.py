#!/usr/bin/env python3
"""Validation report: compare train → walk-forward → holdout → paper trading.

Generates a side-by-side comparison of all four evaluation periods to detect:
  - Overfitting: train metrics much better than walk-forward
  - Degradation: holdout/paper worse than walk-forward by >10-15%
  - Live execution gap: paper worse than holdout (slippage, fills, stale signals)

Usage:
    python scripts/validation_report.py                              # auto-detect paths
    python scripts/validation_report.py --report ml_models/latest/training_report.json
    python scripts/validation_report.py --paper-log paper_accounts/confirmed_pullback/event_log.json
    python scripts/validation_report.py --holdout-results holdout_results_20260526_*/backtest_results*.json

Output: validation_summary.json + terminal table

WARNING: This report is for DIAGNOSTIC purposes only. Do NOT use it to:
  - Select thresholds or ML hyperparameters
  - Choose between model variants
  - Declare a strategy "working" based on one holdout window
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


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_training_report(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _load_backtest_json(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _paper_metrics_from_log(log_path: Path) -> Optional[Dict]:
    """Extract metrics from paper trade event_log.json."""
    if not log_path.exists():
        return None
    try:
        data = json.loads(log_path.read_text())
        events = data if isinstance(data, list) else data.get("events", [])
        trades = [e for e in events if e.get("type") == "TRADE_CLOSED" or (
            "pnl" in e and e.get("type") not in ("SIZING_DECISION", "SKIP", "ML_DRIFT_ALERT")
        )]
        if not trades:
            return None
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        total = len(trades)
        wr = len(wins) / total if total else 0.0
        pnls = [t.get("pnl", 0) for t in trades]
        avg_win = sum(p for p in pnls if p > 0) / max(len(wins), 1)
        avg_loss = sum(p for p in pnls if p <= 0) / max(total - len(wins), 1)
        expectancy = sum(pnls) / total if total else 0.0
        return {
            "total_trades": total,
            "win_rate": round(wr, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "expectancy_pct": round(expectancy, 4),
        }
    except Exception as e:
        print(f"  Warning: could not parse paper log: {e}")
        return None


def _auto_find(root: Path) -> Dict[str, Optional[Path]]:
    """Auto-discover standard paths."""
    training_report = root / "ml_models" / "latest" / "training_report.json"

    # Most recent holdout results dir
    holdout_dirs = sorted(root.glob("holdout_results_*/"), reverse=True)
    holdout_json = None
    for d in holdout_dirs:
        candidates = sorted(d.glob("backtest_results*.json"))
        if candidates:
            holdout_json = candidates[-1]
            break

    # Paper trading event log (look in paper_accounts/*/event_log.json)
    paper_logs = sorted(root.glob("paper_accounts/*/event_log.json"))
    paper_log = paper_logs[-1] if paper_logs else None

    return {
        "training_report": training_report,
        "holdout_json": holdout_json,
        "paper_log": paper_log,
    }


# ── Metric extractors ─────────────────────────────────────────────────────────

def extract_train_metrics(report: Dict) -> Dict:
    """Extract train-period (in-sample) metrics from training_report."""
    out = {}
    out["created_at"] = report.get("created_at", "unknown")
    settings = report.get("settings", {})
    out["hold"] = settings.get("hold")
    out["train_rows"] = settings.get("train_rows")
    out["test_rows"] = settings.get("test_rows")
    out["calibrated"] = settings.get("calibrated", False)
    out["feature_count"] = settings.get("feature_count")

    win = report.get("models", {}).get("win_probability", {})
    metrics = win.get("metrics", {})
    out["win_roc"] = metrics.get("roc_auc")
    out["win_precision"] = metrics.get("precision")
    out["win_recall"] = metrics.get("recall")

    cal = win.get("calibration", {})
    out["brier_before"] = cal.get("brier_before")
    out["brier_after"] = cal.get("brier_after")

    gate = report.get("gate_analysis", {})
    out["gate_win_rate"] = gate.get("win_rate")
    out["gate_expectancy"] = gate.get("avg_return")
    out["gate_n"] = gate.get("n_trades")

    wf = report.get("walk_forward", {})
    # Support both old key format (from backtest.py) and new format (from train_ml_models.py)
    out["wf_win_rate"] = wf.get("high_conf_win_rate") or wf.get("win_rate")
    out["wf_roc_auc"] = wf.get("roc_auc")
    out["wf_expectancy"] = wf.get("avg_return")
    out["wf_n"] = wf.get("high_conf_n") or wf.get("n_oos_rows") or wf.get("n_oos_trades")
    out["wf_evaluation"] = "purged_walk_forward" if wf.get("roc_auc") else wf.get("status", "unknown")

    thr = report.get("threshold_search", {})
    out["recommended_threshold"] = thr.get("recommended_threshold")

    return out


def extract_holdout_metrics(holdout_json: Optional[Path]) -> Optional[Dict]:
    """Extract holdout backtest metrics."""
    if holdout_json is None:
        return None
    data = _load_backtest_json(holdout_json)
    if data is None:
        return None
    out = {"source": str(holdout_json)}

    # Backtest JSON structure varies — try common keys
    sim = data.get("account_simulation", data.get("simulation", {}))
    out["win_rate"] = sim.get("win_rate") or data.get("win_rate")
    out["total_return"] = sim.get("total_return") or data.get("total_return")
    out["max_drawdown"] = sim.get("max_drawdown") or data.get("max_drawdown")
    out["total_trades"] = sim.get("n_trades") or data.get("n_trades")
    out["expectancy"] = sim.get("avg_trade_return") or data.get("expectancy")
    out["sharpe"] = sim.get("sharpe") or data.get("sharpe")
    return out


# ── Comparison logic ──────────────────────────────────────────────────────────

def _flag(label: str, val: Optional[float], ref: Optional[float],
          warn_gap: float, bad_gap: float, higher_is_better: bool = True) -> str:
    """Return ✓/⚠/✗ comparison flag."""
    if val is None or ref is None:
        return "–"
    gap = val - ref if higher_is_better else ref - val
    if gap >= -warn_gap:
        return "✓"
    if gap >= -bad_gap:
        return "⚠"
    return "✗"


def compare_all(
    train: Dict,
    holdout: Optional[Dict],
    paper: Optional[Dict],
) -> Dict:
    """Build comparison dict and flag degradations."""
    result = {
        "generated_at": dt.datetime.now().isoformat(),
        "train": train,
        "holdout": holdout,
        "paper": paper,
        "flags": {},
        "warnings": [],
        "pass_": True,
    }

    wf_wr = train.get("wf_win_rate")

    # Walk-forward vs holdout degradation
    h_wr = holdout.get("win_rate") if holdout else None
    f = _flag("holdout_wr", h_wr, wf_wr, warn_gap=0.05, bad_gap=0.10)
    result["flags"]["holdout_vs_wf_wr"] = f
    if f == "⚠":
        result["warnings"].append(
            f"Holdout WR ({h_wr:.3f}) is >5% below walk-forward WR ({wf_wr:.3f}) — possible overfitting"
        )
    if f == "✗":
        result["warnings"].append(
            f"DEGRADATION: Holdout WR ({h_wr:.3f}) is >10% below walk-forward WR ({wf_wr:.3f})"
        )
        result["pass_"] = False

    # Paper vs holdout
    p_wr = paper.get("win_rate") if paper else None
    h_wr2 = holdout.get("win_rate") if holdout else wf_wr
    f2 = _flag("paper_vs_holdout_wr", p_wr, h_wr2, warn_gap=0.05, bad_gap=0.15)
    result["flags"]["paper_vs_holdout_wr"] = f2
    if paper and f2 == "⚠":
        result["warnings"].append(
            f"Paper WR ({p_wr:.3f}) is >5% below reference ({h_wr2:.3f}) — live execution gap"
        )
    if paper and f2 == "✗":
        result["warnings"].append(
            f"DEGRADATION: Paper WR ({p_wr:.3f}) is >15% below reference — review live fills/slippage"
        )
        result["pass_"] = False

    # Calibration quality
    brier = train.get("brier_after")
    if brier is not None and brier > 0.24:
        result["warnings"].append(f"Calibration Brier={brier:.4f} > 0.24 — probabilities less reliable")

    # ROC quality — check both test-period ROC and walk-forward ROC
    roc = train.get("win_roc")
    if roc is not None and roc < 0.56:
        result["warnings"].append(f"Win ROC={roc:.4f} < 0.56 — model near random; retrain needed")
        result["pass_"] = False

    # Walk-forward ROC is the most honest estimate (no train/test spillover)
    wf_roc = train.get("wf_roc_auc")
    if wf_roc is not None and wf_roc < 0.54:
        result["warnings"].append(f"Walk-forward ROC={wf_roc:.4f} < 0.54 — OOS signal is very weak; retrain needed")
        result["pass_"] = False
    elif wf_roc is not None and wf_roc < 0.56:
        result["warnings"].append(f"Walk-forward ROC={wf_roc:.4f} is marginal (0.54–0.56); monitor closely")

    # Model staleness
    try:
        created = dt.datetime.fromisoformat(train.get("created_at", "")[:19])
        age = (dt.datetime.now() - created).days
        if age > 45:
            result["warnings"].append(f"Model is {age}d old (> 45d); retrain recommended")
    except Exception:
        pass

    return result


# ── Terminal display ──────────────────────────────────────────────────────────

def print_table(result: Dict) -> None:
    train = result.get("train", {})
    holdout = result.get("holdout") or {}
    paper = result.get("paper") or {}

    print("\n" + "="*72)
    print("  VALIDATION REPORT — TradingAgents ML Pipeline")
    print(f"  Generated: {result.get('generated_at', '?')[:19]}")
    print("="*72)
    print(f"\n  Model created:  {train.get('created_at', '?')[:19]}")
    print(f"  Hold period:    {train.get('hold', '?')} days")
    print(f"  Calibrated:     {train.get('calibrated', False)}")
    print(f"  Features:       {train.get('feature_count', '?')}")
    print(f"  Train rows:     {train.get('train_rows', '?')}")

    print("\n  ┌──────────────────────────┬────────────────┬────────────────┬────────────────┐")
    print("  │ Metric                   │ Train (OOS WF) │    Holdout     │   Paper Live   │")
    print("  ├──────────────────────────┼────────────────┼────────────────┼────────────────┤")

    def row(label, t_val, h_val, p_val, fmt=".4f"):
        def fv(v):
            return f"{v:{fmt}}" if v is not None else "    –   "
        print(f"  │ {label:<24} │ {fv(t_val):>14} │ {fv(h_val):>14} │ {fv(p_val):>14} │")

    row("Win ROC (test)",     train.get("win_roc"),         None,                      None)
    row("WF ROC (purged OOS)",train.get("wf_roc_auc"),      None,                      None)
    row("Win rate",           train.get("wf_win_rate"),     holdout.get("win_rate"),   paper.get("win_rate"))
    row("Expectancy %/trade", train.get("wf_expectancy"),   holdout.get("expectancy"), paper.get("expectancy_pct"))
    row("Brier (after cal)",  train.get("brier_after"),     None,                      None)
    row("Sharpe",             None,                          holdout.get("sharpe"),     None)
    row("Max drawdown",       None,                          holdout.get("max_drawdown"), None)
    row("N trades",           train.get("wf_n"),             holdout.get("total_trades"), paper.get("total_trades"), fmt="d" if isinstance(train.get("wf_n"), int) else ".0f")

    print("  └──────────────────────────┴────────────────┴────────────────┴────────────────┘")

    flags = result.get("flags", {})
    print(f"\n  Holdout vs walk-forward WR: {flags.get('holdout_vs_wf_wr', '–')}")
    print(f"  Paper vs holdout WR:        {flags.get('paper_vs_holdout_wr', '–')}")

    warnings = result.get("warnings", [])
    if warnings:
        print("\n  ⚠ Findings:")
        for w in warnings:
            print(f"    • {w}")
    else:
        print("\n  ✓ No degradation flags.")

    overall = "✓ PASS" if result.get("pass_") else "✗ FAIL — review warnings above"
    print(f"\n  Overall: {overall}")
    print("="*72)
    print("\n  REMINDER: Do NOT use this report to tune thresholds or select features.")
    print("  Holdout data is read-once diagnostic only.\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validation report: train → walk-forward → holdout → paper."
    )
    parser.add_argument("--report", default=None,
                        help="Path to training_report.json (default: ml_models/latest/)")
    parser.add_argument("--holdout-results", default=None,
                        help="Path to holdout backtest results JSON (default: auto-detect)")
    parser.add_argument("--paper-log", default=None,
                        help="Path to paper trade event_log.json (default: auto-detect)")
    parser.add_argument("--output", default=None,
                        help="Write validation_summary.json here (default: cwd)")
    args = parser.parse_args()

    auto = _auto_find(ROOT)

    report_path = Path(args.report) if args.report else auto["training_report"]
    holdout_path = Path(args.holdout_results) if args.holdout_results else auto["holdout_json"]
    paper_path = Path(args.paper_log) if args.paper_log else auto["paper_log"]

    print(f"Training report: {report_path}")
    print(f"Holdout results: {holdout_path}")
    print(f"Paper log:       {paper_path}")

    report = load_training_report(report_path)
    if report is None:
        print(f"ERROR: Cannot load training report from {report_path}")
        sys.exit(1)

    train_metrics = extract_train_metrics(report)
    holdout_metrics = extract_holdout_metrics(holdout_path)
    paper_metrics = _paper_metrics_from_log(paper_path) if paper_path else None

    result = compare_all(train_metrics, holdout_metrics, paper_metrics)
    print_table(result)

    out_path = Path(args.output) if args.output else ROOT / "validation_summary.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"  Saved: {out_path}\n")

    sys.exit(0 if result.get("pass_") else 1)


if __name__ == "__main__":
    main()
