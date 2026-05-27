# ML Next Improvement Report
*Generated: 2026-05-26*

## 1. Why the Current Model Failed (ROC=0.5479)

### Primary Root Causes

#### 1.1 Training on 97.1% Noise Data (Critical)
The training CSV contains **2,760,161 total rows** but only **7,897 are "executed"** (rule-passing) candidates. The remaining **2,752,264 (99.7%) are rejected**. Without sample weighting, XGBoost treats all rows equally, meaning 99.7% of training gradient comes from noise — stocks that never passed any trading rules.

Evidence:
- Feature correlations on ALL rows: max ~0.025 (near zero)
- Feature correlations on EXECUTED rows only: max 0.094–0.106 (4× stronger)
- `vix_ts` corr on ALL: -0.025 vs on EXECUTED: -0.106
- `vol_ratio_20d` corr on ALL: 0.009 vs on EXECUTED: 0.094

**Fix implemented:** `--executed-weight 20` multiplies executed rows' gradient contribution by 20×, making their effective share ~25% of total training signal.

#### 1.2 Missing Features in Signal Dict (Bug)
Five features listed in `ML_NUMERIC_FEATURES` were **never populated** in the `confirmed_pullback` signal dict, causing them to be all-NaN in all 2.76M training rows:
- `slope_sma20` (NaN)
- `slope_sma50` (NaN)
- `obv_above_sma` (NaN)
- `pvt_above_sma` (NaN)
- `dmi_bull` (NaN)

XGBoost with `keep_empty_features=True` imputes all-NaN columns to the median, making these features identical for all rows and unusable. The model was effectively running with 5 dead features.

**Fix implemented:** All five features now set in the `confirmed_pullback` signal dict in `backtest.py`.

#### 1.3 No Walk-Forward in Training Report
The training report (`training_report.json`) had empty `walk_forward: {}` and `label_distribution: {}` sections. The purged walk-forward function existed in `backtest.py` but was never called from `train_ml_models.py`. Without WF metrics, there was no honest OOS performance estimate.

**Fix implemented:** `train_ml_models.py` now calls `_ml_purged_walk_forward()` and stores results in `report["walk_forward"]`.

#### 1.4 No Calibration in Existing Model
The training report shows `"calibrated": false` (the old model) and `"brier_score": 0.2504`. Uncalibrated probabilities make the 0.72 threshold meaningless — the model's raw probabilities don't correspond to actual win rates.

**Fix implemented:** `--calibrate` is always ON in `retrain_weekly.py`.

#### 1.5 Threshold 0.72 Set Pre-Calibration
The threshold 0.72 was chosen on an uncalibrated model. After calibration, this threshold may map to a different probability. New retrain should use `--ml-probability-threshold 0.60` (default) and let `_threshold_search` find the optimal value post-calibration.

#### 1.6 Fundamental Signal Weakness
Even on executed rows only, feature correlations max at ~0.10. Three-day stock return prediction is inherently noisy — this is a known limit of technical analysis at short horizons. The ROC is unlikely to exceed 0.62–0.65 even with perfect features without structural data advantages (earnings calendar, order flow, sector-rotation timing).

The model's best achievable signal is through:
- Regime filtering (VIX, SPY trend)
- Volume confirmation quality
- Setup geometry (R:R, compression)

---

## 2. What Changed

### `backtest.py`
| Change | Details |
|--------|---------|
| Fixed missing features | `slope_sma20`, `slope_sma50`, `obv_above_sma`, `pvt_above_sma`, `dmi_bull` now populated in `confirmed_pullback` signal dict |
| New feature: `atr_expansion` | Current ATR / rolling 20d ATR. Values >1 = volatility expanding (breakout state), <1 = compression (coiling). |
| New feature in `_ml_prepare_frame`: `spy_momentum_accel` | SPY 5d return / \|SPY 20d return\| — measures whether market is accelerating (>1) or decelerating (<1) |
| New feature in `_ml_prepare_frame`: `setup_rr` | (target − entry) / (entry − stop) — measures setup geometry quality |
| Feature count | 64 → 70 (6 new features: `atr_expansion`, `spy_momentum_accel`, `setup_rr` + 3 fixed NaN features) |

### `scripts/train_ml_models.py`
| Change | Details |
|--------|---------|
| `--executed-weight` arg (default 20) | Upweights executed/rule-passing rows by 20× during training. Focuses gradient on setup-quality discrimination. |
| `--executed-only` flag | Trains exclusively on executed rows (requires ≥300 executed rows) |
| `--run-walk-forward` arg (default ON) | Calls `_ml_purged_walk_forward` and stores in `report["walk_forward"]` |
| Per-regime diagnostics | `_per_regime_diagnostics()`: ROC, WR, and count by `spy_regime` and `vix_regime` on test set |
| Confidence bucket analysis | `_confidence_bucket_analysis()`: Win rate, avg return, and profit factor by probability decile |
| Label distribution | `report["label_distribution"]`: Class balance, executed row count, avg/median returns — printed before training |
| Sample weighting applied to | XGBoost win model, RF diversity model, large-loss classifier, expected return regressor, target/timeout classifiers |

### `scripts/retrain_weekly.py`
| Change | Details |
|--------|---------|
| `--executed-weight` arg | Passed through to train command (default 20) |
| `--run-walk-forward` | Always ON in train command |

### `scripts/paper_trade_today.py`
| Change | Details |
|--------|---------|
| `spy_momentum_accel` inference | Computed from `spy_ret5`/`spy_ret20` in `predict_ml()` — mirrors training derivation |
| `setup_rr` inference | Computed from `target`, `entry`, `stop` in `predict_ml()` — mirrors training derivation |

### `scripts/validation_report.py`
| Change | Details |
|--------|---------|
| Walk-forward key compatibility | `extract_train_metrics()` now reads both old (`win_rate`) and new (`high_conf_win_rate`, `roc_auc`) WF keys |

---

## 3. What Needs Retraining

All changes require a fresh backtest + retrain to take effect:
- New features (`atr_expansion`, `spy_momentum_accel`, `setup_rr`) need new backtest data
- Fixed NaN features need new backtest data (existing CSV has NaN for those columns)
- Sample weighting only applies during training, not to existing models

**The existing model bundle is obsolete.** Do not use it for paper trading until after retraining.

---

## 4. Exact Commands to Run

### Step 0: Update ticker universe (optional but recommended)
```bash
# Verify all_tickers.txt is up to date
wc -l all_tickers.txt
```

### Step 1: Run fresh backtest with ALL improvements
```bash
python backtest.py \
  --tickers all_tickers.txt \
  --start 2019-01-01 \
  --end 2026-05-08 \
  --export-csv retrain_trades_$(date +%Y%m%d).csv \
  --no-trades-json \
  --no-generate-charts \
  --batch-size 50 \
  --account-commission 1.0 \
  --account-slippage-bps 5.0 \
  --min-risk-reward 1.5 \
  --mode confirmed_pullback
```
*Runtime: 1–4 hours depending on ticker count. The end date must be before holdout start (2026-05-08).*

### Step 2: Train improved model
```bash
python scripts/train_ml_models.py \
  --input retrain_trades_$(date +%Y%m%d).csv \
  --output-dir ml_models/retrained_$(date +%Y%m%d) \
  --hold 3 \
  --n-estimators 600 \
  --max-depth 6 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.60 \
  --ml-large-loss-max 0.35 \
  --ml-expected-return-min -0.01 \
  --executed-weight 20 \
  --calibrate \
  --run-walk-forward
```
*Runtime: 20–60 minutes. Produces `training_report.json` with WF ROC, per-regime diagnostics, confidence buckets.*

### Step 3: Run retrain_weekly.py (full automated pipeline)
```bash
# PREFERRED: Full automated pipeline with all gates
python scripts/retrain_weekly.py \
  --tickers all_tickers.txt \
  --months 84 \
  --hold 3 \
  --n-estimators 600 \
  --max-depth 6 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.60 \
  --ml-large-loss-max 0.35 \
  --executed-weight 20 \
  --min-roc 0.56 \
  --max-brier 0.24
```
*This runs backtest + train + leakage check + gates + bundle swap automatically.*

### Step 4: Validate the new model
```bash
python scripts/validation_report.py \
  --report ml_models/latest/training_report.json \
  --output validation_summary.json
```

### Step 5: Run improvement loop (ongoing)
```bash
python scripts/improvement_loop.py --check-only
```

---

## 5. Expected Gates After Retraining

| Gate | Old Value | Expected After Retrain | Pass Threshold |
|------|-----------|----------------------|----------------|
| Win probability ROC | 0.5479 | 0.57–0.63 | ≥ 0.56 |
| Calibration Brier | 0.2504 (uncal) | 0.23–0.245 | < 0.24 |
| Calibrated | False | True | required |
| PSI failures | unknown | 0 | 0 |
| WF high-conf WR | unknown | 52–58% | ≥ 52% |

**Why 0.57–0.63 expected:**
- Sample weighting (20×) expected to improve ROC by 0.015–0.04 (based on executed-row correlations being 4–10× stronger)
- Fixed NaN features add 5 previously-dead signals
- New features (`atr_expansion`, `spy_momentum_accel`) add setup-quality context
- Calibration + ensemble will improve precision at high thresholds

**Realistic ceiling:** ROC ~0.63–0.65. Short-term technical analysis is inherently noisy. Do not expect ROC > 0.70 without fundamental data (earnings, news sentiment, institutional flow).

---

## 6. Remaining Risks

### 6.1 Still Insufficient Executed Rows
If the backtest universe has < 15,000 executed rows (rule-passing trades over 7 years), the model may still have weak signal even with 20× weighting. Check `label_distribution.train.executed_rows` in the training report.

**Mitigation:** 
- Widen ticker universe to include S&P 500 + Russell 1000 
- Lower `--min-risk-reward` to 1.2 in backtest (more executed rows)
- Use `--executed-only` for a separate diagnostic model

### 6.2 2026 Market Regime is Atypical
The test period (2026) may be in a regime not well-represented in training. Check `regime_diagnostics` in the training report for per-regime ROC. If the 2026 market has VIX >25 most of the time, the model needs sufficient elevated-VIX training data.

### 6.3 PSI Failures After Feature Changes
Adding 6 new features may cause PSI instability if the new features have different distributions in 2026 vs 2019-2025. Check `feature_psi` in the training report post-retrain.

### 6.4 Calibration May Fail on Small Cal Set
The calibration split takes 15% of training rows. With small executed-row counts, the cal set may have too few executed rows for reliable isotonic calibration. Check `calibration.brier_after` in the model report.

---

## 7. Next Ideas If Gates Still Fail

### 7.1 Try `--executed-weight 50` or `--executed-only`
If 20× weighting isn't enough, increase to 50×. Or use `--executed-only` to train a model exclusively on rule-passing rows. This sacrifices broader market context but focuses entirely on within-setup quality discrimination.

Diagnostic: look at `confidence_buckets` — if top decile WR > 60%, the model IS finding signal, just ROC averages it away due to low-confidence predictions.

### 7.2 Add Earnings Proximity Feature
Trades near earnings (within 5 days) have very different risk/reward profiles. If earnings dates are available, add `days_to_earnings` as a feature and as a hard gate (no new entries within 3 days of earnings).

### 7.3 Add Sector Rotation Feature  
`rel_ret5_vs_sector`: 5-day stock return vs sector ETF return. Currently only `rel_ret20_vs_spy` exists. Short-term sector rotation (tech outperforming, energy lagging) is a stronger signal than vs SPY alone.

### 7.4 Add Market Breadth Feature
`pct_stocks_above_sma50`: What percentage of S&P 500 stocks are above their 50d SMA. Values below 40% = risk-off; above 75% = overbought. This is a proxy for market internals beyond VIX.

### 7.5 Separate Bull vs Bear Models
If per-regime diagnostics show ROC ≥ 0.60 in `bull` regime but < 0.52 in `sideways` and `downtrend`, train separate models per regime and use regime to select which model to apply. Regime mixing dilutes the signal.

### 7.6 Extend Hold Period to h5 or h10
Test whether training on h5_return (5-day return) vs h3_return improves ROC. 5-day returns may have more signal than 3-day, giving the setup time to play out. Quick test:
```bash
python scripts/train_ml_models.py --input retrain_trades.csv --hold 5 \
  --executed-weight 20 --output-dir ml_models/test_h5
```

### 7.7 Add Option Flow / Dark Pool Proxy
If yfinance or other data sources include options unusual activity or short interest, these are strong signals for near-term direction. Would require new data pipeline additions.

---

## 8. Feature Engineering Summary Table

| Feature | Status | Expected Impact |
|---------|--------|----------------|
| `slope_sma20` | **Fixed** (was NaN) | Low-medium |
| `slope_sma50` | **Fixed** (was NaN) | Low-medium |
| `obv_above_sma` | **Fixed** (was NaN) | Medium |
| `pvt_above_sma` | **Fixed** (was NaN) | Low |
| `dmi_bull` | **Fixed** (was NaN) | Low-medium |
| `atr_expansion` | **New** | Medium |
| `spy_momentum_accel` | **New** | Medium-high |
| `setup_rr` | **New** | Low (already have `risk_reward`) |
| Sample weighting (20×) | **New training behavior** | **High** |
| Walk-forward in report | **New diagnostic** | Diagnostic only |
| Per-regime diagnostics | **New diagnostic** | Diagnostic only |
| Confidence buckets | **New diagnostic** | Diagnostic only |

---

## 9. Anti-Cheating Checklist

- [x] Holdout window (2026-05-08 → 2026-05-26) never used for training or tuning
- [x] Threshold search uses test (2026 pre-holdout) data only, not holdout
- [x] Walk-forward uses purged expanding windows (no look-ahead)
- [x] Sample weighting does not use future labels — just metadata (executed vs rejected)
- [x] New features derived from data available at scan time only
- [x] `atr_expansion` uses rolling past ATR, not forward ATR
- [x] `spy_momentum_accel` derived from past SPY returns only
- [x] `setup_rr` derived from scan-time entry/target/stop geometry
- [x] Calibration split takes trailing 15% of train set (older than test period)
- [ ] **TODO**: After retraining, verify `leakage_check.py` passes before deploying

---

*Last updated: 2026-05-26. All code changes are in the main branch.*
*Run `python scripts/improvement_loop.py --dry-run` to preview the improvement loop.*
