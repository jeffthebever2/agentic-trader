# ML Repository Map

This document maps where the core machine learning, reinforcement learning, and quantitative logic reside in the TradingAgents system.

## Where Logic Lives
- **ML Gate Models**: 
  - Code: `scripts/train_ml_models.py` (Trains models on backtest outputs), `web/api/ml.py` (API endpoints).
  - Storage: `ml_models/latest/model_bundle.joblib`
- **RL Trading Models**: 
  - Code: `tradingagents/rl/td3_agent.py` (Twin-Delayed DDPG algorithm), `tradingagents/rl/environment.py` (Custom OpenAI-gym-like environment).
  - Storage: `rl_models/`
- **Signal Generation & Feature Engineering**: 
  - `tradingagents/agents/analysts/` (Market, Regime, Social Media, News, Fundamentals)
  - `tradingagents/dataflows/` (Data connectors for FMP, Alpha Vantage, yfinance)
- **Execution & Validation**: 
  - `tradingagents/agents/trader/trader.py` (The main actor that evaluates signals against ML predictions).
  - `tradingagents/backtesting/backtest_engine.py` & `backtest.py` (Orchestrates historical testing).

## What Each Folder Does
- **`ml_models/`**: Holds the serialized `joblib` bundles for the traditional ML classifiers/regressors (Random Forests) that act as "gates" (e.g., `win_probability`, `expected_return`).
- **`rl_models/`**: Holds PyTorch `.pt` checkpoints for the RL Actor-Critic networks.
- **`tradingagents/rl/`**: Deep RL code. Not used for standard signals, but handles continuous portfolio allocation via TD3.
- **`tradingagents/agents/analysts/`**: LLM and rule-based feature generation. These compute the input vectors that eventually feed into the ML models.
- **`tradingagents/backtesting/`**: Simulates trades. The output JSON of backtests is actually the *training data* for the ML gate models.

## What Should Be Ignored
- Avoid touching `tradingagents/agents/schemas.py` or `tradingagents/metrics.py` as they define rigid data structures used across the app.
- Ignore cache directories (`.backtest_cache/`, `tmp/`).

## What Not to Edit Casually
- **Broker Code**: `web/api/fidelity.py`, `web/api/webull_portfolio.py`, `web/api/paper.py`.
- **Risk Managers**: `tradingagents/agents/risk_mgmt/`. These are hard-coded safety nets preventing AI hallucinations from wiping out the portfolio.
