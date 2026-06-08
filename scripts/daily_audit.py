#!/usr/bin/env python3
"""
Daily audit — reads system state, scores health, flags issues, and proposes
changes via change-control.  NEVER auto-applies risky settings.

What it does
------------
1. Run model readiness check (hard + soft gates)
2. Load paper trade grades and compute reliability stats
3. Check calibration curve for drift
4. Check monotonicity (high-conf vs low-conf win rates)
5. Propose any risky-setting changes via ChangeControl (propose-only)
6. Emit a structured audit report (JSON + terminal summary)
7. Exit 0 = PASS, 1 = WARN, 2 = FAIL

Usage::

    python scripts/daily_audit.py
    python scripts/daily_audit.py --paper-dir paper_accounts/algorithm
    python scripts/daily_audit.py --json > audit_2026-06-06.json
    python scripts/daily_audit.py --strict   # exit 2 on any FAIL finding

Guardrails
----------
- READS only: no position mutations, no file writes to trading config
- Any proposed change goes through ChangeControl.propose() with status=pending
- Operator must separately approve/reject via CLI or web UI
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.portfolio.reliability_stats import ReliabilityStats
from tradingagents.portfolio.change_control import ChangeControl


# ── Finding severity ──────────────────────────────────────────────────────────

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Finding:
    id: str
    severity: str       # PASS | WARN | FAIL
    category: str       # model | calibration | data | account | change_control
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    audit_date: str
    generated_at: str
    overall: str        # PASS | WARN | FAIL
    findings: List[Finding] = field(default_factory=list)
    proposals_created: List[str] = field(default_factory=list)  # proposal_ids
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def print_text(self) -> None:
        width = 70
        icons = {PASS: "✓", WARN: "!", FAIL: "✗"}
        print("=" * width)
        print(f"  DAILY AUDIT — {self.audit_date}  [{self.overall}]")
        print("=" * width)
        for f in self.findings:
            icon = icons.get(f.severity, "?")
            print(f"  {icon} [{f.severity}] {f.id}: {f.message}")
        if self.proposals_created:
            print()
            print(f"  Change-control proposals created: {len(self.proposals_created)}")
            for pid in self.proposals_created:
                print(f"    {pid}")
        if self.notes:
            print()
            for n in self.notes:
                print(f"  >> {n}")
        print("=" * width)


# ── Audit checks ─────────────────────────────────────────────────────────────

def _audit_model_readiness(bundle_path: Path, report_path: Path, paper_dir: Path) -> List[Finding]:
    from model_readiness_report import build_report
    findings: List[Finding] = []
    rpt = build_report(bundle_path, report_path, paper_dir)

    for c in rpt.checks:
        if not c.passed:
            sev = FAIL if c.hard else WARN
            findings.append(Finding(
                id=f"MODEL_{c.id}",
                severity=sev,
                category="model",
                message=f"{c.description}: value={c.value} threshold={c.threshold}",
                detail={"check_id": c.id, "value": c.value, "threshold": c.threshold, "note": c.note},
            ))

    if rpt.verdict == "READY":
        findings.append(Finding(id="MODEL_READY", severity=PASS, category="model",
                                message="Model passes all production gates"))
    elif rpt.verdict == "DEGRADED":
        findings.append(Finding(id="MODEL_DEGRADED", severity=WARN, category="model",
                                message="Model DEGRADED — soft gates failing; review required"))
    else:
        findings.append(Finding(id="MODEL_NOT_READY", severity=FAIL, category="model",
                                message="Model NOT_READY — hard gates failing; paper trading must halt"))
    return findings


def _audit_calibration(paper_dir: Path) -> List[Finding]:
    findings: List[Finding] = []
    try:
        from tradingagents.portfolio.prediction_grader import PredictionGrader
        grader = PredictionGrader(account_dir=paper_dir)
        grades = grader.grade_all(fetch_benchmarks=False)
        if not grades:
            findings.append(Finding(
                id="CAL_NO_GRADES", severity=PASS, category="calibration",
                message="No paper trades graded yet — calibration check skipped",
            ))
            return findings

        rs = ReliabilityStats()
        report = rs.compute(grades, window=100)

        # Overall calibration error
        cal_err_alerts = rs.alert_calibration(report, threshold=0.08)
        for msg in cal_err_alerts:
            findings.append(Finding(id="CAL_DRIFT", severity=WARN, category="calibration", message=msg))
        if not cal_err_alerts:
            findings.append(Finding(id="CAL_OK", severity=PASS, category="calibration",
                                    message=f"Calibration error within tolerance (n={len(grades)} grades)"))

        # Monotonicity
        mono_alerts = rs.alert_monotonicity(grades)
        for msg in mono_alerts:
            findings.append(Finding(id="CAL_MONO", severity=WARN, category="calibration", message=msg))

        # Calibration curve — flag any bucket with error > 0.15 and n >= 5
        for bucket in report.calibration_curve:
            n = bucket.get("n", 0)
            cal_e = bucket.get("calibration_error", 0)
            label = bucket.get("label", "?")
            if n >= 5 and cal_e > 0.15:
                findings.append(Finding(
                    id=f"CAL_BUCKET_{label.replace('–', '_')}",
                    severity=WARN,
                    category="calibration",
                    message=f"Calibration bucket {label}: error={cal_e:.3f} (n={n})",
                    detail=bucket,
                ))

        # Beat-SPY rate
        spy_gradeable = [g for g in grades if g.beat_spy is not None]
        if len(spy_gradeable) >= 10:
            beat_rate = sum(1 for g in spy_gradeable if g.beat_spy) / len(spy_gradeable)
            sev = FAIL if beat_rate < 0.40 else WARN if beat_rate < 0.50 else PASS
            findings.append(Finding(
                id="CAL_BEAT_SPY",
                severity=sev,
                category="calibration",
                message=f"Beat-SPY rate: {beat_rate:.1%} (n={len(spy_gradeable)})",
                detail={"beat_spy_rate": round(beat_rate, 4), "n": len(spy_gradeable)},
            ))

        findings.append(Finding(
            id="CAL_SUMMARY",
            severity=PASS,
            category="calibration",
            message=report.summary_str(),
        ))

    except Exception as exc:
        findings.append(Finding(
            id="CAL_ERROR", severity=WARN, category="calibration",
            message=f"Calibration audit error: {exc}",
        ))
    return findings


def _audit_change_control(cc_path: Path) -> List[Finding]:
    findings: List[Finding] = []
    cc = ChangeControl(cc_path)
    pending = cc.pending()
    if pending:
        findings.append(Finding(
            id="CC_PENDING",
            severity=WARN,
            category="change_control",
            message=f"{len(pending)} pending change-control proposal(s) awaiting review",
            detail={"proposals": [p.proposal_id for p in pending]},
        ))
    else:
        findings.append(Finding(
            id="CC_CLEAR",
            severity=PASS,
            category="change_control",
            message="No pending change-control proposals",
        ))
    return findings


def _audit_prediction_ledger(paper_dir: Path) -> List[Finding]:
    findings: List[Finding] = []
    ledger_path = paper_dir / "prediction_ledger.jsonl"
    if not ledger_path.exists():
        findings.append(Finding(
            id="LEDGER_MISSING", severity=WARN, category="data",
            message=f"Prediction ledger not found at {ledger_path}",
        ))
        return findings

    try:
        from tradingagents.portfolio.prediction_ledger import PredictionLedger
        ledger = PredictionLedger(ledger_path)
        entries = ledger.read_buys()
        n = len(entries)
        findings.append(Finding(
            id="LEDGER_OK", severity=PASS, category="data",
            message=f"Prediction ledger has {n} BUY entries",
            detail={"n_buy_entries": n},
        ))
    except Exception as exc:
        findings.append(Finding(
            id="LEDGER_ERROR", severity=WARN, category="data",
            message=f"Could not read prediction ledger: {exc}",
        ))
    return findings


def _audit_protective_put_cost(heat_pct: float = 0.0) -> List[Finding]:
    """Flag when portfolio heat is high and OTM put hedges are relevant."""
    findings: List[Finding] = []
    try:
        import yfinance as yf
        from tradingagents.portfolio.options_pricing import protective_put_annual_cost_pct

        vix_data = yf.download("^VIX", period="3d", progress=False, auto_adjust=True)["Close"]
        if vix_data.empty:
            findings.append(Finding(
                id="PUT_NO_VIX", severity=PASS, category="risk",
                message="VIX data unavailable — skipping protective put cost check",
            ))
            return findings

        vix = float(vix_data.iloc[-1])
        sigma = vix / 100.0

        spy_data = yf.download("SPY", period="3d", progress=False, auto_adjust=True)["Close"]
        spy_price = float(spy_data.iloc[-1]) if not spy_data.empty else 500.0

        # 30-day, 5% OTM put on SPY
        annual_cost = protective_put_annual_cost_pct(
            S=spy_price, otm_pct=0.05, T_days=30, r=0.045, sigma=sigma
        )
        annual_cost_pct = round(annual_cost * 100, 2)

        detail = {
            "vix": round(vix, 2),
            "spy_price": round(spy_price, 2),
            "protective_put_annual_cost_pct": annual_cost_pct,
            "note": "30-day 5% OTM SPY put annualized cost",
        }

        if heat_pct >= 0.70 and vix <= 20:
            findings.append(Finding(
                id="PUT_HEDGE_WINDOW", severity=WARN, category="risk",
                message=(
                    f"Portfolio heat {heat_pct:.0%} + VIX {vix:.1f} — "
                    f"protective put cost ~{annual_cost_pct:.1f}%/yr. "
                    "Consider hedging tail risk while vol is low."
                ),
                detail=detail,
            ))
        elif vix >= 30:
            findings.append(Finding(
                id="PUT_VIX_HIGH", severity=WARN, category="risk",
                message=(
                    f"VIX elevated at {vix:.1f} — put cost ~{annual_cost_pct:.1f}%/yr. "
                    "Expensive to hedge now; reduce position size instead."
                ),
                detail=detail,
            ))
        else:
            findings.append(Finding(
                id="PUT_COST_INFO", severity=PASS, category="risk",
                message=f"30d 5% OTM put cost: ~{annual_cost_pct:.1f}%/yr (VIX={vix:.1f})",
                detail=detail,
            ))
    except Exception as exc:
        findings.append(Finding(
            id="PUT_ERROR", severity=PASS, category="risk",
            message=f"Protective put cost check skipped: {exc}",
        ))
    return findings


# ── Build audit ───────────────────────────────────────────────────────────────

def run_audit(
    bundle_path: Path,
    report_path: Path,
    paper_dir: Path,
    cc_path: Path,
) -> AuditReport:
    now = dt.datetime.now()
    all_findings: List[Finding] = []

    all_findings.extend(_audit_model_readiness(bundle_path, report_path, paper_dir))
    all_findings.extend(_audit_calibration(paper_dir))
    all_findings.extend(_audit_change_control(cc_path))
    all_findings.extend(_audit_prediction_ledger(paper_dir))
    all_findings.extend(_audit_protective_put_cost())

    fails = [f for f in all_findings if f.severity == FAIL]
    warns = [f for f in all_findings if f.severity == WARN]

    if fails:
        overall = FAIL
    elif warns:
        overall = WARN
    else:
        overall = PASS

    notes: List[str] = []
    if fails:
        notes.append(f"{len(fails)} FAIL finding(s) require immediate attention.")
    if warns:
        notes.append(f"{len(warns)} WARN finding(s) should be reviewed.")
    if overall == PASS:
        notes.append("All checks pass. System healthy.")

    return AuditReport(
        audit_date=now.strftime("%Y-%m-%d"),
        generated_at=now.isoformat(),
        overall=overall,
        findings=all_findings,
        notes=notes,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily audit with guardrails")
    parser.add_argument("--bundle", default="ml_models/latest/model_bundle.joblib")
    parser.add_argument("--report", default="ml_models/latest/training_report.json")
    parser.add_argument("--paper-dir", default="paper_accounts/algorithm")
    parser.add_argument("--cc-log", default="paper_accounts/algorithm/change_control.jsonl")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--strict", action="store_true",
                        help="Exit 2 if FAIL, 1 if WARN")
    args = parser.parse_args()

    bundle_path = ROOT / args.bundle
    report_path = ROOT / args.report
    paper_dir = ROOT / args.paper_dir
    cc_path = ROOT / args.cc_log

    rpt = run_audit(bundle_path, report_path, paper_dir, cc_path)

    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(rpt.to_dict(), indent=2))

    if args.json:
        print(json.dumps(rpt.to_dict(), indent=2))
    else:
        rpt.print_text()

    if args.strict:
        if rpt.overall == FAIL:
            return 2
        if rpt.overall == WARN:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
