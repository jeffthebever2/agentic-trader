# Market Regime Prediction System — Design Plan
*Created: 2026-05-26*

## 1. Goal

Replace the current coarse regime handling (3-bucket size factor + 2-bucket ML threshold) with a full probabilistic market-environment engine that:

- Assigns probabilistic scores to 9 regime states
- Computes `regime_confidence` and `crash_risk_score` (numeric, 0–1)
- Controls aggression via `regime_rules` (size_factor, ml_threshold_boost, stop_mult, tp_mult, max_open_trades)
- Enforces `no_trade` in crash/high_vol_bear conditions
- Logs regime state with every trade for per-regime validation
- Requires no live broker changes

## 2. What Already Exists (do not duplicate)

| Component | Location | Status |
|-----------|----------|--------|
| `build_spy_regime()` | backtest.py:1452 | 5-level SPY label (bull/uptrend/sideways/downtrend/bear) |
| `build_combined_regime()` | backtest.py:1487 | Adds crash_risk, high_vol_bear, high_vol_bull, crash_rebound |
| `build_vix_regime()` | backtest.py:1532 | 4-level VIX label |
| `build_vix_term_structure()` | backtest.py:1542 | VIX3M/VIX ratio |
| `build_sector_breadth()` | backtest.py:1560 | 11 SPDR sector 20d breadth |
| `REGIME_SCORE` dict | candidate_ranker.py:34 | String → [0.1,1.0] multiplier |
| `regime_ml_threshold()` | paper_trade_today.py:1578 | Only 2 buckets, weak |
| `regime_size_factor` | paper_trade_today.py:3139 | 3 buckets, ignores high_vol_* |

**Gaps:**
- No probabilistic regime scores (just hard labels)
- No `crash_risk_score` numeric
- No `regime_confidence`
- No SPY 60d return
- No drawdown-from-recent-high feature
- No rebound detection beyond VIX
- No risk-on/risk-off proxy
- No volatility expansion/compression score
- `regime_ml_threshold()` ignores high_vol_bull, crash_rebound
- `regime_size_factor` ignores high_vol_bear, crash_risk → trades at 50% instead of 0%
- `build_combined_regime()` called in backtest but NOT in paper_trade_today.py
- No per-regime ROC/Brier validation (partially there but gated)

## 3. New Feature Set (all leakage-free)

### 3.1 SPY Trend Features
| Feature | Definition |
|---------|-----------|
| `spy_above_sma50` | SPY close > SMA50 |
| `spy_above_sma200` | SPY close > SMA200 |
| `spy_sma50_above_sma200` | SMA50 > SMA200 (golden cross) |
| `spy_ret5` | SPY 5d return (already exists) |
| `spy_ret20` | SPY 20d return (already exists) |
| `spy_ret60` | SPY 60d return (new) |
| `spy_drawdown_20d` | SPY distance below its 20d high (0 = at high) |
| `spy_drawdown_from_ath` | SPY distance below 252d high |

### 3.2 Volatility Features
| Feature | Definition |
|---------|-----------|
| `vix_level` | Raw VIX close |
| `vix_1d_chg` | VIX 1-day change (already exists) |
| `vix_5d_chg` | VIX 5-day change |
| `vix_20d_zscore` | (VIX - 20d mean) / 20d std — expansion/compression |
| `vix_ts` | VIX3M/VIX ratio (already exists) |
| `vol_expansion` | 1 if vix_20d_zscore > 1.5 (volatility expanding) |
| `vol_compression` | 1 if vix_20d_zscore < -1.0 (volatility compressed) |

### 3.3 Market Breadth
| Feature | Definition |
|---------|-----------|
| `sector_breadth` | Fraction of 11 SPDR sectors positive 20d (already exists) |
| `spy_breadth_diverge` | sector_breadth < 0.45 while SPY > SMA200 (hidden weakness) |

### 3.4 Rebound & Gap Risk
| Feature | Definition |
|---------|-----------|
| `spy_rebound_pct` | SPY gain from its 20d low (0 = at low) |
| `spy_gap_pct` | SPY overnight gap today (open vs prior close) |
| `crash_recovery_score` | VIX dropped >20% from 10d max AND SPY up >5% from 10d low |

### 3.5 Risk-On / Risk-Off Proxy
| Feature | Definition |
|---------|-----------|
| `risk_on_score` | sector_breadth × spy_above_sma200 × (1 - vol_expansion) |
| `risk_off_score` | (1 - sector_breadth) × vol_expansion |

## 4. MarketRegimeState Dataclass

```python
@dataclass
class MarketRegimeState:
    # Primary label (from build_combined_regime)
    regime: str          # bull/uptrend/sideways/downtrend/bear/high_vol_bull/
                         # high_vol_bear/crash_risk/crash_rebound/unknown

    # Probabilistic scores (each 0–1, do not need to sum to 1)
    prob_bull:       float   # probability market is in bull/uptrend
    prob_bear:       float   # probability market is in bear/downtrend
    prob_chop:       float   # probability market is choppy/sideways
    prob_high_vol:   float   # probability elevated volatility regime
    prob_crash:      float   # probability crash / extreme risk
    prob_rebound:    float   # probability post-crash rebound
    prob_risk_on:    float   # risk-on environment probability
    prob_risk_off:   float   # risk-off environment probability

    # Aggregate scores
    regime_score:     float  # [0,1] — quality multiplier for candidate ranking
    crash_risk_score: float  # [0,1] — higher = more likely crash/tail event
    regime_confidence: float # [0,1] — how clearly defined the regime is

    # Regime rules (used to adjust sizing, thresholds, stops)
    no_trade:         bool   # True in crash_risk + high crash_risk_score
    size_factor:      float  # multiply base position size (0.0–1.0)
    ml_threshold:     float  # minimum ML probability to accept trade
    stop_mult:        float  # ATR stop multiplier adjustment (1.0 = no change)
    tp_mult:          float  # ATR take-profit multiplier adjustment
    max_open_trades:  int    # maximum concurrent open positions

    # Raw features (for logging + ML feature dict)
    features: Dict[str, float]

    # Timestamp
    as_of_date: str
```

## 5. Regime Rules Table

| Regime | no_trade | size_factor | ml_threshold | stop_mult | tp_mult | max_trades | rationale |
|--------|----------|-------------|-------------|-----------|---------|------------|-----------|
| bull | False | 1.00 | base | 1.0 | 1.0 | 8 | Normal aggression |
| uptrend | False | 0.90 | base | 1.0 | 1.0 | 7 | Early recovery |
| sideways | False | 0.75 | base + 0.03 | 1.0 | 0.9 | 5 | Chop = faster exits |
| downtrend | False | 0.60 | base + 0.04 | 1.1 | 0.85 | 4 | Trend against us |
| bear | False | 0.50 | base + 0.05 | 1.15 | 0.80 | 3 | Reduced exposure |
| high_vol_bull | False | 0.65 | base + 0.03 | 1.2 | 0.90 | 5 | Vol spike in bull |
| high_vol_bear | False | 0.30 | base + 0.08 | 1.25 | 0.75 | 2 | Near-no-trade |
| crash_risk | True | 0.00 | — | — | — | 0 | No new longs |
| crash_rebound | False | 0.55 | base + 0.02 | 1.15 | 1.10 | 4 | Mean-rev edge |
| unknown | False | 0.80 | base | 1.0 | 1.0 | 6 | Slight caution |

`base` = configured ML threshold (e.g. 0.60). `no_trade` candidates get score=0.

## 6. Architecture

```
yfinance SPY/VIX/VIX3M/Sectors daily
         ↓
MarketRegimeEngine.compute(as_of_date)
         ↓
MarketRegimeState
  ├── regime (string label)
  ├── regime_score, crash_risk_score, regime_confidence
  ├── no_trade, size_factor, ml_threshold, stop_mult
  └── features dict (for logging + ML)
         ↓
paper_trade_today.py
  ├── regime_ml_threshold() ← replaced by state.ml_threshold
  ├── regime_size_factor    ← replaced by state.size_factor
  └── no_trade gate         ← new: skip all entries if state.no_trade
         ↓
CandidateRanker.score_one(regime=state.regime, regime_score=state.regime_score)
         ↓
every trade event_log entry now includes:
  regime, regime_score, crash_risk_score, size_factor, ml_threshold
```

## 7. Per-Regime Validation (required, not optional)

For each backtest/WF fold, report:
- `regime_performance`: {regime_label: {n, win_rate, avg_return, max_dd, roc_auc, brier}}
- Comparison: bull vs bear vs high_vol_* shows whether regime adjustments are working
- Flag: if `high_vol_bear` win_rate > `bull` win_rate, regime rules are backwards

## 8. Files to Create / Modify

### New file:
- [x] `tradingagents/screening/market_regime.py` — MarketRegimeEngine, MarketRegimeState, REGIME_RULES, per_regime_validation()

### Modified files:
- [x] `backtest.py` — regime features in `_collect_trades()`: spy_ret60, spy_drawdown_20d, spy_above_sma50/200, spy_golden_cross, vix_20d_zscore, vol_expansion, regime_score, crash_risk_score, risk_on_score, risk_off_score. Also: ML_NUMERIC_FEATURES extended, breakout_v2 score mode, breakout labels.
- [x] `scripts/paper_trade_today.py` — MarketRegimeEngine integrated; regime_ml_threshold() uses MarketRegimeState.ml_threshold; regime_size_factor uses MarketRegimeState.size_factor; no_trade enforcement; max_open_trades enforcement; regime fields on Position + BUY/SELL events
- [x] `tradingagents/portfolio/candidate_ranker.py` — accepts `regime_state: MarketRegimeState`; uses regime_state.regime_score; enforces no_trade
- [x] `tradingagents/screening/__init__.py` — exports MarketRegimeEngine, MarketRegimeState, get_market_regime_state, REGIME_QUALITY_SCORE
- [x] `scripts/train_ml_models.py` — per_regime_diagnostics extended with full metrics; --mode breakout added; ML_NUMERIC_FEATURES_BREAKOUT imported

## 9. Anti-Cheating Checklist

- [x] No future SPY prices in regime features (all using shift/rolling lookback via `si_gate >= 200` guard)
- [x] crash_risk_score computed from data available at scan time only (MarketRegimeEngine uses `as_of_date` gate)
- [x] Per-regime validation on train/WF data only — never on holdout (`_per_regime_diagnostics` operates on training frame)
- [x] Regime rules (size_factor etc.) set by knowledge, not optimized on holdout (REGIME_RULES table hand-coded)
- [x] Holdout window (2026-05-08 → present) never used to tune any threshold
- [x] No weakening of existing risk controls (stop distances not reduced; only size_factor reduced in bad regimes)

## 10. Retraining After This

The new regime features (`spy_ret60`, `vix_20d_zscore`, `spy_drawdown_20d`, `risk_on_score`, `crash_risk_score`) need to be added to `ML_NUMERIC_FEATURES` in backtest.py for inclusion in next retrain. Commands:

```bash
# Full retrain after adding new features to backtest.py
python scripts/retrain_weekly.py \
  --tickers all_tickers.txt \
  --months 84 \
  --hold 3 \
  --executed-weight 20 \
  --min-roc 0.56 \
  --max-brier 0.24

# Validate
python scripts/validation_report.py
```

*Last updated: 2026-05-26*
