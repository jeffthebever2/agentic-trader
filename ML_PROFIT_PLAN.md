# ML Profitability Improvement Plan
**Created:** 2026-05-26  
**Goal:** ~20% annualized return with controlled drawdown — only if supported by out-of-sample evidence  
**Constraint:** Robustness first. No fake edge. No leakage.

---

## 1. Holdout Window Definition

### Model timestamps
| Model | Trained file date | Data end | Test period | Status |
|-------|------------------|----------|-------------|--------|
| `ml_models/latest/` | 2026-05-16 | 2026-05-07 | 2026 (Jan–May 7) | **Active** |
| `ml_models/stock_universe_candidate_20260512/` | 2026-05-12 | 2026-05-07 | 2026 | Parent of latest |
| `ml_models/stock_universe/` | 2026-05-10 | ~2026-05-10 | 2024 | Older, large dataset |

### Unseen holdout window
- **Start:** 2026-05-08 (first trading day after last training data row)
- **End:** 2026-05-26 (today)
- **Trading days:** ~13 days
- **Note:** This window is too short for statistically meaningful standalone validation (~13 trades max if running daily on a single ticker). Use it as a sanity check only. The honest out-of-sample validation is the 2026 test-year data in the training report.

### What "seen" means
Any backtest run against data before 2026-05-08 has been used to tune thresholds, select features, or evaluate the system — it is training/diagnostic data, not proof. The 2026 test split (Jan–May 7, 159K rows, ROC-measured) is the best internal validation we have.

---

## 2. Current State Audit

### Model quality
| Model | win_probability ROC | large_loss ROC | Verdict |
|-------|-------------------|---------------|---------|
| `latest` | **0.5479** | 0.7216 | Win model barely above random |
| `candidate_20260512` | **0.5428** | 0.7270 | Same issue |
| `stock_universe` | **0.7128** | 0.8472 | Strong — but tested on 2024, tuned on 2025+ |

**Root cause of low win ROC:** The `latest` model was trained on 471K rows from an enriched CSV with 64 features, test year = 2026 (just 28,840 rows = 5 months). The win model sees too few 2026 test samples to demonstrate real edge. The `stock_universe` model trained on 6.3M rows shows much stronger ROC but its test_period=2024 means 2025/2026 data was in training.

### Gate analysis (latest model, test set 2026)
| Strategy | N | Win Rate | Profit Factor | Expectancy/trade |
|----------|---|----------|---------------|-----------------|
| rule_only | 62 | 45.2% | 0.582 | -0.80% |
| ml_only (threshold=0.72) | 224 | 56.7% | 1.051 | +0.08% |
| rule+ml | 0 | — | — | No trades passed both |

**Interpretation:** ML alone beats rules alone, but expectancy is near zero on test set. The threshold (0.72) is very aggressive and leaves many trades on the table (missed_winner_rate=99.6%). The large_loss model (ROC=0.72) is working well as a filter.

### Known issues
1. **No probability calibration** in `train_ml_models.py` — XGBoost probabilities are not isotonically calibrated
2. **Single train/test split** — no walk-forward; one bad year contaminates the test metric
3. **Commission = $0 default** — all backtests, gate analysis, and validation are cost-free
4. **No slippage model** — assumes perfect fills at next open/close
5. **RL transaction_cost = 10bps round-trip** — not the same model as backtest
6. **max_rows sampling is random** (line 138–140 in train_ml_models.py) — when capping rows, samples randomly then sorts, which is fine for split but biases toward over-representing certain date ranges if distribution is uneven
7. **`rule_plus_ml` produces 0 trades** — threshold combo is too tight; needs threshold search

---

## 3. Leakage Audit

### Feature list status
All features in `ML_NUMERIC_FEATURES` and `ML_CATEGORICAL_FEATURES` (backtest.py:1595–1628) are scan-time features:

| Feature | Direction | Leaky? |
|---------|-----------|--------|
| `ret_1d`, `ret_3d`, `ret_5d`, `ret_10d`, `ret_20d` | PAST price returns | ✅ Clean |
| `spy_ret1`, `spy_ret5`, `spy_ret20` | PAST SPY returns | ✅ Clean |
| `rsi9`, `rsi14`, `macd_hist` | Computed at scan date | ✅ Clean |
| `score`, `coil_pts`, `brk_pts` | Scan-time scoring | ✅ Clean |
| `vix_ts`, `sector_breadth`, `vix_1d_chg` | Market context at scan | ✅ Clean |
| `spy_regime`, `vix_regime` | Computed at scan time | ✅ Clean |
| `confirmed_pullback_gates` | Gate results at scan time | ✅ Clean |
| `candidate_status` | accepted/rejected at scan time | ✅ Clean |
| `h3_return`, `h3_outcome`, `h3_mae`, `h3_mfe` | **Forward outcomes** | In dataframe but NOT in feature list — must stay that way |

**Critical check:** `_ml_design_matrix` only uses `numeric` and `categorical` lists, which are drawn from `ML_NUMERIC_FEATURES`/`ML_CATEGORICAL_FEATURES`. The `h3_*` columns are kept in the frame as labels but never fed to the model. This is correct as-is but must be guarded with a runtime assertion.

### Potential soft leakage
- **`score`** includes `regime_adj` which uses VIX/SPY regime. This is computed at scan time from past data. OK.
- **`confirmed_pullback_gates`** encodes which rule gates passed — this is a scan-time evaluation of past price/volume data. OK.
- **`candidate_status`** could encode selection bias: rejected candidates never got traded, so their outcomes are simulated (backtest runs them anyway). This is acceptable for training but means the model may not generalize to real "rejected at the time" candidates.

---

## 4. Required Changes (Implementation Plan)

### Priority 1: Probability Calibration in train_ml_models.py
**File:** `scripts/train_ml_models.py`  
**Change:** Wrap each classifier with `CalibratedClassifierCV(model, method='isotonic', cv='prefit')` after training. Store both raw model and calibrated model in bundle. Default ON.

**Why:** XGBoost's `predict_proba` outputs are not true probabilities. A model that says 0.62 may actually have 55% empirical win rate. Calibration aligns probability outputs with actual outcomes, making threshold selection meaningful.

**Expected impact:** Better threshold calibration → correct rejection rate → higher expectancy per trade.

### Priority 2: Walk-Forward Validation Report
**File:** `scripts/train_ml_models.py`  
**Change:** Add `--walk-forward-report` flag. When set, run `_ml_purged_walk_forward` from backtest.py on the full dataset and write a fold-by-fold report alongside the standard bundle.

**Why:** Single train/test split with 1 test year is noisy. Walk-forward over 3–5 folds gives a more stable AUC estimate and reveals regime sensitivity.

### Priority 3: Realistic Cost Defaults
**Files:** `backtest.py` (ARG_DEFAULTS), `web/api/backtest.py`  
**Change:** 
- Commission: `0.0` → `1.0` ($1 per trade entry + $1 exit = $2 round trip on 100 shares)
- Add `slippage_bps: float = 5` parameter (5 basis points per side, applied to entry price)
- RL env: add `slippage_bps=5` parameter

**Why:** Zero-cost backtests overstate all return metrics. At 5% annual turnover with $5k positions, $2 round-trip = 4bps friction per trade. At higher frequency, this matters more.

**Realistic assumptions for $5k–$10k accounts:**
- Commission: $1–$2 round trip (IB, Webull zero-commission, but spread cost modeled)
- Slippage: 3–7bps for liquid stocks >$50
- Bad fills: +15bps on entries (buying at ask), -15bps on exits (selling at bid)

### Priority 4: Threshold Search for Expectancy
**File:** `scripts/train_ml_models.py`  
**Change:** Add threshold search over `[0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65, 0.70]` in gate_analysis. Report expectancy, drawdown, and n_trades at each threshold. Flag the threshold that maximizes `expectancy * sqrt(n_trades)` (Kelly-weighted).

**Why:** Current threshold 0.72 for latest model results in 0 rule+ml trades. Need to find the sweet spot between selectivity and trade count.

### Priority 5: Leakage Guard Assertion
**File:** `scripts/train_ml_models.py`  
**Change:** Add runtime check before training that verifies no `h{hold}_*` column (h3_return, h3_outcome, h3_mae, h3_mfe, h3_exit_date, h3_entry, h3_target, h3_stop) appears in `feature_names` list.

### Priority 6: Holdout Validation Script
**New file:** `scripts/validate_holdout.py`  
**Purpose:** Run backtest on a specified date range (default: 2026-05-08 to today) using the model bundle, report stats separately. Never trains or tunes on this data.

### Priority 7: Ticker Reliability Tracking
**New file:** `scripts/ticker_performance_tracker.py`  
**Purpose:** Aggregate per-ticker prediction accuracy from backtest trade logs. Tickers with consistent below-50% win rate from the ML gate (over 20+ predictions) get a reliability score. This informs position sizing in paper/live, not model training.

---

## 5. Target Return Analysis

### Honest current state
| Metric | Value | Source |
|--------|-------|--------|
| win_probability ROC | 0.55 | 2026 test data, latest model |
| ml_only win rate | 56.7% | Gate analysis, test set |
| ml_only expectancy | +0.08%/trade | Gate analysis, test set |
| ml_only profit_factor | 1.051 | Gate analysis, test set |
| annualized at 1 trade/week | ~4.2% | 52 trades × 0.08% expectancy |

### What's needed for 20% annualized
With expectancy X per trade and N trades/year:
- `N × X = 20%` annualized
- At 1 trade/week (52/year): need **+0.38%/trade** expectancy
- At 2 trades/week (104/year): need **+0.19%/trade** expectancy

The stock_universe model (ROC=0.713) tested on 2024 data shows much stronger metrics. If calibration + tighter feature set reproduces ROC ≥ 0.65 on fresh 2026 data, expectancy in the 0.3–0.5% range per trade is plausible.

### Realistic target
**Without retraining improvements:** 5–8% annualized (current model + realistic costs)  
**After calibration + walk-forward training on full dataset:** 12–18% plausible if ROC reaches 0.65+  
**20% target:** Achievable only if out-of-sample ROC ≥ 0.65 AND paper trading confirms 55%+ win rate over 50+ trades  

**Verdict: Do not claim 20% until paper trading confirms it over 50+ live trades.**

---

## 6. Retraining Checklist

### Prerequisites (verify before running)
- [ ] Data end date confirmed (should be close to today, 2026-05-26)
- [ ] `all_tickers.txt` current (no delisted tickers in active list)
- [ ] `ml_models/stock_universe/stock_candidate_training_data.csv` covers through 2026-05-20+
- [ ] Holdout range noted: 2026-05-08 to 2026-05-26 is reserved — never add to training
- [ ] Calibration flag enabled (`--calibrate` default True after code changes)

### Training Commands (run after code changes below are deployed)

#### Option A: Train from existing enriched CSV (fast, ~5 minutes)
```bash
# Use the stock_universe_candidate training data with full feature set
python scripts/train_ml_models.py \
  --input ml_models/stock_universe_candidate_20260512/stock_candidate_training_data.csv \
  --output-dir ml_models/candidate_v2 \
  --hold 3 \
  --ml-probability-threshold 0.60 \
  --ml-expected-return-min -99.0 \
  --ml-large-loss-max 1.0 \
  --n-estimators 600 \
  --max-depth 6 \
  --min-samples-leaf 25 \
  --calibrate
```

#### Option B: Full retrain from raw price data (slow, 2–4 hours)
```bash
python scripts/train_ml_from_stock_data.py \
  --tickers all_tickers.txt \
  --start 2019-01-01 \
  --end 2026-05-07 \
  --output-dir ml_models/full_retrain_20260526 \
  --hold 3 \
  --target-mult 0.9 \
  --stop-mult 1.1 \
  --ml-probability-threshold 0.60 \
  --ml-large-loss-max 0.15 \
  --rebuild-dataset
```

#### Option C: Train from backtest trade log (medium, requires recent backtest)
```bash
# First run backtest to generate trade log:
python backtest.py \
  --tickers all_tickers.txt \
  --start 2020-01-01 \
  --end 2026-05-07 \
  --hold-periods 3 5 10 \
  --primary-hold 3 \
  --target-mult 0.75 \
  --stop-mult 1.0 \
  --account-commission 1.0 \
  --threshold 70 \
  --no-charts

# Then train from that output:
python scripts/train_ml_models.py \
  --input backtest_results_XXXXXXXX_XXXXXX.json \
  --output-dir ml_models/from_backtest_20260526 \
  --hold 3 \
  --calibrate
```

### Validation Commands (run after training)
```bash
# 1. Check training report quality
python -c "
import json
d = json.load(open('ml_models/candidate_v2/training_report.json'))
for mname, mdata in d['models'].items():
    met = mdata.get('metrics', {})
    print(f'{mname}: roc={met.get(\"roc_auc\",\"?\")} brier={met.get(\"brier_score\",\"?\")}')
print('Win rate at gate:', d['gate_analysis']['strategy_comparison'].get('ml_only',{}).get('win_rate'))
"

# 2. Run holdout backtest (NEVER train on this period)
python backtest.py \
  --tickers all_tickers.txt \
  --start 2026-05-08 \
  --end 2026-05-26 \
  --hold-periods 3 5 10 \
  --primary-hold 3 \
  --model-bundle ml_models/candidate_v2/model_bundle.joblib \
  --account-commission 1.0 \
  --no-charts \
  --no-cache

# 3. Run leakage check
python scripts/leakage_check.py \
  --input ml_models/candidate_v2/training_report.json

# 4. Compare paper trading results (if running)
# Check web UI > Analysis tab > Paper trade history
# Filter to trades after 2026-05-08 and check win rate
```

---

## 7. Success Criteria

Before promoting any model to production:
- [ ] win_probability ROC ≥ 0.62 on test set
- [ ] ml_only expectancy ≥ +0.20%/trade on test set
- [ ] ml_only profit_factor ≥ 1.30 on test set
- [ ] No leakage assertion failures
- [ ] Calibration plot shows probabilities within 5% of empirical rates
- [ ] Holdout backtest (2026-05-08+) shows positive expectancy (even with <10 trades)
- [ ] Paper trading: 20+ trades with ≥50% win rate before increasing position size

---

## 8. Files Changed

| File | Change | Status |
|------|--------|--------|
| `ML_PROFIT_PLAN.md` | This document | ✅ Done |
| `scripts/train_ml_models.py` | Add calibration (isotonic/sigmoid), threshold search, leakage guard assertion | ✅ Done |
| `backtest.py` | Add `slippage_bps` param, default commission $1, default slippage 5bps | ✅ Done |
| `web/api/backtest.py` | Realistic cost defaults, slippage, hold_periods default fix | ✅ Done |
| `tradingagents/rl/environment.py` | Add `slippage_bps` param; buy at ask, sell at bid in `_rebalance` | ✅ Done |
| `scripts/validate_holdout.py` | New: holdout validation runner (read-only, no tuning) | ✅ Done |
| `scripts/leakage_check.py` | New: training data leakage audit (bundle + report + CSV) | ✅ Done |
| `web/static/index.html` | Hold periods: [1,2,3,5] → [3,5,10] | ✅ Done |

---

## 9. Remaining Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| win_probability ROC stuck at 0.55 even after calibration | High | Retrain with full stock_universe dataset (6M rows); explore ensemble of strategies |
| Regime shift invalidates 2024-trained features | High | Add regime-conditional evaluation; monitor paper trading by regime |
| Survivorship bias in ticker list | Medium | Active-listed filter already in place; add delisted stock check |
| Overfitting to 2019–2025 bull market | Medium | Walk-forward folds over bear periods (2022, COVID) required |
| Position sizing erases edge at small account | Medium | Kelly/half-Kelly sizing from model; document min account size |
| yfinance data gaps causing NaN features | Low | Imputer handles; leakage check will flag constant columns |
| RL agent exploiting simulator | Medium | RL not used in live path; backtest simulator has regime filtering |
