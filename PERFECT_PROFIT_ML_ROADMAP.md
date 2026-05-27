# Perfect Profit ML Roadmap
**Created:** 2026-05-26  
**Goal:** Near-perfect realistic profitability — consistent positive expectancy across all regimes, highest achievable Sharpe/Sortino, no fake backtest edge, no weakened risk controls.

---

## 0. Honest Baseline (Current State)

| Metric | Value | Source |
|--------|-------|--------|
| Model bundle created | 2026-05-16 | `ml_models/latest/model_bundle.joblib` |
| Win probability ROC | 0.5479 | `training_report.json` |
| Calibrated | ❌ No | bundle `calibrated: False` |
| Training rows | 471,160 | training_report |
| Test rows | 28,840 | training_report |
| Hold period | 3 days | bundle |
| Probability threshold | 0.72 | bundle thresholds |
| Large-loss cap | disabled (1.0) | bundle thresholds |
| Walk-forward | ✅ `_ml_purged_walk_forward` exists | backtest.py:1747 |
| Regime features | ✅ SPY SMA200 + VIX tiers + sector breadth + VIX term structure | backtest.py |
| Calibration code | ✅ `_calibrate_classifier()` added | train_ml_models.py |
| Holdout validation | ✅ validate_holdout.py | scripts/ |
| Leakage check | ✅ leakage_check.py | scripts/ |
| ML drift alert | ✅ basic (paper trader logs ML_DRIFT_ALERT >15%) | paper_trade_today.py:1037 |
| CandidateRanker | ✅ composite score + allocation weights | tradingagents/portfolio/ |
| ExitManager | ✅ dynamic ATR stop/target + trailing | tradingagents/portfolio/ |
| ATR dollar-risk sizing | ✅ (default 1% risk/trade) | position_sizing.py |

**Bottom line:** ROC=0.5479 means the model barely predicts better than random. Calibration off. Probability threshold 0.72 hardcoded before calibration. Large-loss cap disabled. System infrastructure is solid but the *model itself* is weak.

---

## 1. ML Pipeline Map

```
market data (yfinance)
    │
    ▼
backtest.py: _score_confirmed_pullback_v3() + regime scoring
    │  ← spy_regime, vix_regime, vix_ts, sector_breadth, score, gates
    ▼
candidate rows (scan_date, ticker, score, features, h3_return, h3_outcome, ...)
    │
    ▼
_ml_purged_walk_forward() — expanding window, purge+embargo=hold*1.5+1 days
    │  ← produces OOS win_prob, loss_prob, expected_return for each candidate
    ▼
train_ml_models.py — XGBoost/RF → 5 models:
    • win_probability       (target: h3_return > 0.5%)
    • large_loss_probability (target: h3_return < -3%)
    • expected_return        (regression: h3_return)
    • target_before_stop_prob (target: h3_outcome == TARGET_HIT)
    • timeout_probability    (target: h3_outcome == TIMED_OUT)
    │
    ├─ _calibrate_classifier() → CalibratedClassifierCV(isotonic, cv=prefit)
    ├─ _threshold_search() → scan [0.50–0.70] for best expectancy×√n
    ├─ _check_feature_leakage() → assert no h{N}_return/outcome in feature matrix
    └─ → ml_models/latest/model_bundle.joblib
          + training_report.json
    │
    ▼
paper_trade_today.py: CandidateRanker → position_sizing → ExitManager
    │  → ml_passes_gate() → scan_account_once()
    ▼
positions / trades / SIZING_DECISION audit log
```

---

## 2. Current Gaps → Required Work

### 2.1 Model Quality (CRITICAL)
| Gap | Current State | Fix |
|-----|---------------|-----|
| ROC=0.5479 | barely above random | retrain with more data, better features, calibration |
| Not calibrated | probabilities unreliable | `--calibrate` flag in retrain_weekly.py |
| Threshold 0.72 before calibration | wrong threshold | re-search threshold post-calibration |
| Large-loss cap disabled (1.0) | **all** large-loss candidates pass | set `--ml-large-loss-max 0.35` in retrain |
| Training data: 18 months rolling | ok for trends, misses multiple full cycles | extend to 3 years minimum |
| Single model per target | no ensemble diversity | add voting ensemble (XGB + RF) |

### 2.2 Regime Intelligence (HIGH)
| Gap | Current State | Fix |
|-----|---------------|-----|
| SPY regime = only SMA200 bull/bear | misses chop, trend strength, momentum | add ADX, slope, breadth thresholds |
| No "chop" regime | sideways = false bull or false bear | new regime: `adx<20 + flat_sma200` |
| No crash-risk regime | VIX spike + breadth collapse missed | add `crash_risk` = VIX>35 + breadth<0.25 |
| No mean-reversion vs trend mode | pullback strategy needs trend context | tag each scan_date |
| No per-ticker regime | stock can be in personal bear in bull market | add stock-level regime to features |

### 2.3 High-Volatility Special Mode (HIGH)
Current: VIX regime exists but does NOT reduce entries, size, or tighten holds.
Need: When VIX>25 (elevated) or VIX>35 (crisis):
- Size ≤ 50% of normal
- Require `ml_probability ≥ threshold + 0.08` (stricter gate)
- Use ATR×0.75 stop (tighter) — less room for wide stops in vol spikes
- Max hold = 2 days (don't sit through vol chaos)
- If VIX>35: **no new entries** unless in crash-rebound regime

### 2.4 Ticker Reliability (MEDIUM)
Current: ML drift tracks aggregate predicted vs actual. No per-ticker stats.
Need:
- Per-ticker win rate rolling (last 20 trades)
- Per-ticker reliability score [0,1]: smoothed win rate vs model's avg
- Feed `ticker_reliability` as ML feature
- Paper trader: reduce size by `(1 - reliability_penalty)` for unreliable tickers

### 2.5 Feature Stability & Model Drift (MEDIUM)
Current: `ML_DRIFT_ALERT` fires when |predicted_wr - actual_wr| > 0.15 (last 10 trades).
Need:
- Per-feature mean/std tracking: alert if feature distribution shifts >2σ
- PSI (Population Stability Index) per feature on each run
- Model age staleness alert: if model >30d old and accuracy declining, trigger retrain
- Drift report in `ml_drift.json` per scan cycle

### 2.6 Safe No-Trade Mode (MEDIUM)
Current: Only `stale_data_{age_days}d` gate in `_passes_freshness_gate()`.
Need a `SafeTradeGuard` that returns `(allow_trade: bool, reason: str)` checking:
1. Stale data (`signal_date != today`)
2. Hostile regime: VIX>35 + bear SPY
3. Model drift too high: |predicted_wr - actual_wr| > 0.20 (rolling 15 trades)
4. Win rate collapse: rolling 10-trade WR < 30%
5. Model age: bundle created_at > 45 days ago
6. Portfolio drawdown > `--max-portfolio-drawdown`

### 2.7 Validation Reports (MEDIUM)
Current: `validate_holdout.py` runs backtest on holdout. No cross-period comparison.
Need `scripts/validation_report.py`:
- Load training_report.json + holdout backtest results
- Compare: train ROC, walk-forward OOS WR, holdout WR, paper WR
- Flag: holdout WR < walk-forward WR - 10% → potential overfitting
- Flag: paper WR < holdout WR - 10% → live slippage/execution degradation
- Output: `validation_summary.json` + terminal table

### 2.8 Retrain Pipeline Quality (LOW)
Current: `retrain_weekly.py` runs 18-month backtest → train. Does not:
- Pass `--calibrate` to train_ml_models.py
- Set `--ml-large-loss-max 0.35`
- Run leakage check post-train
- Run holdout validation post-train
- Save retrain log to `ml_models/retrain_history.jsonl`

---

## 3. Implementation Plan

### Phase 1 — Model Quality (Do first; everything else depends on good predictions)

#### 3.1 Fix retrain_weekly.py
**File:** `scripts/retrain_weekly.py`  
**Changes:**
- Add `--calibrate` to train_cmd
- Set `--ml-large-loss-max 0.35`  
- Set `--ml-expected-return-min -0.01`
- Extend window from 18 → 36 months (`--months 36`)
- Add post-train steps: leakage_check.py → validate_holdout.py
- Save retrain metadata to `ml_models/retrain_history.jsonl`
- Add `--min-risk-reward 1.5` to backtest_cmd

#### 3.2 Extend SPY regime granularity  
**File:** `backtest.py`  
**Change `build_spy_regime()` to return 5 levels:**
```python
def build_spy_regime(spy_df):
    sma50  = spy_df["Close"].rolling(50).mean()
    sma200 = spy_df["Close"].rolling(200).mean()
    adx    = _calc_adx(spy_df, 14)   # new helper
    close  = spy_df["Close"]
    regime = pd.Series("unknown", index=spy_df.index)
    # Strong trend (ADX>25)
    regime[close > sma200 * 1.02] = "bull"
    regime[close < sma200 * 0.98] = "bear"
    # Weak trend near SMA200
    regime[(close >= sma200 * 0.98) & (close <= sma200 * 1.02)] = "sideways"
    # Crash risk: VIX>35 + bear
    # (applied in build_combined_regime using VIX data)
    return regime
```
Also add `build_combined_regime(spy_df, vix_df)` → `crash_risk` | `high_vol_bear` | `high_vol_bull` | `bull` | `bear` | `sideways`

#### 3.3 High-volatility mode in paper trader
**File:** `scripts/paper_trade_today.py`  
**In `scan_account_once()` before entry loop:**
```python
hv_mode = vix_level > 25  # set when fetching spy_regime
crisis_mode = vix_level > 35
if crisis_mode:
    # No new entries in pure crash — wait for rebound signal
    account.log_event({"type": "NO_TRADE_MODE", "reason": "crisis_vix", "vix": vix_level})
    return {"bought": 0, "sold": sold, "skipped": len(candidates)}
if hv_mode:
    # Tighten gates
    effective_min_prob = min_prob_threshold + 0.08
    effective_size_factor = combined_size_factor * 0.5
    max_hold_override = 2  # days
```

#### 3.4 Ticker reliability feature
**File:** `tradingagents/portfolio/ticker_reliability.py` (new)  
**Purpose:** Compute per-ticker rolling reliability score from paper trade history.
```python
def get_ticker_reliability(ticker: str, trades: list, n: int = 20) -> float:
    """Returns [0,1]. 1.0 = always wins; 0.5 = at market baseline."""
    ticker_trades = [t for t in trades if t.get("ticker") == ticker and "pnl" in t][-n:]
    if len(ticker_trades) < 3:
        return 0.5  # no data → neutral
    wr = sum(1 for t in ticker_trades if t["pnl"] > 0) / len(ticker_trades)
    # Blend with prior of 0.5
    blend_weight = min(1.0, len(ticker_trades) / 10)
    return 0.5 * (1 - blend_weight) + wr * blend_weight
```
Used in `CandidateRanker` as optional `ticker_reliability` multiplier on composite score.

#### 3.5 Safe no-trade guard
**File:** `tradingagents/portfolio/safe_trade_guard.py` (new)  
**Used in:** `scan_account_once()` before the candidate loop  
**Checks:** model drift, win streak collapse, model age, crisis VIX, hostile regime, portfolio drawdown  

#### 3.6 Validation report script
**File:** `scripts/validation_report.py` (new)  
**Compares:** train metrics → walk-forward OOS → holdout → paper trading (from event_log.json)  
**Output:** JSON + terminal table  

### Phase 2 — Feature & Model Improvements

#### 3.7 Add stock-level regime feature
**File:** `backtest.py`  
In `_score_confirmed_pullback_v3()` compute:
- `stock_vs_spy_20d`: stock 20d return - SPY 20d return  
- `stock_regime`: `"outperforming"` | `"neutral"` | `"underperforming"`  
Add to `ML_CATEGORICAL_FEATURES`

#### 3.8 Add crash-risk regime
When `vix_level > 35 and spy_regime == "bear"` → `vix_regime = "crash_risk"` → 0 entries.  
When `vix_level > 35 but spy_regime starts recovering` → `vix_regime = "crash_rebound"` → strong size reduction + high probability gate.

#### 3.9 Feature PSI monitoring
**File:** `tradingagents/portfolio/feature_monitor.py` (new)  
On each training run, compute PSI per feature vs prior month distribution.  
PSI > 0.25 = feature likely drifted → flag for review, don't retrain blindly.

#### 3.10 Ensemble upgrade
In `train_ml_models.py`, when XGBoost available:
- Train XGBoost + RandomForest separately
- Calibrate each
- Ensemble final probability = weighted average (XGB 0.6 + RF 0.4)
- Report per-model ROC + final ensemble ROC

### Phase 3 — Continuous Improvement Loop

#### 3.11 Retrain checklist enforcement
`retrain_weekly.py` enforces:
1. Leakage check passes before bundle swap
2. Holdout ROC ≥ 0.56 (minimum viability)
3. Calibration Brier score < 0.24
4. No features with PSI > 0.25
5. Bundle swapped only if checks pass; old bundle backed up

#### 3.12 Paper vs backtest comparison
After each week of paper trading, `validation_report.py` compares:
- Paper WR vs walk-forward WR → if gap > 15%, flag degradation
- Paper expectancy vs model expected_return → if gap > 0.5%, flag
- Per-regime paper WR → identify regime-specific failure modes

---

## 4. Retraining Commands

**WARNING: Never run on data after 2026-05-07 (current holdout start). Holdout = 2026-05-08 → 2026-05-26.**

### Option A: Retrain from enriched training CSV (fastest)
```bash
python scripts/train_ml_models.py \
  --input ml_models/stock_universe_candidate_20260512/training_data_enriched.csv \
  --output-dir ml_models/retrain_$(date +%Y%m%d) \
  --hold 3 \
  --calibrate \
  --n-estimators 600 \
  --max-depth 6 \
  --min-samples-leaf 20 \
  --ml-probability-threshold 0.60 \
  --ml-large-loss-max 0.35 \
  --ml-expected-return-min -0.01
# Then run leakage check:
python scripts/leakage_check.py --bundle ml_models/retrain_$(date +%Y%m%d)/model_bundle.joblib
# Then validate on holdout:
python scripts/validate_holdout.py --start 2026-05-08 --end 2026-05-26
```

### Option B: Full retrain from backtest (freshest data, ~30min)
```bash
python retrain_weekly.py \
  --months 36 \
  --output-dir ml_models/retrain_$(date +%Y%m%d) \
  --hold 3 \
  --n-estimators 600
# Note: retrain_weekly.py will need Phase 1 changes (--calibrate, --ml-large-loss-max 0.35)
```

### Option C: Dry run to see commands
```bash
python retrain_weekly.py --dry-run --months 36
```

### Validation steps after any retrain:
1. `python scripts/leakage_check.py` — must exit 0
2. Check `training_report.json`: win_roc ≥ 0.56, calibrated=true, brier_after < 0.24
3. `python scripts/validate_holdout.py` — check holdout WR vs walk-forward WR
4. **Never tune thresholds using holdout output** — once you look at it, it's used up

---

## 5. Target Metrics

| Metric | Current | Minimum Viable | Aspirational |
|--------|---------|---------------|-------------|
| Win probability ROC | 0.5479 | 0.58 | 0.68+ |
| Calibration (Brier) | ~0.25 (est.) | < 0.24 | < 0.20 |
| Paper WR (3d hold) | unknown | > 55% | > 62% |
| Annualized return (honest) | unknown | 5-10% | 20%+ |
| Max drawdown | unknown | < 15% | < 8% |
| Sharpe (annual) | unknown | > 0.8 | > 1.5 |
| Expectancy/trade | unknown | > +0.15% | > +0.40% |
| Bear regime WR | unknown | > 50% | > 55% |
| High-vol regime WR | unknown | > 48% | > 55% |

**Note:** 20%+ annualized is realistic ONLY IF win_roc ≥ 0.65 + calibrated + ATR sizing + no leakage. At ROC=0.5479 expect 5-8% pre-regime-optimization.

---

## 6. Files Impacted

| File | Change | Phase | Status |
|------|--------|-------|--------|
| `PERFECT_PROFIT_ML_ROADMAP.md` | This document | 0 | ✅ Done |
| `scripts/retrain_weekly.py` | Add --calibrate, --ml-large-loss-max 0.35, 36mo window, leakage+holdout post-checks, quality gates, history log | 1 | ✅ Done |
| `backtest.py` | Extended build_spy_regime() to 5 levels + build_combined_regime() with crash_risk/high_vol tiers | 1 | ✅ Done |
| `scripts/paper_trade_today.py` | SafeTradeGuard (6 halt checks), high-vol mode (VIX>25: +8% prob gate, 50% size), crisis no-trade (VIX>35), VIX fetched per scan cycle | 1 | ✅ Done |
| `tradingagents/portfolio/ticker_reliability.py` | New: per-ticker rolling reliability [0,1] with blend prior + size_multiplier() | 1 | ✅ Done |
| `tradingagents/portfolio/safe_trade_guard.py` | New: no-trade guard with crisis_vix, hostile_regime, dd, model_drift, wr_collapse, stale_model | 1 | ✅ Done |
| `scripts/validation_report.py` | New: train→walk-forward→holdout→paper comparison with degradation flags | 1 | ✅ Done |
| `tradingagents/portfolio/candidate_ranker.py` | Added ticker_reliability multiplier, crash_risk/high_vol regime scores, trades= param for rank() | 1 | ✅ Done |
| `backtest.py` | Extended build_spy_regime 5-level + build_combined_regime crash_risk/high_vol + crash_rebound detection + stock_regime feature per candidate | 2 | ✅ Done |
| `tradingagents/portfolio/feature_monitor.py` | New: PSI per-feature stability (compute_psi_report, gates, print_report) | 2 | ✅ Done |
| `scripts/train_ml_models.py` | Ensemble XGB+RF win model (60/40 weighted), per-model calibration, PSI check post-train, feature_stats reference snapshot in bundle | 2 | ✅ Done |
| `scripts/retrain_weekly.py` | Retrain checklist: leakage + ROC + Brier + PSI gates before bundle swap | 3 | ✅ Done (all 4 gates enforced) |
| `scripts/improvement_loop.py` | New: continuous improvement loop — validate → assess retrain need → retrain → post-compare → log; cron-schedulable | 3 | ✅ Done |
| `scripts/paper_trade_today.py` | Live feature drift tracking per scan cycle; ensemble win prob (XGB+RF) in predict_ml(); SafeTradeGuard; high-vol mode | 3 | ✅ Done |
| `tradingagents/rl/environment.py` | Fixed double-slippage reward bug; enforced max_position_size on target fractions; documented close-price look-ahead limitation | 3 | ✅ Done |

---

## 7. Success Criteria (Checkable)

**Infrastructure (code complete, pending first retrain):**
- [x] `SafeTradeGuard` active: crisis_vix, hostile_regime, drawdown, model_drift, wr_collapse, staleness
- [x] Crisis mode: VIX > 35 → zero new entries (`SafeTradeGuard.check()`)
- [x] High-vol mode: VIX 25-35 → 50% size + 8% probability gate (`SafeTradeGuard.high_vol_adjustments()`)
- [x] Safe no-trade guard active and logging events to event_log.json
- [x] Validation report comparing all 4 periods — `scripts/validation_report.py`
- [x] Retrain checklist enforced: leakage + ROC≥0.56 + Brier<0.24 + PSI=0 fails before bundle swap
- [x] Continuous improvement loop — `scripts/improvement_loop.py` (cron-schedulable)
- [x] Ticker reliability scores computed and fed to CandidateRanker
- [x] Feature PSI monitoring: live candidates vs training reference (per scan cycle)
- [x] Ensemble XGB+RF win model in training + inference
- [x] stock_regime feature added to ML_CATEGORICAL_FEATURES
- [x] crash_rebound regime detection in build_combined_regime()
- [x] RL double-slippage bug fixed
- [x] feature_stats reference snapshot saved in model bundle for live drift comparison

**Pending first honest retrain:**
- [ ] Model calibrated: `bundle["calibrated"] == True`  ← run `retrain_weekly.py --calibrate`
- [ ] Win ROC ≥ 0.58 on walk-forward OOS               ← currently 0.5479
- [ ] Brier score < 0.24 after calibration               ← currently uncalibrated
- [ ] No leakage: `leakage_check.py` exits 0             ← currently exits 1 (not calibrated)
- [ ] Large-loss cap active in bundle: `ml_large_loss_max == 0.35` ← currently 1.0 in bundle
- [ ] Holdout validation run and reported (not used for tuning)
- [ ] Paper vs walk-forward WR gap tracked (requires paper trading data)

**To trigger first retrain:**
```bash
python scripts/improvement_loop.py  # or python scripts/retrain_weekly.py --dry-run to preview
```

---

## 8. Anti-Cheating Rules

1. **Holdout data is read-once diagnostic only.** Once you tune against it, discard it and define new holdout.
2. **Never improve train-period metrics as a proxy for live performance.** Backtest on train data = memorization check, not profitability proof.
3. **All claimed WRs must come from purged walk-forward or unseen holdout.** Paper trading results on live data are the most honest.
4. **Regime rules must be derived from training period only.** Don't add a regime gate specifically because it would have avoided a recent holdout loss.
5. **Cost assumptions must be conservative:** $1 commission + 10bps slippage round-trip minimum. Never remove costs to improve numbers.
6. **Feature additions require leakage check.** Every new feature runs through `leakage_check.py` before training.
