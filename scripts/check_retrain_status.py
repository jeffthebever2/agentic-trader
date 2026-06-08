#!/usr/bin/env python3
"""Quick check: last retrain result, model health, and gate metrics.

Usage:
    python3 scripts/check_retrain_status.py
    python3 scripts/check_retrain_status.py --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fmt_age(trained_at: str | None) -> str:
    if not trained_at:
        return "unknown"
    try:
        ts = dt.datetime.fromisoformat(trained_at.replace("Z", "+00:00"))
        now = dt.datetime.now(dt.timezone.utc)
        delta = now - ts
        if delta.days == 0:
            hours = delta.seconds // 3600
            return f"{hours}h ago"
        return f"{delta.days}d ago"
    except Exception:
        return trained_at


def check_model(model_dir: Path, label: str) -> dict:
    bundle = model_dir / "model_bundle.joblib"
    report_path = model_dir / "training_report.json"
    result = {
        "label": label,
        "dir": str(model_dir),
        "bundle_exists": bundle.exists(),
        "report_exists": report_path.exists(),
        "healthy": False,
        "wf_roc": None,
        "brier_after": None,
        "trained_at": None,
        "age": None,
        "qlib_features": False,
        "feature_count": None,
        "rows_used": None,
        "warnings": [],
    }

    if not bundle.exists():
        result["warnings"].append("model_bundle.joblib MISSING")
        return result

    if not report_path.exists():
        result["warnings"].append("training_report.json missing")
        result["healthy"] = bundle.exists()
        return result

    report = json.loads(report_path.read_text())
    wf = report.get("walk_forward", {}) or {}
    wf_roc = wf.get("roc_auc")
    cal = (report.get("models", {}).get("win_probability", {}) or {}).get("calibration", {}) or {}
    brier_after = cal.get("brier_after")
    calibrated = report.get("settings", {}).get("calibrated", False)
    trained_at = report.get("trained_at") or report.get("timestamp")
    feature_names = report.get("feature_names", [])
    qlib_features = any(f.startswith("qlib_") for f in feature_names)
    rows_used = report.get("settings", {}).get("rows_used")

    result.update({
        "wf_roc": wf_roc,
        "brier_after": brier_after,
        "trained_at": trained_at,
        "age": _fmt_age(trained_at),
        "qlib_features": qlib_features,
        "feature_count": len(feature_names) if feature_names else None,
        "rows_used": rows_used,
        "high_conf_win_rate": wf.get("high_conf_win_rate"),
    })

    healthy = True
    if wf_roc is not None and wf_roc < 0.49:
        result["warnings"].append(f"WF ROC {wf_roc:.4f} BELOW gate 0.49")
        healthy = False
    if not calibrated:
        result["warnings"].append("NOT calibrated")
    if not qlib_features:
        result["warnings"].append("No Qlib features — retrain with --include-qlib-features")
    result["healthy"] = healthy
    return result


def check_retrain_history() -> list[dict]:
    history_path = ROOT / "ml_models" / "retrain_history.jsonl"
    if not history_path.exists():
        return []
    entries = []
    for line in history_path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-5:]  # last 5


def main() -> None:
    p = argparse.ArgumentParser(description="Check deployed model health and retrain history.")
    p.add_argument("--json", action="store_true", help="Output JSON instead of human-readable text.")
    args = p.parse_args()

    models = {
        "latest": check_model(ROOT / "ml_models" / "latest", "Main Model (latest)"),
        "stock_universe": check_model(ROOT / "ml_models" / "stock_universe", "Stock Universe"),
    }
    history = check_retrain_history()

    if args.json:
        print(json.dumps({"models": models, "retrain_history": history}, indent=2, default=str))
        return

    for key, m in models.items():
        status = "OK" if m["healthy"] else "WARN"
        print(f"\n{'='*60}")
        print(f"  {m['label']}   [{status}]")
        print(f"{'='*60}")
        print(f"  Directory   : {m['dir']}")
        print(f"  Bundle      : {'EXISTS' if m['bundle_exists'] else 'MISSING'}")
        if m["report_exists"]:
            roc_str = f"{m['wf_roc']:.4f}" if m["wf_roc"] else "N/A"
            brier_str = f"{m['brier_after']:.4f}" if m["brier_after"] else "N/A"
            print(f"  WF ROC      : {roc_str} {'(PASS)' if m['wf_roc'] and m['wf_roc'] >= 0.49 else '(FAIL)' if m['wf_roc'] else ''}")
            print(f"  Brier       : {brier_str}")
            print(f"  Features    : {m['feature_count'] or 'N/A'} {'(Qlib: YES)' if m['qlib_features'] else '(Qlib: NO)'}")
            print(f"  Rows used   : {m['rows_used'] or 'N/A'}")
            print(f"  Trained     : {m['trained_at'] or 'N/A'} ({m['age'] or 'unknown'})")
            if m.get("high_conf_win_rate"):
                print(f"  HC Win Rate : {m['high_conf_win_rate']:.4f}")
        for w in m.get("warnings", []):
            print(f"  ⚠  {w}")

    if history:
        print(f"\n{'='*60}")
        print(f"  Last 5 Retrain Cycles")
        print(f"{'='*60}")
        for entry in reversed(history):
            ts = entry.get("timestamp") or entry.get("trained_at") or "?"
            roc = entry.get("wf_roc") or entry.get("roc_auc")
            roc_str = f"ROC={roc:.4f}" if roc else ""
            passed = entry.get("gate_passed", entry.get("promoted", "?"))
            print(f"  {ts[:19]}  {roc_str}  {'PASS' if passed else 'FAIL' if passed is False else '?'}")

    print()
    print("  To retrain:  ./start.sh retrain")
    print("  To view log: tail -f /tmp/retrain_cycle47_qlib.log")
    print()

    any_unhealthy = any(not m["healthy"] for m in models.values())
    sys.exit(1 if any_unhealthy else 0)


if __name__ == "__main__":
    main()
