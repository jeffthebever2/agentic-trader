# Scripts

## Paper trade today with live data

Run the confirmed-pullback algorithm, saved ML gate, and OpenRouter AI agent
against the latest real Yahoo data, then simulate separate local paper accounts. Each account
starts with $10,000 and checks entries/exits every 15 minutes:

- `Algorithm`: trades rule-pass algorithm signals only
- `Machine Learning`: lets the ML gate select from all scored setups
- `Algorithm + ML`: trades only when both the rule gate and ML gate pass
- `Pure AI`: asks OpenRouter to pick trades from market snapshots only

```powershell
python .\scripts\paper_trade_today.py --reset
```

Set your OpenRouter key before launching the Pure AI account. Do not commit the
key into the repo:

```powershell
$env:OPENROUTER_API_KEY="sk-or-v1-..."
python .\scripts\paper_trade_today.py --reset
```

For the explicit no-news mode, use:

```powershell
python .\scripts\paper_trade_today.py --ml-algo-only --reset
```

It opens a live terminal dashboard by default, showing the current phase,
market clock, next-scan countdown, all three paper accounts, open positions,
top candidates, and recent activity. Use `--no-dashboard` if you prefer plain
logs.

The script does not place broker orders and does not run news research. It only
uses price/volume data, the technical algorithm, the saved ML gate, and the
optional OpenRouter Pure AI account. It writes today's state, candidates,
summary, and event log under
`tmp/paper_trading_today/YYYYMMDD/`. At market close it flattens open paper
positions and writes `end_of_day_statistics.json` plus
`end_of_day_statistics.csv`. For a quick smoke test that does not wait for
market hours:

```powershell
python .\scripts\paper_trade_today.py --once --force --max-tickers 100
```

## Train ML gate models

Train a reusable ML gate model bundle from backtest trade rows:

```powershell
python .\scripts\train_ml_models.py --input .\ml_training_trades.csv --output-dir .\ml_models\latest
```

The input can be:

- a CSV created by `python backtest.py --export-csv ml_training_trades.csv`
- a backtest JSON that includes `all_trades`

If your JSON was created with `--no-trades-json`, train from an exported CSV instead.

The script writes:

- `model_bundle.joblib`: reusable model bundle with feature metadata and thresholds
- `training_report.json`: metrics, feature importance, and rule/ML gate comparisons

## Train from the full stock universe

Pull OHLCV for every ticker, create one labeled row per stock/date, and train from
the generated stock-candidate dataset:

```powershell
python .\scripts\train_ml_from_stock_data.py --tickers .\all_tickers.txt --start 2019-01-01 --end 2024-12-31 --output-dir .\ml_models\stock_universe
```

By default this uses every generated row. Use `--max-tickers` only for quick tests,
and `--max-train-rows` only if your machine cannot handle the full dataset.
Downloads use ticker batches of 100 and 8 yfinance worker threads by default.
You can tune them with `--batch-size 150 --yfinance-threads 12`, or use
`--yfinance-threads 0` if Yahoo starts rate-limiting.

The stock-universe trainer filters out obvious warrants/units/rights/preferreds
and symbols not in the active listed-stock universe before it calls Yahoo. Some
active tickers still may not have data for an old date range; those are skipped
and written to `unavailable_yahoo_tickers.txt`, while Yahoo's detailed warnings
go to `yfinance_download_warnings.log`.

It also reuses work:

- if `stock_candidate_training_data.csv` already exists, it trains from that file
  and does not download prices again
- use `--rebuild-dataset` to rebuild the candidate CSV from cached prices
- use `--rebuild-price-cache` only when you intentionally want to call Yahoo again

## Weekly retrain pipeline

Run the full production retrain: download price data, generate labeled signals, train
XGBoost + RF ensemble, walk-forward validate, and deploy if quality gate passes (WF ROC ≥ 0.49):

```bash
python scripts/retrain_weekly.py --months 84
```

The pipeline runs in three stages: (1) full backtest to generate training CSV with
correct `--target-mult 1.2 --stop-mult 1.0 --skip-thursday --skip-vix-low-vol
--skip-extended-bounce` filters; (2) XGBoost + RF ensemble train with PSI pruning;
(3) deploy to `ml_models/latest/` if gate passes.

Dry-run (prints command, does not execute):

```bash
python scripts/retrain_weekly.py --dry-run --months 84
```

## Train everything in one resumable command

Run the production ML retrain, stock-universe ML retrain, HMM regime retrain,
Qlib smoke validation, Qlib research report, readiness checks, and audit as one
checkpointed workflow:

```bash
python3 scripts/train_everything.py
```

Before a long run, inspect the exact commands without training:

```bash
python3 scripts/train_everything.py --dry-run
```

If the machine sleeps, the network drops, or you stop the run, resume from the
state file printed by the script:

```bash
python3 scripts/train_everything.py --resume tmp/train_everything/<run_id>/state.json
```

Safety behavior:

- long stages write logs under `tmp/train_everything/<run_id>/logs/`
- new artifacts train into `tmp/train_everything/<run_id>/staging/`
- deployed model directories are backed up under `tmp/train_everything/<run_id>/backups/`
- `ml_models/latest`, `ml_models/stock_universe`, and `ml_models/hmm_regime`
  are promoted only after validation passes
- CPCV, Deflated Sharpe, and noise-feature checks are enabled by default where
  the trainer supports them

Use `--profile quick --max-tickers 25` for a smoke-sized workflow and
`--profile full --include-rl` when you intentionally want the long TD3/RL
training stage included.

Add `--include-qlib-features` when you want both production retraining and
stock-universe retraining to merge leakage-checked, one-day-lagged `qlib_*`
features before training. If a staged ML artifact actually used Qlib features,
promotion is blocked until the Qlib paper account has enough graded forward
evidence. Defaults: at least 20 graded Qlib paper trades, win rate >= 50%, and
average return >= 0%. Tune with `--qlib-min-forward-grades`,
`--qlib-min-forward-win-rate`, and `--qlib-min-forward-avg-return`. The
`--qlib-forward-evidence-warning-only` flag exists for research/staging only; do
not use it for production promotion.

## Live broker safety

Live broker execution must pass `tradingagents.compliance.validate_live_order`.
That shared gate blocks market orders, stale quotes, missing quote timestamps,
yfinance/Yahoo/fallback-only quotes, untrusted providers, market-closed quotes,
and wide spreads. Fidelity, Webull, paper-runner Fidelity routing, and thematic
Fidelity routing all use this contract; preview/paper-only paths may use
historical feeds, but `execute=True` broker orders require fresh trusted quote
evidence.

## Qlib paper portfolio

Run a separate, paper-only Qlib factor account to gather forward evidence before
any production decision:

```bash
python3 scripts/paper_trade_qlib.py --tickers-file all_tickers.txt --max-tickers 100 --reset
```

This runner writes under `tmp/paper_trading_qlib/qlib_factor_paper/`, uses the
same leakage-lagged Qlib feature code as training, and never imports live broker
execution paths. Inspect `qlib_signals_YYYYMMDD.json`,
`qlib_factor_audit_YYYYMMDD.jsonl`, `state.json`, and
`paper_decisions.jsonl` for forward/paper evidence. BUY predictions are also
logged before paper execution in `prediction_ledger.jsonl`; closed trades can be
graded with `PredictionGrader` from the same account directory. `daily_audit.py`
also checks this Qlib paper account via `--qlib-paper-dir`; missing Qlib forward
evidence is a WARN, not production proof.

## Validate thematic signals

Backtest historical thematic scanner signals against realized returns. Reads
`tmp/thematic_score_history.jsonl` and `tmp/thematic_exit_log.jsonl`:

```bash
python scripts/backtest_thematic_signals.py --days 90 --min-score 20
```

Reports: overall win rate and avg return; by score bucket (low/mid/high); insider+social
combo vs. no combo; multi-source vs. single-source; top/worst performers; exit reason
breakdown.

## Rule-based portfolio simulation

Backtest a purely rule-based 3-day hold strategy from a signal CSV:

```bash
python scripts/simulate_rule_based.py --input /tmp/signals.csv --capital 10000
```

Reports capital progression, win rate, monthly P&L, and market violations (wash sale,
insufficient capital, PDT warnings).

## Autofix monitor

Watch paper trading and web server processes, auto-restart on failure, and send
SMS/email alerts:

```bash
python scripts/autofix_monitor.py --config autofix_config.json
```

Runs as a background daemon. Configured via JSON; processes monitored, restart
commands, and alert recipients all specified in config.

## Breakout scan (standalone)

Run a single breakout scan without the full paper trading harness:

```bash
python scripts/scan_breakouts.py --tickers NVDA AAPL MSFT --output /tmp/scan.json
```

## Log rotation

Rotate server and paper trading logs (capped at 10 MB each, keeps 7 gzip archives).
Normally run by launchd at 00:05 daily:

```bash
python scripts/rotate_logs.py
```
