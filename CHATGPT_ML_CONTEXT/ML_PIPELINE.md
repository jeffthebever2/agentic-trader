# ML & Trading Pipeline

The system operates via a continuous loop of data gathering, feature generation, signal filtering, and backtest-driven model training.

## 1. Market Data Source
Data is fetched via connectors in `tradingagents/dataflows/` (Financial Modeling Prep, Alpha Vantage, yfinance, SEC, DuckDuckGo). This includes raw OHLCV, macro indicators, and news sentiment.

## 2. Feature Generation
`tradingagents/agents/analysts/` process the raw data.
- **LLM Features**: Agents pass news and SEC filings to LLMs to generate structured sentiment/risk scores.
- **Technical Features**: Computed using libraries like `stockstats` via `dataflows/stockstats_utils.py`.

## 3. Base Signal Generation
The analyst agents pass feature vectors to `tradingagents/agents/trader/trader.py`. The trader forms an initial hypothesis (Long, Short, Hold).

## 4. ML Gate Filtering (Prediction)
If ML models exist (`ml_models/latest/model_bundle.joblib`), the base signal is evaluated:
- Inputs: The generated features.
- Predictions: `win_probability`, `large_loss_probability`, `expected_return`.
- **Signal Ranking/Scoring**: If `win_probability` < threshold (e.g., 0.6) or `large_loss_probability` > threshold, the signal is discarded. 

## 5. Risk Filtering & Position Sizing
- Signals that pass the ML Gate go to `tradingagents/agents/risk_mgmt/`.
- Position sizes are calculated dynamically in `tradingagents/portfolio/position_sizing.py` (Kelly Criterion, volatility scaling, etc.).

## 6. Execution Path
Approved orders are sent to paper (`web/api/paper.py`) or live brokers (`web/api/fidelity.py`).

## 7. The Backtest Training Loop
1. The user runs `backtest.py` across historical periods using *base signals* (or prior ML models).
2. The backtest outputs JSON files containing all taken trades, including their resulting PnL, duration, and the features present at the time.
3. `scripts/train_ml_models.py` parses these backtest JSON files. It uses the features as `X` and the trade outcome (win/loss, return) as `y`.
4. It trains new ML gate models and saves them back to `ml_models/latest/`.

## Alternative: RL Flow
The RL pipeline (`tradingagents/rl/`) bypasses the discrete ML Gates. The TD3 Agent observes a continuous state vector from `environment.py` and outputs an array of portfolio weights directly, adjusting allocations dynamically over time.
