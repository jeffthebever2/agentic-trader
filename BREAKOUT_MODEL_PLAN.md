# Breakout Detection & Big-Move Prediction — Design Plan
*Created: 2026-05-26*

## 1. Goal
Build a breakout scanner that:
- Scores candidates 0-100 on breakout quality
- Outputs `breakout_type` (range_breakout, volume_breakout, gap_continuation, trend_continuation, failed_breakout_risk)
- Predicts probability outputs: `breakout_success_probability`, `expected_move_3d`, `expected_move_5d`, `expected_move_10d`, `failed_breakout_probability`, `large_loss_probability`
- Provides levels: entry, stop, take-profit, invalidation, confidence
- Validates against failed breakouts (not just winners)
- Uses walk-forward validation (no holdout leakage)

## 2. Where This Fits in the Pipeline

```
yfinance/Alpha Vantage OHLCV
         ↓
backtest.py pre_compute_features()
         ↓
_score_breakout_v2() [NEW — score_mode="breakout_v2"]
         ↓
BreakoutScanner.score_one() [tradingagents/screening/breakout_scanner.py]
         ↓
ML_NUMERIC_FEATURES_BREAKOUT → train_ml_models.py (breakout-specific model)
         ↓
model_bundle_breakout.joblib
         ↓
paper_trade_today.py --mode breakout [uses BreakoutScanner for live screening]
```

## 3. Breakout Feature Set (all leakage-free)

### 3.1 Resistance & High Proximity
| Feature | Definition | Why |
|---------|-----------|-----|
| `pct_from_20d_high` | (close - 20d high) / 20d high | Near 20d high = potential breakout |
| `pct_from_52w_high` | (close - 52w high) / 52w high | Near 52w high = major resistance |
| `pct_from_50d_high` | (close - 50d high) / 50d high | Mid-term resistance zone |
| `near_resistance` | 1 if within 2% of any key level (20d/52w/round number) | Binary confirmation |

### 3.2 Volatility Compression → Expansion
| Feature | Definition | Why |
|---------|-----------|-----|
| `bb_width` | (BB upper - BB lower) / BB mid | Bollinger Band squeeze = coil |
| `bb_width_pct10` | bb_width percentile rank (10d window) | Low rank = unusually tight |
| `atr_compression` | 5d ATR / 20d ATR (< 0.8 = compressed) | True range narrowing |
| `atr_expansion` | Today ATR / 20d avg ATR (> 1.2 = expanding) | Day-of-breakout confirmation |
| `range_contraction_5_20` | 5d range / 20d range | Same as SwingScreener |
| `keltner_squeeze` | BB inside Keltner = 1 | Classic squeeze setup |

### 3.3 Volume Confirmation
| Feature | Definition | Why |
|---------|-----------|-----|
| `vol_surge_1d` | today vol / 20d avg vol | Demand surge = institutional |
| `vol_surge_3d` | 3d avg vol / 20d avg vol | Accumulation over days |
| `vol_dryup_5d` | prior 5d avg vol / 20d avg vol (< 0.8 = quiet) | Contraction before explosion |
| `vol_price_confirm` | vol_surge_1d × abs(ret_1d) | High vol + big move = confirmation |
| `obv_slope_5d` | OBV change over 5 days | Accumulation direction |

### 3.4 Trend Alignment
| Feature | Definition | Why |
|---------|-----------|-----|
| `above_sma20`, `above_sma50`, `above_sma200` | Binary | Trend structure |
| `sma_alignment` | sma20 > sma50 > sma200 = 1 | Full uptrend alignment |
| `sma50_slope_5d` | SMA50 change / ATR (normalized) | Trend accelerating/decelerating |
| `price_vs_ema9` | (close - EMA9) / ATR | Short-term momentum |

### 3.5 Relative Strength
| Feature | Definition | Why |
|---------|-----------|-----|
| `rel_strength_20d` | stock 20d return − SPY 20d return | Leading vs lagging market |
| `rel_strength_5d` | stock 5d return − SPY 5d return | Recent RS improvement |
| `rs_momentum` | rel_strength_5d > rel_strength_20d (improving RS) | RS trending up = bullish |

### 3.6 Momentum Quality
| Feature | Definition | Why |
|---------|-----------|-----|
| `rsi14_level` | RSI14 value | Overbought/oversold context |
| `rsi_slope_3d` | RSI14 change over 3d | Momentum building |
| `macd_hist` | MACD histogram value | Trend momentum |
| `macd_hist_slope3` | MACD hist change over 3d | Momentum acceleration |
| `price_momentum_20d` | 20d return zscore vs universe | Relative momentum rank |

### 3.7 Failed Breakout Warning
| Feature | Definition | Why |
|---------|-----------|-----|
| `prev_breakout_failed` | 1 if stock had vol surge + high but returned to base within 5d | Recidivism pattern |
| `upper_wick_pct` | upper wick / day range | Large upper wick = rejection |
| `close_loc` | (close - low) / (high - low) | Closing near lows = weakness |
| `consec_failed_highs` | count of prior 5d where price made new high but closed lower | Failed push pattern |

### 3.8 Liquidity & Quality Filters
| Feature | Definition | Why |
|---------|-----------|-----|
| `dollar_vol_20d` | price × 20d avg volume | Min $5M ADV filter |
| `price_level` | close price | Min $5 filter |
| `atr_pct` | ATR / price | Filter >8% ATR (penny/volatile) |
| `avg_spread_proxy` | atr_pct / vol_surge_1d | Rough bid-ask spread proxy |

## 4. Breakout Score (0-100)

```
breakout_score = compression_pts (25) + confirmation_pts (25) + trend_pts (25) + volume_pts (25)
```

### Compression (25 pts) — Is the stock coiling?
- Range contraction 5d/20d: ≤0.30 = 12pts, ≤0.45 = 8pts, ≤0.60 = 4pts
- Keltner squeeze: 5pts
- Volume dry-up prior 5d: ≤0.65 avg = 8pts, ≤0.80 = 5pts

### Confirmation (25 pts) — Is today the breakout day?
- Price at/above 20d high: within 0.5% = 15pts, within 2% = 10pts, within 4% = 5pts
- RSI14 in 50-65 zone: 10pts (avoiding overbought >75 = 0pts)
- Upper wick < 30% of day range: bonus 3pts

### Trend (25 pts) — Is the trend favorable?
- Above SMA20: 8pts, SMA50: 9pts, SMA200: 8pts

### Volume (25 pts) — Is volume confirming?
- Vol surge today: ≥2× = 25pts, ≥1.5× = 17pts, ≥1.2× = 10pts, ≥1.0× = 5pts

### Hard gates (auto-fail):
- Price < $5 or > $500: score = 0
- ADV < $5M: score = 0
- ATR% > 8%: score = 0
- Upper wick > 60% of range (rejection day): cap compression = 0

## 5. Breakout Type Classification

```python
breakout_type = classify(score_components, features):
  - "range_breakout": near 20d/52w high + high vol + low wick
  - "volume_breakout": vol_surge_1d > 2× + price > SMA50
  - "gap_continuation": gap_pct > 1% + above_all_smas + vol_surge
  - "trend_continuation": sma_alignment + moderate breakout + 3d momentum
  - "failed_breakout_risk": prior_fail + upper_wick > 40% + close_loc < 0.4
  - "consolidation_setup": squeeze + vol_dryup (not yet breaking out — watch list)
```

## 6. ML Model Additions

### New labels for breakout model:
- `_breakout_win_label`: h5_return > 0.01 (1% in 5 days) AND h5_outcome != STOP_HIT
- `_big_move_label`: h10_return > 0.03 (3% in 10 days, the "big move")
- `_failed_breakout_label`: close < entry_price after h3 (gave back gains)
- `_large_loss_label`: h5_return < -0.03 (existing)

### New ML features specific to breakout:
```
ML_NUMERIC_FEATURES_BREAKOUT = [
    # All existing pullback features PLUS:
    "bb_width", "atr_compression", "vol_surge_1d", "vol_surge_3d",
    "vol_dryup_5d", "obv_slope_5d", "sma_alignment", "rs_momentum",
    "price_vs_ema9", "pct_from_20d_high", "pct_from_50d_high",
    "upper_wick_pct", "consec_failed_highs", "breakout_score",
    "keltner_squeeze", "range_contraction_5_20",
]
```

### Training:
- `hold = 5` (breakouts play out over 5d, not 3d)
- `--executed-weight 20` (same anti-noise weighting)
- `--min-risk-reward 1.5`
- Separate model bundle: `ml_models/breakout/`

## 7. Leakage Prevention

**All features must use only data available at `scan_date`:**

| Feature | Leakage risk | Mitigation |
|---------|-------------|-----------|
| `pct_from_20d_high` | Need 20d lookback | Use `high.rolling(20).max()` shifted 1 day |
| `vol_surge_1d` | Today's volume | OK — today's scan-time volume |
| `h5_return` (label) | Forward 5d return | Label only, NOT feature. Embargo enforced. |
| `breakout_score` | No forward data | Computed from today's OHLCV only |

Leakage check in `_check_feature_leakage()` will be extended to cover h5/h10 prefixes.

## 8. Walk-Forward Validation

Same purged walk-forward as existing ML:
- Embargo = ceil(5 × 1.5) + 1 = 9 days
- Expanding window: train on [start, test_start - embargo], test on [test_start, test_start + 21d]
- Report: WF ROC, WF win rate at threshold, per-regime breakdown

## 9. Reporting Fields

Every breakout candidate will output:
```json
{
  "ticker": "AAPL",
  "breakout_score": 82.5,
  "breakout_type": "range_breakout",
  "breakout_success_probability": 0.61,
  "failed_breakout_probability": 0.22,
  "large_loss_probability": 0.18,
  "expected_move_5d": 0.024,
  "expected_move_10d": 0.041,
  "confidence": "high",
  "entry": 185.20,
  "stop": 182.10,
  "take_profit": 191.50,
  "invalidation_level": 181.80,
  "score_components": {
    "compression_pts": 18,
    "confirmation_pts": 22,
    "trend_pts": 25,
    "volume_pts": 17.5
  },
  "signal_reasons": ["near_20d_high", "vol_surge_2.1x", "keltner_squeeze", "above_all_smas"],
  "warning_flags": []
}
```

## 10. Files to Create / Modify

### New files:
- `tradingagents/screening/breakout_scanner.py` — BreakoutScanner class
- `scripts/scan_breakouts.py` — CLI for live/historical breakout scanning

### Modified files:
- `backtest.py` — add `score_mode="breakout_v2"` with full feature set + new labels
- `scripts/train_ml_models.py` — add `--mode breakout` training path  
- `scripts/retrain_weekly.py` — add `--mode breakout` variant
- `tradingagents/screening/__init__.py` — export BreakoutScanner

### Not modified:
- Live broker execution files
- Risk management logic
- Core schemas

## 11. Anti-Cheating Checklist

- [ ] No future highs/lows/returns in features
- [ ] All labels embargo-purged in WF
- [ ] Holdout (2026-05-08 → now) never used for tuning
- [ ] Failed breakouts included in training (not only winners)
- [ ] ROC must pass gate ≥ 0.56 before deploying
- [ ] Threshold search on train/WF only
