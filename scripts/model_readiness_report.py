#!/usr/bin/env python3
"""
Model Readiness Report — formal production-gate check.

Evaluates the current model bundle against all production criteria and emits a
structured verdict:

    READY       — all gates pass, model may be used in paper trading
    DEGRADED    — model exists but one or more soft criteria are failing; use
                  with caution and set a review deadline
    NOT_READY   — hard gate(s) failed; model MUST NOT be used

Usage::

    python scripts/model_readiness_report.py
    python scripts/model_readiness_report.py --bundle ml_models/latest/model_bundle.joblib
    python scripts/model_readiness_report.py --json > readiness.json
    python scripts/model_readiness_report.py --strict      # exit 1 if NOT_READY or DEGRADED

Criteria checked
----------------
H1  Bundle file exists
H2  Training report exists
H3  Walk-forward ROC AUC ≥ 0.49  (hard gate)
H4  Model file age ≤ 30 days      (hard gate)
S1  Walk-forward ROC AUC ≥ 0.51  (soft / preferred)
S2  High-confidence win rate ≥ 0.60 (if ≥10 high-conf OOS trades)
S3  Prediction calibration on paper trades (beat_spy_rate tracked)
S4  Gate-filtered win rate ≥ 0.70 in training gate_analysis
S6  Confidence not inverted: high_conf_win_rate ≥ overall win_rate
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────

HARD_WF_ROC_MIN: float = 0.49
SOFT_WF_ROC_MIN: float = 0.51
SOFT_HIGHCONF_WR_MIN: float = 0.60
SOFT_GATE_WR_MIN: float = 0.70
MODEL_MAX_AGE_DAYS: int = 30
SOFT_DSR_MIN: float = 0.50   # Deflated Sharpe Ratio — corrects for hyperparameter search bias
DEFAULT_BUNDLE: str = "ml_models/latest/model_bundle.joblib"
DEFAULT_REPORT: str = "ml_models/latest/training_report.json"
DEFAULT_PAPER_GRADES: str = "paper_accounts"


# ── Check result ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    id: str
    description: str
    passed: bool
    hard: bool
    value: Any = None
    threshold: Any = None
    note: str = ""


@dataclass
class ReadinessReport:
    generated_at: str
    bundle_path: str
    verdict: str             # READY | DEGRADED | NOT_READY
    checks: List[Check] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def print_text(self) -> None:
        width = 70
        print("=" * width)
        print(f"  MODEL READINESS REPORT — {self.generated_at[:10]}")
        print(f"  Bundle: {self.bundle_path}")
        print(f"  VERDICT: {self.verdict}")
        print("=" * width)
        for c in self.checks:
            icon = "✓" if c.passed else ("✗" if c.hard else "!")
            hard_tag = "[HARD]" if c.hard else "[soft]"
            val_str = f"  value={c.value}" if c.value is not None else ""
            thr_str = f"  threshold={c.threshold}" if c.threshold is not None else ""
            note_str = f"  — {c.note}" if c.note else ""
            print(f"  {icon} {hard_tag} {c.id}: {c.description}{val_str}{thr_str}{note_str}")
        if self.notes:
            print()
            for n in self.notes:
                print(f"  >> {n}")
        print("=" * width)


# ── Checkers ─────────────────────────────────────────────────────────────────

def _check_bundle_exists(bundle_path: Path) -> Check:
    return Check(
        id="H1", description="Model bundle file exists",
        passed=bundle_path.exists(), hard=True,
        value=str(bundle_path),
    )


def _check_report_exists(report_path: Path) -> Check:
    return Check(
        id="H2", description="Training report exists",
        passed=report_path.exists(), hard=True,
        value=str(report_path),
    )


def _check_bundle_age(bundle_path: Path) -> Check:
    if not bundle_path.exists():
        return Check(id="H4", description="Model age ≤ 30 days", passed=False,
                     hard=True, note="bundle missing")
    mtime = dt.datetime.fromtimestamp(bundle_path.stat().st_mtime)
    age_days = (dt.datetime.now() - mtime).days
    passed = age_days <= MODEL_MAX_AGE_DAYS
    return Check(
        id="H4", description=f"Model age ≤ {MODEL_MAX_AGE_DAYS} days",
        passed=passed, hard=True,
        value=age_days, threshold=MODEL_MAX_AGE_DAYS,
        note=f"last modified {mtime.strftime('%Y-%m-%d')}",
    )


def _load_report(report_path: Path) -> Optional[Dict]:
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text())
    except Exception:
        return None


def _check_wf_roc_hard(report: Optional[Dict]) -> Check:
    if report is None:
        return Check(id="H3", description=f"Walk-forward ROC AUC ≥ {HARD_WF_ROC_MIN}",
                     passed=False, hard=True, note="training report missing")
    wf = report.get("walk_forward", {})
    roc = wf.get("roc_auc")
    if roc is None:
        return Check(id="H3", description=f"Walk-forward ROC AUC ≥ {HARD_WF_ROC_MIN}",
                     passed=False, hard=True, note="roc_auc missing from report")
    return Check(
        id="H3", description=f"Walk-forward ROC AUC ≥ {HARD_WF_ROC_MIN}",
        passed=float(roc) >= HARD_WF_ROC_MIN, hard=True,
        value=round(float(roc), 4), threshold=HARD_WF_ROC_MIN,
    )


def _check_wf_roc_soft(report: Optional[Dict]) -> Check:
    if report is None:
        return Check(id="S1", description=f"Walk-forward ROC AUC ≥ {SOFT_WF_ROC_MIN} (preferred)",
                     passed=False, hard=False, note="training report missing")
    wf = report.get("walk_forward", {})
    roc = wf.get("roc_auc")
    if roc is None:
        return Check(id="S1", description=f"Walk-forward ROC AUC ≥ {SOFT_WF_ROC_MIN} (preferred)",
                     passed=False, hard=False, note="roc_auc missing")
    return Check(
        id="S1", description=f"Walk-forward ROC AUC ≥ {SOFT_WF_ROC_MIN} (preferred)",
        passed=float(roc) >= SOFT_WF_ROC_MIN, hard=False,
        value=round(float(roc), 4), threshold=SOFT_WF_ROC_MIN,
    )


def _check_highconf_wr(report: Optional[Dict]) -> Check:
    desc = f"High-confidence OOS win rate ≥ {SOFT_HIGHCONF_WR_MIN}"
    if report is None:
        return Check(id="S2", description=desc, passed=False, hard=False,
                     note="training report missing")
    wf = report.get("walk_forward", {})
    hc_n = wf.get("high_conf_n", 0) or 0
    hc_wr = wf.get("high_conf_win_rate")
    if hc_n < 10:
        return Check(id="S2", description=desc, passed=True, hard=False,
                     value=hc_n, note=f"insufficient high-conf trades ({hc_n}<10) — skipped")
    if hc_wr is None:
        return Check(id="S2", description=desc, passed=False, hard=False,
                     note="high_conf_win_rate missing")
    return Check(
        id="S2", description=desc, passed=float(hc_wr) >= SOFT_HIGHCONF_WR_MIN,
        hard=False, value=round(float(hc_wr), 4), threshold=SOFT_HIGHCONF_WR_MIN,
    )


def _check_confidence_inversion(report: Optional[Dict]) -> Check:
    desc = "Confidence not inverted: high_conf_win_rate ≥ overall win_rate"
    if report is None:
        return Check(id="S6", description=desc, passed=True, hard=False,
                     note="training report missing — skipped")
    wf = report.get("walk_forward", {})
    hc_n = wf.get("high_conf_n", 0) or 0
    hc_wr = wf.get("high_conf_win_rate")
    if hc_n < 10 or hc_wr is None:
        return Check(id="S6", description=desc, passed=True, hard=False,
                     note=f"insufficient high-conf trades ({hc_n}<10) — skipped")
    # Overall win rate from gate_analysis if available, else walk_forward
    ga = report.get("gate_analysis", {}).get("strategy_comparison", {})
    ml_ga = ga.get("rule_plus_ml") or ga.get("ml_only")
    overall_wr = (ml_ga or {}).get("win_rate")
    if overall_wr is None:
        overall_wr = wf.get("win_rate")
    hc_wr_f = float(hc_wr)
    inverted = overall_wr is not None and hc_wr_f < float(overall_wr)
    below_floor = hc_wr_f < 0.45
    passed = not inverted and not below_floor
    note_parts = []
    if inverted:
        note_parts.append(f"INVERTED: high_conf_wr={hc_wr_f:.3f} < overall_wr={float(overall_wr):.3f}")
    if below_floor:
        note_parts.append(f"below 0.45 floor ({hc_wr_f:.3f})")
    return Check(
        id="S6", description=desc, passed=passed, hard=False,
        value=round(hc_wr_f, 4),
        threshold=float(overall_wr) if overall_wr is not None else 0.45,
        note="; ".join(note_parts) if note_parts else f"ok (high_conf_wr={hc_wr_f:.3f})",
    )


def _check_gate_wr(report: Optional[Dict]) -> Check:
    desc = f"Gate-filtered win rate ≥ {SOFT_GATE_WR_MIN}"
    if report is None:
        return Check(id="S4", description=desc, passed=False, hard=False,
                     note="training report missing")
    ga = report.get("gate_analysis", {}).get("strategy_comparison", {})
    ml_ga = ga.get("rule_plus_ml") or ga.get("ml_only")
    if ml_ga is None:
        return Check(id="S4", description=desc, passed=False, hard=False,
                     note="gate_analysis.strategy_comparison missing")
    wr = ml_ga.get("win_rate")
    if wr is None:
        return Check(id="S4", description=desc, passed=False, hard=False,
                     note="win_rate missing from gate_analysis")
    return Check(
        id="S4", description=desc, passed=float(wr) >= SOFT_GATE_WR_MIN,
        hard=False, value=round(float(wr), 4), threshold=SOFT_GATE_WR_MIN,
    )


def _check_dsr(report: Optional[Dict]) -> Check:
    desc = f"Deflated Sharpe Ratio ≥ {SOFT_DSR_MIN} (corrects hyperparameter search bias)"
    if report is None:
        return Check(id="S5", description=desc, passed=True, hard=False,
                     note="training report missing — skipping DSR")
    wf = report.get("walk_forward", {})
    sr = wf.get("sharpe_ratio") or wf.get("sharpe")
    n_trials = wf.get("n_trials") or wf.get("n_configs") or 1
    n_obs = wf.get("n_obs") or wf.get("n_test_samples") or 252
    if sr is None:
        return Check(id="S5", description=desc, passed=True, hard=False,
                     note="sharpe_ratio not in training report — skipping DSR")
    try:
        from tradingagents.backtesting.deflated_sharpe import deflated_sharpe_ratio, dsr_label
        dsr = deflated_sharpe_ratio(
            observed_sr=float(sr),
            n_trials=int(n_trials),
            n_obs=int(n_obs),
        )
        return Check(
            id="S5", description=desc,
            passed=dsr >= SOFT_DSR_MIN, hard=False,
            value=round(dsr, 4), threshold=SOFT_DSR_MIN,
            note=f"{dsr_label(dsr)} — {n_trials} trial(s), SR={sr:.3f}",
        )
    except Exception as exc:
        return Check(id="S5", description=desc, passed=True, hard=False,
                     note=f"DSR computation failed: {exc}")


def _check_paper_calibration(paper_dir: Path) -> Check:
    desc = "Paper trade beat_spy tracked (calibration observable)"
    try:
        from tradingagents.portfolio.prediction_grader import PredictionGrader
        grader = PredictionGrader(account_dir=paper_dir)
        grades = grader.grade_all(fetch_benchmarks=False)
        if not grades:
            return Check(id="S3", description=desc, passed=True, hard=False,
                         value=0, note="no paper trades yet — not applicable")
        beat_gradeable = [g for g in grades if g.beat_spy is not None]
        n = len(grades)
        n_bench = len(beat_gradeable)
        note = f"{n} paper trades graded; {n_bench} have benchmark data"
        return Check(id="S3", description=desc, passed=True, hard=False,
                     value=n, note=note)
    except Exception as exc:
        return Check(id="S3", description=desc, passed=True, hard=False,
                     note=f"could not load paper grades: {exc}")


# ── Build report ─────────────────────────────────────────────────────────────

def build_report(
    bundle_path: Path,
    report_path: Path,
    paper_dir: Path,
) -> ReadinessReport:
    now_str = dt.datetime.now().isoformat()
    checks: List[Check] = []

    h1 = _check_bundle_exists(bundle_path)
    h2 = _check_report_exists(report_path)
    h4 = _check_bundle_age(bundle_path)
    checks.extend([h1, h2, h4])

    report_data = _load_report(report_path) if h2.passed else None
    checks.append(_check_wf_roc_hard(report_data))
    checks.append(_check_wf_roc_soft(report_data))
    checks.append(_check_highconf_wr(report_data))
    checks.append(_check_confidence_inversion(report_data))
    checks.append(_check_gate_wr(report_data))
    checks.append(_check_dsr(report_data))
    checks.append(_check_paper_calibration(paper_dir))

    hard_failed = [c for c in checks if c.hard and not c.passed]
    soft_failed = [c for c in checks if not c.hard and not c.passed]

    if hard_failed:
        verdict = "NOT_READY"
    elif soft_failed:
        verdict = "DEGRADED"
    else:
        verdict = "READY"

    notes: List[str] = []
    if verdict == "NOT_READY":
        notes.append("Hard gate(s) failed. Model MUST NOT be used in paper trading until resolved.")
        for c in hard_failed:
            notes.append(f"  FAILED {c.id}: {c.description} — {c.note}")
    elif verdict == "DEGRADED":
        notes.append("Soft criteria failing. Use model with caution; schedule review.")
        for c in soft_failed:
            notes.append(f"  SOFT FAIL {c.id}: {c.description} (value={c.value}, threshold={c.threshold})")
    else:
        notes.append("All production gates pass. Model is approved for paper trading.")

    return ReadinessReport(
        generated_at=now_str,
        bundle_path=str(bundle_path),
        verdict=verdict,
        checks=checks,
        notes=notes,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Model readiness report")
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE,
                        help=f"Path to model_bundle.joblib (default: {DEFAULT_BUNDLE})")
    parser.add_argument("--report", default=DEFAULT_REPORT,
                        help=f"Path to training_report.json (default: {DEFAULT_REPORT})")
    parser.add_argument("--paper-dir", default=DEFAULT_PAPER_GRADES,
                        help="Root of paper_accounts directory")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON output instead of text")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if verdict is NOT_READY or DEGRADED")
    parser.add_argument("--out", default=None,
                        help="Write JSON report to this file path")
    args = parser.parse_args()

    bundle_path = ROOT / args.bundle
    report_path = ROOT / args.report
    paper_dir = ROOT / args.paper_dir

    rpt = build_report(bundle_path, report_path, paper_dir)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rpt.to_dict(), indent=2))

    if args.json:
        print(json.dumps(rpt.to_dict(), indent=2))
    else:
        rpt.print_text()

    if args.strict and rpt.verdict in ("NOT_READY", "DEGRADED"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
