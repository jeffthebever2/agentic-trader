# Profitability Audit

This document outlines structural vulnerabilities in the current ML and RL pipelines that likely overstate expected returns in backtests, leading to poor live performance.

## 1. Data Leakage & Lookahead Bias Risks
- **Feature Construction**: If analyst agents generate technical or LLM-based features using the *Close* price of a day, but the trade is simulated as executing at the *Open* or *Close* of the identical day, the model is looking into the future.
- **Data Peeking**: Normalizing or scaling features across the entire dataset before splitting into train/test leaks future distributions into the training set.

## 2. Weak Train/Test Splits
- Financial data is highly autocorrelated. Standard random `train_test_split` logic allows the model to train on Day 10 and test on Day 9, leaking subsequent market states. 
- **Missing**: A strict Time-Series split or Walk-Forward Optimization (WFO) is necessary to ensure the model only ever predicts the future based on the past.

## 3. Overfitting Risks
- The `train_ml_models.py` script uses Random Forests with `max_depth=6` and `n_estimators=500`. If a backtest only produces 500-1,000 trades, the model has too much capacity and will perfectly memorize the historical trades, fitting to noise rather than signal.

## 4. Unrealistic Slippage / Cost Assumptions
- Deep RL models (`rl_models`) and `backtest_engine.py` can fall into the trap of assuming infinite liquidity or filling exactly at the recorded Close/Open without slippage. 
- Without penalizing turnover with transaction costs, RL agents learn to rapidly trade noise, which is completely unprofitable live.

## 5. Weak Confidence Calibration
- The ML Gate uses a hard probability threshold (e.g., `0.60`). However, Random Forests output the fraction of voting trees, which is **not** a calibrated empirical probability. A 0.6 output might actually correspond to a 40% real-world win rate. 
- **Missing**: Platt Scaling or Isotonic Regression to calibrate probabilities.

## 6. Missing Market Regime Handling
- Models trained heavily on backtests from 2020-2021 (a massive bull run) will learn that buying dips always works. They will fail catastrophically during a regime shift (e.g., high-volatility bear markets).
- **Missing**: Explicit regime tracking as a top-level feature or training separate models per regime.

## 7. Backtest vs Live Mismatch
- Paper trading executes based on real-time bid/ask spread and order book depth. Backtesting often assumes infinite volume at a single price point. 
- Stop-loss and Take-profit orders might trigger intra-day in real life, but if the backtest only uses EOD (End of Day) data, the risk simulation is wildly inaccurate.
