# Safe Improvement Ideas

This document outlines structural improvements to the ML and Backtesting pipeline to improve realistic profitability and robustness, prioritized by impact.

## Quick Wins
- **Probability Calibration**: Add Platt Scaling (`CalibratedClassifierCV`) to the Random Forest classifiers in `scripts/train_ml_models.py` so the `0.60` threshold maps to a true 60% empirical win rate.
- **Aggressive Feature Pruning**: Log the feature importance output from `train_ml_models.py`. Aggressively drop the bottom 50% of features to reduce dimensionality and overfitting.
- **Regularize Trees**: Reduce `max_depth` to 3 or 4, or increase `min_samples_leaf` significantly when training on small trade datasets.

## Medium Improvements
- **Time-Series Split (Strict)**: Implement `TimeSeriesSplit` in `scripts/train_ml_models.py` instead of generic evaluation. Ensure test sets are strictly chronologically after train sets.
- **Slippage Injection**: Add configurable, randomized slippage (e.g., 1-5 bps) and commission models to `tradingagents/backtesting/backtest_engine.py` and the RL `environment.py`.
- **Intra-day Drawdown Simulation**: Modify the backtester to utilize daily High/Low data to accurately trigger stop-losses, preventing the illusion of surviving intra-day crashes.

## Advanced Improvements
- **Stationary RL States**: Update the RL environment observation space in `tradingagents/rl/environment.py` to use Fractional Differencing or log-returns rather than raw prices, making the network resilient to price scaling over time.
- **Regime-Switched Ensembles**: Train separate ML Gate models for High Volatility (VIX > 30) vs Low Volatility environments, and dynamically route signals to the correct gate.

## Highest Expected Impact
Eliminating Lookahead Bias and Overfitting by instituting strict Walk-Forward out-of-sample validation.

## Safest Files to Edit
- `scripts/train_ml_models.py` (Offline training logic only; cannot break live trading directly).
- `tradingagents/agents/analysts/*` (Feature generation logic; safe to expand).
- `tradingagents/rl/environment.py` (Safe to modify reward functions and state representations).

## Dangerous Files to Avoid
- `tradingagents/agents/risk_mgmt/*` (Do not weaken hard stop-loss/exposure limits).
- `web/api/fidelity.py` / `web/api/webull_portfolio.py` (Live execution logic; highly sensitive).
- `tradingagents/agents/schemas.py` (Changing schemas breaks IPC between agents).

## Validation Plan
1. **Offline**: Any ML change must show stable or improved metrics on a strict Walk-Forward out-of-sample backtest.
2. **Paper**: Deploy the updated `model_bundle.joblib` to the paper trading system (`web/api/paper.py`) for a minimum of 2 weeks.
3. **Audit**: Compare Paper PnL to the Backtested PnL for the identical time period to identify execution gaps or slippage miscalculations.
