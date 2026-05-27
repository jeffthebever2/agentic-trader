# Model Inventory

## 1. ML Gate Models (Ensemble)
- **File Path**: `ml_models/latest/model_bundle.joblib` (Trained by `scripts/train_ml_models.py`)
- **Model Type**: Random Forest / Decision Tree Ensembles (via scikit-learn).
- **Input Features**: Mixed technical indicators, LLM sentiment scores, and fundamental data.
- **Target/Label**: 
  - Binary: Trade resulted in a win (`_win_label`), Trade hit a large loss (`_large_loss_label`), Target hit before stop (`_target_label`).
  - Continuous: Absolute return over holding period (`h{N}_return`).
- **Prediction Output**: Probabilities (0 to 1) for binary outcomes, float for expected return.
- **How it is Trained**: Supervised learning on historical trades exported from `backtest.py`.
- **How it is Evaluated**: Precision, Recall, F1, ROC AUC, Brier Score (Classification) / MAE, R2 (Regression).
- **Where Used**: `tradingagents/agents/trader/trader.py` queries these models to validate or reject a generated signal.
- **Affects Real Trades**: **YES**. It acts as the final gatekeeper.
- **Risks/Weaknesses**: 
  - **Data Leakage**: Features generated during backtest might inadvertently look ahead.
  - **Overfitting**: High `max_depth` or small trade sample sizes lead to memorizing the backtest.
  - **Calibration**: Output probabilities may be uncalibrated (e.g., a 0.6 output doesn't mean exactly 60% empirical probability).

## 2. Reinforcement Learning TD3 Agent
- **File Path**: `tradingagents/rl/td3_agent.py` & `rl_models/`
- **Model Type**: Twin-Delayed DDPG (Actor-Critic framework using PyTorch).
- **Input Features**: Dense state vector from `tradingagents/rl/environment.py` (prices, holdings, indicators).
- **Prediction Output**: Continuous action space `[-1, 1]^N` representing asset allocations/weights.
- **How it is Trained**: Episodic trial-and-error interacting with historical price environments. Rewards are likely based on Sharpe ratio or raw PnL minus penalties.
- **How it is Evaluated**: Cumulative episodic reward and portfolio metrics.
- **Where Used**: Advanced / experimental portfolio allocation.
- **Affects Real Trades**: Depends on system configuration; mostly used in experimental or strictly defined RL portfolios.
- **Risks/Weaknesses**: 
  - Extreme sample inefficiency.
  - Deep RL in finance is highly prone to exploiting environment simulator bugs (e.g., zero slippage, instant fills).
  - Non-stationary market regimes cause trained RL agents to fail abruptly out-of-sample.
