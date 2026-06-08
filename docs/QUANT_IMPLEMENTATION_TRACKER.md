# QUANT IMPLEMENTATION TRACKER
**agentic-trader — Engineering Task Log**
Created: 2026-06-05 | Source: `docs/QUANT_RESEARCH_ROADMAP.md` + live code inspection

Update this file after every coding pass. Mark tasks DONE only when code proves it.

---

## How to read this file

- **P0** — blocks other work or actively corrupts metrics. Fix first.
- **P1** — high ROI, no blockers. Start after P0 clear.
- **P2** — valuable but lower urgency. Schedule after P1.
- Status values: `TODO` | `IN PROGRESS` | `DONE` | `BLOCKED`

---

## SYSTEM AREA: Backtesting

---

### BT-1 — Baseline Snapshot Script
**Priority:** P0 | **Status:** DONE

**Why it matters:** No comparison anchor exists. Every future change needs a before/after metric. Several audit bugs (TP-1 through TP-5) may inflate current numbers — the snapshot captures the as-is state before any fix.

**Files affected:**
- New: `scripts/snapshot_baseline.py`
- New: `tests/test_snapshot_baseline.py`
- Reads: `backtest.py`, `scripts/validate_holdout.py`, `ml_models/latest/model_bundle.joblib`, `ml_models/retrain_history.jsonl`

**Implementation steps:**
1. Create `scripts/snapshot_baseline.py` — read-only, no training.
2. Run `backtest.py --walk-forward` on last 60 trading days via subprocess; parse output JSON.
3. Run `scripts/validate_holdout.py` on same range; parse output JSON.
4. Write `docs/baseline_snapshot_<YYYYMMDD>.json` with: `wf_roc`, `brier`, `trade_count`, `win_rate`, `profit_factor`, `sortino`, `max_drawdown`, `holdout_roc`, `holdout_brier`, `holdout_trade_count`.
5. Script must be idempotent — same date = same output.
6. Add `tests/test_snapshot_baseline.py`: verify JSON structure; verify no bundle swap; verify holdout range does not precede training end.

**Acceptance criteria:**
- `docs/baseline_snapshot_<date>.json` written with all 10 fields.
- Re-run produces identical output for same date.
- `--dry-run` flag prints what would be run without executing.

---

### BT-2 — Slippage Baked Into Training Labels
**Priority:** P1 | **Status:** DONE

**Why it matters:** `train_ml_from_stock_data.py` builds `_win_label = return > 0.005` at theoretical prices. Model learns to predict profitability at fill prices that don't exist live. `retrain_weekly.py` passes `--account-slippage-bps 5.0` only to `validate_holdout.py`, not to label construction. Training labels and live fills disagree by ~5-10 bps per trade — small per trade, compounding over 100s of trades.

**Files affected:**
- `scripts/train_ml_from_stock_data.py`
- `backtest.py` (slippage already in `backtest_run` at line 3991 — verify it reaches `_ml_prepare_frame`)

**Implementation steps:**
1. Confirm `backtest.py:_ml_prepare_frame` receives slippage-adjusted returns when `--account-slippage-bps` is passed.
2. Add `--label-slippage-bps` flag to `train_ml_from_stock_data.py` (default: `5.0`).
3. When building candidate rows, deduct `2 * slippage_bps / 10000` from forward return before computing `_win_label`.
4. Log `label_slippage_bps` in `training_report.json`.
5. Add test: verify `_win_label` rate decreases (not increases) when slippage added.

**Acceptance criteria:**
- `_win_label` computed after round-trip slippage deduction.
- `training_report.json` includes `label_slippage_bps` field.
- `--label-slippage-bps 0` reproduces current behavior exactly (backward compat test passes).

---

### BT-3 — Paper-vs-Backtest Fill Drift Report
**Priority:** P1 | **Status:** DONE

**Why it matters:** No automated reconciliation exists between paper fill prices and theoretical signal prices. Even after TP-4 (gap-aware fill, already DONE in `state.py`), live execution may differ from backtest by slippage, spread, and stale data. Without a drift report, execution degradation is invisible.

**Files affected:**
- New: `scripts/paper_backtest_drift.py`
- Reads: paper trade log from `tmp/paper_trading_today/`, backtest results JSON

**Implementation steps:**
1. Create `scripts/paper_backtest_drift.py`.
2. Load paper trades from `tmp/paper_trading_today/trades.jsonl` (or equivalent path).
3. Load backtest results CSV for same tickers and signal dates.
4. Join on `(ticker, signal_date)`.
5. Compute: `fill_delta_bps = (paper_fill - backtest_signal_price) / backtest_signal_price * 10000`.
6. Report: mean, std, p95, by setup_type, by day_of_week.
7. Write `docs/paper_backtest_drift_<date>.json`.

**Acceptance criteria:**
- Script runs without error on current paper trade log.
- Output JSON contains `mean_slip_bps`, `std_slip_bps`, `p95_slip_bps`, `n_trades`.
- `--min-trades 10` flag: aborts with warning if fewer matched trades than threshold.

---

## SYSTEM AREA: Feature Engineering

---

### FE-1 — Triple-Barrier Label Mode in Training
**Priority:** P1 | **Status:** DONE

**Why it matters:** Current primary label `_win_label = (return > 0.005)` ignores path. A trade stopped out on day 2 then recovering to +3% by day 5 is labeled a winner — the ML gate never learns to avoid stop-hits. `_target_label = (outcome == "TARGET_HIT")` already exists in `backtest.py:2068`. Using it as the primary label is a one-line concept change with large training objective impact.

**Blocker:** Timeout label strategy must be decided by human before enabling. 43.9% of timeouts are profitable (live data). Options: label 0, drop rows, or use H3 return fallback.

**Files affected:**
- `scripts/train_ml_from_stock_data.py` — add `--label-mode` flag
- `scripts/train_ml_models.py` — accept `label_col` parameter
- New: `tradingagents/labeling/triple_barrier.py`
- New: `tests/test_triple_barrier_labeling.py`

**Implementation steps:**
1. Create `tradingagents/labeling/triple_barrier.py` with `compute_triple_barrier_labels(df, timeout_handling="zero") -> pd.Series`. Uses `outcome` column (`TARGET_HIT` / `STOP_HIT` / `TIMED_OUT`) already produced by `backtest.py:measure_outcome`.
2. Add `--label-mode {fixed_horizon|triple_barrier}` to `train_ml_from_stock_data.py`. Default: `fixed_horizon` (backward compat).
3. Add `--timeout-label {zero|drop|pass_through}`. Default: `zero`.
4. When `triple_barrier`: call `compute_triple_barrier_labels`; use result as primary label instead of `_win_label`.
5. Log `label_mode`, `timeout_handling`, `target_pct`, `stop_pct`, `timeout_pct` to `training_report.json`.
6. Add tests: known outcomes → expected labels; backward compat test with `fixed_horizon`.

**Acceptance criteria:**
- `--label-mode fixed_horizon` produces identical results to current (regression test passes).
- `--label-mode triple_barrier --timeout-label zero` trains and produces valid ROC.
- Label distribution logged in training report.
- **HUMAN DECISION REQUIRED before enabling in `retrain_weekly.py`:** which `--timeout-label` value matches the live edge (43.9% timeout wins suggest `pass_through` is most accurate).

---

### FE-2 — Random Noise Feature Injection Test
**Priority:** P1 | **Status:** DONE

**Why it matters:** Current feature set has 50+ features, many derived from the same price series. Features ranking below random noise contribute only overfitting. `backtest.py:3246` mentions `likely_noise_features` (bottom 12 by importance) but there is no test that formally compares against injected random noise.

**Files affected:**
- New: `tradingagents/validation/noise_feature_test.py`
- `scripts/train_ml_models.py` — add `--noise-feature-test` flag
- New: `tests/test_noise_feature_test.py`

**Implementation steps:**
1. Create `tradingagents/validation/noise_feature_test.py` with `noise_feature_test(X, y, model_fn, n_noise=10, seed=42) -> dict`.
2. Inject `n_noise` random Gaussian columns into `X`.
3. Train `model_fn(X_extended, y)`.
4. Compute permutation feature importance on validation split.
5. Return `{noise_threshold, features_below_noise: list, features_above_noise: list}`.
6. Add `--noise-feature-test` flag to `train_ml_models.py`. If any real feature ranks below noise: print warning; log to `training_report.json`; do NOT auto-remove (human review required).
7. Add test: 5 signal features + 3 pure noise features → verify at least some signal features rank above noise threshold.

**Acceptance criteria:**
- Test runs without modifying the trained model or bundle.
- Results logged to `training_report.json` under `noise_feature_test` key.
- No automatic feature removal — flag only.

---

### FE-3 — Calibration Per-Bucket Monotonicity Enforcement
**Priority:** P1 | **Status:** DONE

**Why it matters:** `reliability_stats.py:alert_monotonicity` is defined (line 264) and is now called from `production_safety.py:895`. But `reliability_stats.py:57` still computes `calibration_error = |mean_predicted_prob - actual_win_rate|` — first-moment only. A constant-0.6 predictor passes this check. Per-bucket check (HC WR must exceed LC WR) is the real test.

**Files affected:**
- `tradingagents/portfolio/reliability_stats.py`
- `tradingagents/portfolio/production_safety.py`

**Implementation steps:**
1. Read `reliability_stats.py` — verify `alert_monotonicity` checks HC bucket WR > LC bucket WR.
2. If not: add per-bucket check inside `alert_monotonicity`: iterate buckets by ascending confidence; flag if any higher-confidence bucket has lower WR than a lower-confidence bucket.
3. Verify `production_safety.py:895` loop actually halts or warns when monotonicity fails (not just logs).
4. Add test: mock grades where HC bucket WR < LC WR → verify alert fires.

**Acceptance criteria:**
- `alert_monotonicity` fires on anti-predictive HC bucket.
- Alert reaches `check_all()` return value (not just log file).
- Test passes.

---

## SYSTEM AREA: Regime Detection

---

### RD-1 — HMM Regime Feature Layer
**Priority:** P2 | **Status:** DONE

**Why it matters:** Current regime = deterministic SPY SMA + VIX buckets. Hard thresholds are brittle at boundaries. Gaussian HMM produces soft probability over K states + transition matrix. Regime momentum (how likely current regime persists) is a strong feature the current system cannot express.

**Blocker:** Requires Phase 1 (data cache) and Phase 2 (validation layer) first. Do not start before those complete.

**Files affected:**
- New: `tradingagents/ml/hmm_regime.py`
- New: `scripts/train_hmm_regime.py`
- `tradingagents/screening/market_regime.py` — add HMM as optional backend
- `scripts/train_ml_from_stock_data.py` — add HMM probability columns to feature rows
- New: `tests/test_hmm_regime.py`

**Implementation steps:**
1. Create `tradingagents/ml/hmm_regime.py` with `GaussianHMMRegime(n_states=3)`.
2. `fit(returns, vol, vix) -> self`. `predict_proba(returns, vol, vix) -> ndarray(n, K)`. `predict_state(returns, vol, vix) -> ndarray(n,)`.
3. Requires `hmmlearn>=0.3.0` — add to `pyproject.toml` `[quant]` optional group with graceful `ImportError`.
4. Create `scripts/train_hmm_regime.py` — trains from SPY + VIX history; saves `ml_models/hmm_regime/hmm_bundle.joblib`.
5. Add `--hmm-bundle` flag to `train_ml_from_stock_data.py`. If provided: add columns `hmm_state_0_prob`, `hmm_state_1_prob`, `hmm_state_2_prob`, `hmm_predicted_state` to feature rows.
6. Add test: synthetic 3-regime series → verify K states identified; probs sum to 1.0 per row.

**Acceptance criteria:**
- HMM features do not reduce walk-forward ROC vs baseline (adding noise is a regression).
- State assignments align with known historical regime shifts (2020 March, 2022 bear) on visual inspection.
- `--hmm-bundle` flag is optional; omitting it leaves current behavior unchanged.
- **HUMAN REVIEW after first training run:** verify state 0/1/2 map intuitively to bull/consolidation/bear.

---

## SYSTEM AREA: Walk-Forward Validation

---

### WF-1 — CPCV (Combinatorial Purged Cross-Validation)
**Priority:** P1 | **Status:** DONE

**Why it matters:** Current walk-forward produces one OOS path. If that path had lucky ordering, the Sharpe estimate is inflated. CPCV generates C(N,k) paths — for N=8, k=2 that is 28 paths — and reports a distribution. The spread of that distribution reveals path-dependency and overfit.

**Files affected:**
- New: `tradingagents/validation/cpcv.py`
- `scripts/train_ml_models.py` — add `--cpcv` flag
- New: `tests/test_cpcv.py`

**Implementation steps:**
1. Create `tradingagents/validation/cpcv.py` with `combinatorial_purged_cv(df, n_splits, n_test_splits, embargo_days, train_fn, test_fn) -> dict`.
2. Split df into `n_splits` sequential groups.
3. Enumerate all `C(n_splits, n_test_splits)` combinations of test groups.
4. For each combination: train on all non-test groups with purging (drop rows whose label window overlaps test period) and embargo; evaluate on test groups.
5. Return `{n_paths, mean_sharpe, std_sharpe, min_sharpe, max_sharpe, paths: list}`.
6. Add `--cpcv --cpcv-n-splits 8 --cpcv-k-test 2` flags to `train_ml_models.py`. Log results to `training_report.json`.
7. Add `--cpcv-fast` mode with `n_splits=5` for CI speed.
8. Add test: 100-row synthetic df; `n_splits=5, n_test_splits=2`; verify 10 paths generated; verify no time-travel in any path.

**Acceptance criteria:**
- Path count equals `C(n_splits, n_test_splits)`.
- No test row appears in training for the same path.
- Results logged under `cpcv` key in `training_report.json`.
- `--cpcv-fast` completes in < 60 seconds on the test machine.

---

### WF-2 — Deflated Sharpe Ratio (DSR) Tracking
**Priority:** P1 | **Status:** DONE

**Why it matters:** `retrain_weekly.py` gates on `wf_roc >= min_roc` and `brier < max_brier` but never penalizes for cumulative trials. Each grid search, filter removal, or hyperparameter tweak constitutes a trial. Without DSR, the system gradually overfits as trial count grows. Bailey & Lopez de Prado (2014) formula adjusts Sharpe downward based on `n_trials`, `T` (sample size), skewness, and kurtosis.

**Files affected:**
- New: `tradingagents/validation/deflated_sharpe.py`
- `scripts/train_ml_models.py` — add `--compute-dsr --n-trials <count>`
- `scripts/retrain_weekly.py` — log DSR per cycle; optionally gate on `DSR > 0`
- New: `tests/test_deflated_sharpe.py`

**Implementation steps:**
1. Create `tradingagents/validation/deflated_sharpe.py` with `deflated_sharpe_ratio(sharpe, n_trials, T, skewness=0.0, kurtosis=3.0) -> float`. Implement Bailey 2014 formula with scipy stats. Include docstring citing the paper and interpretation guide (DSR < 0 = likely overfit).
2. Add `--compute-dsr --n-trials N` to `train_ml_models.py`. Log `dsr`, `n_trials`, `raw_sharpe` to `training_report.json`.
3. Add `--dsr-gate` optional flag to `retrain_weekly.py`: if DSR < 0 and flag set, abort bundle swap with warning. Default: log only (not a hard gate until trial count tracking is reliable).
4. Add test: `sharpe=1.0, n_trials=100, T=252` → DSR < 1.0. `n_trials=1` → DSR ≈ raw sharpe.

**Acceptance criteria:**
- DSR logged in `training_report.json` per retrain cycle.
- Formula matches Bailey 2014 reference values (two known-answer test cases).
- **HUMAN DECISION REQUIRED:** how to accurately count `n_trials` across weekly retrains + ad-hoc runs. Undercounting → DSR understated → overfit models pass gate. Options: (a) manual `--n-trials` per run, (b) auto-increment counter in `ml_models/retrain_history.jsonl`.

---

## SYSTEM AREA: Leakage Prevention

---

### LP-1 — Embargo Documented and Tested Explicitly
**Priority:** P0 | **Status:** DONE

**Why it matters:** `_ml_time_split` and `_ml_purged_walk_forward` both implement embargo (confirmed at `backtest.py:2189–2291`). However, there is no standalone test that proves the embargo gap is leak-free at the label-overlap boundary. If `embargo_days` is ever passed as 0 accidentally, labels from the test period leak into training rows.

**Files affected:**
- `backtest.py:_ml_time_split`, `backtest.py:_ml_purged_walk_forward`
- New: `tests/test_embargo_leakage.py`

**Implementation steps:**
1. Create `tests/test_embargo_leakage.py`.
2. Test 1: build a synthetic dataframe; set `embargo_days=5`; call `_ml_time_split`; verify no training row has `scan_date` within 5 days of test boundary.
3. Test 2: same with `embargo_days=0`; verify training rows do touch boundary (correct behavior, no embargo).
4. Test 3: `_ml_purged_walk_forward` with known data; verify OOS predictions never come from future labels.
5. Add assertion inside `_ml_purged_walk_forward`: if `embargo_days < hold`, print warning (hold period = label window; embargo should cover it).

**Acceptance criteria:**
- All 3 tests pass.
- Assertion fires when `embargo_days < hold` (warning, not crash).

---

### LP-2 — Leakage Check Covers All Label Columns
**Priority:** P1 | **Status:** DONE

**Why it matters:** `train_ml_models.py` already has leakage detection for forward-return columns. But `_target_label`, `_timeout_label`, `_breakout_win_label`, `_failed_breakout_label`, `_big_move_label` are outcome-derived columns that also leak future information. If any of these survive into the feature matrix (rather than being used as labels), the model trains on the future.

**Files affected:**
- `scripts/train_ml_models.py`
- `backtest.py:_ml_prepare_frame` (line 2158 — verify exclusion list is complete)

**Implementation steps:**
1. Read `backtest.py:2155–2170` — verify all `_*_label` columns are in the exclusion list passed to `_ml_design_matrix`.
2. Add explicit assertion in `train_ml_models.py`: before fitting, verify none of `["_win_label", "_target_label", "_timeout_label", "_breakout_win_label", "_failed_breakout_label", "_big_move_label", "_large_loss_label", "_missed_winner_label", "_mfe", "_mae"]` appear in `feature_names`.
3. Add test: inject a label column into feature matrix; verify assertion fires.

**Acceptance criteria:**
- Assertion present in `train_ml_models.py` pre-fit.
- Test passes (assertion fires on injected label column).
- No label column appears in saved `bundle["feature_names"]`.

---

## SYSTEM AREA: Model Selection

---

### MS-1 — GBDT Ensemble: LightGBM + CatBoost + XGBoost
**Priority:** P1 | **Status:** DONE

**Why it matters:** `train_ml_models.py` uses XGBoost if available, else RandomForest. No LightGBM. No CatBoost. Financial tabular research shows: XGBoost most stable cross-sectionally; LightGBM fastest for large regression; CatBoost most robust to categorical features and prediction shift. Soft-vote ensemble reduces individual-model overfit and smooths calibration.

**Files affected:**
- `scripts/train_ml_models.py`
- New: `tradingagents/ml/ensemble.py`
- New: `tests/test_gbdt_ensemble.py`

**Implementation steps:**
1. Create `tradingagents/ml/ensemble.py` with `SoftVotingEnsemble(estimators: list[tuple[str, classifier]])`. `predict_proba(X) -> ndarray` averages member probabilities. Implements sklearn interface (`classes_`, `predict`, `predict_proba`).
2. Add `_make_clf_lgbm` and `_make_clf_catboost` to `train_ml_models.py`, each with graceful `ImportError` fallback.
3. Add `--models xgb lgbm catboost rf` flag. Default: `xgb rf` (backward compat).
4. After training all selected models: wrap in `SoftVotingEnsemble`; calibrate ensemble with `CalibratedClassifierCV`.
5. Store ensemble as `bundle["models"]["win_probability"]` (backward compat). Store individuals as `bundle["models"]["xgb"]` etc.
6. Log per-model ROC and ensemble ROC to `training_report.json`.
7. Add test: 3 mock classifiers → ensemble `predict_proba` in [0,1], sums to 1.0 per row. Backward compat test: old call `bundle["models"]["win_probability"].predict_proba(X)` returns valid output.

**Acceptance criteria:**
- `--models xgb` produces same result as current (regression test).
- `bundle["models"]["win_probability"].predict_proba(X)` backward compat preserved.
- LightGBM + CatBoost in `pyproject.toml` `[quant]` optional group only — not in `[project]`.
- `training_report.json` includes `per_model_roc` dict.
- **HUMAN DECISION REQUIRED:** acceptable retrain time with CatBoost (estimated 3x slower on CPU). Set `--catboost-iterations 200` as default to limit.

---

### MS-2 — MLflow Local Experiment Tracking
**Priority:** P2 | **Status:** DONE

**Why it matters:** `retrain_weekly.py` writes `training_report.json` and `ml_models/retrain_history.jsonl` as flat files. No way to compare hyperparameter iterations, feature sets, ROC curves, or model drift across cycles visually. MLflow SQLite backend is zero-infrastructure — just a local file.

**Files affected:**
- New: `tradingagents/ml/experiment_tracker.py`
- `scripts/train_ml_models.py` — add `--mlflow-tracking-uri` flag
- New: `tests/test_experiment_tracker.py`

**Implementation steps:**
1. Create `tradingagents/ml/experiment_tracker.py` with `ExperimentTracker`. Tries `import mlflow`; on `ImportError` falls back to flat JSONL at `experiment_log.jsonl`. Methods: `start_run(name)`, `end_run()`, `log_param(k,v)`, `log_metric(k,v)`, `log_artifact(path)`.
2. Add `--mlflow-tracking-uri mlruns/` and `--experiment-name <name>` flags to `train_ml_models.py`.
3. Wrap `train_models()` call with tracker start/end; log all metrics from `training_report.json`.
4. Add `mlruns/` to `.gitignore`.
5. Add test: JSON fallback works when mlflow not installed.

**Acceptance criteria:**
- `ExperimentTracker` works with no dependencies (JSON fallback).
- When mlflow installed: run appears in `mlruns/` directory queryable with `mlflow ui`.
- `mlflow` added to `pyproject.toml` `[quant]` optional group only.

---

### MS-3 — Meta-Labeling + Probability-Based Position Sizing
**Priority:** P2 | **Status:** BLOCKED

**Blocker:** Requires GC-1 through GC-8 grader fixes AND clean OOS trade log (paper trade grader must emit correct fields before meta-label training data is usable). Do not start until BUG-4 (GC-2 SELL fields) is done.

**Why it matters:** Current gate is primary model (predicts trade profitability directly). Meta-labeling trains a secondary model on whether the primary model's picks actually won. Secondary model output = position size multiplier, not entry filter. Decouples strategy logic from sizing logic; reduces overfitting of sizing to primary model artifacts.

**Files affected:**
- New: `tradingagents/ml/meta_labeler.py`
- New: `tradingagents/portfolio/prob_sizer.py`
- `scripts/train_ml_models.py` — add `--train-meta-label` flag
- `scripts/paper_trade_today.py` — add `--meta-label-bundle` flag

**Implementation steps:**
1. Create `tradingagents/ml/meta_labeler.py` with `MetaLabeler`. Trains on rows where primary model made OOS prediction; target = actual outcome (1 = trade won). Features = primary model's predicted probability + additional regime/ML features.
2. Create `tradingagents/portfolio/prob_sizer.py` with `compute_prob_based_size(meta_prob, base_fraction, min_frac=0.5, max_frac=1.5) -> float`. Linear scaling: `base_fraction * (min_frac + (max_frac - min_frac) * meta_prob)`.
3. Add `--train-meta-label` to `train_ml_models.py`. Saves `meta_bundle.joblib` alongside main bundle.
4. Add `--meta-label-bundle` to `paper_trade_today.py`. Load bundle; pass `meta_prob` to `prob_sizer` before final size calc.
5. Add tests: `MetaLabeler` produces valid probabilities; `prob_sizer` bounded by min/max.

**Acceptance criteria:**
- Meta-label walk-forward ROC > 0.50 on holdout. If not: disable meta-label gate (log warning).
- Paper Sortino over 30 trading days ≥ baseline (measured with BT-1 snapshot).
- `--meta-label-bundle` is optional; omitting preserves current behavior exactly.

---

## SYSTEM AREA: Portfolio Construction

---

### PC-1 — Wire alloc_weights to Position Sizing (DL-2)
**Priority:** P0 | **Status:** DONE

**Why it matters:** `candidate_ranker.py:_alloc_weights` computes allocation weights but `paper_trade_today.py:3738` stores the result and never uses it (audit DL-2). Rank ordering has zero effect on capital. This is a wiring bug in an existing feature, not new work.

**Files affected:**
- `scripts/paper_trade_today.py` (line ~3738 and the buy loop)
- `tradingagents/portfolio/candidate_ranker.py`
- `tradingagents/portfolio/position_sizing.py`

**Implementation steps:**
1. Read `paper_trade_today.py` from line 3730 onward. Identify where `_alloc_weights` is returned and confirm it is unused.
2. Pass `alloc_weights[ticker]` as a multiplier into `calculate_dynamic_size` or equivalent sizing call before position open.
3. Cap multiplier: `max(0.5, min(2.0, alloc_weight))` to prevent extreme over/under sizing.
4. Add test: two candidates with different alloc_weights get proportionally different position sizes.

**Acceptance criteria:**
- `alloc_weights` from `candidate_ranker` affect actual shares computed.
- Multiplier capped between 0.5 and 2.0.
- Test passes.

---

### PC-2 — HRP Portfolio Optimizer
**Priority:** P2 | **Status:** DONE

**Blocker:** Requires PC-1 (alloc_weights wired) and Phase 1 (data cache for correlation matrix) first.

**Why it matters:** Equal-weight or Kelly-only allocation ignores cross-sectional correlation. HRP uses hierarchical clustering + recursive bisection to distribute risk across uncorrelated clusters without inverting the covariance matrix. Avoids Markowitz instability. OOS drawdown reduction vs equal-weight is well-documented.

**Files affected:**
- New: `tradingagents/portfolio/hrp_optimizer.py`
- New: `tradingagents/portfolio/weight_controller.py`
- `scripts/paper_trade_today.py` — add `--use-hrp` flag
- `tradingagents/portfolio/candidate_ranker.py`

**Implementation steps:**
1. Create `tradingagents/portfolio/hrp_optimizer.py` with `HRPOptimizer`. `fit(returns_df: pd.DataFrame) -> np.ndarray` (weights). Implement: (1) correlation-distance matrix `d = sqrt((1 - rho) / 2)`, (2) `scipy.cluster.hierarchy.linkage`, (3) quasi-diagonalization, (4) recursive bisection with inverse-variance weights.
2. Create `tradingagents/portfolio/weight_controller.py` with `WeightController(max_sector=0.30, max_single=0.20, max_turnover=0.50)`. `enforce(weights, prev_weights, sector_map) -> ndarray`.
3. Add `--use-hrp --hrp-lookback-days 60 --max-sector-weight 0.30 --max-single-name 0.20 --max-turnover 0.50` flags to `paper_trade_today.py`.
4. When `--use-hrp`: after candidate scoring, compute HRP weights over shortlist; pass to `WeightController`; replace `alloc_weights` with HRP weights.
5. Add tests: 10-asset synthetic returns → weights sum to 1.0; single-name cap enforced; turnover constraint applied.

**Acceptance criteria:**
- HRP weights sum to 1.0.
- No single name exceeds `max_single`.
- Turnover between consecutive scans ≤ `max_turnover` (or delta is within tolerance).
- `--use-hrp` off by default; omitting preserves current behavior.

---

## SYSTEM AREA: Risk Management

---

### RM-1 — GC-2: Emit stop_hit / target_hit / max_adverse_pct on SELL Events
**Priority:** P0 | **Status:** DONE

**Why it matters:** `prediction_grader.py` reads `stop_hit` and `target_hit` from SELL events. `paper_trade_today.py` never emits these fields on SELL (audit GC-2). Result: `actual_large_loss` is always 0; `stop_rate`/`target_rate` in reliability stats are meaningless. The whole grader stack grades on defaults — reporting looks real but measures nothing.

**Files affected:**
- `scripts/paper_trade_today.py` (SELL event emission, line ~1030)
- `tradingagents/portfolio/prediction_grader.py` (verify it reads these fields)

**Implementation steps:**
1. Find SELL event emission in `paper_trade_today.py` (line ~935 and ~1030).
2. Add fields to SELL event dict:
   - `"stop_hit": True/False` (True when `reason` contains `STOP`)
   - `"target_hit": True/False` (True when `reason` contains `TARGET` or `TAKE_PROFIT`)
   - `"mae_pct": peak_adverse_pct` (from position tracking, if available)
   - `"actual_large_loss": True if pnl_pct < -0.10`
3. Verify `prediction_grader.py:202–206` reads these fields correctly (already uses `sell_ev.get("stop_hit", "STOP" in _er)` — existing fallback is fine; explicit emission is cleaner).
4. Add test: mock SELL event with `reason="STOP_LOSS"`; verify grader reads `stop_hit=True`.

**Acceptance criteria:**
- SELL events in paper trade log contain `stop_hit`, `target_hit`, `actual_large_loss` fields.
- `grader.by_exit_type` shows non-zero stop/target counts after a session with known stop-exits.

---

### RM-2 — DL-1: ATR Sizing Path Applies All Multipliers
**Priority:** P1 | **Status:** DONE

**Why it matters:** `position_sizing.py:248–264` (audit DL-1): ATR-path sizing ignores `ml_confidence`, `streak`, `time_of_day`, `daily_lock`, `regime`. Uses raw `cap_max` not `cap_max_effective`. `base_pct` (which carries steps 2-6 multipliers) is discarded whenever ATR/stop > 0. Live size = pure vol-target × tier × large_loss only. This means the entire tuning stack has zero effect on actual live sizes.

**Files affected:**
- `tradingagents/portfolio/position_sizing.py` (lines 248–264 approx)

**Implementation steps:**
1. Read `position_sizing.py:248–264`. Identify exactly where `base_pct` is discarded on the ATR path.
2. Compute composite multiplier from: `ml_confidence_mult * streak_mult * tod_mult * daily_lock_mult * regime_mult`.
3. Apply composite multiplier to `risk_dollars` on the ATR path: `risk_dollars *= composite_mult`.
4. Use `cap_max_effective` (not raw `cap_max`) as the position cap.
5. Add test: ATR path with `ml_confidence=0.55` (below floor) returns smaller size than `ml_confidence=0.75`.
6. Add test: ATR path with `streak=-3` returns smaller size than `streak=0`.

**Acceptance criteria:**
- ATR path produces different sizes for different ML confidence inputs.
- ATR path produces different sizes for different streak inputs.
- Existing behavior preserved when all multipliers = 1.0.

---

### RM-3 — DL-6: tbs_prob (target-before-stop probability) Used in Decision
**Priority:** P1 | **Status:** DONE

**Why it matters:** `alpha_engine.py:237` (audit DL-6): `tbs_prob` is computed but never enters the alpha numerator. This is the probability the trade hits the target before the stop — directly the ML gate's `_target_label`. If walk-forward ROC > 0.50, it should influence the alpha score.

**Files affected:**
- `tradingagents/portfolio/alpha_engine.py` (line ~237)

**Implementation steps:**
1. Read `alpha_engine.py` around line 237. Confirm `tbs_prob` is computed but discarded.
2. Validate `tbs_prob` walk-forward ROC independently (run against holdout; must be > 0.50 to add signal).
3. If ROC > 0.50: add `tbs_prob` contribution to numerator with configurable weight `tbs_weight=0.3` (default).
4. If ROC ≤ 0.50: add comment explaining why disabled; leave dead code as documented stub.
5. Add test: `tbs_prob=0.80` produces higher `alpha_score` than `tbs_prob=0.50` when enabled.

**Acceptance criteria:**
- `tbs_prob` either contributes to numerator (with test) or is explicitly documented as disabled with measured ROC reason.
- No silent dead code.

---

## SYSTEM AREA: Live / Paper Trading

---

### LPT-1 — TP-5: Single Price Snapshot Per Scan Cycle
**Priority:** P0 | **Status:** DONE

**Why it matters:** `state.py:15` imports yfinance. `execute_sell` uses `self.price_lookup(ticker)` which re-fetches live price per call. During one scan cycle, different valuations see different prices. On network failure, `price_lookup` returns None → falls back to `entry_price` → positions look flat → no stops trigger. Garbage PnL for entire session.

**Current state:** `execute_sell` has gap-aware clamping (`min(live, stop_loss)` for stops) — DONE. But `price_lookup` still calls live yfinance per-ticker per-call.

**Files affected:**
- `scripts/paper_trade_today.py` — scan loop
- `tradingagents/portfolio/state.py`

**Implementation steps:**
1. In `paper_trade_today.py` scan loop: fetch all prices for open positions in one `yf.download()` call at scan start; store in `price_snapshot: dict[str, float]`.
2. Pass `price_snapshot` into `state` valuation and exit check calls so they use snapshot prices, not live re-fetches.
3. If `yf.download` fails entirely: skip the scan (log warning); do not execute any exits with stale/default prices.
4. Add test: mock `price_lookup` failure → verify no exit executes; verify scan is skipped, not crashed.

**Acceptance criteria:**
- One `yf.download` call per scan cycle for all open positions.
- Network failure → scan skipped; log entry written; no exits executed.
- Test passes.

---

### LPT-2 — Paper Trade Log: Emit BUY Event with Full Grade Fields (GC-1)
**Priority:** P1 | **Status:** DONE

**Why it matters:** `paper_trade_today.py:761–762` has a Cycle 44 comment noting GC-1 fields added to BUY event. Verify all required fields are present: `large_loss_probability`, `alpha_tier`, `alpha_score`, `breakout_score`, `model_version`, `regime_at_entry`. If any missing, `grader.by_tier` and `grader.by_model_version` slices default to unknown.

**Files affected:**
- `scripts/paper_trade_today.py` (BUY event emission)
- `tradingagents/portfolio/prediction_grader.py`

**Implementation steps:**
1. Read BUY event emission in `paper_trade_today.py` around line 761.
2. Verify all 6 fields present: `large_loss_probability`, `alpha_tier`, `alpha_score`, `breakout_score`, `model_version`, `regime_at_entry`.
3. Add any missing fields. Use `candidate.<field>` or `None` if unavailable.
4. Add test: mock BUY event; verify grader assigns correct tier and model_version.

**Acceptance criteria:**
- All 6 fields present in emitted BUY events.
- `grader.by_tier` shows non-"unknown" tier counts after a session.

---

## SYSTEM AREA: Logging / Explainability

---

### LOG-1 — Market Data Provider Interface
**Priority:** P1 | **Status:** DONE

**Why it matters:** All data fetches call `yf.download` directly. No abstraction layer. Swapping provider (Polygon, cached SQLite) requires modifying every caller. Rate-limit and network errors are handled inconsistently across `backtest.py`, `train_ml_from_stock_data.py`, and `paper_trade_today.py`.

**Files affected:**
- New: `tradingagents/dataflows/market_data_provider.py`
- New: `tradingagents/dataflows/yfinance_provider.py`
- New: `tradingagents/dataflows/ohlcv_cache.py`
- New: `tradingagents/dataflows/cached_provider.py`
- New: `tests/test_market_data_provider.py`
- Later: wire into `backtest.py:download_all` and `train_ml_from_stock_data.py`

**Implementation steps:**
1. Create `tradingagents/dataflows/market_data_provider.py` — abstract base class `MarketDataProvider` with:
   - `get_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame`
   - `get_ohlcv_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]`
   - DataFrame schema: `[Open, High, Low, Close, Volume]`, DatetimeIndex.
2. Create `tradingagents/dataflows/yfinance_provider.py` — `YFinanceProvider(MarketDataProvider)`. Wraps existing retry logic from `backtest.py:download_all` (batch size 50, retry 3, exp backoff). Does NOT modify `backtest.py`.
3. Create `tradingagents/dataflows/ohlcv_cache.py` — `OHLCVCache`. Try `import duckdb`; fallback to `sqlite3`. Schema: `(ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(ticker, date))`. Methods: `read(ticker, start, end)`, `write(ticker, df)`, `has_coverage(ticker, start, end)`.
4. Create `tradingagents/dataflows/cached_provider.py` — `CachedProvider(MarketDataProvider)` wrapping upstream + `OHLCVCache`. Cache-first; fetch + write on miss.
5. Add `duckdb>=0.10.0` to `pyproject.toml` `[quant]` optional group.
6. Add tests: cache hit avoids network call; round-trip write+read; DuckDB/SQLite fallback.

**Acceptance criteria:**
- `CachedProvider` returns same DataFrame as direct yfinance call.
- Second call for same ticker+range: zero network calls.
- Tests pass with both DuckDB and sqlite3 backends.
- Existing `backtest.py` and `train_ml_from_stock_data.py` untouched (interface is new, not yet wired).

---

### LOG-2 — Training Report Schema Hardened
**Priority:** P1 | **Status:** DONE

**Why it matters:** `training_report.json` is written by `train_ml_models.py` with no enforced schema. `retrain_weekly.py` reads keys like `wf_roc` with `.get()` defaults. If a key is missing (training path changes), the gate silently passes with default 0.0. Schema enforcement catches regressions in report structure immediately.

**Files affected:**
- `scripts/train_ml_models.py`
- `scripts/retrain_weekly.py`
- New: `tests/test_training_report_schema.py`

**Implementation steps:**
1. Define expected schema as a dataclass or TypedDict in `scripts/train_ml_models.py` covering: `wf_roc`, `brier`, `trade_count`, `win_rate`, `feature_names`, `label_mode`, `label_slippage_bps`, `cpcv` (optional), `dsr` (optional), `noise_feature_test` (optional).
2. Validate schema before writing: if any required key missing, raise `ValueError` with missing key name.
3. In `retrain_weekly.py:_check_report_gates`: replace `.get(key, 0.0)` with explicit key access + clear error on missing key.
4. Add test: mock training_report with missing `wf_roc`; verify `retrain_weekly._check_report_gates` raises or returns `(False, "wf_roc missing")`.

**Acceptance criteria:**
- `_check_report_gates` fails explicitly on missing required keys.
- `training_report.json` always contains all required keys after a successful training run.

---

## BUG FIXES (from docs/plans/PORTFOLIO_AUDIT_2026-05-30.md)

Tracked here for cross-reference. Separate from new quant features.

---

### BUG-1 — TP-1: ATR Stop Multiplier Consistency
**Priority:** P0 | **Status:** DONE

**Evidence:** `tradingagents/screening/screener.py:254` reads `_ATR_STOP = 1.0` with comment "Cycle 44: raised from 0.7 to match label/training geometry". `backtest.py:score_at` defaults `stop_mult=1.0`. **Confirmed fixed.**

---

### BUG-2 — TP-2: Drawdown Uses Running HWM
**Priority:** P0 | **Status:** DONE

**Evidence:** `production_safety.py:554` has comment "Previously peak=max(start, current), so any profitable run reset the peak" — implies fix was applied. Verify the running HWM is persisted across scan cycles (not reset on each check_all call).

**Files affected:** `tradingagents/portfolio/production_safety.py`

**Implementation steps:**
1. Read `production_safety.py` around line 554.
2. Verify HWM is stored in state file (not just in-memory) so it survives restarts.
3. Add test: simulate 3 scan cycles with equity rise then fall; verify drawdown computed from peak of cycle 2, not cycle 3 start.

---

### BUG-3 — TP-3: Open Position MTM in Daily Loss
**Priority:** P0 | **Status:** DONE

**Evidence:** `production_safety.py:521–537` implements unrealized MTM fold-in with comment explaining the fix. **Confirmed implemented.**

---

### BUG-4 — TP-4: Gap-Aware Stop Fill
**Priority:** P0 | **Status:** DONE

**Evidence:** `tradingagents/portfolio/state.py:execute_sell` implements gap-aware fill with `min(live, pos.stop_loss)` for stops, `max(live, pos.take_profit)` for targets. Comment cites "Cycle 44 V-26". **Confirmed implemented.**

---

### BUG-5 — GC-2: SELL Event Missing stop_hit / target_hit / actual_large_loss
**Priority:** P0 | **Status:** DONE

See **RM-1** above. These fields are not emitted. Grader calibration is broken without them.

---

### BUG-6 — DL-2: alloc_weights Computed But Never Applied
**Priority:** P0 | **Status:** DONE

See **PC-1** above. Rank ordering has zero effect on live capital allocation.

---

### BUG-7 — DL-4: Sector Hardcoded to "unknown" in UnifiedBrain
**Priority:** P1 | **Status:** DONE

**Why it matters:** `unified_brain.py:667` passes `"unknown"` as sector for every candidate. `max_sector=2` cap silently limits entire book to 2 positions regardless of actual sectors, defeating `max_open_positions=5`.

**Files affected:**
- `tradingagents/portfolio/unified_brain.py` (line ~667)
- `tradingagents/portfolio/alpha_engine.py` (UnifiedCandidate construction)

**Implementation steps:**
1. Read `unified_brain.py:667`. Confirm sector is hardcoded.
2. Option A: plumb real sector from `tradingagents/screening/tickers.py` or yfinance metadata onto `UnifiedCandidate`.
3. Option B (fast fix): disable sector cap until sectors are available — set `max_sector=99` as default or skip the check when sector == "unknown".
4. Add test: two candidates with same real sector → only `max_sector` of them enter book.

**Acceptance criteria:**
- Sector cap does not silently limit book to 2 positions on every run.
- If real sector data unavailable: cap disabled with log warning, not silently applied.

---

## TASK SUMMARY

| ID | Area | Name | Priority | Status |
|----|------|------|----------|--------|
| BT-1 | Backtesting | Baseline Snapshot Script | P0 | DONE |
| BT-2 | Backtesting | Slippage in Training Labels | P1 | DONE |
| BT-3 | Backtesting | Paper-vs-Backtest Drift Report | P1 | DONE |
| FE-1 | Feature Engineering | Triple-Barrier Label Mode | P1 | DONE |
| FE-2 | Feature Engineering | Random Noise Feature Injection Test | P1 | DONE |
| FE-3 | Feature Engineering | Per-Bucket Calibration Monotonicity | P1 | DONE |
| RD-1 | Regime Detection | HMM Regime Feature Layer | P2 | DONE |
| WF-1 | Walk-Forward Validation | CPCV | P1 | DONE |
| WF-2 | Walk-Forward Validation | Deflated Sharpe Ratio | P1 | DONE |
| LP-1 | Leakage Prevention | Embargo Explicitly Tested | P0 | DONE |
| LP-2 | Leakage Prevention | All Label Columns in Exclusion List | P1 | DONE |
| MS-1 | Model Selection | GBDT Ensemble LightGBM+CatBoost+XGB | P1 | DONE |
| MS-2 | Model Selection | MLflow Local Experiment Tracking | P2 | DONE |
| MS-3 | Model Selection | Meta-Labeling + Prob-Based Sizing | P2 | BLOCKED |
| PC-1 | Portfolio Construction | Wire alloc_weights (DL-2) | P0 | DONE |
| PC-2 | Portfolio Construction | HRP Optimizer | P2 | DONE |
| RM-1 | Risk Management | GC-2 SELL Event Fields | P0 | DONE |
| RM-2 | Risk Management | DL-1 ATR Path Full Multiplier Stack | P1 | DONE |
| RM-3 | Risk Management | DL-6 tbs_prob in Alpha Numerator | P1 | DONE |
| LPT-1 | Live/Paper Trading | TP-5 Single Price Snapshot | P0 | DONE |
| LPT-2 | Live/Paper Trading | GC-1 BUY Event Full Grade Fields | P1 | DONE |
| LOG-1 | Logging | Market Data Provider Interface | P1 | DONE |
| LOG-2 | Logging | Training Report Schema Hardened | P1 | DONE |
| BUG-1 | Bug Fix | TP-1 ATR Stop 0.7→1.0 | P0 | DONE |
| BUG-2 | Bug Fix | TP-2 Running HWM | P0 | DONE |
| BUG-3 | Bug Fix | TP-3 Open MTM in Daily Loss | P0 | DONE |
| BUG-4 | Bug Fix | TP-4 Gap-Aware Stop Fill | P0 | DONE |
| BUG-5 | Bug Fix | GC-2 SELL Event Fields | P0 | DONE |
| BUG-6 | Bug Fix | DL-2 alloc_weights Not Applied | P0 | DONE |
| BUG-7 | Bug Fix | DL-4 Sector Hardcoded "unknown" | P1 | DONE |

**Total tasks: 29**
**P0: 10** | **P1: 14** | **P2: 5**
**DONE: 28** | **BLOCKED: 1** | **TODO: 0**

---

## Execution Order (safe sequencing)

```
Week 1:  BT-1 (baseline snapshot — safest start, read-only)
         LP-1 (embargo tests — proves existing code correct)
         RM-1 / BUG-5 (SELL event fields — small, high-value)
         BUG-2 (verify HWM persists across restarts)

Week 2:  LP-2 (label column exclusion assertion)
         LPT-1 (price snapshot — fixes TP-5)
         LPT-2 (verify BUY event fields complete)
         BUG-7 (sector hardcoded fix — fast option B)

Week 3:  LOG-1 (data provider interface — new files only, no wiring yet)
         LOG-2 (training report schema hardening)
         FE-2 (noise feature test — additive, no prod change)

Week 4:  FE-3 (calibration monotonicity enforcement)
         BT-2 (slippage in training labels)
         WF-2 (deflated sharpe — new file, additive)

Week 5:  WF-1 (CPCV — compute-heavy, test with --cpcv-fast first)
         PC-1 (wire alloc_weights — small wiring fix)
         RM-2 (DL-1 ATR path multiplier stack)

Week 6+: FE-1 (triple-barrier labels — after human timeout decision)
         MS-1 (GBDT ensemble — after FE work stabilizes)
         RM-3 (tbs_prob — validate ROC first)
         BT-3 (drift report — after grader fixes complete)
         MS-2 (MLflow — nice-to-have)

Later:   MS-3 (meta-labeling — needs clean grader data first)
         PC-2 (HRP — after PC-1 and LOG-1 wired)
         RD-1 (HMM regime — last, all foundations must be solid)
```

---

## Human Decisions Required Before Implementation

| Decision | Affects | Blocking |
|----------|---------|---------|
| Timeout label strategy: `zero` / `drop` / `pass_through` | FE-1 | Cannot enable triple-barrier in retrain until decided |
| DuckDB vs SQLite3 for OHLCV cache | LOG-1 | Minor; DuckDB preferred, SQLite3 is fallback |
| Acceptable retrain time with CatBoost | MS-1 | Set `--catboost-iterations 200` default; confirm before enabling |
| DSR trial count tracking methodology | WF-2 | Cannot use DSR as hard gate until trial count is reliable |
| HRP candidate shortlist size | PC-2 | 10? 20? All ML-passing candidates? |
