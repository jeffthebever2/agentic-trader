# TradingAgents ML Review Packet — 2026-05-26

## System Context
TradingAgents paper-trading ML system. RF classifier predicts win probability, expected return,
large-loss probability, and timeout probability for equity momentum setups.

### Critical Data Rules
- Holdout period: 2026-05-08 → 2026-05-26. DO NOT tune on this data.
- Old backtests are diagnostic only.

## Retrain Triggers
- cannot parse model created_at — assume stale
- win_roc=0.5479 < 0.56

## Drift Alerts
- No drift detected (n_grades=0)

## Paper Trade Reliability
- No closed trades graded yet. Recommend more paper trading.

## Validation Summary
- ROC: 0.5479
- Brier: None
- WF Win Rate: None
- Pass: False

## Safe Files (you MAY inspect and suggest changes to these)
- scripts/train_ml_models.py
- scripts/retrain_weekly.py
- scripts/improvement_loop.py
- scripts/validation_report.py
- scripts/leakage_check.py
- tradingagents/screening/screener.py
- tradingagents/screening/breakout_scanner.py
- tradingagents/screening/market_regime.py
- tradingagents/dataflows/interface.py
- tradingagents/dataflows/alpha_vantage_utils.py
- tradingagents/portfolio/alpha_engine.py
- tradingagents/portfolio/candidate_ranker.py
- tradingagents/portfolio/exit_manager.py
- tradingagents/portfolio/position_sizing.py
- tradingagents/portfolio/ticker_reliability.py
- tradingagents/portfolio/prediction_grader.py
- tradingagents/portfolio/reliability_stats.py
- tradingagents/portfolio/drift_detector.py
- tradingagents/portfolio/production_safety.py

## Dangerous Files (you MUST NOT edit or weaken these)
- web/api/fidelity.py
- web/api/webull_portfolio.py
- web/api/paper.py
- web/api/admin.py
- scripts/paper_trade_today.py

---

## Your Instructions
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
