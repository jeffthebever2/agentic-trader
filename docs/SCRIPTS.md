# Scripts

Reference for the helper scripts under `scripts/`. Commands assume macOS/zsh and
are run from the repo root with `python3`. In **production these do not run via
`start.sh`** — the launchd services (`org.agentictrader.*`) own the long-lived
loops; see `CLAUDE.md`. The tables below cover the notable scripts grouped by
purpose. Anything not listed is a one-off/analysis helper (e.g.
`analyze_strategy_results.py`, `analyze_sweep_20260610.py`,
`sweep_hold_stop_20260610.sh`).

## Paper trading

The system runs a **15-portfolio paper-trading competition** (no longer the old
3–4 "Algorithm / ML / AI" local accounts). The leaderboard is served at
`/portfolios`. State lives under `tmp/paper_trading_today/` and
`tmp/paper_trading_qlib/`.

| Script | What it does | Example |
| --- | --- | --- |
| `paper_trade_unified.py` | **Production papertrader** (run by the launchd `papertrader` service on a 15-min scan loop). Downloads the universe + prices, builds candidates, runs them through `UnifiedBrain.process()`, and manages the competing paper portfolios with short-hold limits. Writes `unified_brain_audit_{YYYYMMDD}.jsonl`; honors the same kill-switch / model-health checks as live. | `python3 scripts/paper_trade_unified.py` |
| `paper_trade_today.py` | Candidate engine invoked by `./start.sh paper`. Builds breakout/pullback candidates against fresh Yahoo data and scores them (technical rule gate + saved ML gate + optional OpenRouter AI). Live terminal dashboard by default (`--no-dashboard` for plain logs). Writes state/candidates/summary/event-log under `tmp/paper_trading_today/YYYYMMDD/`; flattens at close and writes `end_of_day_statistics.{json,csv}`. No broker orders, no news research. | `python3 scripts/paper_trade_today.py --reset` |
| `paper_trade_qlib.py` | Separate **paper-only Qlib factor account** for forward evidence before any production decision. Uses the same leakage-lagged Qlib feature code as training; never imports broker execution. Writes under `tmp/paper_trading_qlib/qlib_factor_paper/` (`qlib_signals_*.json`, `prediction_ledger.jsonl`, `state.json`, …). | `python3 scripts/paper_trade_qlib.py --tickers-file all_tickers.txt --max-tickers 100 --reset` |
| `portfolio_report.py` | CLI leaderboard from the competition state files. `--json`, `--group risk`, `--min-trades N`. | `python3 scripts/portfolio_report.py` |
| `simulate_rule_based.py` | Backtest a purely rule-based 3-day-hold strategy from a signal CSV. Reports capital progression, win rate, monthly P&L, market violations. | `python3 scripts/simulate_rule_based.py --input /tmp/signals.csv --capital 10000` |

For an OpenRouter AI account in `paper_trade_today.py`, export the key first
(never commit it):

```zsh
export OPENROUTER_API_KEY="sk-or-v1-..."
python3 scripts/paper_trade_today.py --reset
```

Explicit no-news mode and a quick smoke test (does not wait for market hours):

```zsh
python3 scripts/paper_trade_today.py --ml-algo-only --reset
python3 scripts/paper_trade_today.py --once --force --max-tickers 100
```

## Training

| Script | What it does | Example |
| --- | --- | --- |
| `train_everything.py` | One **resumable, checkpointed** pipeline: production ML retrain + stock-universe retrain + HMM regime + Qlib smoke/validation + Qlib research + readiness checks + audit. Logs/staging/backups under `tmp/train_everything/<run_id>/`; models promoted only after validation passes. `--dry-run`, `--resume <state.json>`, `--profile quick|full`, `--include-rl`, `--include-qlib-features`. | `python3 scripts/train_everything.py` |
| `retrain_weekly.py` | Weekly production ML refresh: backtest → labeled signals → XGBoost + RF ensemble (PSI pruning) → walk-forward validate → deploy to `ml_models/latest/` if the gate passes (WF ROC ≥ 0.49). `--dry-run` prints the command without running. | `python3 scripts/retrain_weekly.py --months 84` |
| `train_ml_models.py` | Train a reusable ML gate bundle from backtest trade rows (CSV from `python3 backtest.py --export-csv …`, or a backtest JSON with `all_trades`). Writes `model_bundle.joblib` + `training_report.json`. | `python3 scripts/train_ml_models.py --input ml_training_trades.csv --output-dir ml_models/latest` |
| `train_ml_from_stock_data.py` | Train from the **full stock universe**: pulls OHLCV per ticker, builds one labeled row per stock/date, then trains. Reuses cached prices / `stock_candidate_training_data.csv`; `--rebuild-dataset`, `--rebuild-price-cache`, `--max-tickers` (tests only). | `python3 scripts/train_ml_from_stock_data.py --tickers all_tickers.txt --start 2019-01-01 --end 2024-12-31 --output-dir ml_models/stock_universe` |
| `train_hmm_regime.py` | Fit a Gaussian HMM on SPY log-returns; saves `ml_models/hmm_regime/hmm_regime.joblib` (regime-probability features for training). `--dry-run` tests without saving. | `python3 scripts/train_hmm_regime.py --ticker SPY --start 2015-01-01 --end 2025-01-01` |
| `train_rl_agent.py` | Train a TD3 RL agent (continuous allocation policy) on historical OHLCV; weights loaded later by `rl_signal.py`. Long stage — run under the torch venv (`.venv-torch`). | `python3 scripts/train_rl_agent.py` |
| `qlib_research.py` | Standalone Qlib alpha-factor IC/ICIR analysis + walk-forward model tournament. **Research only** by default (does not feed production training). Writes `qlib_reports/<ts>_qlib_research.json`. | `python3 scripts/qlib_research.py` |
| `warm_cache.py` | Pre-download/cache all price data for a date range so backtests run instantly. | `python3 scripts/warm_cache.py --start 2025-11-01 --end 2026-05-01` |
| `gen_signals.py` | Fast, parallel, fidelity-exact signal generator (reuses `backtest.precompute`/`score_at`, strictly as-of, no look-ahead). Output CSV feeds `honest_sweep_run.py --sig-csv`. | `python3 scripts/gen_signals.py --mode oversold_bounce --threshold 70 --start 2019-01-01 --end 2026-05-07 --out tmp/ob_signals.csv` |

## Validation & analysis

| Script | What it does | Example |
| --- | --- | --- |
| `check_retrain_status.py` | Quick status: last retrain result, model health, gate metrics. `--json`. | `python3 scripts/check_retrain_status.py` |
| `model_readiness_report.py` | Formal production-gate check on the current bundle → `READY` / `DEGRADED` / `NOT_READY`. | `python3 scripts/model_readiness_report.py` |
| `daily_audit.py` | Daily health audit: model readiness + paper-grade reliability + calibration/monotonicity drift; proposes risky-setting changes via change-control (**propose-only, never auto-applies**). Exit 0/1/2 = PASS/WARN/FAIL. Checks the Qlib paper account via `--qlib-paper-dir`. | `python3 scripts/daily_audit.py` |
| `validate_holdout.py` | Read-only holdout evaluation: runs the backtest engine over an unseen window with a pre-trained bundle. **Never trains/tunes.** | `python3 scripts/validate_holdout.py --start 2026-05-08 --end 2026-05-26 --model-bundle ml_models/latest/model_bundle.joblib --tickers all_tickers.txt` |
| `validation_report.py` | Side-by-side compare train → walk-forward → holdout → paper to catch overfitting / degradation / live-execution gap. Writes `validation_summary.json`. | `python3 scripts/validation_report.py` |
| `snapshot_baseline.py` | Read-only baseline snapshot: reads `training_report.json` + runs `validate_holdout.py` on the last 60 trading days → combined JSON. Never swaps a bundle. | `python3 scripts/snapshot_baseline.py` |
| `paper_backtest_drift.py` | Fill-price drift: match paper BUYs to backtest signal prices, compute slippage (mean/std/p95 bps). `--dry-run`. | `python3 scripts/paper_backtest_drift.py --paper-log tmp/paper_trading_today/account_log.jsonl --backtest-csv backtest_results_latest.csv` |
| `analyze_paper_trades.py` | Paper telemetry: WR / PF / avg-return by alpha tier and ML-prob bucket, win/loss (TARGET/STOP/TIMEOUT), regime context. | `python3 scripts/analyze_paper_trades.py` |
| `analyze_new_model.py` | Post-retrain model analysis: WF ROC + calibration, year-by-year high-conf WR, feature-importance comparison. | `python3 scripts/analyze_new_model.py` |
| `score_candidates_with_ml.py` | Apply a pre-trained bundle to a backtest CSV and report win rate by threshold. | `python3 scripts/score_candidates_with_ml.py --input /tmp/candidates.csv --bundle ml_models/latest/model_bundle.joblib --threshold 0.65` |
| `backtest_thematic_signals.py` | Backtest historical thematic-scanner signals vs realized returns (reads `tmp/thematic_score_history.jsonl`, `tmp/thematic_exit_log.jsonl`). Reports WR/avg-return by score bucket, insider+social combo, multi- vs single-source, exit reasons. | `python3 scripts/backtest_thematic_signals.py --days 90 --min-score 20` |
| `scan_breakouts.py` | Single breakout scan without the full paper-trading harness. | `python3 scripts/scan_breakouts.py --tickers NVDA AAPL MSFT --output /tmp/scan.json` |

## Ops & notifications

| Script | What it does | Example |
| --- | --- | --- |
| `autofix_monitor.py` | Background daemon: watches paper-trading + web-server processes, auto-restarts on failure, sends SMS/email alerts. JSON-configured. | `python3 scripts/autofix_monitor.py --config autofix_config.json` |
| `rotate_logs.py` | Rotate server/paper logs (cap 10 MB each, keep 7 gzip archives). Run by launchd ~00:05 daily. | `python3 scripts/rotate_logs.py` |
| `notify.py` | Notification module — BlueBubbles iMessage + Gmail SMTP (`notify_down` / `notify_fixed`). Used by autofix. | imported (see docstring) |
| `sms_alerts.py` | Small SMS alert backends for paper-trading notifications. | imported |
| `sync_users_to_d1.py` | One-shot: push `tmp/users.json` into Cloudflare D1. | `python3 scripts/sync_users_to_d1.py` |
| `sync_users_to_supabase.py` | One-shot: upsert `tmp/users.json` into the Supabase `agentic_users` table (needs `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`). Safe to re-run. | `python3 scripts/sync_users_to_supabase.py` |
| `migrate_to_cloudflare_users.py` | Seed the local user registry with an initial admin (`CF_ACCESS_BOOTSTRAP_ADMIN` and/or positional emails). | `python3 scripts/migrate_to_cloudflare_users.py you@example.com` |

Other messaging backends present as importable helpers:
`email_sender.py`, `gmail_mms.py`, `telegram_sender.py`, `textnow_sms.py`.

## Build & deploy

| Script | What it does | Example |
| --- | --- | --- |
| `start_public_tunnel.sh` | Start a Cloudflare quick tunnel to `localhost:8001`, print the public URL (logs to `tmp/tunnel.log`). | `bash scripts/start_public_tunnel.sh` |
| `build_macos_dmg.sh` | Build the macOS app bundle + `.dmg` via PyInstaller (`dist/Agentic-Trader-macOS.dmg`). | `bash scripts/build_macos_dmg.sh` |

Windows counterparts exist but are not used on this macOS host:
`build_windows_exe.ps1`, `update_from_github.ps1` (use `update_from_github.sh`).

## Live broker safety

Live broker execution must pass `tradingagents.compliance.validate_live_order`.
That shared gate blocks market orders, stale quotes, missing quote timestamps,
yfinance/Yahoo/fallback-only quotes, untrusted providers, market-closed quotes,
and wide spreads. Fidelity, Webull, paper-runner Fidelity routing, and thematic
Fidelity routing all use this contract; preview/paper-only paths may use
historical feeds, but `execute=True` broker orders require fresh trusted quote
evidence.
