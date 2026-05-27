# Continuous ML Improvement Loop — Design Plan
*Created: 2026-05-26*

---

## 1. Current State

| Component | File | Status |
|-----------|------|--------|
| Weekly retrain (age/ROC/Brier triggers) | scripts/retrain_weekly.py | ✅ |
| Improvement loop driver | scripts/improvement_loop.py | ✅ basic |
| Leakage check | scripts/leakage_check.py | ✅ |
| Holdout validation | scripts/validate_holdout.py | ✅ |
| Validation report | scripts/validation_report.py | ✅ |
| PaperFeedbackTracker (drift aggression) | tradingagents/portfolio/alpha_engine.py | ✅ |
| ProductionSafetyMonitor | tradingagents/portfolio/production_safety.py | ✅ |
| CANDIDATE_EVALUATED events | scripts/paper_trade_today.py | ✅ |
| Improvement log (improvement_log.jsonl) | ml_models/ | ✅ basic |
| Prediction grading (predicted vs actual) | — | ❌ |
| Rolling reliability stats by regime/tier | — | ❌ |
| PSI feature drift (live candidates) | scripts/paper_trade_today.py | ✅ basic |
| Calibration drift tracking | — | ❌ |
| Model promotion gate (beat old model) | — | ❌ |
| Model rollback to last known good | — | ❌ |
| AI CLI advisory review | — | ❌ |

---

## 2. Architecture: New Modules

### A. `tradingagents/portfolio/prediction_grader.py`

Grades closed paper trades against ML predictions recorded at entry.

```
PredictionGrader.grade(trade_record) → GradeResult
  ├── win_correct:     predicted_prob vs actual win (binary)
  ├── return_error:    expected_return - actual_return
  ├── ll_correct:      large_loss_predicted and actual_drawdown > threshold
  ├── stop_hit:        stop triggered vs take-profit hit
  ├── breakout_result: breakout_score at entry vs position hit target
  └── regime_context:  regime at entry / exit
```

```python
@dataclass
class GradeResult:
    ticker: str
    trade_id: str
    predicted_win_prob: float
    actual_win: bool
    predicted_return: float
    actual_return: float
    predicted_ll_prob: float
    actual_max_drawdown: float
    stop_hit: bool
    target_hit: bool
    regime_at_entry: str
    regime_at_exit: str | None
    breakout_score_at_entry: float
    alpha_tier: str
    model_version: str
    confidence_bucket: str    # "low"(<0.60), "mid"(0.60–0.70), "high"(0.70+)
    graded_at: str
```

Storage: `{paper_account}/prediction_grades.jsonl`

### B. `tradingagents/portfolio/reliability_stats.py`

Rolling statistics computed from graded trades.

```
ReliabilityStats.compute(grades, window=50) → StatsReport
  ├── overall win_rate, avg_return, max_drawdown
  ├── by_ticker: {AAPL: {win_rate, n, avg_return}}
  ├── by_regime: {bull: {...}, bear: {...}}
  ├── by_tier:   {A+: {...}, A: {...}, B: {...}}
  ├── by_confidence_bucket: {high: {...}, mid: {...}, low: {...}}
  └── by_model_version: {v1.2: {...}}
```

Storage: `{paper_account}/reliability_stats.json` (recomputed on demand)

### C. `tradingagents/portfolio/drift_detector.py`

```
DriftDetector.check(grades, bundle, candidates) → DriftReport
  ├── calibration_drift: mean(predicted_prob) - actual_win_rate (last N)
  ├── high_conf_failure_rate: % of high-confidence trades that lost
  ├── paper_vs_wf_gap: paper_wr - wf_wr from validation_summary.json
  ├── regime_collapse: win_rate for bear/crash regime > threshold
  └── psi_summary: read from live PSI check (already exists in paper_trade_today)
```

Storage: `ml_models/drift_report.json` (overwritten each cycle)

### D. Updates to `scripts/improvement_loop.py`

- Load grades from all strategy paper accounts
- Call DriftDetector + ReliabilityStats
- Check model promotion gates after retrain
- Rollback support
- AI CLI review runner (advisory mode)
- Write all events to `improvement_log.jsonl`

---

## 3. Prediction Grading Flow

**At entry (paper_trade_today.py BUY event — already logged):**
```json
{
  "type": "BUY",
  "ticker": "AAPL",
  "ml_probability": 0.68,
  "expected_return": 0.042,
  "large_loss_probability": 0.08,
  "alpha_tier": "A",
  "alpha_score": 0.38,
  "breakout_score": 72,
  "regime_at_entry": "bull"
}
```

**At exit (SELL event — already logged):**
```json
{
  "type": "SELL",
  "ticker": "AAPL",
  "pnl": 142.0,
  "pnl_pct": 0.031,
  "stop_hit": false,
  "target_hit": true,
  "max_drawdown_pct": -0.008
}
```

**Grader joins BUY + SELL events by ticker + entry_time and computes GradeResult.**

---

## 4. Model Promotion Rules

After retrain + quality gates pass, compare new vs old model:

| Check | Rule |
|-------|------|
| `wf_roc_improvement` | new_roc >= old_roc - 0.005 (no regression > 0.5%) |
| `wf_wr_improvement` | new_wf_wr >= old_wf_wr - 0.02 |
| `drawdown_increase` | new_max_dd <= old_max_dd * 1.15 (max 15% increase) |
| `calibration_pass` | Brier <= max_brier |
| `psi_pass` | psi_fail == 0 |

If ALL pass → promote (swap bundle to latest/)
If ANY fail → archive candidate, keep old bundle, log PROMOTION_REJECTED
Never promote because train metrics improved — only WF metrics count.

### Rollback

Retrain_weekly.py already backs up old bundle. Rollback = copy backup to latest/.
improvement_loop.py adds `--rollback` flag.

---

## 5. AI CLI Review Runner

### Config flags (in improvement_loop.py or .env)
```
ENABLE_AI_CODE_REVIEW=false
ENABLE_CLAUDE_CODE_REVIEW=false
ENABLE_CODEX_REVIEW=false
CLAUDE_CODE_CMD=claude
CODEX_CLI_CMD=codex
AI_CODE_REVIEW_DRY_RUN=true
AI_CODE_REVIEW_MAX_FILES=20
```

### Review packet (written to `ml_models/ai_reviews/review_packet_YYYYMMDD.md`)
```
## Review Packet — 2026-05-26

### System Context
...

### Validation Summary
...

### Drift Alerts
...

### Reliability Stats
...

### Failing Gates
...

### Safe Files (may inspect)
- scripts/train_ml_models.py
- scripts/retrain_weekly.py
- tradingagents/screening/*.py
- tradingagents/dataflows/*.py
- tradingagents/portfolio/alpha_engine.py
...

### Dangerous Files (must NOT edit)
- web/api/fidelity.py
- web/api/webull_portfolio.py
- Any file matching *broker*, *live*, *fidelity*, *webull*
```

### AI Prompt (hardcoded, non-editable at runtime)
```
You are an ML research assistant reviewing a trading ML system.

RULES — MUST FOLLOW:
1. Advisory only. Do not modify live broker code (fidelity.py, webull_portfolio.py).
2. Do not tune thresholds using holdout data (post 2026-05-08).
3. Do not weaken risk gates (stops, drawdown limits, loss caps).
4. Do not auto-trade or auto-promote models.
5. Do not apply patches without explicit human approval.

YOUR TASK:
- Explain why ML gates are failing (ROC, Brier, win rate issues)
- Suggest feature engineering, label changes, or threshold adjustments
- Inspect only files in the safe file list
- Propose a concrete patch plan or dry-run diff
- Recommend more paper trading if data is insufficient (< 30 closed trades)

OUTPUT FORMAT:
## Root Cause Analysis
## Suggested Improvements
## Patch Plan
## Files to Review
## Risk Assessment (what could go wrong with each suggestion)
```

### Output
- `ml_models/ai_reviews/YYYY-MM-DD_claude_review.md`
- `ml_models/ai_reviews/YYYY-MM-DD_codex_review.md`
- `improvement_log.jsonl` event type: `AI_REVIEW`

---

## 6. improvement_log.jsonl Event Types

| Event | When | Key Fields |
|-------|------|------------|
| `RETRAIN_TRIGGERED` | triggers detected | triggers, model_age, paper_wr |
| `RETRAIN_STARTED` | before backtest | window, months, config |
| `RETRAIN_COMPLETED` | gates pass | roc, brier, wf_wr |
| `RETRAIN_FAILED` | any step fails | step, error |
| `PROMOTION_ACCEPTED` | new model wins | old_roc, new_roc, delta |
| `PROMOTION_REJECTED` | new model loses | reason, old vs new metrics |
| `ROLLBACK_EXECUTED` | --rollback flag | backup_path, reason |
| `DRIFT_ALERT` | drift > threshold | calibration_drift, type |
| `RELIABILITY_UPDATE` | after grading | by_tier, by_regime summary |
| `AI_REVIEW` | after AI run | tool, review_path, dry_run |
| `RETRAIN_SKIPPED` | all checks pass | checked_at |

---

## 7. Files to Create / Modify

### New:
- `tradingagents/portfolio/prediction_grader.py` — PredictionGrader, GradeResult
- `tradingagents/portfolio/reliability_stats.py` — ReliabilityStats, StatsReport
- `tradingagents/portfolio/drift_detector.py` — DriftDetector, DriftReport

### Modified:
- `scripts/improvement_loop.py` — prediction grading, drift, promotion gates, rollback, AI review
- `tradingagents/portfolio/__init__.py` — export new classes
- `CONTINUOUS_IMPROVEMENT_PLAN.md` — this file

---

## 8. Anti-Cheating Checklist

- [x] Grader only reads from paper trades (not backtest)
- [x] AI review prompt explicitly forbids holdout tuning
- [x] Promotion gate uses walk-forward, not train metrics
- [x] Rollback only copies bundle — does not retune
- [x] AI CLI subprocess cannot write to live broker files (advisory only, read-only prompt)
- [x] All AI suggestions require human approval before applying (AI_CODE_REVIEW_DRY_RUN=true by default)
- [x] Review packet includes dangerous file list to warn AI
- [x] improvement_log.jsonl is append-only (no overwrite)

## 9. Implementation Status

- [x] CONTINUOUS_IMPROVEMENT_PLAN.md created
- [x] `tradingagents/portfolio/prediction_grader.py` — PredictionGrader, GradeResult
- [x] `tradingagents/portfolio/reliability_stats.py` — ReliabilityStats, StatsReport, SliceStats
- [x] `tradingagents/portfolio/drift_detector.py` — DriftDetector, DriftReport
- [x] `scripts/improvement_loop.py` — prediction grading, drift, promotion gates, rollback, AI CLI runner
- [x] `tradingagents/portfolio/__init__.py` — exports all new classes
- [x] Smoke tests passed: all modules import, StatsReport computes, DriftDetector fires correctly
- [x] AI review dry-run produces review packet + stub output file
- [x] --rollback flag restores last backup bundle
- [x] improvement_log.jsonl event types: RETRAIN_TRIGGERED, RETRAIN_STARTED, RETRAIN_COMPLETED, RETRAIN_FAILED, PROMOTION_ACCEPTED, PROMOTION_REJECTED, ROLLBACK_EXECUTED, DRIFT_ALERT, RELIABILITY_UPDATE, AI_REVIEW, RETRAIN_SKIPPED

---

## 9. Success Criteria

- Every closed paper trade gets a GradeResult with predicted vs actual comparison
- Calibration drift detected within 30 trades of model going stale
- New model never promoted unless WF metrics match or beat old model
- Rollback restores old bundle in one command
- AI review packet produced with zero code executed automatically
- All suggestions in `ai_reviews/` require human to apply
