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
