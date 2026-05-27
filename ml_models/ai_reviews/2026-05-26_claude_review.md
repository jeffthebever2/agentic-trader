# Claude Review — 2026-05-26 [DRY RUN]

*Dry-run placeholder. No AI invoked.*

CMD: `claude --print --dangerously-skip-permissions --max-turns 3 -p <packet 3008 chars>`

## Review Packet Preview

```
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
- scripts/retrain_weekly.
```
