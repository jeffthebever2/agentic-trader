# TradingAgents Command Sheet (v1.0.0)

Run these from the project root:

```bash
cd /path/to/TradingAgents
# or if cloned from GitHub:
cd ~/projects/agentic-trader
```

## Start The Web App

```bash
python3 web/start.py --port 8001
```

Open:

```text
http://localhost:8001
```

If the port is already busy, either stop the old server with `Ctrl+C`, or use another port:

```bash
python3 web/start.py --port 8002
```

## Restore A Fresh Computer And Start Everything

After cloning the repo on another computer, run the restore command from the
project root. It checks dependencies, restores optional local ML/data artifacts,
starts the web app, starts Cloudflare Tunnel, and prints troubleshooting logs.

First install the command:

```bash
uv sync --extra web --extra dev
```

If you do not have `uv` yet:

```bash
python3 -m pip install -e ".[web,dev]"
```

Run diagnostics:

```bash
agentic-restore doctor
```

Start the app and named Cloudflare tunnel:

```bash
agentic-restore start --restart
```

If Cloudflare named tunnel is not set up yet, use a temporary Quick Tunnel:

```bash
agentic-restore start --restart --quick-tunnel
```

Check status and logs:

```bash
agentic-restore status
```

Stop the local web server and tunnel screen sessions:

```bash
agentic-restore stop
```

### Restore ML / Data Artifacts

Git intentionally does not include large local folders like `ml_models`,
`rl_models`, `.backtest_cache`, and generated DB/cache files.

On the old computer, bundle local ML/data artifacts:

```bash
agentic-restore bundle-data --output ~/Desktop/agentic-trader-artifacts.tar.gz
```

Move that tarball to the new computer, then restore and start everything:

```bash
agentic-restore all \
  --artifact-tar ~/Desktop/agentic-trader-artifacts.tar.gz \
  --install \
  --restart
```

If you copied a folder instead of a tarball:

```bash
agentic-restore all \
  --artifact-dir /path/to/agentic-trader-artifacts \
  --install \
  --restart
```

The restore command expects these artifact paths when present:

```text
ml_models/
rl_models/
.backtest_cache/
backtest_index.db
```

Useful tunnel troubleshooting:

```bash
cloudflared tunnel login
cloudflared tunnel info dsadsa
tail -n 80 tmp/cloudflared.screen.log
tail -n 80 tmp/web.screen.log
```

## Stop Running Jobs

In the terminal running the job:

```text
Ctrl+C
```

For long ML training, one `Ctrl+C` is safe. It keeps the downloaded price cache and already-written CSV rows.

## Normal Backtest

Basic confirmed-pullback backtest:

```bash
python3 backtest.py \
  --tickers all_tickers.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --threshold 100 \
  --score-mode confirmed_pullback \
  --entry-timing trigger_break \
  --hold-periods 1 2 3 \
  --primary-hold 3 \
  --export-csv backtest_trades.csv
```

Recent backtest through 2026:

```bash
python3 backtest.py \
  --tickers all_tickers.txt \
  --start 2020-01-01 \
  --end 2026-05-07 \
  --threshold 100 \
  --score-mode confirmed_pullback \
  --entry-timing trigger_break \
  --hold-periods 1 2 3 \
  --primary-hold 3 \
  --export-csv backtest_trades_2026.csv
```

Faster backtest without charts:

```bash
python3 backtest.py \
  --tickers all_tickers.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --threshold 100 \
  --no-generate-charts \
  --export-csv backtest_trades.csv
```

Grid-search thresholds:

```bash
python3 backtest.py \
  --tickers all_tickers.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --grid-search \
  --grid-thresholds 70 80 90 100 \
  --export-csv backtest_grid_trades.csv
```

## Paper Trading

Start live paper trading from the web page, or run from terminal:

```bash
python3 scripts/paper_trade_today.py \
  --tickers all_tickers.txt \
  --model-bundle ml_models/stock_universe/model_bundle.joblib \
  --starting-cash 10000 \
  --scan-interval-minutes 15 \
  --output-dir tmp/paper_trading_today \
  --no-dashboard
```

One scan only, useful for testing:

```bash
python3 scripts/paper_trade_today.py \
  --tickers all_tickers.txt \
  --model-bundle ml_models/stock_universe/model_bundle.joblib \
  --starting-cash 10000 \
  --max-tickers 200 \
  --once \
  --force \
  --no-ai \
  --no-dashboard \
  --output-dir tmp/paper_test
```

Compare old ML vs new challenger ML:

```bash
python3 scripts/paper_trade_today.py \
  --tickers all_tickers.txt \
  --model-bundle ml_models/stock_universe/model_bundle.joblib \
  --new-model-bundle ml_models/stock_universe_candidate_20260512/model_bundle.joblib \
  --starting-cash 10000 \
  --scan-interval-minutes 15 \
  --output-dir tmp/paper_trading_today \
  --no-dashboard
```

## Train New ML Separately

This creates a separate challenger model and does not overwrite the old model:

```bash
python3 scripts/train_ml_from_stock_data.py \
  --tickers all_tickers.txt \
  --start 2019-01-01 \
  --end 2026-05-07 \
  --output-dir ml_models/stock_universe_candidate_20260512 \
  --dataset-csv ml_models/stock_universe_candidate_20260512/stock_candidate_training_data.csv \
  --rebuild-dataset \
  --rebuild-price-cache \
  --hold 3 \
  --batch-size 100 \
  --yfinance-threads 8 \
  --n-estimators 250 \
  --max-depth 8 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.58 \
  --ml-expected-return-min 0.0 \
  --ml-large-loss-max 0.20
```

## Resume ML Training

Use this if training was stopped after it already created the price cache and partial CSV:

```bash
python3 scripts/train_ml_from_stock_data.py \
  --tickers all_tickers.txt \
  --start 2019-01-01 \
  --end 2026-05-07 \
  --output-dir ml_models/stock_universe_candidate_20260512 \
  --dataset-csv ml_models/stock_universe_candidate_20260512/stock_candidate_training_data.csv \
  --price-cache ml_models/stock_universe_candidate_20260512/price_data_2017-11-07_2026-05-20_SPY_4815.pkl \
  --rebuild-dataset \
  --resume-dataset \
  --hold 3 \
  --batch-size 100 \
  --yfinance-threads 8 \
  --n-estimators 250 \
  --max-depth 8 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.58 \
  --ml-expected-return-min 0.0 \
  --ml-large-loss-max 0.20
```

If moving to another computer, copy this folder:

```text
ml_models/stock_universe_candidate_20260512
```

Also make sure the other computer has the updated code with `--resume-dataset`.

## Use Existing Dataset Only

If the CSV is already fully built and you only want to train from it:

```bash
python3 scripts/train_ml_from_stock_data.py \
  --tickers all_tickers.txt \
  --output-dir ml_models/stock_universe_candidate_20260512 \
  --dataset-csv ml_models/stock_universe_candidate_20260512/stock_candidate_training_data.csv \
  --reuse-dataset \
  --hold 3 \
  --n-estimators 250 \
  --max-depth 8 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.58 \
  --ml-expected-return-min 0.0 \
  --ml-large-loss-max 0.20
```

## Check Model Files

Old/current big model:

```text
ml_models/stock_universe/model_bundle.joblib
```

New challenger model:

```text
ml_models/stock_universe_candidate_20260512/model_bundle.joblib
```

Check that the new model exists:

```bash
ls -lh ml_models/stock_universe_candidate_20260512/model_bundle.joblib
```

## Useful Health Checks

Compile key Python files:

```bash
python3 -m py_compile \
  backtest.py \
  scripts/paper_trade_today.py \
  scripts/train_ml_from_stock_data.py \
  web/api/paper.py
```

Run paper trading tests:

```bash
python3 -m pytest tests/test_paper_trading_state.py -q
```

Check if the web server or paper runner is already running:

```bash
ps aux | rg "web/start.py|paper_trade_today.py"
```

Check paper status API:

```bash
curl -s http://localhost:8001/api/paper/status | head -c 500
```
