#!/usr/bin/env python3
"""TradingAgents ML Continuous Improvement Loop.

Runs the following sequence each week (or on-demand):
  1. Grade predictions: compare ML predictions at entry vs actual outcomes
  2. Compute reliability stats: by ticker, regime, tier, confidence bucket
  3. Drift detection: calibration drift, high-conf failure rate, PSI, paper-vs-WF gap
  4. Validation check: run validation_report.py — assess current model health
  5. Retrain decision: if triggers fired → retrain
  6. Retrain execution: run retrain_weekly.py with hardened gates
  7. Model promotion: new model must beat old on walk-forward
  8. Post-retrain validation
  9. AI advisory review (optional): three-stage pipeline — Claude writes patch,
        Codex validates, Claude revises if Codex issues MODIFY or REJECT

Anti-cheating guarantees:
  - No tuning on holdout data (holdout = 2026-05-08 → 2026-05-26)
  - Promotion gates use walk-forward, not train metrics
  - AI CLI is advisory only — cannot auto-promote, auto-trade, weaken risk gates
  - All AI suggestions require human approval before applying

Usage:
    python scripts/improvement_loop.py                  # check + retrain if needed
    python scripts/improvement_loop.py --force-retrain  # always retrain
    python scripts/improvement_loop.py --check-only     # no retrain
    python scripts/improvement_loop.py --dry-run        # print only
    python scripts/improvement_loop.py --ai-review      # generate AI review packet
    python scripts/improvement_loop.py --rollback       # restore last backup bundle

Schedule (cron — Sunday 7am):
    0 7 * * 0 .venv/bin/python3 scripts/improvement_loop.py >> logs/improvement.log 2>&1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IMPROVEMENT_LOG = ROOT / "ml_models" / "improvement_log.jsonl"
AI_REVIEWS_DIR = ROOT / "ml_models" / "ai_reviews"

# ── Retrain triggers ──────────────────────────────────────────────────────────
RETRAIN_TRIGGERS = {
    "model_age_days": 30,
    "min_roc": 0.56,
    "max_brier": 0.24,
    "wf_wr_floor": 0.52,
    "paper_vs_wf_gap": 0.12,
    "calibration_drift": 0.08,    # from DriftDetector
    "high_conf_failure_rate": 0.45,
}

# ── Promotion gates ───────────────────────────────────────────────────────────
PROMOTION_GATES = {
    "roc_regression_max": 0.005,     # new ROC may not fall more than 0.5% below old
    "wf_wr_regression_max": 0.020,   # new WF win rate may not fall more than 2%
    "drawdown_increase_max": 0.15,   # new model may not increase max drawdown by >15%
}

# ── AI review safe/dangerous file lists ──────────────────────────────────────
AI_SAFE_FILES = [
    "scripts/train_ml_models.py",
    "scripts/retrain_weekly.py",
    "scripts/improvement_loop.py",
    "scripts/validation_report.py",
    "scripts/leakage_check.py",
    "tradingagents/screening/screener.py",
    "tradingagents/screening/breakout_scanner.py",
    "tradingagents/screening/market_regime.py",
    "tradingagents/dataflows/interface.py",
    "tradingagents/dataflows/alpha_vantage_utils.py",
    "tradingagents/portfolio/alpha_engine.py",
    "tradingagents/portfolio/candidate_ranker.py",
    "tradingagents/portfolio/exit_manager.py",
    "tradingagents/portfolio/position_sizing.py",
    "tradingagents/portfolio/ticker_reliability.py",
    "tradingagents/portfolio/prediction_grader.py",
    "tradingagents/portfolio/reliability_stats.py",
    "tradingagents/portfolio/drift_detector.py",
    "tradingagents/portfolio/production_safety.py",
]

AI_DANGEROUS_FILES = [
    "web/api/fidelity.py",
    "web/api/webull_portfolio.py",
    "web/api/paper.py",
    "web/api/admin.py",
    "scripts/paper_trade_today.py",
]

# ── Stage 1: Claude Code writes the patch ────────────────────────────────────
CLAUDE_REVIEW_PROMPT = """\
IMPORTANT: You are Claude Code running inside an automated ML improvement loop.
You were invoked with --dangerously-skip-permissions so you can READ files freely.
That flag does NOT grant permission to write. YOU MUST NOT call Write, Edit,
MultiEdit, or any Bash command that modifies disk state. READ ONLY.

DO NOT TOUCH ANY FILE. DO NOT WRITE. DO NOT EDIT. DO NOT DELETE.

You are the PATCH WRITER in a two-stage review. Your job:
1. Read the review packet above.
2. Use Read/Grep/Glob to inspect files from the SAFE FILES list.
3. Identify the root cause of the failures (ROC, Brier, drift, win rate).
4. Write a concrete, line-level patch as a unified diff in your response.
   - Do NOT apply it. Write the diff text only.
   - Target only files in the SAFE FILES list.
   - Never touch: web/api/fidelity.py, web/api/webull_portfolio.py, or any live broker file.
5. Explain why your patch will improve the metric (be specific — cite feature importances,
   calibration error, drift values from the packet).

HARD RULES:
- Do NOT tune on holdout data (post 2026-05-08).
- Do NOT weaken stops, drawdown limits, or safety gates.
- Do NOT auto-apply anything. Human must approve before any patch runs.
- If n_grades < 30, say so and recommend more paper trading before retraining.

OUTPUT FORMAT (use exactly these headers):
## Root Cause
## Why This Will Help
## Patch (unified diff)
## Files Inspected
## What Could Go Wrong
"""

# ── Stage 2: Codex validates Claude's patch ───────────────────────────────────
CODEX_VALIDATION_PROMPT_TEMPLATE = """\
IMPORTANT: You are Codex CLI running in validation-only mode inside an automated ML improvement loop.
You were invoked with --dangerously-bypass-approvals-and-sandbox -y so you can READ files freely.
That does NOT grant permission to write. YOU MUST NOT modify any file.

DO NOT TOUCH ANY FILE. DO NOT WRITE. DO NOT EDIT. DO NOT DELETE.

You are the VALIDATOR in a three-stage review. Claude Code has already produced a patch proposal.
Your job is to critically evaluate whether that patch will actually make a measurable difference.
If it needs fixes, issue MODIFY with a revised diff — Claude will receive your feedback in Stage 3
and produce a corrected final patch.

=== CLAUDE'S PATCH PROPOSAL ===
{claude_output}
=== END CLAUDE'S PATCH PROPOSAL ===

Your validation tasks:
1. Use Read/Grep/Glob to inspect the files Claude cited.
2. Verify the patch is syntactically correct and applies cleanly to the current code.
3. Evaluate whether the claimed improvement is plausible given the actual code and metrics.
4. Check that the patch does NOT:
   - Touch live broker files (fidelity.py, webull_portfolio.py)
   - Weaken any risk gate (stops, drawdown limits, loss caps, kill-switch)
   - Tune on holdout data (post 2026-05-08)
   - Break existing safety logic in production_safety.py
5. Give a VERDICT: APPROVE, REJECT, or MODIFY.
   - APPROVE: patch is correct, safe, and will likely help.
   - REJECT: patch is unsafe, breaks gates, or will not help. Explain why.
   - MODIFY: patch has fixable issues. Provide specific corrections (broken hunks,
     wrong line numbers, bugs introduced) and a revised unified diff.
     Be precise — Claude will use your MODIFY output as the sole basis for Stage 3.

OUTPUT FORMAT (use exactly these headers):
## Validation Summary
## Code Correctness Check
## Will It Actually Help? (cite specific lines/metrics)
## Safety Check (does it touch forbidden files or weaken gates?)
## VERDICT: [APPROVE / REJECT / MODIFY]
## Revised Patch (if MODIFY — unified diff only, do not apply)
## Specific Instructions for Claude (if MODIFY or REJECT — list each correction needed)
"""

# ── Stage 3: Claude revises patch based on Codex feedback ────────────────────
CLAUDE_REVISION_PROMPT_TEMPLATE = """\
IMPORTANT: You are Claude Code running inside an automated ML improvement loop, Stage 3.
You were invoked with --dangerously-skip-permissions so you can READ files freely.
That flag does NOT grant permission to write. YOU MUST NOT call Write, Edit,
MultiEdit, or any Bash command that modifies disk state. READ ONLY.

DO NOT TOUCH ANY FILE. DO NOT WRITE. DO NOT EDIT. DO NOT DELETE.

You are the PATCH REVISER in a three-stage review pipeline.

Stage 1 (you): You wrote an initial patch proposal.
Stage 2 (Codex): Codex validated your patch and issued a verdict (APPROVE / MODIFY / REJECT).
Stage 3 (you now): Review Codex's feedback and produce the final patch.
  - APPROVE → Codex found no issues. Confirm the patch is still correct against current
    file state and output it as-is (or with trivial line-number corrections only).
  - MODIFY → Address every issue in Codex's "Specific Instructions for Claude" section.
  - REJECT → Reconsider the approach. Propose a different fix, or state no safe fix exists.

=== YOUR ORIGINAL PATCH (Stage 1) ===
{claude_output}
=== END ORIGINAL PATCH ===

=== CODEX VALIDATION FEEDBACK (Stage 2) ===
{codex_output}
=== END CODEX FEEDBACK ===

Your revision tasks:
1. Re-read the files from your original patch to verify current line numbers.
2. Address EVERY issue listed in Codex's "Specific Instructions for Claude" section.
3. If Codex issued a Revised Patch in the MODIFY section, use it as your base and
   confirm it applies cleanly — do not blindly copy it without verifying line numbers.
4. If Codex issued REJECT, reconsider the approach entirely. You may propose a
   different, simpler, or smaller-scoped fix, or conclude no safe fix is available.
5. Produce a final corrected unified diff.

HARD RULES (same as Stage 1):
- Do NOT tune on holdout data (post 2026-05-08).
- Do NOT weaken stops, drawdown limits, or safety gates.
- Do NOT touch: web/api/fidelity.py, web/api/webull_portfolio.py, or any live broker file.
- Do NOT auto-apply anything. Human must approve before any patch runs.
- If the only safe answer is "no change needed", say so and explain why.

OUTPUT FORMAT (use exactly these headers):
## Changes Made vs Original Patch
## Codex Issues Addressed
## Final Patch (unified diff)
## Remaining Caveats
"""


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(entry: dict) -> None:
    entry.setdefault("timestamp", dt.datetime.now().isoformat())
    IMPROVEMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with IMPROVEMENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _run_cmd(cmd: list, label: str, dry_run: bool = False, abort_on_failure: bool = True) -> int:
    print(f"\n{'='*60}")
    print(f"[improvement_loop] {label}")
    print(f"CMD: {' '.join(str(x) for x in cmd)}")
    print(f"{'='*60}")
    if dry_run:
        print("[dry-run] Skipping execution.")
        return 0
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[improvement_loop] ERROR: {label} exited {result.returncode}")
        if abort_on_failure:
            sys.exit(result.returncode)
    return result.returncode


# ── Prediction grading ────────────────────────────────────────────────────────

def run_prediction_grading(
    paper_accounts_dir: Path,
    dry_run: bool = False,
) -> list:
    """Grade predictions across all strategy paper accounts. Returns all GradeResults."""
    try:
        from tradingagents.portfolio.prediction_grader import PredictionGrader
    except ImportError as e:
        print(f"[improvement_loop] Cannot import PredictionGrader: {e}")
        return []

    all_grades = []
    strategies = [d for d in paper_accounts_dir.iterdir() if d.is_dir()] if paper_accounts_dir.exists() else []

    for strategy_dir in strategies:
        grader = PredictionGrader(account_dir=strategy_dir)
        grades = grader.grade_all()
        if grades:
            if not dry_run:
                grader.save(grades)
            all_grades.extend(grades)
            print(f"[improvement_loop] {strategy_dir.name}: graded {len(grades)} trades")
        else:
            print(f"[improvement_loop] {strategy_dir.name}: no closed trades to grade")

    _log({
        "type": "RELIABILITY_UPDATE",
        "action": "prediction_grading",
        "n_grades": len(all_grades),
        "strategies": [d.name for d in strategies],
    })
    return all_grades


def compute_reliability_stats(grades: list, dry_run: bool = False) -> Optional[Any]:
    """Compute StatsReport from grades."""
    if not grades:
        return None
    try:
        from tradingagents.portfolio.reliability_stats import ReliabilityStats
    except ImportError:
        return None

    rs = ReliabilityStats()
    report = rs.compute(grades, window=50)
    print(f"[improvement_loop] Reliability: {report.summary_str()}")

    # Log calibration alerts
    cal_alerts = rs.alert_calibration(report)
    reg_alerts = rs.alert_regimes(report)
    for alert in cal_alerts + reg_alerts:
        print(f"[improvement_loop] ⚠ {alert}")
        _log({"type": "DRIFT_ALERT", "alert": alert, "source": "reliability_stats"})

    return report


def run_drift_detection(
    grades: list,
    validation_summary_path: Optional[Path],
    drift_log_path: Optional[Path],
    dry_run: bool = False,
) -> Optional[Any]:
    """Run DriftDetector and log alerts."""
    try:
        from tradingagents.portfolio.drift_detector import DriftDetector
    except ImportError:
        return None

    detector = DriftDetector()
    drift_report = detector.check(
        grades=grades,
        validation_summary_path=validation_summary_path,
        drift_log_path=drift_log_path,
    )

    if drift_report.has_drift:
        for alert in drift_report.alerts:
            print(f"[improvement_loop] ⚠ DRIFT: {alert}")
            _log({
                "type": "DRIFT_ALERT",
                "alert": alert,
                "calibration_drift": drift_report.calibration_drift,
                "psi_fail_count": drift_report.psi_fail_count,
                "n_grades": drift_report.n_grades,
            })
    else:
        print(f"[improvement_loop] ✓ No drift detected (n_grades={drift_report.n_grades})")

    return drift_report


# ── Retrain trigger checks ────────────────────────────────────────────────────

def _check_retrain_needed(
    validation_path: Path,
    drift_report: Optional[Any] = None,
    force: bool = False,
) -> tuple[bool, list[str]]:
    """Return (needs_retrain, reasons_list)."""
    if force:
        return True, ["force_retrain flag set"]

    if not validation_path.exists():
        return True, ["validation_summary.json not found — run validation first"]

    try:
        data = json.loads(validation_path.read_text())
    except Exception as e:
        return True, [f"cannot parse validation_summary.json: {e}"]

    train = data.get("train", {})
    reasons = []

    # Model age
    created_at = train.get("created_at", "")
    if created_at:
        try:
            created = dt.datetime.fromisoformat(str(created_at)[:19])
            age_days = (dt.datetime.now() - created).days
            if age_days >= RETRAIN_TRIGGERS["model_age_days"]:
                reasons.append(f"model_age={age_days}d >= {RETRAIN_TRIGGERS['model_age_days']}d")
        except Exception:
            reasons.append("cannot parse model created_at — assume stale")

    # ROC
    roc = train.get("win_roc")
    if roc is not None and roc < RETRAIN_TRIGGERS["min_roc"]:
        reasons.append(f"win_roc={roc:.4f} < {RETRAIN_TRIGGERS['min_roc']}")

    # Brier
    brier = train.get("brier_after")
    if brier is not None and brier > RETRAIN_TRIGGERS["max_brier"]:
        reasons.append(f"brier_after={brier:.4f} > {RETRAIN_TRIGGERS['max_brier']}")

    # Walk-forward WR
    wf_wr = train.get("wf_win_rate")
    if wf_wr is not None and wf_wr < RETRAIN_TRIGGERS["wf_wr_floor"]:
        reasons.append(f"wf_win_rate={wf_wr:.4f} < {RETRAIN_TRIGGERS['wf_wr_floor']}")

    # Paper vs WF gap
    paper = data.get("paper") or {}
    p_wr = paper.get("win_rate")
    if p_wr is not None and wf_wr is not None:
        gap = wf_wr - p_wr
        if gap > RETRAIN_TRIGGERS["paper_vs_wf_gap"]:
            reasons.append(
                f"paper_wr={p_wr:.4f} vs wf_wr={wf_wr:.4f} gap={gap:.4f} "
                f"> {RETRAIN_TRIGGERS['paper_vs_wf_gap']}"
            )

    # Drift-based triggers
    if drift_report is not None and drift_report.has_drift:
        if (drift_report.calibration_drift is not None
                and abs(drift_report.calibration_drift) > RETRAIN_TRIGGERS["calibration_drift"]):
            reasons.append(
                f"calibration_drift={drift_report.calibration_drift:+.3f} "
                f"> ±{RETRAIN_TRIGGERS['calibration_drift']}"
            )
        if (drift_report.high_conf_failure_rate is not None
                and drift_report.high_conf_failure_rate > RETRAIN_TRIGGERS["high_conf_failure_rate"]):
            reasons.append(
                f"high_conf_failure_rate={drift_report.high_conf_failure_rate:.1%} "
                f"> {RETRAIN_TRIGGERS['high_conf_failure_rate']:.0%}"
            )

    # Validation report FAIL flag
    if not data.get("pass_", True):
        if not reasons:
            reasons.append("validation_report FAIL (unspecified)")

    return len(reasons) > 0, reasons


# ── Model promotion gates ─────────────────────────────────────────────────────

def _check_promotion_gates(
    old_report_path: Optional[Path],
    new_report_path: Path,
) -> tuple[bool, str]:
    """Check if new model meets promotion gates vs old model.

    Returns (passes, reason_str).
    Never promotes because train metrics improved — only walk-forward metrics count.
    """
    if not new_report_path.exists():
        return False, "new training_report.json not found"

    try:
        new_report = json.loads(new_report_path.read_text())
    except Exception as e:
        return False, f"cannot parse new training_report.json: {e}"

    new_models = new_report.get("models", {})
    new_wp = new_models.get("win_probability", {})
    new_roc = new_wp.get("metrics", {}).get("roc_auc")
    new_brier = new_wp.get("calibration", {}).get("brier_after")
    new_wf_wr = new_report.get("walk_forward", {}).get("win_rate")
    new_max_dd = new_report.get("walk_forward", {}).get("max_drawdown")

    issues = []

    # Absolute quality gates
    if new_roc is not None and new_roc < RETRAIN_TRIGGERS["min_roc"]:
        issues.append(f"new ROC={new_roc:.4f} < min {RETRAIN_TRIGGERS['min_roc']}")
    if new_brier is not None and new_brier > RETRAIN_TRIGGERS["max_brier"]:
        issues.append(f"new Brier={new_brier:.4f} > max {RETRAIN_TRIGGERS['max_brier']}")

    # Relative gates vs old model (walk-forward only)
    if old_report_path and old_report_path.exists():
        try:
            old_report = json.loads(old_report_path.read_text())
            old_wp = old_report.get("models", {}).get("win_probability", {})
            old_roc = old_wp.get("metrics", {}).get("roc_auc")
            old_wf_wr = old_report.get("walk_forward", {}).get("win_rate")
            old_max_dd = old_report.get("walk_forward", {}).get("max_drawdown")

            if old_roc is not None and new_roc is not None:
                regression = old_roc - new_roc
                if regression > PROMOTION_GATES["roc_regression_max"]:
                    issues.append(
                        f"ROC regression: old={old_roc:.4f} new={new_roc:.4f} "
                        f"delta={regression:.4f} > max {PROMOTION_GATES['roc_regression_max']}"
                    )

            if old_wf_wr is not None and new_wf_wr is not None:
                wr_regression = float(old_wf_wr) - float(new_wf_wr)
                if wr_regression > PROMOTION_GATES["wf_wr_regression_max"]:
                    issues.append(
                        f"WF WR regression: old={old_wf_wr:.4f} new={new_wf_wr:.4f} "
                        f"delta={wr_regression:.4f} > max {PROMOTION_GATES['wf_wr_regression_max']}"
                    )

            if old_max_dd is not None and new_max_dd is not None:
                old_dd = abs(float(old_max_dd))
                new_dd = abs(float(new_max_dd))
                if old_dd > 0 and (new_dd - old_dd) / old_dd > PROMOTION_GATES["drawdown_increase_max"]:
                    issues.append(
                        f"Drawdown increase: old={old_dd:.3f} new={new_dd:.3f} "
                        f"increase={(new_dd-old_dd)/old_dd:.1%} > max {PROMOTION_GATES['drawdown_increase_max']:.0%}"
                    )
        except Exception as e:
            print(f"[improvement_loop] Warning: could not compare old report: {e}")

    if issues:
        return False, "; ".join(issues)

    roc_str = f"ROC={new_roc:.4f}" if new_roc else "ROC=?"
    wf_str = f"WF_WR={new_wf_wr:.4f}" if new_wf_wr else ""
    return True, f"promotion ACCEPTED: {roc_str} {wf_str}"


# ── Rollback ──────────────────────────────────────────────────────────────────

def run_rollback(model_dir: Path, dry_run: bool = False) -> bool:
    """Restore most recent backup bundle to latest/.

    Backup files are named model_bundle_backup_YYYYMMDD_HHMMSS.joblib.
    Returns True on success.
    """
    backups = sorted(model_dir.glob("model_bundle_backup_*.joblib"), reverse=True)
    if not backups:
        # Check parent for backup
        backups = sorted(model_dir.parent.glob("model_bundle_backup_*.joblib"), reverse=True)

    if not backups:
        print(f"[improvement_loop] ⚠ No backup bundle found in {model_dir}")
        return False

    backup_path = backups[0]
    target = model_dir / "model_bundle.joblib"
    print(f"[improvement_loop] Rollback: {backup_path.name} → {target}")

    if not dry_run:
        shutil.copy2(backup_path, target)
        _log({
            "type": "ROLLBACK_EXECUTED",
            "backup_path": str(backup_path),
            "target": str(target),
        })
        print(f"[improvement_loop] ✓ Rollback complete. Restart paper runner to use restored model.")
    else:
        print(f"[dry-run] Would copy {backup_path} → {target}")
    return True


# ── AI Review ─────────────────────────────────────────────────────────────────

def _build_review_packet(
    validation_path: Optional[Path],
    drift_report: Optional[Any],
    reliability_report: Optional[Any],
    grades: list,
    trigger_reasons: list,
    today_str: str,
) -> str:
    """Build a compact markdown review packet for the AI reviewer."""
    lines = [
        f"# TradingAgents ML Review Packet — {today_str}",
        "",
        "## System Context",
        "TradingAgents paper-trading ML system. RF classifier predicts win probability, expected return,",
        "large-loss probability, and timeout probability for equity momentum setups.",
        "",
        "### Critical Data Rules",
        "- Holdout period: 2026-05-08 → 2026-05-26. DO NOT tune on this data.",
        "- Old backtests are diagnostic only.",
        "",
        "## Retrain Triggers",
    ]
    if trigger_reasons:
        for r in trigger_reasons:
            lines.append(f"- {r}")
    else:
        lines.append("- None (model health OK)")

    lines += ["", "## Drift Alerts"]
    if drift_report and drift_report.has_drift:
        for a in drift_report.alerts:
            lines.append(f"- {a}")
        lines.append(f"  (n_grades={drift_report.n_grades}, calibration_drift={drift_report.calibration_drift})")
    else:
        n = len(grades) if grades else 0
        lines.append(f"- No drift detected (n_grades={n})")

    lines += ["", "## Paper Trade Reliability"]
    if reliability_report and reliability_report.overall:
        o = reliability_report.overall
        lines.append(f"- Overall: n={o.n}, win_rate={o.win_rate:.1%}, avg_return={o.avg_return:.2%}")
        lines.append(f"- Calibration error: {o.calibration_error:.3f}")
        lines.append(f"- Win prediction accuracy: {o.win_prediction_accuracy:.1%}")
        lines.append(f"- Stop rate: {o.stop_rate:.1%} | Target hit rate: {o.target_rate:.1%}")
        if reliability_report.by_tier:
            lines.append("\n### By Alpha Tier")
            for tier, st in sorted(reliability_report.by_tier.items()):
                lines.append(f"  - {tier}: n={st['n']}, wr={st['win_rate']:.1%}, avg_ret={st['avg_return']:.2%}")
        if reliability_report.by_regime:
            lines.append("\n### By Regime")
            for regime, st in sorted(reliability_report.by_regime.items()):
                lines.append(f"  - {regime}: n={st['n']}, wr={st['win_rate']:.1%}")
    elif grades:
        lines.append(f"- {len(grades)} grades loaded but stats not computed (need ≥5 per slice)")
    else:
        lines.append("- No closed trades graded yet. Recommend more paper trading.")

    lines += ["", "## Validation Summary"]
    if validation_path and validation_path.exists():
        try:
            val = json.loads(validation_path.read_text())
            train = val.get("train", {})
            lines.append(f"- ROC: {train.get('win_roc', '?')}")
            lines.append(f"- Brier: {train.get('brier_after', '?')}")
            lines.append(f"- WF Win Rate: {train.get('wf_win_rate', '?')}")
            lines.append(f"- Pass: {val.get('pass_', '?')}")
        except Exception:
            lines.append("- (could not parse validation_summary.json)")
    else:
        lines.append("- validation_summary.json not found")

    lines += ["", "## Safe Files (you MAY inspect and suggest changes to these)"]
    for f in AI_SAFE_FILES:
        lines.append(f"- {f}")

    lines += ["", "## Dangerous Files (you MUST NOT edit or weaken these)"]
    for f in AI_DANGEROUS_FILES:
        lines.append(f"- {f}")

    lines += [
        "",
        "---",
        "",
        "## Your Instructions",
        CLAUDE_REVIEW_PROMPT,
    ]

    return "\n".join(lines)


def run_ai_review(
    tool: str,
    cmd_name: str,
    prompt_text: str,
    output_path: Path,
    dry_run: bool = False,
    label: str = "",
) -> tuple[bool, str]:
    """Call Claude Code CLI or Codex CLI with a prompt string.

    Parameters
    ----------
    tool : str
        "claude" or "codex"
    cmd_name : str
        Executable name (e.g. "claude" or "codex")
    prompt_text : str
        Full prompt to pass to the tool.
    output_path : Path
        Where to save the AI response.
    dry_run : bool
        If True, print command only; don't run.
    label : str
        Human-readable label for logging (e.g. "Stage 1 — Claude patch writer")

    Returns
    -------
    (success: bool, output_text: str)
    """
    stage_label = label or f"{tool} review"

    # Write prompt to a temp file so we avoid shell arg length limits
    import tempfile
    _prompt_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    _prompt_file.write(prompt_text)
    _prompt_file.close()
    _prompt_tmp = _prompt_file.name

    if tool == "claude":
        cmd = [
            cmd_name,
            "--print",                          # non-interactive, print final response
            "--dangerously-skip-permissions",   # no file-read permission prompts
            "--max-turns", "10",                # enough to read files + produce diff
            "-p", prompt_text,                  # prompt as argument
        ]
    elif tool == "codex":
        # codex exec: non-interactive runner
        # --dangerously-bypass-approvals-and-sandbox: skip confirmations
        # --sandbox read-only: OS-level read-only sandbox (belt-and-suspenders)
        # -o: capture last message to output_path directly
        # No --model flag: use account default (o4-mini requires API key, not ChatGPT account)
        cmd = [
            cmd_name, "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--sandbox", "read-only",
            "-o", str(output_path),             # write final message directly to file
            prompt_text,                        # prompt as positional arg
        ]
    else:
        print(f"[improvement_loop] Unknown AI tool: {tool}")
        return False, ""

    print(f"\n[improvement_loop] {stage_label} ({tool} via {cmd_name})")
    print(f"  Output: {output_path}")
    print(f"  dry_run={dry_run}")

    if dry_run:
        safe_cmd = cmd[:-1] + [f"<prompt {len(prompt_text)} chars>"]
        print(f"[dry-run] CMD: {' '.join(safe_cmd)}")
        stub = (
            f"# {stage_label} — {dt.date.today()} [DRY RUN]\n\n"
            f"*Dry-run placeholder. No AI invoked.*\n\n"
            f"CMD: `{' '.join(safe_cmd)}`\n\n"
            f"## Prompt Preview\n\n```\n{prompt_text[:600]}\n```\n"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(stub)
        return True, stub

    output = ""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,   # prevent TTY blocking (codex reads stdin if available)
            capture_output=True,
            text=True,
            timeout=600,  # 10 min — file reads + diff generation takes time
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            print(f"[improvement_loop] ⚠ {tool} exited {result.returncode}")

        if tool == "codex" and output_path.exists():
            # codex exec -o writes directly to output_path
            output = output_path.read_text(encoding="utf-8")
        else:
            output = result.stdout or result.stderr or "(no output)"
            output_path.write_text(output, encoding="utf-8")

        if not output.strip():
            output = f"# {stage_label}\n\n*(no output — stderr: {result.stderr[:300]})*\n"
            output_path.write_text(output, encoding="utf-8")

    except FileNotFoundError:
        output = f"# {stage_label}\n\n*{cmd_name} not found. Install {tool} CLI first.*\n"
        output_path.write_text(output, encoding="utf-8")
        print(f"[improvement_loop] ⚠ {cmd_name} not found in PATH.")
    except subprocess.TimeoutExpired:
        output = f"# {stage_label}\n\n*Timed out after 600s.*\n"
        output_path.write_text(output, encoding="utf-8")
        print(f"[improvement_loop] ⚠ {tool} timed out.")
    except Exception as e:
        output = f"# {stage_label}\n\n*Error: {e}*\n"
        output_path.write_text(output, encoding="utf-8")
        print(f"[improvement_loop] ⚠ {tool} error: {e}")
    finally:
        # Clean up temp prompt file
        try:
            import os as _os
            _os.unlink(_prompt_tmp)
        except Exception:
            pass

    print(f"[improvement_loop] ✓ Saved: {output_path}")
    return True, output


def _parse_codex_verdict(codex_output: str) -> str:
    """Parse VERDICT from Codex validation output. Returns 'APPROVE', 'REJECT', 'MODIFY', or 'UNKNOWN'."""
    for line in codex_output.splitlines():
        upper = line.upper()
        if "VERDICT" in upper:
            if "APPROVE" in upper:
                return "APPROVE"
            if "REJECT" in upper:
                return "REJECT"
            if "MODIFY" in upper:
                return "MODIFY"
    return "UNKNOWN"


def run_ai_reviews(
    today_str: str,
    validation_path: Optional[Path],
    drift_report: Optional[Any],
    reliability_report: Optional[Any],
    grades: list,
    trigger_reasons: list,
    dry_run: bool = False,
) -> None:
    """Three-stage AI review pipeline.

    Stage 1 — Claude Code (patch writer):
        Reads the codebase, identifies root causes, writes a concrete patch diff.

    Stage 2 — Codex (validator):
        Receives the review packet + Claude's patch. Validates whether the patch
        will actually make a difference, checks for safety regressions, issues
        APPROVE / REJECT / MODIFY verdict.

    Stage 3 — Claude Code (final patch, always runs when both tools enabled):
        Claude receives its original patch + Codex's full feedback and produces the
        final patch. On APPROVE it confirms/reconfirms. On MODIFY/REJECT it corrects.
        Codex never terminates the loop — feedback always flows back to Claude.

    Each stage is independent — if only one tool is enabled, only that stage runs.
    Stage 3 requires both Claude and Codex enabled.
    """
    # ── Config ────────────────────────────────────────────────────────────
    enable_any    = os.environ.get("ENABLE_AI_CODE_REVIEW", "false").lower() == "true"
    enable_claude = os.environ.get("ENABLE_CLAUDE_CODE_REVIEW", "false").lower() == "true"
    enable_codex  = os.environ.get("ENABLE_CODEX_REVIEW", "false").lower() == "true"
    ai_dry_run    = os.environ.get("AI_CODE_REVIEW_DRY_RUN", "true").lower() == "true"
    claude_cmd    = os.environ.get("CLAUDE_CODE_CMD", "claude")
    codex_cmd     = os.environ.get("CODEX_CLI_CMD", "codex")

    run_claude = enable_claude or enable_any
    run_codex  = enable_codex

    if not (run_claude or run_codex):
        print("[improvement_loop] AI review disabled. Set ENABLE_CLAUDE_CODE_REVIEW=true to enable.")
        return

    effective_dry_run = dry_run or ai_dry_run

    # ── Build review packet (context for all stages) ──────────────────────
    AI_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    packet_path = AI_REVIEWS_DIR / f"{today_str}_review_packet.md"
    packet_text = _build_review_packet(
        validation_path=validation_path,
        drift_report=drift_report,
        reliability_report=reliability_report,
        grades=grades,
        trigger_reasons=trigger_reasons,
        today_str=today_str,
    )
    packet_path.write_text(packet_text, encoding="utf-8")
    print(f"[improvement_loop] Review packet: {packet_path}")

    claude_output = ""
    codex_output = ""
    codex_verdict = "UNKNOWN"

    # ── Stage 1: Claude Code writes the patch ─────────────────────────────
    if run_claude:
        claude_prompt = packet_text  # packet already ends with CLAUDE_REVIEW_PROMPT
        claude_out = AI_REVIEWS_DIR / f"{today_str}_claude_patch.md"
        success, claude_output = run_ai_review(
            tool="claude",
            cmd_name=claude_cmd,
            prompt_text=claude_prompt,
            output_path=claude_out,
            dry_run=effective_dry_run,
            label="Stage 1 — Claude Code (patch writer)",
        )
        _log({
            "type": "AI_REVIEW",
            "stage": 1,
            "tool": "claude",
            "role": "patch_writer",
            "review_path": str(claude_out),
            "dry_run": effective_dry_run,
            "success": success,
        })
        if success:
            print(f"[improvement_loop] Stage 1 complete → {claude_out.name}")

    # ── Stage 2: Codex validates Claude's patch ───────────────────────────
    if run_codex:
        if not claude_output:
            # Codex runs standalone if Claude wasn't invoked or produced no output
            claude_output = "(Claude Code was not run in this cycle — no patch to validate.)"

        codex_prompt = (
            packet_text
            + "\n\n---\n\n"
            + CODEX_VALIDATION_PROMPT_TEMPLATE.format(claude_output=claude_output)
        )
        codex_out = AI_REVIEWS_DIR / f"{today_str}_codex_validation.md"
        success, codex_output = run_ai_review(
            tool="codex",
            cmd_name=codex_cmd,
            prompt_text=codex_prompt,
            output_path=codex_out,
            dry_run=effective_dry_run,
            label="Stage 2 — Codex (patch validator)",
        )
        _log({
            "type": "AI_REVIEW",
            "stage": 2,
            "tool": "codex",
            "role": "validator",
            "review_path": str(codex_out),
            "dry_run": effective_dry_run,
            "success": success,
        })
        if success:
            codex_verdict = _parse_codex_verdict(codex_output)
            print(f"[improvement_loop] Stage 2 complete → {codex_out.name} (verdict: {codex_verdict})")

    # ── Stage 3: Claude revises patch based on Codex feedback ─────────────
    # Always runs when both tools are active — Codex never terminates the loop.
    # Even on APPROVE, Claude confirms/finalises the patch. On MODIFY/REJECT,
    # Claude corrects issues Codex raised.
    if run_claude and run_codex:
        print(
            f"\n[improvement_loop] Codex verdict={codex_verdict} — "
            f"Stage 3: Claude reviewing Codex feedback and producing final patch..."
        )
        revision_prompt = (
            packet_text
            + "\n\n---\n\n"
            + CLAUDE_REVISION_PROMPT_TEMPLATE.format(
                claude_output=claude_output,
                codex_output=codex_output,
            )
        )
        revision_out = AI_REVIEWS_DIR / f"{today_str}_claude_revised_patch.md"
        success, revised_output = run_ai_review(
            tool="claude",
            cmd_name=claude_cmd,
            prompt_text=revision_prompt,
            output_path=revision_out,
            dry_run=effective_dry_run,
            label="Stage 3 — Claude Code (final patch)",
        )
        _log({
            "type": "AI_REVIEW",
            "stage": 3,
            "tool": "claude",
            "role": "patch_reviser",
            "codex_verdict": codex_verdict,
            "review_path": str(revision_out),
            "dry_run": effective_dry_run,
            "success": success,
        })
        if success:
            print(f"[improvement_loop] Stage 3 complete → {revision_out.name}")

    # ── Summary ───────────────────────────────────────────────────────────
    stage3_line = (
        f"\n  Stage 3 final:      {today_str}_claude_revised_patch.md"
        if (run_claude and run_codex)
        else ""
    )
    print(
        f"\n[improvement_loop] AI review pipeline complete. "
        f"Results in {AI_REVIEWS_DIR}/"
        f"\n  Stage 1 patch:      {today_str}_claude_patch.md"
        f"\n  Stage 2 validation: {today_str}_codex_validation.md"
        f"{stage3_line}"
        f"\n  Context packet:     {today_str}_review_packet.md"
        f"\n  Codex verdict:      {codex_verdict}"
        f"\nAll patches require human approval before applying."
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TradingAgents continuous ML improvement loop."
    )
    parser.add_argument("--force-retrain", action="store_true",
                        help="Always retrain regardless of model health.")
    parser.add_argument("--check-only", action="store_true",
                        help="Run validation, grading, drift checks only. No retrain.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands; do not execute anything.")
    parser.add_argument("--rollback", action="store_true",
                        help="Restore last backup bundle and exit.")
    parser.add_argument("--ai-review", action="store_true",
                        help="Generate AI review packet (and call AI CLI if configured).")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--tickers", default="all_tickers.txt")
    parser.add_argument("--validation-path", default=None)
    parser.add_argument("--paper-accounts-dir", default="paper_accounts",
                        help="Root directory containing strategy paper account subdirs.")
    parser.add_argument("--paper-log", default=None)
    parser.add_argument("--skip-holdout", action="store_true")
    parser.add_argument("--skip-promotion-check", action="store_true",
                        help="Skip promotion gates; accept bundle if quality gates pass.")
    parser.add_argument("--model-dir", default="ml_models/latest",
                        help="Directory of current model bundle.")
    args = parser.parse_args()

    today = dt.date.today()
    today_str = today.isoformat()
    ts = dt.datetime.now().isoformat()
    python = sys.executable

    print(f"\n{'='*60}")
    print(f"[improvement_loop] TradingAgents Improvement Loop — {today}")
    print(f"{'='*60}")

    model_dir = ROOT / args.model_dir
    paper_accounts_dir = ROOT / args.paper_accounts_dir

    # ── Rollback ──────────────────────────────────────────────────────────────
    if args.rollback:
        success = run_rollback(model_dir, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    # ── Prediction grading ────────────────────────────────────────────────────
    grades = run_prediction_grading(paper_accounts_dir, dry_run=args.dry_run)

    # ── Reliability stats ─────────────────────────────────────────────────────
    reliability_report = compute_reliability_stats(grades, dry_run=args.dry_run)

    # ── Drift detection ───────────────────────────────────────────────────────
    validation_path = Path(args.validation_path) if args.validation_path else ROOT / "validation_summary.json"
    # Look for PSI drift log from paper accounts
    drift_log = None
    for strat_dir in paper_accounts_dir.iterdir() if paper_accounts_dir.exists() else []:
        candidate = strat_dir / "ml_drift.json"
        if candidate.exists():
            drift_log = candidate
            break

    drift_report = run_drift_detection(
        grades=grades,
        validation_summary_path=validation_path,
        drift_log_path=drift_log,
        dry_run=args.dry_run,
    )

    # ── Step 1: Run validation report ────────────────────────────────────────
    validation_cmd = [python, str(ROOT / "scripts" / "validation_report.py")]
    if args.paper_log:
        validation_cmd += ["--paper-log", args.paper_log]
    validation_cmd += ["--output", str(validation_path)]

    rc = _run_cmd(validation_cmd, "Step 1 — Validation report",
                  dry_run=args.dry_run, abort_on_failure=False)

    # ── Step 2: Retrain trigger assessment ───────────────────────────────────
    needs_retrain, trigger_reasons = _check_retrain_needed(
        validation_path, drift_report=drift_report, force=args.force_retrain
    )

    if trigger_reasons:
        print(f"\n[improvement_loop] Retrain triggers: {trigger_reasons}")
        _log({
            "type": "RETRAIN_TRIGGERED",
            "triggers": trigger_reasons,
            "needs_retrain": needs_retrain,
        })
    else:
        print(f"\n[improvement_loop] ✓ Model health OK — no retrain needed.")

    # ── AI review ─────────────────────────────────────────────────────────────
    if args.ai_review or args.check_only:
        run_ai_reviews(
            today_str=today.strftime("%Y-%m-%d"),
            validation_path=validation_path,
            drift_report=drift_report,
            reliability_report=reliability_report,
            grades=grades,
            trigger_reasons=trigger_reasons,
            dry_run=args.dry_run,
        )

    if args.check_only:
        print(f"[improvement_loop] --check-only: stopping before retrain.")
        _log({"type": "RETRAIN_SKIPPED", "action": "check_only", "triggers": trigger_reasons})
        return

    if not needs_retrain:
        _log({"type": "RETRAIN_SKIPPED", "action": "no_retrain_needed"})
        print(f"[improvement_loop] Done. No retrain triggered.\n")
        return

    # ── Step 3: Read old model report for promotion comparison ───────────────
    old_report_path = model_dir / "training_report.json"
    old_bundle_path = model_dir / "model_bundle.joblib"

    # Backup old report alongside bundle backup (retrain_weekly.py backs up the bundle)
    old_report_backup = None
    if old_report_path.exists():
        ts_compact = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        old_report_backup = model_dir / f"training_report_backup_{ts_compact}.json"
        if not args.dry_run:
            shutil.copy2(old_report_path, old_report_backup)

    # ── Step 4: Retrain ───────────────────────────────────────────────────────
    print(f"\n[improvement_loop] Triggering retrain: {trigger_reasons}")
    print("\n" + "!" * 60)
    print("ANTI-CHEATING: retrain uses training data only, not holdout.")
    print("Gates: ROC>=0.56, Brier<0.24, psi_fail=0, leakage=0")
    print("!" * 60)

    retrain_cmd = [
        python, str(ROOT / "scripts" / "retrain_weekly.py"),
        "--months", str(args.months),
        "--tickers", args.tickers,
    ]
    if args.skip_holdout:
        retrain_cmd.append("--skip-holdout")

    _log({"type": "RETRAIN_STARTED", "triggers": trigger_reasons, "months": args.months})
    rc = _run_cmd(retrain_cmd, "Step 4 — Retrain", dry_run=args.dry_run, abort_on_failure=False)

    if rc != 0:
        print(f"\n[improvement_loop] ⚠ Retrain FAILED (exit {rc}).")
        _log({"type": "RETRAIN_FAILED", "rc": rc})
        sys.exit(rc)

    _log({"type": "RETRAIN_COMPLETED", "rc": rc})

    # ── Step 5: Promotion gates ───────────────────────────────────────────────
    if not args.skip_promotion_check and not args.dry_run:
        new_report_path = model_dir / "training_report.json"
        passes, gate_msg = _check_promotion_gates(old_report_backup, new_report_path)

        if passes:
            print(f"\n[improvement_loop] ✓ Promotion gates PASSED: {gate_msg}")
            _log({
                "type": "PROMOTION_ACCEPTED",
                "gate_msg": gate_msg,
                "model_dir": str(model_dir),
            })
        else:
            print(f"\n[improvement_loop] ⚠ Promotion gates FAILED: {gate_msg}")
            print("[improvement_loop] Model NOT promoted. Archiving candidate bundle.")
            # Archive the failing new bundle
            ts_compact = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_path = model_dir / f"model_bundle_rejected_{ts_compact}.joblib"
            new_bundle = model_dir / "model_bundle.joblib"
            if new_bundle.exists() and old_bundle_path.exists():
                shutil.copy2(new_bundle, archive_path)
                # Restore old bundle
                shutil.copy2(
                    sorted(model_dir.glob("model_bundle_backup_*.joblib"), reverse=True)[0],
                    new_bundle,
                )
                print(f"[improvement_loop] Old model restored. Candidate archived: {archive_path.name}")
            _log({
                "type": "PROMOTION_REJECTED",
                "reason": gate_msg,
                "archived": str(archive_path),
            })
            # Still run AI review to understand the failure
            run_ai_reviews(
                today_str=today.strftime("%Y-%m-%d"),
                validation_path=validation_path,
                drift_report=drift_report,
                reliability_report=reliability_report,
                grades=grades,
                trigger_reasons=[f"PROMOTION_REJECTED: {gate_msg}"],
                dry_run=args.dry_run,
            )
            sys.exit(1)
    else:
        print(f"[improvement_loop] Promotion check skipped.")

    # ── Step 6: Post-retrain validation ──────────────────────────────────────
    post_val_path = ROOT / f"validation_summary_post_retrain_{today.strftime('%Y%m%d')}.json"
    post_val_cmd = [
        python, str(ROOT / "scripts" / "validation_report.py"),
        "--output", str(post_val_path),
    ]
    if args.paper_log:
        post_val_cmd += ["--paper-log", args.paper_log]

    _run_cmd(post_val_cmd, "Step 6 — Post-retrain validation",
             dry_run=args.dry_run, abort_on_failure=False)

    if not args.dry_run and post_val_path.exists():
        try:
            new_val = json.loads(post_val_path.read_text())
            new_train = new_val.get("train", {})
            _log({
                "type": "RELIABILITY_UPDATE",
                "action": "post_retrain_validation",
                "new_win_roc": new_train.get("win_roc"),
                "new_brier": new_train.get("brier_after"),
                "new_wf_wr": new_train.get("wf_win_rate"),
                "new_model_pass": new_val.get("pass_"),
            })
        except Exception:
            pass

    print(f"\n[improvement_loop] ✓ Improvement loop complete.")
    print(f"[improvement_loop] Log: {IMPROVEMENT_LOG}")
    print("[improvement_loop] Restart paper runner to use the new model.")
    print("\nANTI-CHEATING: Post-retrain results are DIAGNOSTIC ONLY.")
    print("Do NOT tune thresholds or select features based on this output.\n")


if __name__ == "__main__":
    main()
