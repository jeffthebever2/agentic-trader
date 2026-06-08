# QUANT RESEARCH ROADMAP
**agentic-trader — Institutional-Grade Upgrade Path**
Generated: 2026-06-05 | Status: Planning only. No code changed.

---

## 1. Executive Summary

**Goal:** Transform agentic-trader from a signal-heavy AI trading system — already among the most mature retail quant frameworks — into an institutional-style quant research pipeline with stronger data caching, rigorous walk-forward validation, path-dependent labeling, GBDT ensembles, meta-labeling, weight-centric portfolio construction, and probabilistic risk overlays.

**Not a rewrite.** Every existing feature stays. New layers are additive modules. The current pullback/breakout scoring engine, SPY/VIX/sector regime stack, multi-agent LLM framework, paper trading loop, and ML gate remain the production core. This roadmap adds new plumbing *underneath and around* that core.

**Why now:** The audit in `docs/plans/PORTFOLIO_AUDIT_2026-05-30.md` identified ~60 live discrepancies. Several of them (TP-1 through TP-5, DL-1 through DL-9) are bugs in existing code that inflate backtest metrics relative to live. Before adding new models on top, the foundation must be clean. This roadmap assumes those bug fixes happen in parallel (tracked separately) and focuses on the *new architectural layers* listed in `agentic_trader_RAW_reference_full.txt`.

**Primary edge insight from live data:** Timeout (time-exit) trades have 43.9% timeout win rate with 98.7% reliability. The alpha lives in *survival to expiry*, not target-hitting. Every new layer must preserve and amplify this edge, not override it.

---

## 2. Current Local Repo Strengths

Based on direct inspection of local files. Not assumed from GitHub.

### 2.1 TradingAgents Multi-Agent Framework
- `tradingagents/agents/` — full agent suite: fundamentals, sentiment, news, technical, bull/bear researchers, trader, risk debaters (aggressive/conservative/neutral), portfolio manager.
- `tradingagents/graph/trading_graph.py` — LangGraph orchestration with checkpoint resume.
- `tradingagents/llm_clients/` — multi-provider: Anthropic, Azure, Google, OpenAI, OpenRouter.
- `tradingagents/dataflows/` — AlphaVantage, FMP, SEC fundamentals, yfinance, DuckDuckGo, social signals, macro signals.

### 2.2 backtest.py
- TradingAgents Backtest v3 — full walk-forward, grid search, Monte Carlo modes.
- Batch yfinance download with week-floored cache keys (`.backtest_cache/` pickle files).
- Actual High/Low intraday stop/target detection (`measure_outcome`).
- SPY/VIX/sector regime stack: `build_spy_regime`, `build_combined_regime`, `build_vix_regime`, `build_vix_term_structure`, `build_sector_breadth`.
- VIX regime tuning (backwardation/contango, low_vol/normal/elevated/crisis buckets with documented PF and Sortino per bucket).
- 20+ technical indicators computed as series: ATR, Stochastic, ADX, OBV, MFI, CMF, CCI, ROC, RSI, MACD, Bollinger Bands, Keltner channels, EMA/SMA, volume ratios, candlestick features.
- Look-ahead bias awareness: price filter deliberately disabled at whole-history level; applied per scan date.

### 2.3 ML Training Pipeline
- `scripts/train_ml_models.py` — XGBoost + RandomForest gate with Brier-score calibration (isotonic for large datasets, sigmoid for small), calibration diagnostics, leakage checking, threshold search, per-regime diagnostics, confidence bucket analysis.
- `scripts/train_ml_from_stock_data.py` — full OHLCV-to-labels-to-model pipeline without requiring prior backtest export; SPY/VIX/sector features baked in at training time.
- `backtest.py:_ml_purged_walk_forward` — purged walk-forward CV with configurable embargo already implemented.
- `backtest.py:_ml_time_split` — embargo-aware time split already implemented.
- `scripts/validate_holdout.py` — strict holdout runner that refuses to train; detects model data-end from bundle.
- `scripts/retrain_weekly.py` — weekly automated retrain with leakage check gate, ROC/Brier gates, bundle swap with backup, retrain history log.
- VIX enrichment cache at `/tmp/vix_sector_features.csv`.

### 2.4 Portfolio & Risk Stack
- `tradingagents/portfolio/` — 19 modules: `alpha_engine.py`, `candidate_ranker.py`, `correlation.py`, `drawdown.py`, `drift_detector.py`, `exit_manager.py`, `feature_monitor.py`, `position_sizing.py`, `prediction_grader.py`, `production_safety.py`, `reliability_stats.py`, `safe_trade_guard.py`, `short_hold_exits.py`, `state.py`, `ticker_reliability.py`, `unified_brain.py`.
- Kelly sizing (fractional), ATR volatility scaling, streak adjustments, correlation clustering cap (max 2 correlated), drawdown circuit breakers, kill-switch.
- `drift_detector.py` — ML feature drift detection.
- `prediction_grader.py` — per-trade prediction grading.
- `reliability_stats.py` — per-regime and per-ticker reliability stats.

### 2.5 Paper Trading & Simulation
- `scripts/paper_trade_today.py` — 15-minute live paper loop; ML gate applied; no broker orders.
- `scripts/paper_trade_unified.py` — UnifiedBrain parallel paper account.
- `tradingagents/rl/` — RL environment (TD3 agent) for research sandbox.

### 2.6 Web/Runtime Tooling
- `web/` — FastAPI dashboard with paper, portfolio, ML, RL, backtest, scanner, admin, settings routes.
- `cli/restore_runtime.py` — agentic-restore: doctor, start, stop, status, bundle-data, all restore modes.
- `.backtest_cache/` — batch pickle cache with week-floored keys (fast re-runs).
- `ml_models/retrain_history.jsonl` — per-cycle retrain provenance log.

### 2.7 Local-Only Artifacts Not Visible on GitHub
- `.backtest_cache/` — large batch price cache (hundreds of pickle files confirmed present).
- `ml_models/stock_universe/model_bundle.joblib` and `ml_models/latest/model_bundle.joblib` — trained bundles.
- `backtest_index.db` — SQLite backtest trade index.
- `tmp/paper_trading_today/` — live paper account state.
- `docs/plans/PORTFOLIO_AUDIT_2026-05-30.md` — 60-finding audit (not on GitHub).
- `docs/plans/UNIFIED_BRAIN_UPDATES.md` — B1-B21 improvement path.
- `agentic_trader_RAW_reference_full.txt` — full quant research reference (not on GitHub).

---

## 3. Current Local Repo Weaknesses

Based on code inspection + reference file cross-reference.

### 3.1 yfinance Fragility
- All price data goes through `yf.download()` with 3-attempt retry and exponential backoff (`backtest.py:100-126`), but there is no persistent centralized cache layer outside `.backtest_cache/` pickle batches.
- The paper trading loop calls `yf` inline every 15-minute scan (`paper_trade_today.py`); a rate-limit or network failure returns stale or empty data — feed failure is not clearly separated from signal absence.
- No fallback provider. No rate-limit token bucket. No DuckDB/SQLite OHLCV store for offline training.
- `state.py:253,291` (audit TP-5) re-fetches live yfinance price on every valuation cycle — inconsistent intra-decision prices and garbage PnL on network fail.

### 3.2 No Centralized Market Data Provider / Interface
- `tradingagents/dataflows/y_finance.py` wraps yfinance for the LLM agent tools.
- `backtest.py:download_all` is a standalone function, not an interface.
- No `MarketDataProvider` abstract class. Swapping to Polygon/Alpaca/cached SQLite requires modifying every caller.

### 3.3 Validation Limitations
- Purged walk-forward CV exists (`_ml_purged_walk_forward`) but CPCV (Combinatorial Purged Cross-Validation) — which generates multiple OOS paths and a distribution of Sharpe ratios — is not implemented. The system produces one OOS path and treats it as ground truth.
- Deflated Sharpe Ratio (DSR) — which adjusts backtest Sharpe downward based on the number of trials/hyperparameter iterations — is not computed. Every grid search risks selecting an overfit model.
- No random noise feature injection test: features that rank below random noise should be pruned.
- No paper-vs-backtest fill drift report. The audit found stops fill at re-fetched live price, not stop level (TP-4), so the existing PnL log is inflated.
- `reliability_stats.py:57` — calibration = first-moment only. Does not check per-bucket monotonicity.

### 3.4 Fixed-Horizon Label Weakness
- `measure_outcome` in `backtest.py` uses H1/H2/H3/H5 fixed forward returns as labels. This ignores path: a trade that hits the stop on day 2 then recovers to +3% by day 5 is labeled a winner.
- Triple-barrier labels (hit target first = 1, hit stop first = 0, time expires = tie/0) align with actual fill behavior. The live system already uses ATR-based stops and targets (`screener._ATR_STOP`), so the backtest label should match.
- Fixed-horizon creates path-reversal bias and misaligns the ML gate's objective with actual trade profitability.

### 3.5 No CPCV
- Without CPCV, all walk-forward Sharpe estimates are path-dependent. One unlucky path order gives a different Sharpe than a different ordering of the same data. CPCV generates C(N,k) paths and reports a distribution.

### 3.6 No Deflated Sharpe Ratio
- `retrain_weekly.py` gates on `wf_roc >= min_roc` and `brier < max_brier` but does not penalize for number of trials. Cycle 46 removed min-adv/min-price filters because they cut too much data — but that filter removal itself constitutes a trial. Without DSR tracking, cumulative overfitting is invisible.

### 3.7 Slippage / Market Impact Not in Training Labels
- `validate_holdout.py` supports `--account-commission` and `--account-slippage-bps` flags, but slippage is not embedded in the training label. The model learns to predict profitability at theoretical prices, not fill prices. Realistic labels require slippage baked in.

### 3.8 No Paper-vs-Backtest Drift Tracking
- The audit (TP-4) found stops fill at re-fetched live price rather than stop level, inflating paper WR. No daily reconciliation script aligns paper fill prices vs theoretical signal prices.

### 3.9 No GBDT Ensemble
- `train_ml_models.py` uses XGBoost if available, else RandomForest. No LightGBM. No CatBoost. No ensemble averaging. Financial tabular research (reference Section 6) finds CatBoost more robust to categorical features and prediction shift; LightGBM faster for large regression tasks; XGBoost most stable cross-sectionally. All three together reduce individual-model overfitting.

### 3.10 No Meta-Labeling
- The current ML gate is a primary model: it predicts trade profitability directly. Meta-labeling means training a *secondary* model on the *primary model's outcomes* — i.e., given that the primary model said BUY, did the trade actually profit? The secondary model output becomes the position sizing weight, not the entry filter. This decoupling limits overfitting and aligns sizing with actual probability of success.

### 3.11 Weight-Centric Portfolio Construction Missing
- Current portfolio: candidate_ranker computes alloc_weights but they are never applied to capital (DL-2 in audit). Position size is determined by ATR sizing, Kelly, tier, streak — but not by a portfolio optimizer that considers cross-sectional correlation and risk contribution simultaneously.
- No HRP. No turnover constraint. No sector/theme exposure cap enforced at optimizer level. Correlation cap (`max 2 correlated`) is wired only in `trading_graph.py`, absent from the paper trade entry loop (DL-3 in audit).

### 3.12 HMM Regime Layer Missing
- Current regime: SPY 50/200 SMA + VIX buckets + sector breadth. This is a deterministic rule-based classifier.
- A Gaussian HMM trained on returns, vol, volume, and VIX produces probabilistic regime states and a transition matrix — letting the system fade into or out of exposure based on regime probability, not hard thresholds. This is a later upgrade (Phase 7) because it requires clean data infrastructure first.

### 3.13 No Experiment Tracking
- `retrain_weekly.py` produces `training_report.json` and `ml_models/retrain_history.jsonl`. These are flat files. There is no centralized registry to compare hyperparameter iterations, feature sets, ROC curves, or model drift over months of cycles. MLflow local (SQLite backend) would provide this with zero external dependencies.

---

## 4. Implementation Phases

### Phase 0 — Planning + Baseline Snapshot

**Goal:** Freeze current performance metrics. No code changes. Create comparison anchor.

**Why:** Before any change, need a documented baseline so future phases can demonstrate improvement or regression. Several audit findings (TP-1 through TP-5) may already inflate current metrics. The snapshot captures the as-is state.

**Local files likely read:**
- `backtest.py`, `scripts/retrain_weekly.py`, `scripts/validate_holdout.py`
- `ml_models/latest/model_bundle.joblib`
- `ml_models/retrain_history.jsonl`

**New files/modules needed:**
- `scripts/snapshot_baseline.py` — runs backtest on a fixed date range, runs holdout validation, dumps metrics to `docs/baseline_snapshot_<date>.json`. Read-only. Does not train.

**Dependencies needed:** None (already installed).

**CLI flags needed:**
- `--snapshot-date` (default: today)
- `--output docs/baseline_snapshot.json`

**Tests required:**
- `tests/test_snapshot_baseline.py` — verify JSON output structure; verify no bundle swap occurs; verify holdout range does not overlap training range.

**Success metrics:**
- `baseline_snapshot.json` exists with: wf_roc, brier, holdout_roc, holdout_brier, trade_count, win_rate, profit_factor, Sortino, max_drawdown.
- Script is idempotent (re-running produces same output for same date).

**Risks:** None — read-only.

**Rollback:** Delete the snapshot file. Nothing else changes.

**What NOT to do in this phase:**
- Do not re-train or swap the bundle.
- Do not fix any bugs from the audit yet.
- Do not modify backtest.py.

---

### Phase 1 — Market Data Cache / Provider Layer

**Goal:** Introduce a `MarketDataProvider` abstract interface and a local DuckDB/SQLite OHLCV cache. All training, backtest, and paper-trade price fetches go through this interface.

**Why:** yfinance rate limits, network failures, and inconsistent intra-cycle prices (audit TP-5) corrupt training data and paper trade PnL. A local OHLCV store eliminates network dependency for historical data and makes training fully reproducible. The interface allows future swap to Polygon/Alpaca without touching callers.

**Local files likely affected:**
- `backtest.py:download_all` — route through provider.
- `scripts/train_ml_from_stock_data.py` — replace direct `yf.download` calls.
- `scripts/paper_trade_today.py` — replace live `yf` calls for EOD data; keep live polling separate.
- `tradingagents/dataflows/y_finance.py` — wrap behind interface for LLM agent tools.

**New files/modules needed:**
- `tradingagents/dataflows/market_data_provider.py` — abstract base class `MarketDataProvider` with `get_ohlcv(ticker, start, end) -> pd.DataFrame`, `get_ohlcv_batch(tickers, start, end) -> dict`.
- `tradingagents/dataflows/yfinance_provider.py` — concrete implementation wrapping existing `yf.download` with retry logic.
- `tradingagents/dataflows/cached_provider.py` — decorator/wrapper that checks DuckDB/SQLite before calling upstream provider; writes results back.
- `tradingagents/dataflows/ohlcv_cache.py` — DuckDB (preferred for columnar analytics) or SQLite schema: `(ticker TEXT, date DATE, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL, PRIMARY KEY (ticker, date))`. Migration script for existing `.backtest_cache/` pickle data.

**Dependencies needed:**
```
duckdb>=0.10.0        # or just use sqlite3 from stdlib
```
DuckDB preferred because it can directly query Parquet/CSV without loading into memory and supports analytical window functions natively. Add to `pyproject.toml` optional `[quant]` group.

**CLI flags needed:**
- `--data-cache-path` (default: `~/.agentic_trader/ohlcv_cache.duckdb`)
- `--no-data-cache` (bypass cache, force yfinance)
- `--warm-cache` (pre-populate cache for date range)

**Tests required:**
- `tests/test_market_data_provider.py` — mock yfinance; verify cache hit avoids network call; verify schema consistency; verify fallback on empty cache.
- `tests/test_cached_provider.py` — write + read round-trip; verify stale data detection by date range.

**Success metrics:**
- Re-running `backtest.py` on already-cached dates completes with zero network calls.
- Training speed for `train_ml_from_stock_data.py` on cached data improves vs fresh download.
- `paper_trade_today.py` EOD scan uses cache for historical feature computation; only live intraday polls remain network-dependent.

**Risks:**
- DuckDB version conflicts with existing dependencies.
- Schema migration from existing pickle cache is lossy (pickles are batch-level, not ticker-level). May require re-download of some batches.
- `HUMAN REVIEW NEEDED:` Decide DuckDB vs SQLite3. DuckDB is faster for analytics. SQLite is zero-dependency. Both are valid.

**Rollback:** Set `--no-data-cache`. Old pickle cache still exists. New code path is opt-in.

**What NOT to do in this phase:**
- Do not remove the existing `.backtest_cache/` pickle system yet. Keep it as fallback.
- Do not add live streaming/websocket data.
- Do not change the yfinance polling loop in `paper_trade_today.py` for live intraday prices.
- Do not integrate paid data providers (Polygon, Alpaca).

---

### Phase 2 — Institutional Validation Layer

**Goal:** Add CPCV, Deflated Sharpe Ratio tracking, random noise feature injection test, and paper-vs-backtest fill drift reporting to the existing validation stack.

**Why:** The current validation produces one OOS path. CPCV produces C(N,k) paths and reports a distribution of Sharpe ratios, making overfit detection possible. DSR adjusts for the number of trials in `retrain_weekly.py`. Random noise injection prunes features with no real signal. The paper-vs-backtest drift report quantifies execution degradation vs theoretical signal prices.

**Local files likely affected:**
- `backtest.py:_ml_purged_walk_forward` — extend with CPCV logic.
- `scripts/train_ml_models.py` — add DSR computation; add noise injection test.
- `scripts/retrain_weekly.py` — add DSR gate alongside ROC/Brier gates; log trial count.
- `scripts/validate_holdout.py` — add paper-vs-backtest drift comparison flag.

**New files/modules needed:**
- `tradingagents/validation/cpcv.py` — CPCV implementation: `combinatorial_purged_cv(df, n_splits, n_test_splits, embargo_days, model_fn) -> dict` returning distribution of OOS metrics across C(N,k) paths.
- `tradingagents/validation/deflated_sharpe.py` — `deflated_sharpe_ratio(sharpe, n_trials, T, skewness, kurtosis) -> float`. Based on Bailey & Lopez de Prado (2014) formula.
- `tradingagents/validation/noise_feature_test.py` — inject N random noise columns; train model; check if any real feature ranks below noise on permutation importance; flag for removal.
- `scripts/paper_backtest_drift.py` — load `paper_trades.jsonl` + `backtest_results.json` for same signal dates; compute per-trade fill_price - signal_price delta; report mean/std slippage by setup_type, day_of_week, hold_period.

**Dependencies needed:**
```
scipy>=1.11.0         # already implied; needed for DSR stats
```
All others already installed.

**CLI flags needed:**
- `backtest.py --cpcv --cpcv-n-splits 8 --cpcv-k-test 2`
- `train_ml_models.py --compute-dsr --n-trials <count>`
- `train_ml_models.py --noise-feature-test --noise-features 5`
- `scripts/paper_backtest_drift.py --paper-log tmp/paper_trading_today/ --backtest-json backtest_results.json --output docs/drift_report.json`

**Tests required:**
- `tests/test_cpcv.py` — verify C(N,k) path count; verify purging applied; verify no test overlap.
- `tests/test_deflated_sharpe.py` — verify formula matches Bailey 2014 reference values; verify penalization increases with n_trials.
- `tests/test_noise_feature.py` — inject 10 noise features into synthetic dataset; verify at least some real features rank above noise.
- `tests/test_paper_backtest_drift.py` — synthetic paper log + backtest results; verify delta computed correctly; verify CSV output.

**Success metrics:**
- CPCV Sharpe distribution for current model is logged in `training_report.json`.
- DSR for current model is computed and logged. If DSR < 0, flag as potentially overfit (human review required).
- Zero real features rank below random noise (if any do, they are flagged for human review before removal).
- Paper-vs-backtest drift report exists with mean slippage estimate per setup_type.

**Risks:**
- CPCV is compute-intensive. C(8,2) = 28 paths × walk-forward splits. May run for 30-60 min on full universe. Needs `--cpcv-fast` mode with reduced universe for CI.
- DSR formula requires number of trials. Trial count tracking is manual unless we instrument `retrain_weekly.py` to log it. `HUMAN REVIEW NEEDED:` How to count trials accurately across weekly retrains vs ad-hoc runs.
- Paper-vs-backtest drift comparison requires paper trades to include both signal_price and fill_price fields. Audit (TP-4) found fills use re-fetched prices, not stop levels. Fix TP-4 first for accurate drift data.

**Rollback:** All validation features are additive flags. Existing behavior unchanged without `--cpcv` or `--compute-dsr`.

**What NOT to do in this phase:**
- Do not tune any hyperparameter based on DSR output. DSR is diagnostic only.
- Do not prune features based on noise test results without human review.
- Do not use paper-vs-backtest drift to retrain the model — it would become training data.

---

### Phase 3 — Triple-Barrier Labeling

**Goal:** Replace fixed-horizon return labels with triple-barrier labels (hit take-profit barrier first = 1, hit stop-loss barrier first = 0, time expired = tie/abstain) in `train_ml_from_stock_data.py`.

**Why:** The current `measure_outcome` already uses actual High/Low data for intraday stop/target detection. But the ML label uses the horizon return (H1/H3/H5), not whether the stop or target was hit first. This misalignment means the model learns to predict H3 return, not whether to execute the trade. Triple-barrier labels align ML objective with live trade behavior.

The local codebase already has `measure_outcome` which returns `first_hit` (target, stop, or timeout) alongside horizon returns. The triple-barrier label is essentially `1 if first_hit == "target" else 0` — a small change with big implications for what the model learns.

**Local files likely affected:**
- `backtest.py:measure_outcome` — already returns `first_hit`; verify it correctly records which barrier was touched first at intraday resolution.
- `scripts/train_ml_from_stock_data.py` — add `--label-mode triple_barrier` flag; use `first_hit` as label instead of `h<hold>_return > 0`.
- `scripts/train_ml_models.py` — add `label_col` parameter; default to existing behavior; accept `triple_barrier_label` when passed.
- `scripts/retrain_weekly.py` — pass `--label-mode triple_barrier` when feature-gated.

**New files/modules needed:**
- `tradingagents/labeling/triple_barrier.py` — standalone `compute_triple_barrier_labels(df, barrier_col_high, barrier_col_low, target_col, stop_col, max_hold) -> pd.Series`. Separated from backtest.py so it can be unit-tested independently and reused by future strategies.

**Dependencies needed:** None. All pandas/numpy already installed.

**CLI flags needed:**
- `train_ml_from_stock_data.py --label-mode {fixed_horizon|triple_barrier}` (default: `fixed_horizon` to preserve backward compat)
- `train_ml_from_stock_data.py --barrier-atr-multiplier 1.0` (must match live `screener._ATR_STOP`)

**Tests required:**
- `tests/test_triple_barrier_labeling.py` — synthetic price series with known barrier touches; verify correct label assignment; verify time-expiry case; verify tie-breaking logic.
- Integration: run `train_ml_from_stock_data.py --label-mode triple_barrier` on small synthetic dataset; verify label distribution reasonable.

**Success metrics:**
- Triple-barrier label WR (share of label=1 rows) is within 5% of current fixed-horizon WR on same training data.
- Walk-forward ROC with triple-barrier labels >= walk-forward ROC with fixed-horizon labels (or within noise).
- `first_hit` distribution in training data: target%, stop%, timeout% logged in `training_report.json`.

**Risks:**
- Label imbalance: if timeout rate is 43.9% (observed), the `first_hit == timeout` rows have ambiguous labels. Options: (a) label timeout as 0, (b) label timeout as abstain (drop from training), (c) use probability of profiting on timeout separately. `HUMAN REVIEW NEEDED:` How to label timeout trades given that 43.9% of them are winners. Dropping them may improve model precision but lose important training signal.
- ATR multiplier mismatch between backtest labels and live stops (audit TP-1: live uses 0.7 ATR, labels use 1.0 ATR). Fix TP-1 before enabling triple-barrier labeling to avoid training on wrong barriers.

**Rollback:** `--label-mode fixed_horizon` reverts to current behavior. Old model bundles remain valid.

**What NOT to do in this phase:**
- Do not enable triple-barrier labels in `retrain_weekly.py` by default until Phase 4 (GBDT ensemble) is ready. They need to be validated together.
- Do not change `measure_outcome` in `backtest.py` — it is used by the backtest engine, not just training.
- Do not use triple-barrier labels in live scoring yet.

---

### Phase 4 — GBDT Ensemble (CatBoost + LightGBM + XGBoost)

**Goal:** Expand `train_ml_models.py` to train CatBoost and LightGBM alongside XGBoost, ensemble the three classifiers' predicted probabilities, and replace the single-model gate with the ensemble gate.

**Why:** Financial tabular research (reference Section 6) shows XGBoost/LightGBM/CatBoost each have different strengths. XGBoost is most stable cross-sectionally. LightGBM is fastest and best at large regression tasks. CatBoost handles categorical features natively and resists prediction shift. Soft-voting ensemble (average probabilities) reduces individual-model overfitting risk and smooths confidence estimates for Brier calibration.

MLflow local tracking is added here so experiment comparisons across the ensemble configurations are logged automatically.

**Local files likely affected:**
- `scripts/train_ml_models.py` — add `_make_clf_lgbm`, `_make_clf_catboost`; add `--models xgb lgbm catboost rf`; add ensemble averaging; add MLflow logging.
- `scripts/retrain_weekly.py` — pass `--models xgb lgbm catboost` flag.

**New files/modules needed:**
- `tradingagents/ml/ensemble.py` — `SoftVotingEnsemble` class wrapping multiple calibrated classifiers; `predict_proba(X) -> np.ndarray` averages member probabilities.
- `tradingagents/ml/experiment_tracker.py` — thin wrapper around MLflow `mlflow.log_metric`, `mlflow.log_param`, `mlflow.log_artifact`. Falls back to flat JSON if MLflow not installed (for CI/minimal environments).

**Dependencies needed:**
```
lightgbm>=4.0.0       # add to pyproject.toml [quant] optional group
catboost>=1.2.0       # add to pyproject.toml [quant] optional group
mlflow>=2.12.0        # add to pyproject.toml [quant] optional group
```
All are optional. Code must gracefully fall back to XGBoost-only if not installed.

**CLI flags needed:**
- `train_ml_models.py --models xgb lgbm catboost rf` (default: `xgb rf` for backward compat)
- `train_ml_models.py --ensemble-method {soft_vote|rank_avg}` (default: `soft_vote`)
- `train_ml_models.py --mlflow-tracking-uri mlruns/` (default: local file-based)
- `train_ml_models.py --experiment-name <name>`

**Tests required:**
- `tests/test_gbdt_ensemble.py` — train mock classifier; verify ensemble produces probability in [0,1]; verify calibration passes; verify bundle saves with `models` dict containing all three.
- `tests/test_experiment_tracker.py` — verify JSON fallback works without MLflow installed.

**Success metrics:**
- Ensemble walk-forward ROC >= single XGBoost ROC on same data (not a hard gate — log the comparison).
- All three models log metrics to MLflow or fallback JSON.
- `model_bundle.joblib` retains backward compat: `bundle["models"]["win_probability"]` returns ensemble's `predict_proba`.
- `training_report.json` includes per-model ROC before and after ensembling.

**Risks:**
- CatBoost CPU training is slower. Full universe retrain may take 2-3x longer. Add `--catboost-iterations` flag to limit. `HUMAN REVIEW NEEDED:` Acceptable retrain time budget.
- LightGBM sensitive to feature scaling/high-variance noise. Run noise feature test (Phase 2) before enabling LightGBM on full feature set.
- MLflow SQLite backend creates `mlruns/` directory in project root. Add to `.gitignore`.
- Model bundle size increases. Check `ml_models/` storage headroom.

**Rollback:** `--models xgb` reverts to single-model XGBoost behavior. Old bundles remain loadable via backward-compat `bundle["models"]["win_probability"]`.

**What NOT to do in this phase:**
- Do not enable TabPFN or TabNet in production — benchmark only.
- Do not add deep learning models.
- Do not implement online/continual learning (future phase).
- Do not add SHAP values in production inference loop — training only.

---

### Phase 5 — Meta-Labeling + Probability-Based Sizing

**Goal:** Train a secondary meta-label model on the primary model's OOS predictions. Use the secondary model's probability output as a direct position-size multiplier, replacing the current ad-hoc tier/Kelly blend.

**Why:** The current system has `ml_probability` (primary gate), `alpha_tier` (rule-based), `alpha_score`, and Kelly fraction interacting through dead logic paths (audit DL-1: ATR path discards ML confidence, streak, time-of-day). Meta-labeling decouples "should I enter?" (primary) from "how much should I risk?" (secondary). The secondary model learns from actual primary model outcomes — did the trade profit? — rather than from price features directly.

**Local files likely affected:**
- `scripts/train_ml_models.py` — add meta-label training step after primary model.
- `tradingagents/portfolio/position_sizing.py` — replace `base_pct` heuristic blend with `meta_prob * base_fraction`.
- `scripts/paper_trade_today.py` — pass `meta_label_probability` to position sizing.
- `scripts/paper_trade_unified.py` — same.

**New files/modules needed:**
- `tradingagents/ml/meta_labeler.py` — `MetaLabeler` class: trains secondary classifier on rows where primary model was used in OOS walk-forward; target = `actual_outcome` (1 if trade won, 0 if lost); features = primary model's predicted probability + any additional features.
- `tradingagents/portfolio/prob_sizer.py` — `compute_prob_based_size(meta_prob, base_fraction, min_fraction, max_fraction) -> float`. Kelly-like but driven by meta-probability.

**Dependencies needed:** None beyond Phase 4 stack.

**CLI flags needed:**
- `train_ml_models.py --train-meta-label` (off by default)
- `paper_trade_today.py --meta-label-bundle ml_models/latest/meta_bundle.joblib`
- `paper_trade_today.py --prob-sizer-min 0.5 --prob-sizer-max 1.5`

**Tests required:**
- `tests/test_meta_labeler.py` — synthetic primary model OOS output; verify meta-label training produces valid probabilities; verify bundle saved/loaded correctly.
- `tests/test_prob_sizer.py` — verify size output bounded by min/max; verify meta_prob=0.5 returns base fraction; verify edge cases.

**Success metrics:**
- Meta-label model walk-forward ROC > 0.50 on holdout (if < 0.50, meta-labeling adds no signal — disable).
- Paper trading Sortino improves vs current over 30+ trading days.
- Position size variance decreases vs current (better capital efficiency).

**Risks:**
- Meta-label training requires a dataset of primary model OOS outcomes — these come from `ml_models/retrain_history.jsonl` and paper trade logs. Current paper trade logs have grader bugs (audit GC-1 through GC-8). Fix grader bugs before training meta-label model on them. `HUMAN REVIEW NEEDED:` How many clean OOS trades are available after grader fixes.
- If meta-label model overrides Kelly + ATR sizing, the `position_sizing.py` audit fixes (DL-1) may need to be coordinated with this phase.

**Rollback:** Remove `--meta-label-bundle` flag. Falls back to current sizing.

**What NOT to do in this phase:**
- Do not train meta-label model on paper trades until grader bugs (audit GC-1 through GC-8) are fixed.
- Do not use meta-probability to set stops or targets — sizing only.
- Do not add RL execution.

---

### Phase 6 — HRP / Target-Weight Portfolio Optimizer

**Goal:** Add Hierarchical Risk Parity (HRP) allocation to compute target portfolio weights across all open and candidate positions. Fix the dead `alloc_weights` path (audit DL-2). Enforce sector/theme exposure caps and turnover constraints at the optimizer level.

**Why:** Current correlation cap (max 2 correlated positions) is wired only in `trading_graph.py` and is dead on the paper trade path (DL-3). The `candidate_ranker.py` computes allocation weights that are never applied to capital (DL-2). HRP resolves covariance matrix pathologies inherent in Markowitz MVO by using hierarchical clustering + recursive bisection without inverting the covariance matrix. This produces more stable allocations OOS than equal-weight or Kelly-only.

**Local files likely affected:**
- `tradingagents/portfolio/candidate_ranker.py` — wire `alloc_weights` output to HRP optimizer input.
- `tradingagents/portfolio/position_sizing.py` — accept `target_weight` from HRP; blend with ATR risk-dollar floor.
- `scripts/paper_trade_today.py` — call HRP optimizer after candidate scoring; pass target_weights to sizing.
- `scripts/paper_trade_unified.py` — same.
- `tradingagents/portfolio/correlation.py` — feed correlation matrix into HRP (already computes correlation).

**New files/modules needed:**
- `tradingagents/portfolio/hrp_optimizer.py` — `HRPOptimizer` class with `fit(returns_matrix) -> np.ndarray` (weights). Implements: (1) correlation-distance matrix, (2) hierarchical clustering via scipy linkage, (3) quasi-diagonalization, (4) recursive bisection allocation. Based on PyPortfolioOpt's HRP or clean reimplementation.
- `tradingagents/portfolio/weight_controller.py` — enforces: max sector weight (default 30%), max single-name weight (default 20%), max turnover vs prior weights (default 50% daily), cash floor.

**Dependencies needed:**
```
scipy>=1.11.0         # already required; linkage/distance
# PyPortfolioOpt optional — borrow HRP code instead if preferred
pypfopt>=1.5.5        # add to [quant] optional group
```

**CLI flags needed:**
- `paper_trade_today.py --use-hrp` (off by default)
- `paper_trade_today.py --hrp-lookback-days 60`
- `paper_trade_today.py --max-sector-weight 0.30`
- `paper_trade_today.py --max-turnover 0.50`
- `paper_trade_today.py --max-single-name 0.20`

**Tests required:**
- `tests/test_hrp_optimizer.py` — synthetic 10-asset returns matrix; verify weights sum to 1.0; verify no single name > max; verify cluster count reasonable.
- `tests/test_weight_controller.py` — verify sector cap enforced; verify turnover constraint applied; verify cash floor maintained.

**Success metrics:**
- HRP-weighted paper account shows reduced max drawdown vs equal-weight baseline over 30+ trading days.
- Sector concentration does not exceed configured cap.
- Daily turnover does not exceed configured cap.
- `alloc_weights` in `candidate_ranker.py` are actually used (audit DL-2 resolved).

**Risks:**
- HRP requires a historical returns matrix for all candidates simultaneously. With 1200 tickers in `tickers_liquid.txt` but only 5 open positions, the optimizer runs on the *candidate shortlist*, not the full universe. `HUMAN REVIEW NEEDED:` Define candidate shortlist size for HRP (10? 20? all ML-passing candidates?).
- `tradingagents/portfolio/correlation.py` currently calls `yf.download(1y)` per check (DL-3). Expensive. Cache this matrix per scan using Phase 1 data provider.
- HRP is not beta-hedged. Portfolio is still long-only long equity. Accepted for Phase 6; beta hedging is a delayed idea.

**Rollback:** Remove `--use-hrp` flag. Falls back to current ATR/Kelly sizing.

**What NOT to do in this phase:**
- Do not implement Markowitz MVO — it is numerically unstable on small samples.
- Do not implement Black-Litterman (delayed idea).
- Do not implement Riskfolio-Lib CVaR optimization (delayed idea — requires CVXPY).
- Do not implement beta hedging or short positions.

---

### Phase 7 — HMM Regime Feature / Risk Layer

**Goal:** Add a Gaussian Hidden Markov Model trained on daily returns, realized volatility, and VIX to produce probabilistic regime state probabilities as additional features for the ML gate and as a risk overlay multiplier.

**Why:** The current VIX bucket system (low_vol/normal/elevated/crisis) uses hard thresholds from `backtest.py:1208-1238`. These are empirically tuned but brittle at boundaries. An HMM learns latent state transitions from data and produces a soft probability over K regimes. The transition matrix captures regime momentum (how likely a regime is to persist vs flip). This is a later phase because it requires clean data infrastructure (Phase 1) and validated labels (Phases 2-3) first.

**Local files likely affected:**
- `tradingagents/screening/market_regime.py` — add HMM as alternative regime backend.
- `scripts/train_ml_from_stock_data.py` — add HMM regime probability columns to feature rows.
- `backtest.py:build_combined_regime` — add optional HMM-based regime column.

**New files/modules needed:**
- `tradingagents/ml/hmm_regime.py` — `GaussianHMMRegime` class: `fit(returns, vol, vix) -> self`; `predict_proba(returns, vol, vix) -> np.ndarray (n_samples, n_states)`; `predict_state(returns, vol, vix) -> np.ndarray (n_samples,)`. Uses `hmmlearn.GaussianHMM`. Number of states K configurable (default 3: bull/consolidation/bear).
- `scripts/train_hmm_regime.py` — standalone script to train and save HMM bundle from SPY + VIX history; outputs `ml_models/hmm_regime/hmm_bundle.joblib`.

**Dependencies needed:**
```
hmmlearn>=0.3.0       # add to [quant] optional group
```

**CLI flags needed:**
- `train_ml_from_stock_data.py --hmm-bundle ml_models/hmm_regime/hmm_bundle.joblib`
- `paper_trade_today.py --hmm-bundle ml_models/hmm_regime/hmm_bundle.joblib`
- `train_hmm_regime.py --n-states 3 --lookback-years 5`

**Tests required:**
- `tests/test_hmm_regime.py` — synthetic regime-switching time series; verify K states identified; verify probability outputs sum to 1.0 per row; verify `fit` + `predict_proba` consistent.

**Success metrics:**
- HMM regime probabilities added to feature matrix improve walk-forward ROC vs baseline (or do not hurt it — adding useless features is a regression).
- HMM state transitions align intuitively with known historical regime shifts (2020 March, 2022 bear, 2023 recovery) — visual inspection required.

**Risks:**
- HMM state labeling is unsupervised — the model assigns numbers, not human labels. State 0 might be "bull" in one training run and "bear" in another depending on initialization. Use fixed random seed and visual validation. `HUMAN REVIEW NEEDED:` Validate state assignments after first training run.
- HMM is prone to overfitting on short histories. Minimum 3 years of daily data (750 bars) required before using HMM features in production.
- Computational: HMM EM training is fast (seconds on daily data), but forward-pass for each bar during backtest adds ~5ms/bar. For 252 bars × 1200 tickers = acceptable overhead.

**Rollback:** Remove `--hmm-bundle` flag. Current VIX-bucket regime system remains unchanged.

**What NOT to do in this phase:**
- Do not replace the existing VIX-bucket regime logic — HMM is additive as new features only.
- Do not use TDA (topological data analysis) or persistent homology.
- Do not implement LOB/order-book HMM.

---

### Phase 8 — Advanced Research Sandbox

**Goal:** Provide isolated experimental space for ideas not ready for production but valuable for research.

**Why:** Some ideas (TabPFN, fractional differentiation, Riskfolio-Lib CVaR, Qlib factor research, vectorbt parameter sweeps) require infrastructure or compute not appropriate for production retrains. A clearly separated sandbox prevents experimental code from contaminating the production pipeline.

**New files/modules needed:**
- `sandbox/README.md` — rules: no production imports from here; no touching ml_models/latest; no modifying backtest.py; notebooks only.
- `sandbox/tabpfn_benchmark.py` — compare TabPFN vs XGBoost/LightGBM on same purged walk-forward splits.
- `sandbox/fracdiff_features.py` — fractional differentiation via ADF optimization; benchmark whether fracdiff features improve ML ROC vs raw returns.
- `sandbox/vectorbt_sweep.py` — vectorbt parameter sweep on existing signal logic; compare win rates across parameter grid.
- `sandbox/riskfolio_cvxpy.py` — Riskfolio-Lib CVaR portfolio optimization; compare portfolio drawdown vs HRP from Phase 6.

**Dependencies needed (sandbox only, not production):**
```
tabpfn>=2.6.0
fracdiff>=0.4.2
vectorbt>=0.26.0
riskfolio-lib>=6.0.0
```
All in sandbox optional group only. Never added to production `[quant]`.

**Tests required:** None mandatory — sandbox is for research. Add `# type: ignore` markers to avoid mypy failures.

**Success metrics:**
- Sandbox produces documented comparison results. Results inform future production phases.
- Sandbox code never imported by production scripts.

**Risks:** None to production — sandbox is isolated.

**What NOT to do in this phase:**
- Do not promote any sandbox result to production without a proper phase (1-7 pattern: goal, tests, success metrics, rollback).
- Do not add TabPFN or vectorbt to production dependencies.
- Do not run Riskfolio-Lib CVaR in the live paper trade loop.

---

## 5. Accepted Near-Term Ideas (Phases 1-7 Above)

| Idea | Phase | Notes |
|------|-------|-------|
| Local yfinance cache | 1 | DuckDB/SQLite OHLCV store |
| MarketDataProvider interface | 1 | Abstract base class |
| DuckDB OHLCV cache | 1 | Preferred over SQLite for analytics |
| Purged CV | 2 | Already exists; needs CPCV extension |
| Embargo | 2 | Already exists in `_ml_time_split`; document clearly |
| CPCV | 2 | C(N,k) path distribution |
| Deflated Sharpe Ratio | 2 | Bailey 2014 formula |
| Random noise feature test | 2 | Permutation importance vs noise |
| Realistic slippage in labels | 2/3 | Paper-vs-backtest drift; bake slippage into label prices |
| Paper-vs-backtest drift report | 2 | `scripts/paper_backtest_drift.py` |
| Triple-barrier labels | 3 | `--label-mode triple_barrier` in training |
| CatBoost | 4 | `[quant]` optional group |
| LightGBM | 4 | `[quant]` optional group |
| XGBoost ensemble | 4 | Soft-vote with LightGBM + CatBoost |
| MLflow local experiment tracking | 4 | SQLite backend; fallback JSON |
| Meta-labeling | 5 | Secondary model on primary OOS outcomes |
| Probability-based sizing | 5 | Replace tier/Kelly heuristic blend |
| HRP | 6 | `hrp_optimizer.py`; scipy linkage |
| Turnover constraints | 6 | `weight_controller.py` |
| Sector/theme exposure caps | 6 | `weight_controller.py` |
| HMM regime features | 7 | `hmmlearn` optional group |

---

## 6. Delayed Ideas (Not Yet)

| Idea | Reason |
|------|--------|
| **Riskfolio-Lib advanced CVaR optimization** | Requires CVXPY + commercial solver for large cones; complexity > benefit at current universe size (5-10 positions). Revisit if position count > 20. |
| **SEC/Edgar NLP pipeline** | High infrastructure complexity (8-K parsing, point-in-time EDGAR access); LLM API cost; requires validated point-in-time data architecture. Revisit after Phase 2 validation layer. |
| **Options chain features** | IV rank, skew, put/call require paid API (CBOE, Polygon). Free options data is delayed and unreliable. Revisit if paid data budget available. |
| **Gap-and-go strategy** | Pre-market gap screener needs pre-market data feed (yfinance doesn't provide reliable pre-market). Separate strategy with separate backtest. Revisit after Phase 1 data layer. |
| **Intraday mean reversion** | Requires 15m/1h OHLCV history not currently cached; separate strategy bucket with different stops/targets/labels; significant infrastructure addition. Revisit as separate strategy after Phase 3. |
| **Beta hedging (dynamic SPY short)** | Adds short positions to a long-only paper account. Operational risk: requires borrowable shares, margin, negative delta tracking. Higher complexity than benefit at current scale. Revisit if portfolio beta > 1.5 consistently. |
| **Black-Litterman** | Requires subjective return views (where do these come from?). Without a validated view generation process, BL degenerates to market cap weights. Riskier than HRP for this use case. |
| **Qlib factor research platform** | Full Qlib migration means rewriting the data pipeline, model training, and backtest engine. Extremely high integration difficulty. We borrow concepts (binary storage, purged CV) but do not migrate. |
| **vectorbt sandbox** | Reserved for Phase 8 sandbox only. Not a production dependency. |
| **Advanced covariance shrinkage (UPSA/CUPSA)** | Appropriate when N assets > T bars. Current portfolio is 5-10 positions. Overkill. Revisit if universe expands to cross-sectional 50+ position allocation. |

---

## 7. Rejected / Skipped-for-Now Ideas

| Idea | Reason |
|------|--------|
| **Direct price-predicting LSTM production model** | Academic literature and reference report both confirm directional LSTM on raw prices = negative alpha. Memory + stationarity issues make it worse than XGBoost on tabular financial data. Sandbox only. |
| **RL for return prediction** | RL maximizes reward in environments. Financial environments are non-stationary and adversarial. RL for return prediction (not execution) is retail hype per reference Section 3. The existing `tradingagents/rl/` TD3 agent is research-only and should stay that way. |
| **Deep RL execution** | HFT-grade DRL execution requires sub-millisecond infrastructure, L2 order book data, and a real broker adapter. None are available locally. Rejected entirely. |
| **TensorRT / ONNX optimization** | Sub-millisecond inference needed only for HFT. Daily bar + 15-minute paper trading runs at seconds-level latency. TensorRT adds zero benefit. |
| **Kafka / Redpanda streaming** | Event-driven streaming architecture requires persistent process infrastructure, broker integrations, and 24/7 ops. The system runs as scheduled cron jobs. Streaming would replace the entire runtime model. Too large a scope change. |
| **Full Qlib migration** | See "Delayed" above. Borrowing concepts is accepted; full migration is rejected. |
| **Full FinRL migration** | Same rationale. RL environments for production return prediction are retail hype. |
| **LOB / order-book microstructure** | Requires tick or L2 data at millisecond resolution. yfinance and free APIs do not provide this. C++ order book reconstruction (lob-regime-scanner) is out of scope. |
| **Dark pool tracking** | Off-exchange volume spikes as signals require paid alternative data feeds. No free, reliable source exists. |
| **Synthetic GAN / diffusion stress testing** | Training a GAN on market data requires significant compute and produces synthetic paths of unclear fidelity. Standard Monte Carlo (already in backtest.py `--monte-carlo`) covers stress testing adequately. |
| **Automated LLM feature generation loop** | LLM writes feature functions, tests them, loops. High hallucination risk for financial code. No validation against point-in-time data. Could silently introduce lookahead. Rejected until a rigorous validation harness exists. |
| **Microsecond latency infrastructure** | No use case at daily bar + 15-minute polling frequency. |

---

## 8. Success Metrics by Layer

### Data Layer
- Phase 1 complete when: zero network calls during historical re-runs; `ohlcv_cache.duckdb` contains all tickers × all training dates; `download_all` test passes with mocked yfinance.

### Validation Layer
- Phase 2 complete when: CPCV Sharpe distribution logged in training report; DSR computed and logged; noise feature test passes (no real features below noise, or flagged ones reviewed); paper-vs-backtest drift report exists.

### Labeling
- Phase 3 complete when: `first_hit` distribution logged per training run; triple-barrier walk-forward ROC ≥ fixed-horizon ROC (or within 0.01); timeout label strategy documented and human-reviewed.

### ML
- Phase 4 complete when: all three GBDT models train without error; ensemble bundle backward-compat with existing `bundle["models"]["win_probability"]`; MLflow run logged per retrain cycle; no feature ranks below noise injection test.

### Meta-Label / Sizing
- Phase 5 complete when: meta-label model walk-forward ROC > 0.50; paper Sortino over 30 days ≥ baseline; `position_sizing.py` DL-1 bug fixed and confirmed; grader GC-1 through GC-8 bugs fixed first.

### Portfolio / HRP
- Phase 6 complete when: HRP weights sum to 1.0; sector cap enforced; turnover constraint applied; `alloc_weights` in `candidate_ranker.py` actually used (DL-2 resolved); correlation cap wired on live paper path (DL-3 resolved).

### Paper/Backtest Drift
- Phase 2 + Phase 5 complete when: paper fill prices consistent with signal prices; drift report shows mean slippage < 10 bps; grader outputs valid `actual_large_loss`, `stop_hit`, `target_hit` fields.

### Risk
- Ongoing: max drawdown < 12% on 30-day rolling paper window; HMM regime probabilities (Phase 7) do not increase drawdown vs current VIX-bucket regime.

---

## 9. Copy-Paste Codex Implementation Prompts

### Prompt 1 — Baseline Snapshot Script

```
You are working on the agentic-trader repository at local path:
  /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/

Create a new script: scripts/snapshot_baseline.py

Purpose:
  Read-only. Runs backtest.py on a fixed date range and validate_holdout.py,
  then dumps combined metrics to docs/baseline_snapshot_<YYYYMMDD>.json.
  NEVER trains or modifies any model bundle.

Requirements:
  1. Accept --snapshot-date (default: today), --output (default: docs/baseline_snapshot.json),
     --tickers (default: all_tickers.txt), --bundle (default: ml_models/latest/model_bundle.joblib).
  2. Run backtest.py with --walk-forward on the last 60 trading days (ending snapshot-date).
     Capture wf_roc, brier, trade_count, win_rate, profit_factor, Sortino, max_drawdown from output JSON.
  3. Run validate_holdout.py on the same range.
     Capture holdout_roc, holdout_brier, holdout_trade_count from output JSON.
  4. Write a single JSON file: {
       "snapshot_date": ..., "bundle_path": ..., "wf_roc": ..., "brier": ...,
       "trade_count": ..., "win_rate": ..., "profit_factor": ...,
       "sortino": ..., "max_drawdown": ..., "holdout_roc": ...,
       "holdout_brier": ..., "holdout_trade_count": ...
     }
  5. If either subprocess fails, write partial results with error key.
  6. Add test: tests/test_snapshot_baseline.py.
     Test 1: mock subprocess; verify JSON structure complete.
     Test 2: verify --output path is written; verify no bundle swap.
     Test 3: verify holdout date does not precede training end date in bundle.

Safety rules:
  - DO NOT modify backtest.py, validate_holdout.py, or any model bundle.
  - DO NOT train or swap any model.
  - DO NOT touch .env, credentials, or API keys.
  - DO NOT add live broker execution.
  - Keep existing scripts backward-compatible.
```

---

### Prompt 2 — Market Data Cache / Provider Layer

```
You are working on the agentic-trader repository at local path:
  /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/

Create the following new files (do not modify existing files yet):

1. tradingagents/dataflows/market_data_provider.py
   - Abstract base class MarketDataProvider.
   - Methods: get_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame
              get_ohlcv_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]
   - DataFrame schema: columns = [Open, High, Low, Close, Volume]. DatetimeIndex. adj_close optional.

2. tradingagents/dataflows/yfinance_provider.py
   - Concrete class YFinanceProvider(MarketDataProvider).
   - Wraps existing yf.download logic from backtest.py:download_all (batch size 50, retry 3).
   - Does NOT modify backtest.py.

3. tradingagents/dataflows/ohlcv_cache.py
   - OHLCVCache class backed by DuckDB (import duckdb; if ImportError fall back to sqlite3).
   - Table schema: CREATE TABLE IF NOT EXISTS ohlcv (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(ticker, date))
   - Methods: read(ticker, start, end) -> pd.DataFrame | None
              write(ticker, df: pd.DataFrame) -> None
              has_coverage(ticker, start, end) -> bool

4. tradingagents/dataflows/cached_provider.py
   - CachedProvider(MarketDataProvider) wraps any upstream provider + OHLCVCache.
   - On get_ohlcv: check has_coverage; if True read from cache; else fetch upstream + write cache.

5. tests/test_market_data_provider.py
   - Test 1: mock YFinanceProvider; verify CachedProvider hits cache on second call.
   - Test 2: verify OHLCVCache round-trip (write + read produces same DataFrame).
   - Test 3: verify DuckDB fallback to SQLite when duckdb not installed.

Safety rules:
  - DO NOT modify backtest.py.
  - DO NOT modify paper_trade_today.py.
  - DO NOT modify any existing dataflows/ files.
  - DO NOT touch .env, credentials, or API keys.
  - DO NOT add live broker execution.
  - New code is additive only. Existing callers are unaffected.
```

---

### Prompt 3 — Validation Layer (CPCV + Deflated Sharpe + Noise Test)

```
You are working on the agentic-trader repository at local path:
  /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/

Create the following new files. Do not modify train_ml_models.py or retrain_weekly.py yet.

1. tradingagents/validation/cpcv.py
   - Function: combinatorial_purged_cv(df: pd.DataFrame, n_splits: int, n_test_splits: int,
                                        embargo_days: int, train_fn: Callable, test_fn: Callable) -> dict
   - Splits df into n_splits sequential groups.
   - Selects all C(n_splits, n_test_splits) combinations of test folds.
   - For each combination: train on non-test folds with purging and embargo; evaluate test folds.
   - Returns: {paths: list of per-path metrics, mean_sharpe: float, std_sharpe: float, n_paths: int}
   - Uses existing _ml_purged_walk_forward logic from backtest.py as reference — borrow concepts, do NOT import from backtest.py directly.

2. tradingagents/validation/deflated_sharpe.py
   - Function: deflated_sharpe_ratio(sharpe: float, n_trials: int, T: int,
                                      skewness: float = 0.0, kurtosis: float = 3.0) -> float
   - Implements Bailey & Lopez de Prado (2014) DSR formula.
   - Returns DSR value. If < 0.0, model is likely overfit.
   - Include docstring with formula reference and interpretation guide.

3. tradingagents/validation/noise_feature_test.py
   - Function: noise_feature_test(X: pd.DataFrame, y: pd.Series, model_fn: Callable,
                                   n_noise: int = 10, seed: int = 42) -> dict
   - Injects n_noise random Gaussian columns into X.
   - Trains model_fn(X_with_noise, y); computes permutation feature importance.
   - Returns: {noise_threshold: float, features_below_noise: list[str], features_above_noise: list[str]}

4. tests/test_validation_layer.py
   - Test CPCV: 100-row synthetic DataFrame; n_splits=5, n_test_splits=2; verify 10 paths generated; verify no time-travel.
   - Test DSR: known Sharpe=1.0, n_trials=100, T=252; verify DSR < Sharpe.
   - Test noise_feature_test: 500-row synthetic; 5 real signal features; 3 pure noise features; verify at least 1 real feature ranks above noise threshold.

Safety rules:
  - DO NOT modify backtest.py, train_ml_models.py, retrain_weekly.py.
  - DO NOT use holdout data for any training step.
  - DO NOT touch .env or credentials.
  - All new code is in tradingagents/validation/ and tests/ only.
```

---

### Prompt 4 — Triple-Barrier Labeling

```
You are working on the agentic-trader repository at local path:
  /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/

Context:
  - backtest.py:measure_outcome already computes first_hit (target/stop/timeout) using actual High/Low data.
  - train_ml_from_stock_data.py uses h<hold>_return > 0 as the ML label (fixed-horizon).
  - The new triple_barrier label should be: 1 if first_hit == "target", 0 if first_hit == "stop" or "timeout".
  - IMPORTANT: timeout rows may be a special case (43.9% of timeouts are winners in live data).
    Add a --timeout-label {zero|drop|pass_through} flag to handle them.

Create:
1. tradingagents/labeling/triple_barrier.py
   - Function: compute_triple_barrier_labels(df: pd.DataFrame, target_col: str,
                                              stop_col: str, hold_col: str,
                                              timeout_handling: str = "zero") -> pd.Series
   - Accepts a DataFrame with columns: first_hit (target/stop/timeout), plus optional hold returns.
   - Returns label Series (1 = target hit first, 0 = stop/timeout).
   - timeout_handling: "zero" = label 0, "drop" = return NaN (caller drops rows), "pass_through" = label based on h<hold>_return > 0 fallback.

2. Modify train_ml_from_stock_data.py (backward-compatible):
   - Add --label-mode {fixed_horizon|triple_barrier} (default: fixed_horizon).
   - Add --timeout-label {zero|drop|pass_through} (default: zero).
   - When --label-mode triple_barrier: call compute_triple_barrier_labels on the training dataframe using first_hit column from measure_outcome output.
   - Log label distribution (target_pct, stop_pct, timeout_pct) to training report.

3. tests/test_triple_barrier_labeling.py
   - Test 1: 100-row synthetic df; 40 target hits, 30 stop hits, 30 timeouts; verify label counts with timeout=zero.
   - Test 2: timeout=drop; verify returned labels have no NaN; verify row count reduced by timeout count.
   - Test 3: verify fixed_horizon mode is unchanged (backward compat test).

Safety rules:
  - DO NOT modify backtest.py:measure_outcome.
  - DO NOT change default --label-mode (must remain fixed_horizon).
  - DO NOT enable triple_barrier in retrain_weekly.py yet.
  - DO NOT touch .env, credentials, or API keys.
  - DO NOT add live broker execution.
```

---

### Prompt 5 — GBDT Ensemble (LightGBM + CatBoost + XGBoost)

```
You are working on the agentic-trader repository at local path:
  /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/

Context:
  - scripts/train_ml_models.py currently uses XGBoost if available, else RandomForest.
  - The model bundle at ml_models/latest/model_bundle.joblib has key: bundle["models"]["win_probability"].
  - Backward compatibility: any code that calls bundle["models"]["win_probability"].predict_proba(X) must continue to work.

Create:
1. tradingagents/ml/ensemble.py
   - Class SoftVotingEnsemble:
     - __init__(self, estimators: list[tuple[str, any]]): list of (name, fitted_classifier) pairs.
     - predict_proba(self, X: np.ndarray) -> np.ndarray: average predict_proba across all estimators.
     - predict(self, X: np.ndarray) -> np.ndarray: argmax of averaged probabilities.
     - Implements sklearn-compatible interface (classes_, predict, predict_proba).

2. tradingagents/ml/experiment_tracker.py
   - Class ExperimentTracker:
     - Tries to import mlflow; if unavailable, falls back to flat JSON log at experiment_log.jsonl.
     - Methods: log_param(key, value), log_metric(key, value), log_artifact(path), start_run(name), end_run().

3. Modify scripts/train_ml_models.py (backward-compatible):
   - Add --models flag accepting space-separated list: xgb lgbm catboost rf (default: xgb rf).
   - Add _make_clf_lgbm and _make_clf_catboost functions (mirror _make_clf; graceful ImportError fallback).
   - After training all selected models, wrap them in SoftVotingEnsemble.
   - Calibrate the ensemble as a unit (CalibratedClassifierCV wrapping ensemble).
   - Store ensemble as bundle["models"]["win_probability"]; store individual models as bundle["models"]["xgb"], etc.
   - Log all metrics to ExperimentTracker per run.
   - Add --mlflow-tracking-uri (default: mlruns/) and --experiment-name flags.

4. tests/test_gbdt_ensemble.py
   - Test 1: SoftVotingEnsemble with 3 mock classifiers; verify predict_proba shape; verify probabilities in [0,1]; verify sum to 1.0 per row.
   - Test 2: verify backward compat — old code calling bundle["models"]["win_probability"].predict_proba(X) returns valid output.
   - Test 3: ExperimentTracker JSON fallback — verify log written when mlflow not installed.
   - Test 4: train_models() with --models xgb produces same result as before (no regression test).

Safety rules:
  - DO NOT change default --models value (must remain xgb-compatible for backward compat).
  - DO NOT break bundle["models"]["win_probability"] key.
  - DO NOT add LightGBM or CatBoost to pyproject.toml [project] dependencies — add to [quant] optional group only.
  - DO NOT touch .env, credentials, or API keys.
  - DO NOT add live broker execution.
  - DO NOT train on holdout data.
```

---

## 10. Important Notes for Human Review

The following items require explicit human decision before implementation proceeds:

1. **Timeout label handling (Phase 3):** Live data shows 43.9% of timeout trades are profitable. Labeling them 0 loses information. Labeling them 1 would misrepresent stops. `pass_through` uses fixed-horizon fallback. Which is correct depends on position management philosophy. **DECIDE BEFORE ENABLING TRIPLE-BARRIER LABELS.**

2. **DuckDB vs SQLite3 (Phase 1):** DuckDB is faster for analytics but adds a dependency. SQLite3 is zero-dependency (stdlib). For a small universe (1200 tickers, daily bars, 7 years = ~2.1M rows), either works. **DECIDE BEFORE WRITING CACHE LAYER.**

3. **CPCV trial count tracking (Phase 2):** DSR requires accurate trial count (number of backtest parameter variations tried). Need to decide whether to track this per `retrain_weekly.py` run or across all ad-hoc backtest runs. Under-counting trials = understated DSR = overfit models pass. **DECIDE BEFORE USING DSR AS A GATE.**

4. **HRP candidate shortlist size (Phase 6):** HRP runs on candidates passing the ML gate, not the full 1200-ticker universe. With 5 open positions max, running HRP on all ML-passing candidates (potentially 20-50) makes sense. But the correlation matrix for 50 tickers requires 252 days of returns history per ticker. **DECIDE ON SHORTLIST SIZE BEFORE IMPLEMENTING.**

5. **Retrain time budget with CatBoost (Phase 4):** CatBoost training is 3-5x slower than XGBoost on CPU for the same tree count. Full universe retrain (`retrain_weekly.py`) currently runs ~60 minutes. Adding CatBoost with default 500 iterations could push this to 3+ hours. Consider `--catboost-iterations 200` as default. **DECIDE ON ACCEPTABLE RETRAIN TIME BEFORE ENABLING CATBOOST.**

6. **Audit TP-1 through TP-5 dependency:** Triple-barrier labels (Phase 3) require ATR multipliers to match between labels and live execution (TP-1). Meta-labeling (Phase 5) requires grader bugs (GC-1 through GC-8) fixed first. HRP (Phase 6) requires correlation matrix cached (DL-3 fix). **THE PORTFOLIO_AUDIT_2026-05-30.md BUG FIXES ARE PREREQUISITES FOR PHASES 3, 5, AND 6 RESPECTIVELY.**
