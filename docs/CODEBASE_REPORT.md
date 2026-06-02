# TradingAgents Codebase Report

Last updated: 2026-06-02

---

## Project Shape

- `pyproject.toml` — Python package `tradingagents` at version `1.0.0`, requires Python `>=3.10`.
- `tradingagents/` — core LLM trading framework (agents, data flows, portfolio engine, ML, RL).
- `web/` — FastAPI web backend (20+ route files, ~9K+ lines of API code).
- `frontend/` — React 19 + Vite 8 single-page app (`frontend/src/`).
- `cli/` — Typer/Rich command-line tool exposed as `tradingagents` console script.
- `scripts/` — paper trading runners, ML training, backtesting, monitoring, ops scripts.
- `tests/` — pytest suite covering memory logging, checkpoint resume, model validation, structured agents, signal processing, ticker handling, portfolio risk, account simulation honesty.
- `ml_models/` — deployed model bundles (`latest/`, `stock_universe/`).
- `tmp/` — runtime state (paper accounts, thematic signals, scan history).
- `docs/` — design docs, changelogs, API reference, alpha evolution log.
- `Dockerfile`, `docker-compose.yml` — containerized deployment.

---

## Architecture

### LLM Multi-Agent Core (Python SDK)

Entry point: `tradingagents/graph/trading_graph.py` → `TradingAgentsGraph`.

Workflow (LangGraph):
```
Analysts (market/social/news/fundamentals)
  → Bull/Bear Research Debate
    → Research Manager
      → Trader
        → Risk Debate (aggressive/conservative/neutral debaters)
          → Portfolio Manager
            → Signal Processor → Decision
```

- `tradingagents/graph/setup.py` — builds the LangGraph DAG
- `tradingagents/graph/propagation.py` — initial state + invocation
- `tradingagents/graph/checkpointer.py` — SQLite-backed checkpoint/resume
- `tradingagents/agents/` — all agent factories (analysts, researchers, risk, trader, managers)
- `tradingagents/agents/schemas.py` — Pydantic structured-output schemas
- `tradingagents/dataflows/interface.py` — routes requests to configured data vendors
- `tradingagents/llm_clients/factory.py` — builds provider-specific LLM clients
  - Supported: OpenAI, Anthropic, Google, xAI, Azure, OpenRouter, Ollama, DeepSeek, Qwen, GLM

### Portfolio Engine

- `tradingagents/portfolio/unified_brain.py` — central decision layer: merge → score → tier → allocate
- `tradingagents/portfolio/alpha_engine.py` — live alpha scoring (AlphaEngine + PaperFeedbackTracker)
- `tradingagents/portfolio/candidate_ranker.py` — pre-AlphaEngine ranking
- `tradingagents/portfolio/position_sizing.py` — 10-layer dynamic sizer (Kelly → ML → streak → ToD → regime → tier → ATR → ADV → LL-safety)
- `tradingagents/portfolio/exit_manager.py` — deterministic stop/target/trail/partial-TP calculator
- `tradingagents/screening/market_regime.py` — probabilistic regime engine (SPY + VIX + 11 sector ETFs)
- `tradingagents/screening/breakout_scanner.py` — breakout score [0-100] per ticker
- `tradingagents/screening/screener.py` — confirmed-pullback breakout screener

### Web Backend (FastAPI)

- `web/app.py` — app factory, CORS, middleware, route mounts, background tasks
- `web/auth.py` — Cloudflare Access JWT auth, admin/user roles, step-up 2FA
- `web/users.py` — file-based user store with D1/Supabase optional remote backend
- `web/api/` — 20+ route files:
  - `paper.py` — paper trading session management, HIL approval queue
  - `thematic_auto.py` — AI-picked signal pipeline (15 sources → AI → HIL queue → auto-execute)
  - `thematic_portfolio.py` — manual conviction portfolio with scoring
  - `fidelity.py` — Playwright RPA for live Fidelity trading
  - `ml.py` — model stats, scoring, retrain trigger
  - `scanner.py` — breakout scan API
  - `market.py` — quotes, news, regime, watchlist
  - `analysis.py` — async LLM analysis jobs
  - `backtest.py` — backtest job management
  - `portfolio.py` — manual position tracker
  - `admin.py` — user management, feature flags, system health
  - `history.py`, `logs.py`, `settings.py`, `rl.py`, `cloudflare_ai.py`, `live_verification.py`, `twofa_routes.py`, `auth_routes.py`

### Frontend (React 19)

- `frontend/src/App.tsx` — router, auth context, app shell
- `frontend/src/pages/` — Dashboard, Signals, Paper, Thematic, History, ML, Analyze, Backtest, Broker, Admin, Settings, HIL, Logs
- `frontend/src/hooks/` — `useAuth`, `useWebSocket`
- `frontend/src/api/` — typed API client modules
- `frontend/src/components/` — charts (LineChart, DrawdownChart), layout (AppShell, Sidebar, AppHeader), modals (HIL toast, onboarding, step-up), UI (DataTable, Toast, GlobalOverlays), candidates (CandidatePanel)
- Build: `npm run build` → `frontend/dist/` (served by FastAPI static mount)

### Scripts

- `scripts/paper_trade_today.py` — primary paper trading runner (web API Popens this)
- `scripts/paper_trade_unified.py` — UnifiedBrain paper trading runner (parallel/experimental)
- `scripts/retrain_weekly.py` — ML model retrain pipeline orchestrator
- `scripts/train_ml_models.py` — XGBoost + RF ensemble trainer with PSI pruning
- `scripts/train_ml_from_stock_data.py` — stock-universe model trainer
- `scripts/autofix_monitor.py` — self-healing monitor, auto-restarts dead processes, sends alerts
- `scripts/backtest_thematic_signals.py` — validates thematic signal quality from history
- `scripts/simulate_rule_based.py` — rule-based backtest simulator
- `scripts/scan_breakouts.py` — standalone breakout scanner
- `scripts/gen_signals.py` — signal generation utility
- `scripts/rotate_logs.py` — log rotation (10 MB cap, 7 gzip archives)
- `scripts/sms_alerts.py`, `scripts/email_sender.py`, `scripts/notify.py` — alerting

---

## Runtime State Files (`tmp/`)

| File | Written by | Purpose |
|------|-----------|---------|
| `paper_trading_today/unified_brain/state.json` | paper runners, thematic trade inject | Live paper account state |
| `thematic_signals.json` | thematic auto scan | Pending/historical signals |
| `thematic_scan_status.json` | auto scan | Scan progress |
| `thematic_score_history.jsonl` | auto scan | Rolling 500-scan ticker scores |
| `thematic_exit_log.jsonl` | exit monitor | Executed exit records |
| `thematic_trades.jsonl` | thematic paper trade | Trade log |
| `thematic_portfolio_{hash}.json` | thematic portfolio API | Per-user positions |
| `brave_search_usage.json` | auto scan | Monthly Brave API usage counter |
| `feedback_tracker.json` | paper runner | PaperFeedbackTracker state |

---

## ML Model Status (Cycle 46)

| Model | File | ROC | Status |
|-------|------|-----|--------|
| win_probability | `ml_models/latest/` | 0.5121 (WF) | Deployed. **Disabled from numerator** (WF HC WR anti-predictive). Used for tier gating only. |
| large_loss | `ml_models/latest/` | 0.73 | **Active** — denominator penalty. |
| expected_return | `ml_models/latest/` | R²=0.012 | **Disabled** — threshold set to -99.0 (noise). |
| timeout | `ml_models/latest/` | 0.40 | **Disabled** — anti-predictive. |
| target_before_stop | `ml_models/latest/` | ~0.47 | **Disabled** — anti-predictive. |

Training: XGBoost + RF ensemble. PSI-pruned features. Walk-forward cross-validation (expanding window, 6-month folds). Quality gate: WF ROC ≥ 0.49.

---

## Key Configs

### AlphaEngine / UnifiedBrain

```
min_risk_reward   = 1.15    (screener R:R=1.2 ± rounding noise)
ll_hard_cap       = 0.50    (large_loss_prob > 50% → reject)
min_confidence    = 0.0     (win_prob disabled — anti-predictive)
breakout_max_boost= 0.50    (score=100 → +50% to numerator)
tier A+           = alpha ≥ 0.72 AND regime_score ≥ 0.85
tier A            = alpha ≥ 0.55
tier B            = alpha ≥ 0.38
```

### Screener Exit Geometry

```
_ATR_TARGET = 1.2×   (Cycle 6)
_ATR_STOP   = 1.0×   (Cycle 44: raised from 0.7)
→ live R:R  = 1.20
```

### Paper Trading Defaults

```
target_mult              = 1.2
stop_mult                = 1.0
ml_probability_threshold = 0.55
ml_large_loss_max        = 0.50
min_risk_reward          = 1.15
skip_vix_low_vol         = True   (VIX < 15 → skip all)
skip_extended_bounce     = True   (consec_up ≥ 2 → skip)
skip_thursday            = True   (Thu WR=50.4% vs 57.4% non-Thu)
```

---

## Remaining Fix Candidates

1. **Target geometry mismatch** — deployed model labels target at 0.75 ATR, live screener targets 1.2 ATR. Highest remaining lever. Requires fresh retrain that passes quality gate.
2. **B6** — promote calibrated `1 − large_loss_prob` into alpha numerator (changes live distribution, needs re-derived tier cutoffs).
3. **B11** — port Kelly sizer from `position_sizing.py` into live paper_trade_today.py path with drawdown throttle.
4. **DL-3/DL-5/DL-8** — correlation check before buy, drift detector halt, high-vol max_hold override.
5. **HIL trade log** — `portfolio/history` endpoint uses a global trade log, not per-user.
6. Regenerate `uv.lock` — appears stale against `pyproject.toml`.
