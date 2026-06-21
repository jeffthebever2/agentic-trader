# Portfolio Deep Dive — All Systems

> **Status (2026-06-21): stale snapshot.** Generated 2026-06-02; predates live broker
> execution, Holdings Brain, the performance tracker, and the latest thematic/sizing work.
> Useful as an architecture reference but verify specific numbers against current code.
> See `docs/CHANGELOG.md` and [`plans/SYSTEM_AUDIT_2026-06-19.md`](plans/SYSTEM_AUDIT_2026-06-19.md).

*Generated 2026-06-02 from live source code. All numbers pulled directly from code — not approximated.*

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Unified Brain (Short-Hold Paper Trading)](#2-unified-brain-short-hold-paper-trading)
3. [Alpha Engine](#3-alpha-engine)
4. [Candidate Ranker](#4-candidate-ranker)
5. [Position Sizer](#5-position-sizer)
6. [Exit Manager](#6-exit-manager)
7. [Market Regime Engine](#7-market-regime-engine)
8. [Short-Hold Exit Plan & Manager](#8-short-hold-exit-plan--manager)
9. [Screener — StockScreener & SwingScreener](#9-screener--stockscreener--swingscreener)
10. [Breakout Scanner](#10-breakout-scanner)
11. [Ticker Reliability Tracker](#11-ticker-reliability-tracker)
12. [Correlation Analyzer](#12-correlation-analyzer)
13. [Drawdown Monitor](#13-drawdown-monitor)
14. [Production Safety Monitor](#14-production-safety-monitor)
15. [Safe Trade Guard](#15-safe-trade-guard)
16. [Prediction Grader](#16-prediction-grader)
17. [Portfolio State](#17-portfolio-state)
18. [Thematic Portfolio (Manual Conviction)](#18-thematic-portfolio-manual-conviction)
19. [Thematic Auto (AI-Picked Momentum)](#19-thematic-auto-ai-picked-momentum)
20. [Rule-Based Portfolio](#20-rule-based-portfolio)
21. [Manual Portfolio (Web UI)](#21-manual-portfolio-web-ui)
22. [Fidelity Live Portfolio](#22-fidelity-live-portfolio)
23. [RL Agent Portfolio](#23-rl-agent-portfolio)
24. [Long-Hold Portfolio](#24-long-hold-portfolio)
25. [Cross-Portfolio Interaction Map](#25-cross-portfolio-interaction-map)
26. [ML Model Status (as of Cycle 46)](#26-ml-model-status-as-of-cycle-46)

---

## 1. System Architecture Overview

This system runs **five concurrent portfolio strategies** feeding into two execution surfaces:

```
Signal Sources                   Brain / Filter              Execution
─────────────────                ───────────────             ─────────────────
Breakout Scanner ──┐             ┌──────────────┐            Paper Account
Rule-Based       ──┤──candidates─▶ Unified Brain ──accepted──▶ (JSON state file)
ML Scorer        ──┤             │ (AlphaEngine  │
Regime Engine    ──┘             │  + Allocator) │            Fidelity Live
                                 └──────────────┘            (Playwright RPA)
Thematic Auto    ──────signals──▶ HIL Queue ──approve──▶ Paper + Fidelity
Thematic Manual  ──────direct ──▶ Paper Account

Manual Portfolio ──────────────────────────────────────────▶ Web UI only (no execution)
```

State persistence: all portfolios use flat JSON files under `tmp/`. The unified paper account is at `tmp/paper_trading_today/unified_brain/state.json`.

---

## 2. Unified Brain (Short-Hold Paper Trading)

**File:** `tradingagents/portfolio/unified_brain.py`  
**Runner:** `scripts/paper_trade_unified.py`, `scripts/paper_trade_today.py`

### What It Does

Central decision layer that ingests candidates from ALL strategy sources, deduplicates by ticker, scores each with `alpha_score`, assigns tiers (A+/A/B/C/NO_TRADE), sizes positions, and writes a full audit trail.

### Configuration (SHORT_HOLD_CONFIG)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `max_hold_days` | 10 | Hard exit after 10 trading days |
| `min_hold_days` | 1 | |
| `horizon_target_days` | 3 | Preferred exit window for scoring |
| `min_confidence` | 0.0 | **DISABLED** — win_prob ROC=0.4684 (anti-predictive) |
| `min_breakout_score` | 55.0 | Min breakout score to pass |
| `ll_hard_cap` | 0.50 | Hard reject if large_loss_prob > 50% |
| `min_rr` | 1.15 | Min reward:risk ratio |
| `risk_pct_per_trade` | 1.0% | ATR-based risk per trade |
| `position_cap_pct` | 20.0% | Max % of account per position |
| `position_cap_min_pct` | 5.0% | Min position size (A+ only) |
| `max_heat_pct` | 75.0% | Max % of account deployed total |
| `max_open_positions` | 5 | |
| `max_sector_positions` | 2 | Per sector cap |
| `adv_cap_pct` | 1% | Max 1% of 20-day ADV |

### Tier Size Multipliers

| Tier | Multiplier | When |
|------|-----------|------|
| A+ | 1.25× | alpha ≥ 0.72 AND regime_score ≥ 0.85 |
| A | 1.0× | alpha ≥ 0.55 |
| B | 0.5× | alpha ≥ 0.38 (watchlist only) |
| C | 0.0× | Rejected |
| NO_TRADE | 0.0× | Crash/regime gate |

### Regime Size Factors

| Regime | Size Factor |
|--------|-------------|
| Bull/Uptrend | 1.00× |
| Neutral/Sideways | 0.75× |
| Bear/Downtrend | 0.50× |
| Crash/Crisis | 0.00× (NO_TRADE) |

### VIX Size Factors

| VIX Level | Factor |
|-----------|--------|
| < 15 | **SKIP ALL TRADES** (low_vol expectancy = -0.248%/trade) |
| 15–25 | 1.0× |
| 25–35 | 0.75× |
| > 35 | 0.50× |

### Alpha Score Formula

```
alpha_score = (regime_score × breakout_boost) / (1 + ll_penalty + vol_penalty + liq_penalty) × rel_mult × feedback_mult
```

Where:
- `breakout_boost` = 1.0 + (breakout_score/100) × 0.50 → range [1.0, 1.5]
- `ll_penalty` = large_loss_probability × 1.5
- `vol_penalty` = max(0, (atr_pct - 0.04) / 0.04) × 1.0  ← normalized Cycle 44
- `liq_penalty` = max(0, 1 - liquidity_score) × 0.20
- `rel_mult` = ticker reliability multiplier [0.50, 1.10]
- `feedback_mult` = PaperFeedbackTracker aggression scalar [0.50, 1.0]

**win_prob REMOVED from numerator** — Cycle 38. WF HC win rate = 39.5% vs 54.2% base = anti-predictive. Restore when WF ROC > 0.55.

### Pipeline Steps

1. **Merge** — deduplicate candidates from all strategies by ticker
2. **Build UnifiedCandidate** — take best ML signals (max win_prob, min ll_prob, max breakout_score), use entry/stop/target from highest-scoring source
3. **Score** — compute alpha_score and assign tier
4. **VIX/Regime gate** — apply no-trade flags
5. **Allocate** — ATR-based risk sizing with heat taper, sector caps, ADV caps
6. **Audit trail** — write JSONL audit file with every decision

### Heat Taper (Cycle 44 B-16)

As book fills toward `max_heat_pct`, each successive entry shrinks:
```python
heat_taper = sqrt(max(0, 1 - deployed/max_heat))
combined_factor = reg_factor × vix_factor × tier_mult × heat_taper
```
Smooth geometric growth instead of hard wall.

### Exit Plan (per position)

- **Breakeven trigger**: entry + 1.0 × ATR → move stop to entry
- **Trailing stop**: peak_price - 0.5 × ATR (never lower)
- **Partial profit**: sell 50% at entry + 0.833 × reward_distance
- **Hard max-hold**: exit after 10 trading days regardless

---

## 3. Alpha Engine

**File:** `tradingagents/portfolio/alpha_engine.py`

The live-scoring engine used by the paper runner. Mirrors the UnifiedBrain scorer but is a standalone class.

### Formula (Cycle 38)

```
alpha = (regime_score × breakout_boost) / (1 + ll_penalty + vol_penalty + corr_penalty + liq_penalty) × rel_mult × feedback_mult
```

### Tier Thresholds

| Tier | Alpha | win_prob | regime_score |
|------|-------|---------|--------------|
| A+ | ≥ 0.72 | ≥ 0.0 (disabled) | ≥ 0.85 |
| A | ≥ 0.55 | ≥ 0.0 | — |
| B | ≥ 0.38 | ≥ 0.0 | — |
| C | otherwise | — | — |

### Size Multipliers

| Tier | Mult |
|------|------|
| A+ | 1.25× |
| A | 1.00× |
| B | 0.50× |
| C | 0.00× |
| NO_TRADE | 0.00× |

### PaperFeedbackTracker

Tracks predicted probability vs actual outcomes over rolling 30-trade window:

- `drift_score()` = mean_predicted - actual_win_rate
- `aggression_mult()`:
  - |drift| < 0.15 → 1.0 (normal)
  - |drift| in [0.15, 0.30] → linear decay 1.0 → 0.75
  - |drift| > 0.30 → 0.50 (minimal)
- `retrain_recommended()` → True when drift persists for 20+ consecutive trades

---

## 4. Candidate Ranker

**File:** `tradingagents/portfolio/candidate_ranker.py`

Pre-AlphaEngine scoring layer. Same formula but older implementation.

### Formula (Cycle 25/26)

```
composite = regime_score / (1 + ll_penalty + vol_penalty) × rel_mult
```

**Neutralized components** (all confirmed anti-predictive):
- `win_prob` — ROC = 0.4684 (disabled, `min_win_prob = 0.0`)
- `expected_return` boost — R² = 0.012 (noise)
- `timeout_penalty` — ROC = 0.4023 (anti-predictive, removed)

**Active components:**
- `large_loss_penalty` — ROC = 0.7116 (strong, retained)
- `vol_penalty` — ATR% above 3% threshold (rule-based)
- `regime_score` — from MarketRegimeState or string lookup

### Ticker Reliability Multiplier

| Reliability | Multiplier |
|-------------|-----------|
| ≥ 0.65 | 1.00 to 1.15 (proven tickers rewarded) |
| 0.40–0.65 | 0.60 to 1.00 (linear) |
| < 0.40 | 0.50 (max penalty) |

### Allocation Weights

`allocation_weights()` maps ranked candidates to position size multipliers [0.5, 2.0] via linear interpolation between worst and best composite scores.

---

## 5. Position Sizer

**File:** `tradingagents/portfolio/position_sizing.py`

10-layer dynamic sizing system. Layers applied in order:

| Layer | What It Does |
|-------|-------------|
| 1. Kelly fraction | Base size from win_rate/avg_win/avg_loss history |
| 2. ML confidence | Scale Kelly toward cap_max based on ML conviction |
| 3. Streak adjustment | 3+ losses → 50%; 2 → 70%; 1 → 85%; 4+ wins → 120% |
| 4. Time-of-day | First 15 min → 0; Last 30 min → 0; Midday → 90% |
| 5. Daily profit lock-in | Up 2%+ today → 50%; up 1%+ → 75% |
| 6. Regime factor | Applied from MarketRegimeState.size_factor |
| 6b. Tier factor | A+=1.25×, A=1.0×, B=0.5× |
| 7. ATR dollar-risk (PRIMARY) | risk_dollars = account × risk_pct × tier × regime |
| 8. Percentage fallback | If no stop/ATR available |
| 9. ADV liquidity cap | Max 1% of 20-day ADV |
| 10. Large-loss safety | ll=0 → 1.0×; ll=0.35 → 0.65×; ll≥0.5 → 0.5× |

### Kelly Formula

```python
f* = (b*p - q) / b
adjusted_kelly = f* × kelly_fraction_multiplier × confidence
```

Default: Half-Kelly (`kelly_fraction_multiplier = 0.5`). No-history prior: p=0.50, avg_win=3%, avg_loss=-2% → Kelly ≈ 0 (conservative).

---

## 6. Exit Manager

**File:** `tradingagents/portfolio/exit_manager.py`

Deterministic exit level calculator. Priority hierarchy:

1. ATR-based stop: `entry - ATR × 1.0`
2. Invalidation level (if tighter than ATR stop)
3. ATR-based target: `entry + ATR × 1.2`
4. Confidence extension: if ml_prob ≥ 0.70 → extend target by 1.15×
5. Expected return anchor: target ≥ entry × (1 + ER)
6. Min R:R enforcement: target ≥ entry + risk_dist × 1.15

### Trailing Stop

- Activates at: `entry + ATR × 1.0` (breakeven)
- Trail: `peak_price - ATR × 0.5` (never lower than current stop)

### Partial Take-Profit

- Trigger: `entry + risk_distance × 1.0` (1R profit)
- Fraction: sell 33% at partial target

### Historical Parameter Evolution

| Cycle | Change | Reason |
|-------|--------|--------|
| Cycle 34 | `stop_atr_mult` raised 0.7 → 1.0 | False stops in pullback setups |
| Cycle 44 E-11 | `confidence_extension_factor` 1.3 → 1.15 | Targets at 1.56 ATR never modeled by ML |
| Cycle 44 | `min_risk_reward` 1.5 → 1.15 | Match screener R:R=1.20 (rounding tolerance) |

---

## 7. Market Regime Engine

**File:** `tradingagents/screening/market_regime.py`

Probabilistic regime detection using SPY, VIX, VIX3M, and 11 sector ETFs.

### Regime Labels and Rules

| Regime | size_factor | ml_delta | stop_mult | tp_mult | max_trades | no_trade |
|--------|-------------|---------|-----------|---------|------------|---------|
| bull | 1.00 | +0.00 | 1.00 | 1.00 | 8 | No |
| uptrend | 0.90 | +0.00 | 1.00 | 1.00 | 7 | No |
| sideways | 0.75 | +0.03 | 1.00 | 0.90 | 5 | No |
| downtrend | 0.60 | +0.04 | 1.10 | 0.85 | 4 | No |
| bear | 0.50 | +0.05 | 1.15 | 0.80 | 3 | No |
| high_vol_bull | 0.65 | +0.03 | 1.20 | 0.90 | 5 | No |
| high_vol_bear | 0.30 | +0.08 | 1.25 | 0.75 | 2 | No |
| crash_risk | 0.00 | +0.99 | 1.50 | 0.70 | 0 | **YES** |
| crash_rebound | 0.55 | +0.02 | 1.15 | 1.10 | 4 | No |
| unknown | 0.80 | +0.00 | 1.00 | 1.00 | 6 | No |

### Classification Logic

```
VIX > 35 AND below SMA200  → crash_risk
Recovery from crash + above SMA200 → crash_rebound
VIX > 25 AND below SMA200  → high_vol_bear
VIX > 25 AND above SMA200  → high_vol_bull
Above SMA200 + golden cross → bull
Above SMA200 + above SMA50 → uptrend
Near SMA200, mixed signals → sideways
Below SMA200 but close     → sideways
Below SMA200 by >5%        → bear
Otherwise                   → downtrend
```

### Probabilistic Scores (output of engine)

7 probability dimensions computed for every run:
- `prob_bull`, `prob_bear`, `prob_chop`, `prob_high_vol`, `prob_crash`, `prob_rebound`, `prob_risk_on`, `prob_risk_off`

`regime_confidence` = 0.5 + 2.0 × (top_prob - second_prob) → how unambiguous the signal is.

### Crash Risk Score

```
crash_risk_score = 0.40 × prob_crash + 0.25 × (VIX>35) + 0.20 × (VIX_ts<0.90) + 0.15 × (prob_bear>0.6)
```

If `crash_risk_score > 0.70` → force `no_trade = True` even without a "crash_risk" label.

### Regime Score Adjustment

Raw label score is soft-adjusted:
```
regime_score_adj = regime_score - 0.20×crash_risk_score - 0.10×prob_risk_off + 0.05×(very_clean_bull_bonus)
```

---

## 8. Short-Hold Exit Plan & Manager

**File:** `tradingagents/portfolio/short_hold_exits.py`

### ShortHoldExitPlan — All Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `stop` | from candidate | Initial stop price |
| `take_profit` | from candidate | Initial take-profit price |
| `max_hold_days` | 10 | Hard exit after N trading days |
| `min_hold_days` | 1 | No exit before this |
| `breakeven_trigger_atr` | 1.0 | Move stop to entry after 1 ATR gain |
| `trail_atr_mult` | 0.5 | Trail = peak − 0.5 × ATR |
| `trail_active` | False | True once breakeven trigger fires |
| `partial_profit_fraction` | 0.50 | Sell 50% at partial trigger |
| `partial_profit_trigger` | 0.833 | Trigger at 83.3% of way to take_profit |
| `min_rr` | 1.15 | Min reward:risk; take_profit raised if violated |
| `trail_stop` | = stop | Current trailing stop (moves up only) |
| `peak_price` | = entry | Highest price seen since entry |

### Computed Properties

```
partial_trigger_price  = entry + 0.833 × (take_profit - entry)
breakeven_trigger_price = entry + 1.0 × ATR
effective_stop          = max(stop, trail_stop)   ← Cycle 44 E-12
```

### Exit Check Order (first match wins)

1. **MAX_HOLD**: `open_days >= max_hold_days`
   - Cycle 44 E-9: if `price > entry AND price >= peak × 0.995` AND `open_days < max_hold_days + 5` → extend (HOLD with trail protection)
   - Otherwise → close (close_fraction = 1.0)
2. **STOP_HIT / TRAILING_STOP**: `price <= effective_stop`
   - If `trail_active=True` → TRAILING_STOP signal
   - Else → STOP_HIT signal
3. **TARGET_HIT**: `price >= take_profit` → close_fraction = 1.0
4. **PARTIAL**: `not partial_taken AND open_days >= min_hold_days AND price >= partial_trigger_price` → close_fraction = 0.50 (fires once)
5. **HOLD**: update trail state silently, no close

### Trail Update Logic (`_update_trail`)

```
Step 1: if price > peak_price → peak_price = price
Step 2: Cycle 44 E-12 intermediate breakeven:
        if not trail_active AND price >= entry + 0.6 × ATR:
            trail_stop = max(trail_stop, entry)
Step 3: if not trail_active AND price >= breakeven_trigger_price:
            trail_active = True
            trail_stop = max(trail_stop, entry)
Step 4: if trail_active:
            new_trail = peak_price - 0.5 × ATR
            if new_trail > trail_stop: trail_stop = new_trail
```

### Exit Signal Enum

`HOLD | STOP_HIT | TARGET_HIT | TRAILING_STOP | PARTIAL | MAX_HOLD | MANUAL`

### R:R Gate at Entry

```python
stop_dist = entry - stop
if stop_dist > 0 and (take_profit - entry) / stop_dist < min_rr:
    take_profit = entry + stop_dist * min_rr  # stretch to meet min_rr
```

---

## 9. Screener — StockScreener & SwingScreener

**File:** `tradingagents/screening/screener.py`

### StockScreener — Scoring (max 100 pts)

Used for pre-screening before full AI analysis pipeline.

| Component | Max Pts | Logic |
|-----------|---------|-------|
| Trend | 30 | +10 per SMA above (20d, 50d, 200d) |
| Momentum vs SPY | 25 | +8 if 1M return > SPY; +9 if 3M > SPY; +8 if 6M > SPY |
| RSI-14 | 20 | 45–65 = 20pts; 35–45 or 65–75 = 10pts; else 0 |
| Volume expansion | 15 | 10d_avg / 30d_avg × 10, capped at 15 |
| MACD | 10 | MACD line > signal line = 10pts |

**Default threshold:** 85/100 to pass to AI pipeline.

### SwingScreener — Scoring (max 100 pts)

Designed for confirmed-pullback / VCP (Volatility Contraction Pattern) setups.

| Component | Max Pts | Logic |
|-----------|---------|-------|
| Consolidation (coil) | 25 | range_5d/range_20d contraction + volume dry-up prior 3 days |
| Breakout setup | 25 | Price at 10d high + RSI9 building |
| Trend health | 25 | Above 20d + 50d SMA |
| Volume trigger | 25 | Today's volume vs 20d baseline |

**Consolidation scoring:**

```
contraction = range_5d / range_20d
≤ 0.30 → +15 pts (very tight coil)
≤ 0.45 → +10 pts (solid base)
≤ 0.60 → +5 pts  (moderate)

dryup_ratio = vol_3d_prior / vol_20d_avg
≤ 0.65 → +10 pts
≤ 0.80 → +6 pts
≤ 0.95 → +3 pts
```

### Price Targets (SwingScreener)

```
ATR = 14-day Average True Range
entry     = current close
target    = entry + 1.2 × ATR    (_ATR_TARGET = 1.2)
stop      = entry - 1.0 × ATR    (_ATR_STOP = 1.0, Cycle 44: raised from 0.7)
risk_reward = (target - entry) / (entry - stop) = 1.2 / 1.0 = 1.20
hold_days = "2-4"
```

### RSI Calculation

```python
delta = closes.diff()
gains = delta.clip(lower=0).rolling(14).mean()
losses = (-delta.clip(upper=0)).rolling(14).mean()
RS = gains / losses
RSI = 100 - 100 / (1 + RS)
```

### MACD Calculation

```python
ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
macd = ema12 - ema26
signal = macd.ewm(span=9, adjust=False).mean()
```

---

## 10. Breakout Scanner

**File:** `tradingagents/screening/breakout_scanner.py`

### Scoring (max 100 pts)

| Component | Max Pts | What It Measures |
|-----------|---------|-----------------|
| Compression | 25 | Range contraction + volume dry-up (coiling) |
| Confirmation | 25 | Price at resistance + RSI zone + low upper wick |
| Trend | 25 | Above 20/50/200-day SMA |
| Volume | 25 | Today's volume vs 20-day baseline |

### Hard Liquidity Gates

```
Price:    $5 ≤ price ≤ $500
ADV:      > $5M daily dollar volume
ATR%:     < 8% of price
```

Tickers failing any gate receive score = 0 and `passed = False`.

### Breakout Type Labels

| Type | Conditions |
|------|-----------|
| `range_breakout` | Near 20d/50d high + rising vol + tight prior range |
| `volume_breakout` | vol_surge > 2× + significant price move |
| `gap_continuation` | Gap up > 1% + above all SMAs + vol confirm |
| `trend_continuation` | All SMAs aligned + moderate breakout + RS positive |
| `failed_breakout_risk` | Prior rejection pattern + high upper wick |
| `consolidation_setup` | Squeeze active but no breakout yet (watchlist) |

### Confidence Levels

| Score | Confidence |
|-------|-----------|
| ≥ 80 | high |
| ≥ 60 | medium |
| < 60 | low |

### BreakoutResult Fields

```
ticker, score (0-100), passed, scan_date, breakout_type, confidence
entry, stop, take_profit, invalidation_level, risk_reward
breakout_success_probability, failed_breakout_probability
large_loss_probability, expected_move_3d/5d/10d
features (ML-ready numeric dict)
score_components (compression/confirmation/trend/volume pts)
signal_reasons, warning_flags
```

### Integration with Alpha Engine

`breakout_score` fed to `AlphaEngine.evaluate()` as parameter. Converted to `breakout_boost`:

```
breakout_boost = 1.0 + (breakout_score / 100) × 0.50
```

Score 0 → boost 1.0×. Score 100 → boost 1.5×. Multiplies `regime_score` in numerator.

---

## 11. Ticker Reliability Tracker

**File:** `tradingagents/portfolio/ticker_reliability.py`

### Formula

```
window = 20 most recent closed trades for this ticker
n = number of trades found
raw_wr = wins / n       (win = pnl > 0)
blend = min(1.0, n / blend_at_n)   (blend_at_n = 10)
score = 0.5 × (1 - blend) + raw_wr × blend
```

`score = 0.5` when no data (neutral prior). Converges to `raw_wr` once `n ≥ 10`.

### Size Multiplier Mapping

| Score | Multiplier | Regime |
|-------|-----------|--------|
| ≥ 0.65 | 1.00 to 1.10 (linear) | Proven tickers rewarded |
| 0.50–0.65 | 1.00 | Neutral |
| 0.40–0.50 | 0.60 to 1.00 (linear) | Caution zone |
| < 0.40 | 0.50 to 0.60 (linear) | Max penalty |

### In AlphaEngine

`ticker_reliability` → `rel_mult` calculation (identical formula in alpha_engine.py and unified_brain.py):

```python
if reliability >= 0.65:
    rel_mult = 1.0 + (reliability - 0.65) / 0.35 × 0.10   # [1.00, 1.10]
elif reliability >= 0.40:
    rel_mult = 0.60 + (reliability - 0.40) / 0.25 × 0.40  # [0.60, 1.00]
else:
    rel_mult = 0.50   # floor (Cycle 44)
rel_mult = clamp(rel_mult, 0.50, 1.10)
```

**Default when no tracker provided:** `rel = 0.65` (neutral, rel_mult = 1.0). Using `rel = 0.5` would penalize all signals by 24%.

---

## 12. Correlation Analyzer

**File:** `tradingagents/portfolio/correlation.py`

### Configuration

```
threshold     = 0.70   (correlation above this = "highly correlated")
max_high_corr = 2      (block if new ticker correlated with ≥ 2 existing positions)
period        = "1y"   (price history for correlation computation)
```

### Computation

```python
prices = yfinance.download(all_tickers, period="1y")["Adj Close"]
returns = prices.pct_change().dropna()
corr_matrix = returns.corr()
new_corr = corr_matrix[new_ticker].drop(new_ticker)
high_corr = new_corr[new_corr > 0.70]
```

### Gate (Cycle 44 SR-10)

```python
if len(high_corr) >= max_high_corr:  # ← >= not > (Cycle 44 fix)
    return False, f"highly correlated with {len(high_corr)} positions"
```

Pre-Cycle-44: `>` allowed a 3-way correlated cluster when `max_high_corr=2`. Fixed to `>=`.

In `AlphaEngine.evaluate()`: `is_correlated=True` → adds `corr_penalty_val = 0.15` to denominator.

---

## 13. Drawdown Monitor

**File:** `tradingagents/portfolio/drawdown.py`

### Circuit Breaker Thresholds

```
max_daily_loss   = -5%  of account
max_monthly_loss = -15% of account
```

### `should_keep_trading(unrealized_pnl_pct)`

```python
today_pnl   = realized_closed_today + unrealized_pnl_pct
monthly_pnl = realized_this_month   + unrealized_pnl_pct

if today_pnl   < -0.05: return False, "daily loss exceeded"
if monthly_pnl < -0.15: return False, "monthly loss exceeded"
return True, "OK"
```

**Cycle 44:** `unrealized_pnl_pct` parameter added. Previously blind to open losers; breaker could be bypassed by holding losing positions open during a selloff.

### Per-Trade Return Weighting

```python
if capital_fraction in trade:
    account_return = trade_pnl_pct × capital_fraction
else:
    account_return = trade_pnl_pct
```

`capital_fraction` = notional / account_value at entry. Weighted sum approximates true account-level P&L.

---

## 14. Production Safety Monitor

**File:** `tradingagents/portfolio/production_safety.py`

### Default Safety Config

| Parameter | Default | Halt/Warn |
|-----------|---------|-----------|
| `kill_switch` | False | HALT if True |
| `max_daily_loss_pct` | 2.0% | HALT |
| `max_weekly_loss_pct` | 5.0% | HALT |
| `max_consecutive_losses` | 4 | HALT |
| `max_trades_per_day` | 8 | HALT |
| `min_model_confidence_floor` | 0.52 | WARN |
| `max_model_age_days` | 45 | HALT |
| `model_age_warn_days` | 30 | WARN |
| `max_nan_rate` | 30% | HALT (>50% tickers) |
| `max_stale_data_hours` | 6.0h | HALT (>20% tickers) |
| `max_abnormal_move_pct` | 25% | WARN |
| `crisis_vix` | 35.0 | HALT |
| `elevated_vix` | 25.0 | used by SafeTradeGuard |
| `max_portfolio_drawdown` | -12% | HALT |
| `ml_drift_halt_threshold` | 0.20 | HALT |
| `rolling_wr_floor` | 30% | HALT |
| `drift_min_trades` | 15 | min trades for drift check |
| `wr_floor_min_trades` | 10 | min trades for WR check |

### check_all() — 6-Stage Pipeline

1. **Kill switch** — instant halt, no other checks
2. **Model health** — age (>45d=HALT, >30d=WARN), drift >0.20=HALT, ROC<0.45=HALT, calibration age >25d=WARN
3. **Data health** — stale check on deterministic sample (sorted, capped at 20 tickers); halt if >20% stale (Cycle 44 V-20)
4. **Account health** — daily loss + weekly loss + drawdown (with open MTM) + consecutive losses + trades-per-day
5. **Market conditions** — delegates to SafeTradeGuard + MarketRegimeEngine no_trade flag
6. **Candidate confidence floor** — WARN if best candidate ml_prob < 0.52 (Cycle 44 V-21)

**ANY halt reason → `safe_to_trade = False`**. All failures logged in SafetyReport.

### Account Health — Open MTM Inclusion (Cycle 44 V-18)

```python
open_unrealized = Σ (shares × (current_price - entry_price))  for each open position
today_pnl  = realized_today + open_unrealized
weekly_pnl = realized_this_week + open_unrealized
```

### High-Water Mark Drawdown (Cycle 44 V-17)

```python
peak = max(account.peak_equity, starting_cash, account_value)
account.peak_equity = peak   # persisted to account state
drawdown = (account_value - peak) / peak
```

Pre-Cycle-44: `peak = max(starting_cash, account_value)` reset peak after every profitable period, so drawdown under-reported after pullbacks from new highs.

### Model Age Check (Cycle 44 V-16)

```python
age = max(0, (now - created_at).days)   # clamp to 0 (tz-aware)
if age > 45: HALT
if age > 30: WARN
```

### ROC Halt Floor (Cycle 44 V-22)

```python
if roc_auc < 0.45: HALT  # catastrophically anti-predictive
if roc_auc < 0.52: WARN  # weak discrimination
```

---

## 15. Safe Trade Guard

**File:** `tradingagents/portfolio/safe_trade_guard.py`

### 6 Checks (first HALT stops further new entries)

| # | Check | Condition | Mode |
|---|-------|-----------|------|
| 1 | Crisis VIX | VIX ≥ 35 | HALT |
| 2 | Hostile regime | is_bear AND VIX ≥ 25 | HALT |
| 3 | Portfolio drawdown | drawdown < -12% | HALT |
| 4 | Model drift | \|pred_wr - actual_wr\| > 0.20 (over last 15+ trades) | HALT |
| 5 | Win rate collapse | rolling WR < 30% (over last 10+ trades) | HALT |
| 6 | Model staleness | age > 45d | WARN only |

### Model Drift Formula

```
pred_wr    = mean(ml_probability) over last 15 scored trades
actual_wr  = wins / n              over same trades
drift      = |pred_wr - actual_wr|
if drift > 0.20: HALT
```

### High-Vol Adjustments (not HALT — applies when 25 ≤ VIX < 35)

```
min_prob_threshold += 0.08   (stricter ML gate)
size_factor        × 0.50    (half position size)
max_hold_override  = 2       (2-day max hold)
```

### Bear Regime Detection

```python
is_bear = spy_regime in ("bear", "sell", "downtrend")
```

---

## 16. Prediction Grader

**File:** `tradingagents/portfolio/prediction_grader.py`

Joins BUY + SELL events from paper account event logs. Produces `GradeResult` per closed trade.

### GradeResult Fields

**Prediction inputs (from BUY event):**
- `predicted_win_prob`, `predicted_return`, `predicted_ll_prob`
- `alpha_tier` (A+/A/B/C), `alpha_score`, `breakout_score`
- `regime_at_entry`, `model_version`

**Actual outcomes (from SELL event):**
- `actual_win`, `actual_return` (pnl_pct fraction)
- `actual_max_drawdown` (negative = down from entry)
- `stop_hit`, `target_hit`, `hold_days`, `regime_at_exit`

**Derived accuracy:**
- `win_prediction_correct` = (pred_wp ≥ 0.60) == actual_win
- `return_error` = predicted_return − actual_return
- `ll_prediction_correct` = (pred_ll ≥ 0.30) == (max_dd ≤ −8%)

### Buckets

| Confidence Bucket | Range |
|------------------|-------|
| "high" | win_prob ≥ 0.70 |
| "mid" | 0.60–0.70 |
| "low" | < 0.60 |

| Return Bucket | Range |
|--------------|-------|
| "gain" | return ≥ 2% |
| "small_gain" | 0–2% |
| "loss" | ≤ 0 |

### Trade ID (Cycle 44 GC-6)

```python
trade_id = f"{ticker}_{entry_time[:19]}"   # includes H:M:S
```

Pre-Cycle-44: used only date (`[:10]`), so same-day round-trips of one ticker had duplicate IDs and the second was dropped.

### Summary Metrics

```
win_rate, win_prediction_accuracy, avg_return, avg_return_error
ll_prediction_accuracy, stop_rate, target_rate
```

### Cycle 44 Grader Fixes

- **GC-4**: Falls back to SELL event for `alpha_tier/ll_prob/regime/model_version` when BUY event is missing those fields (was grading every trade as tier "C" / "unknown")
- **GC-5**: Removed pnl_pct magnitude heuristic (> 2.0 → /100) that corrupted genuine +250%+ winners
- **GC-2**: Exit reason normalized to uppercase (`STOP_LOSS` / `TAKE_PROFIT`) for reliable `stop_rate` / `target_rate` computation
- **GC-3**: BUY event now emits `ll/alpha_tier/alpha_score/breakout_score` fields

---

## 17. Portfolio State

**File:** `tradingagents/portfolio/state.py`

### PortfolioState — Constraints

| Parameter | Default |
|-----------|---------|
| `starting_cash` | $100,000 |
| `max_positions` | 10 |
| `max_sector_exposure` | 30% |
| `max_position_size` | 5% |

### Position Fields

```
ticker, entry_price, shares (float), entry_date, stop_loss, take_profit, thesis, sector
```

### `can_buy()` — Pre-Trade Gates

1. Has price data
2. Not already long ticker
3. `len(positions) < max_positions` (10)
4. Position size would be ≤ 5% of total_value
5. Sector exposure after buy ≤ 30%
6. `cost ≤ cash`

### `execute_buy()` — ATR Fallback Levels (Cycle 44 V-24)

```python
if stop_loss is None:
    stop_loss  = entry - 1.0 × ATR    if ATR > 0  else  entry × 0.98
if take_profit is None:
    take_profit = entry + 1.2 × ATR   if ATR > 0  else  entry × 1.10
```

Pre-Cycle-44: fixed 2%/10% regardless of volatility.

### `execute_sell()` — Gap-Aware Fill (Cycle 44 V-26)

```python
if "STOP" in reason:
    exit_price = min(live_price, stop_loss)     # stop fills AT OR BELOW level
elif "TARGET" or "TAKE_PROFIT" in reason:
    exit_price = max(live_price, take_profit)   # target fills AT OR ABOVE level
else:
    exit_price = live_price
```

Pre-Cycle-44: always used `live_price`, making realized P&L systematically optimistic (stop fills above stop level, target fills below target level).

### Portfolio Metrics

```
invested_value   = Σ (shares × current_price)  for all positions
total_value      = cash + invested_value
portfolio_delta  = invested_value / total_value
sector_exposure  = sector_invested / total_value
position_exposure = position_value / total_value
```

---

## 18. Thematic Portfolio (Manual Conviction)

*(Formerly Section 8 — same content, renumbered)*

**File:** `web/api/thematic_portfolio.py`  
**Storage:** `tmp/thematic_portfolio_{sha256(email)[:16]}.json`

### 12 Themes

| Key | Name | Color |
|-----|------|-------|
| `ai_leaders` | AI Leaders | #6366f1 |
| `ai_infrastructure` | AI Infrastructure | #8b5cf6 |
| `optical_network` | Optical Networking | #06b6d4 |
| `memory_hbm` | Memory / HBM | #0ea5e9 |
| `datacenter_power` | Data Center Power | #f59e0b |
| `nuclear_energy` | Nuclear / Energy | #10b981 |
| `space_defense` | Space & Defense | #64748b |
| `quantum_future` | Quantum / Future Compute | #a78bfa |
| `critical_minerals` | Critical Minerals | #d97706 |
| `reshoring` | Reshoring / Industrial | #78716c |
| `fintech_consumer` | Fintech / Consumer | #ec4899 |
| `future_tech` | Future Tech / Biotech | #14b8a6 |

### Categories & Risk Levels

**Categories:** `core`, `growth`, `satellite`, `speculative`, `watchlist`, `avoid`  
**Risk Levels:** `low`, `medium`, `high`, `very_high`

### Conviction Score Formula (10 components, weights sum to 1.0 - 0.10 chase penalty)

```
final_score = (
    theme_score      × 0.15 +
    catalyst_score   × 0.15 +
    conviction_score × 0.15 +
    thesis_score     × 0.10 +
    entry_score      × 0.10 +
    momentum         × 0.10 +
    risk_score       × 0.10 +
    supply_score     × 0.08 +
    social_score     × 0.07
  ) - chase_risk × 0.10
```

**Theme score:**
```
premium_themes = {ai_leaders, ai_infrastructure, memory_hbm, datacenter_power} → 9.0
growth_themes  = {optical_network, space_defense, quantum_future, nuclear_energy} → 7.0
other → 5.5
```

**Catalyst score:**
```
len(catalyst) > 30 chars → 9.0
any catalyst text        → 5.0
no catalyst              → 2.0
```

**Entry quality** (10 = no chase, 0 = chased 50%+ above entry):
```
chase_pct = (current - entry) / entry × 100
entry_score = max(0, 10 - chase_pct / 5)
```

**Momentum** (0 = down 20%, 5 = flat, 10 = up 20%):
```
momentum = clip((chase_pct + 20) / 4, 0, 10)
```

**Risk score** (inverse):
```
low → risk_int = 1 → score = 10 - 1×2 = 8
medium → 2 → 6
high → 3 → 4
very_high → 4 → 2
risk_score = max(0, 10 - risk_int × 2)
```

**Social score:** normalized from latest scan (0-10 using max score as reference). Falls back to 5.0.

**Chase risk penalty:**
```
chase_risk = clip(chase_pct / 5, 0, 10)
```

### Price Cache

```
TTL: 300s (5 minutes)
Source: yfinance download("Close", period="2d")
```

### Paper Trade Injection (Phase 3 gates)

1. **Price:** fetch live if entry_price not provided
2. **R:R gate:** `rr = target_pct / stop_pct`; if `rr < min_rr (default 1.5)`: `target_pct = stop_pct × min_rr` (warn, don't reject)
3. **Conviction scaling:** `scale = 0.4 + (conviction-1)/9 × 1.1`; `alloc = dollar_amount × scale`; `shares = int(alloc / price)`
4. **Circuit breakers:** `_check_portfolio_circuit_breakers(state, hil_settings, cost)` — blocks if heat ≥ 80% or daily loss ≥ 3%
5. **Cash check:** `cost ≤ settled_cash`
6. **Existing position check:** ticker not already in paper account
7. **Real ATR:** `_real_atr(ticker, price)` → 14-day ATR from yfinance 20d history; fallback 2%
8. **Write:** atomic write to `PAPER_STATE_FILE`

### Summary Stats Computed

```
total_market_value = Σ(current_price × shares)
total_cost_basis   = Σ(entry_price × shares)
total_gain_usd     = total_market_value - total_cost_basis
total_gain_pct     = total_gain_usd / total_cost_basis × 100
winners_count, losers_count, best_winner, worst_loser
```

Per-theme allocation_pct = theme_market_value / total_market_value × 100

---

## 19. Thematic Auto (AI-Picked Momentum)

*(Formerly Section 9 — same content, renumbered and expanded)*

**File:** `web/api/thematic_auto.py`

### 15 Data Sources with Weights

| Source | Base Weight | Signal Type |
|--------|-------------|------------|
| Trusted Twitter RSS (rss.app) | 5 pts/cashtag, 1 pt/word | Explicit trader picks |
| OpenInsider cluster buys | 5 pts | Multiple insiders buying |
| OpenInsider large buys (>$500k, last 3d) | 3 pts | Single large insider |
| Congressional trades (Google News) | 4 pts/cashtag | Political info flow |
| Marketaux (paid) | sentimentScore × 5 + 2 | Sentiment-weighted news |
| Press Releases (BusinessWire/PRN/GlobeNewswire) | 4 pts/cashtag, 1 pt/word ≥4 chars | Corporate announcements |
| Brave Search | 2 pts × mentions | News search (capped 1000/month) |
| Reddit (5 subreddits) | 2 pts × mention count | Community buzz |
| SeekingAlpha RSS | 2 pts × mentions | Curated market news |
| Yahoo Finance movers | 3 pts (gainers), 2 pts (active), 1 pt (losers) | Price/vol momentum |
| Yahoo Finance trending | 3 pts flat | Trending tickers |
| Google News RSS (7 queries) | 1.5 pts × mentions | News mentions |
| DuckDuckGo news (4 queries) | 1.5 pts × mentions | News mentions |
| Finviz (top gainers + unusual vol) | 3 pts (gainers), 2 pts (unusual vol) | Technical momentum |
| StockAnalysis trending | max(4.0 - rank × 0.1, 1.0) | Trending page rank |

### Score Assembly

```
raw_score = Σ(source_weight × mentions)

# Multi-source bonus (Cycle Phase 1)
n_sources = count of distinct sources
bonus = min((n_sources - 1) × 3, 15)   (cap at +15)
raw_score += bonus

# Insider + social combo bonus (Cycle Phase 1)
if insider_pts > 0 AND (trusted_twitter_pts > 0 OR reddit_pts > 0):
    raw_score += 8

# Historical scan memory bonus (decay-weighted avg of last 5 scans)
if ticker in last_5_scans:
    raw_score += min(historical_avg / max_historical × 30, 30)
```

### Signal Quality Gate

```
_QUALITY_SOURCES = {trusted_twitter, reddit, seeking_alpha, google_news,
                    insider, marketaux, twitter, ddg, brave, scan_memory}

pass = has_any_quality_source OR raw_score >= 80
```

### AI Pick Validation (_validate_pick)

```
ticker:      alpha-only, 1-5 chars, not in _SKIP set (500+ words)
conviction:  clamp 1-10
target_pct:  clamp 5-100%
stop_pct:    clamp 2-25%
hold_days:   clamp 1-30
theme:       must be in THEMES_MAP; else _guess_theme() via ticker membership
```

### Scan Memory (last 5 scans)

```python
weights = [0.2 + 0.8 × (i / max(n-1, 1)) for i in range(n)]  # linear 0.2→1.0
raw = {t: sum(scores) / n for t, scores in ticker_scores.items()}
bonus = raw[t] / max_raw × 30   # normalize: strongest ticker → +30pt bonus
```

### Spike Detection

```
is_spike = appeared in only 1 of last 5 scans
confirmed = appeared in 2+ of last 5 scans
```

Spikes are shown in UI but flagged. Auto-trade loop skips spikes.

### Exit Conditions (checked after every scan, execute=True)

| Reason | Condition |
|--------|-----------|
| `stop_hit` | `current_price ≤ position.stop` |
| `target_hit` | `current_price ≥ position.target` |
| `max_hold_exceeded` | `age_days ≥ hold_days` |
| `buzz_collapse` | ticker absent from latest scan AND held > 2 days |
| `buzz_decay` | `entry_raw_score > 0 AND current_score < entry_raw_score × 0.40` AND held > 1 day |

### Auto-Execute Loop (`_auto_execute_confirmed_signals`)

```
For each user with auto_trade_paper=True:
    dollar_amount = hil.dollar_amount (default $500)
    For each pending signal:
        if not confirmed: skip
        if raw_score < 40: skip
        call approve_signal(signal_id, ApproveBody(dollar_amount=dollar_amount), user_mock)
        → circuit breakers (heat, daily loss, cash) enforced inside approve_signal
```

### Portfolio Caps (in approve_signal)

```
PORTFOLIO_MAX_POSITIONS  = 15   total open positions
PORTFOLIO_MAX_PER_THEME  = 3    positions per theme
PORTFOLIO_MAX_SPECULATIVE= 8    total thematic/speculative positions
```

### HIL Settings

| Setting | Default | Notes |
|---------|---------|-------|
| `enabled` | False | Global thematic HIL on/off |
| `dollar_amount` | $500 | Base allocation per trade |
| `auto_trade_paper` | False | Auto-execute confirmed signals |
| `auto_trade_fidelity` | False | Auto-execute on live account |
| `min_rr` | 1.5 | Auto-widened if below |
| `max_portfolio_heat` | 80.0% | Block if deployed ≥ this |
| `daily_loss_limit_pct` | 3.0% | Block if today's loss ≥ this |
| `conviction_scale` | True | Scale by conviction (0.4×→1.5×) |
| `sms_notify` | False | SMS on new signals |
| `fidelity_trade` | False | Route to Fidelity live |

### Brave Search Rate Limiting

```
Monthly limit: 1000 requests
Per scan:      3 queries
Budget check:  if remaining ≤ 0: skip
Counter persisted: tmp/brave_search_usage.json {year-month: used}
```

---

## 20. Rule-Based Portfolio

*(Formerly Section 10)*

**File:** `scripts/simulate_rule_based.py`

### Constants

```
MAX_POSITIONS_PER_DAY  = 20
MAX_SINGLE_POSITION_PCT = 20%
PDT_DAY_TRADE_LIMIT    = 3    (rolling 5-day window, accounts < $25k)
PDT_THRESHOLD          = $25,000
WASH_SALE_DAYS         = 30
```

### Confidence-Based Sizing

```
confidence ≥ 0.75 → base_pct = 12% of capital
confidence ≥ 0.55 → base_pct = 8%
confidence ≥ 0.35 → base_pct = 5%
else               → base_pct = 3%

pos_size = min(capital × base_pct, capital × 20%)
```

Confidence fallback when column missing:
```
rsi_conf = (50 - rsi14.clip(max=50)) / 50
mfi_conf = (50 - mfi14.clip(max=50)) / 50
confidence = rsi_conf × 0.5 + mfi_conf × 0.5
```

### Simulation Loop

1. Each `scan_date` (3-day hold period):
   - Close positions where `exit_date ≤ today`
   - Rank day's candidates by confidence descending
   - Available slots = `MAX_POSITIONS_PER_DAY - len(open_positions)`
   - For each candidate (up to slots):
     - Check wash sale (30-day block on same ticker after loss)
     - Check concentration limit (20%)
     - Check PDT (warning only, not blocking — 3-day hold ≠ day trade)
     - Execute: `capital -= pos_size`

### P&L Formula

```
exit_value = cost + cost × h3_return
pnl        = cost × h3_return
win        = h3_return > 0
```

### Goal Check

```
Win rate ≥ 85%   → PASS/FAIL
Profit ≥ $2,500  → PASS/FAIL
Trades ≥ 150     → PASS/FAIL
```

---

## 21. Manual Portfolio (Web UI)

*(Formerly Section 11)*

**File:** `web/api/portfolio.py`  
**Storage:** Supabase table `agentic_portfolios` → fallback `tmp/positions_{sha256(email)[:16]}.json`

### Position Fields

```
ticker, shares (float), entry_price, stop_loss, take_profit
entry_date (YYYY-MM-DD), sector, thesis
```

### Metrics (computed live)

```
current_price   = yfinance Ticker.history(period="2d")["Close"].iloc[-1]
market_value    = shares × current_price
cost_basis      = shares × entry_price
pnl             = market_value - cost_basis
pnl_pct         = pnl / cost_basis × 100
total_market_value = Σ market_value
total_value        = cash + total_market_value
total_pnl_pct      = total_pnl / total_invested × 100
```

### Constraints

- No automated scanning, signals, or exit rules
- No stops enforced — display only
- Portfolio history endpoint reads global trade log (not per-user, known issue)
- Starting cash: $100,000 (from `DEFAULT_CONFIG["starting_cash"]`)

---

## 22. Fidelity Live Portfolio

*(Formerly Section 12)*

**File:** `web/api/fidelity.py`, `tradingagents/portfolio/fidelity_portfolio.py`

### Safety Gates (must all pass)

```
tradingagents/compliance.py:
    LIVE_TRADING_HARD_BLOCKED = True  ← hardcoded kill switch
    live_trading_enabled()    = os.getenv("LIVE_TRADING_ENABLED") == "true"
```

Both must be satisfied: `LIVE_TRADING_HARD_BLOCKED=False AND LIVE_TRADING_ENABLED=true`.

### Execution Engine

Playwright RPA automating Fidelity Active Trader Pro browser session.

### Order Types

```
market:  execute at market price
limit:   limit_price = round(price × 1.002, 2)   (+0.2% buffer for buy-side)
```

### Cash Estimation

```
Primary:  scrape from Fidelity ATP UI
Fallback: grand_total × 0.15   (15% estimate — known weak point)
```

When scrape fails: warning logged, trade proceeds with estimate. No user notification.

### Per-Ticker Order Lock

```python
lock_key = f"{email}:{ticker}"
order_lock = asyncio.Lock()
# Check-before-acquire (known race, Cycle 44 finding):
if order_lock.locked():
    return {"skipped": "order already in progress"}
async with order_lock:
    execute_order(...)
```

Race window: between `.locked()` check and `async with` acquisition, another coroutine can acquire and submit a duplicate order.

### Compliance Layer

```python
def validate_order(action, order_type, quantity):
    if action.lower() not in ("buy", "sell"):     raise
    if order_type.lower() not in ("market", "limit", "stop"): raise
    if quantity <= 0:                              raise
    if LIVE_TRADING_HARD_BLOCKED:                 raise
    if not live_trading_enabled():                raise
```

### Thematic Fidelity Trade

`POST /api/fidelity/thematic-trade` — same conviction/circuit-breaker logic as paper trade. Requires `execute=True` in request body.

---

## 23. RL Agent Portfolio

*(Formerly Section 13)*

**Files:** `tradingagents/rl/environment.py`, `tradingagents/rl/rl_signal.py`, `tradingagents/rl/td3_agent.py`

### StockTradingEnv — State Vector

Per ticker (stacked for all tickers):

```
[20 normalized log-returns (lookback window)]
RSI-14 / 100
MACD histogram / 10-day std
Volume ratio = 10d_avg_vol / 30d_avg_vol
Current portfolio weight for this ticker
Unrealized PnL as fraction of portfolio value
```

Total features per ticker = 20 + 5 = 25

### Action Space

```
Continuous: [-1, 1] per ticker
longs_only=True: negative actions → 0 (no shorts)
Rescaled and clipped to [0, max_position_size=0.10]
```

### Environment Parameters

| Parameter | Default |
|-----------|---------|
| `starting_cash` | $100,000 |
| `max_position_size` | 10% per ticker |
| `transaction_cost` | 0.001 (10bps round-trip) |
| `slippage_bps` | 5bps per side (10bps round-trip) |
| `longs_only` | True |

### Reward Function

```
reward = portfolio_log_return - transaction_cost_penalty
portfolio_log_return = log(portfolio_value_t / portfolio_value_{t-1})
```

### TD3 Algorithm

Twin Delayed Deep Deterministic Policy Gradient:
- Two Q-networks (critics) to reduce overestimation bias vs DDPG
- Delayed policy (actor) updates (every 2 critic steps)
- Target policy smoothing (Gaussian noise on target actions)

### Integration with UnifiedBrain

`tradingagents/rl/rl_signal.py` wraps trained agent → generates signals in `candidates_by_strategy` format. Signals enter alpha-scoring pipeline identically to rule/ML candidates. RL is a secondary source; alpha score ultimately determines allocation.

---

## 24. Long-Hold Portfolio

*(Formerly Section 14)*

**Status:** Excluded from UnifiedBrain by default.

```python
excluded = set(exclude_strategies or ["long_hold", "pure_ai"])
```

Both `long_hold` and `pure_ai` strategies are bypassed in `merge_candidates()`. Candidates may be generated but receive no capital allocation.

**Rationale:** Short-hold (1-10 day) is the validated approach. Long-hold has separate risk profile (drawdown, fundamental drift, sector rotation) not compatible with ATR-based stop geometry.

---

## 25. Cross-Portfolio Interaction Map

*(Formerly Section 16)*

### State File Ownership

```
tmp/paper_trading_today/unified_brain/state.json
    Writers: paper_trade_today.py, paper_trade_unified.py,
             thematic_paper_trade, approve_signal
    Readers: all portfolio APIs, exit managers, dashboard
    Risk: multiple async writers without process-level locking

tmp/thematic_signals.json
    Writers: _run_scan() (4h cycle or manual trigger)
    Readers: /api/thematic/auto/signals, approve_signal, _auto_execute
    Pattern: atomic write (temp + rename), .json.bak on every write

tmp/thematic_score_history.jsonl
    Writers: _run_scan() → _append_score_history()
    Readers: thematic portfolio social_score, scan memory, buzz_decay logic
    Rolling cap: 500 lines

tmp/thematic_exit_log.jsonl
    Writers: _check_thematic_exits(execute=True)
    Readers: /api/thematic/auto/exit-log
    Appended only (never truncated)

tmp/thematic_portfolio_{hash}.json
    Writers: add_position, edit_position, approve_signal
    Readers: thematic_paper_trade (conviction lookup), portfolio display
    Per-user: one file per user (SHA256 hash of email)

positions_{hash}.json (manual portfolio)
    Writers: add/remove position API endpoints
    Readers: /api/portfolio
    Completely independent — no connection to paper or thematic

feedback_tracker.json
    Writers: paper runner (after each closed trade)
    Readers: AlphaEngine (aggression multiplier)
    PaperFeedbackTracker: 30-trade rolling window
```

### Capital Flow

```
Paper account starting cash: $10,000
Thematic trade injects deduct from: state.json .cash + .settled_cash
Thematic exit adds back to:         state.json .cash + .settled_cash
Fidelity trade: independent real brokerage account (no JSON state)
Manual portfolio: independent, $100,000 starting, no connection to paper
```

### Signal → Execution Path

```
Thematic Auto scan (every 4h if THEMATIC_AUTO_SCAN=true)
    → _run_scan()
    → _ai_pick() [Cloudflare AI or OpenRouter fallback]
    → thematic_signals.json (pending queue)
    → SMS notify (if hil.sms_notify=True)
    → _auto_execute_confirmed_signals() [if hil.auto_trade_paper=True]
    → approve_signal() → thematic_portfolio.json + state.json

Manual HIL approval
    → POST /api/thematic/auto/signals/{id}/approve
    → approve_signal() → same endpoints
    → optionally POST /api/fidelity/thematic-trade (if fidelity_trade=True)
```

---

## 26. ML Model Status (as of Cycle 46)

*(Formerly Section 17)*

### Active Models

| Model | ROC (WF) | Status | Used In |
|-------|----------|--------|---------|
| `large_loss` | 0.73 | **ACTIVE** | Denominator penalty (`ll_penalty = ll_prob × 1.5`) |
| `win_probability` | 0.5121 | Deployed but **DISABLED from alpha numerator** | Tier gating only (B/A/A+ threshold) |
| `expected_return` | R²=0.012 | **DISABLED** | Threshold set to -99.0 |
| `timeout` | 0.40 | **DISABLED** | Anti-predictive |
| `target_before_stop` | ~0.47 | **DISABLED** | Anti-predictive |

### Training Pipeline

```
Algorithm:     XGBoost + RandomForest ensemble (0.6 XGB + 0.4 RF)
Features:      PSI-pruned (drop if PSI > 0.25 between train/test distributions)
Validation:    Walk-forward, expanding window, 6-month folds
Quality gate:  WF ROC ≥ 0.49
Calibration:   sklearn CalibratedClassifierCV(cv=None) [Cycle 3 fix from cv="prefit"]
Label:         target_hit AND NOT stop_hit within 10 days at 1.2/1.0 ATR geometry
```

### ML Feature Categories (72 total)

- Price/returns: `close_to_sma20/50/200`, `ret1/5/10/20d`, `rsi9/14`, `rsi9_slope3`
- Volatility: `atr14_pct`, `vol_accel`, `vol_trend`, `bb_width`
- Volume: `rel_vol20`, `vol_ratio`
- Pattern: `consec_up`, `consec_dn`, `high52w_pct`, `low52w_pct`
- Regime: `regime_score`, `crash_risk_score`, `risk_on_score`, `risk_off_score`
- Market: `spy_ret5/20`, `vix_level`, `sector_breadth`
- Screener: `breakout_score`, `risk_reward`

### Win Rate by Regime (with correct 1.2/1.0 ATR geometry, VIX-normal trades)

| Year | WR | E/trade |
|------|----|---------|
| 2019 | 61.0% | +0.144% |
| 2020 | 64.0% | +0.356% |
| 2021 | 56.7% | +0.039% |
| 2022 | 17.6% | -1.699% |
| 2023 | 48.4% | -0.377% |
| 2024 | 56.6% | +0.054% |
| 2025 | 55.2% | +0.075% |
| 2026 (partial) | 46.3% | -0.647% |

2022 low WR: VIX filter reduced to 34 trades. 2026 bad: Feb crash occurred DURING hold periods, no feasible entry-time filter.

### Timeout Discovery (Cycle 41)

```
timeout_trades (n≈1,349):  WR = 98.7%, avg_return = +2.14%
target_hit_trades:         WR = 100% (by definition), avg_return = +3-4%
stop_hit_trades:           WR = 0% (by definition), avg_return = -2.5%

→ Core edge: 43.9% of trades time out positive (drift/momentum continuation)
→ Not target-hitting
→ Implication: tight stops truncate this drift tail; wider stops (+1.0 ATR) preserve it
```

### Cycle Summary Table

| Cycle | Change | Evidence |
|-------|--------|---------|
| 1 | VIX low_vol filter added | low_vol E = -0.248%/trade (n=1020) |
| 2 | PSI pruning, hold=10 | retrain ROC 0.39 → 0.56 |
| 3 | ER gate disabled (-99), calibration fixed | ~50% more signals pass |
| 4 | Signal-level model deployed | 0% → 24% signals pass gate |
| 5 | min_confidence 0.58→0.55, ll_cap 0.35→0.50 | More valid signals pass |
| 6 | Target/stop 1.5/1.0 → 1.2/0.7 everywhere | E: +0.20% → +0.35%/trade |
| 7 | VIX filter in paper_trade_today.py | Actually applied to live runner |
| 8 | AlphaEngine A+ tier fixed | A+ (1.5×) sizing now accessible |
| 9 | CandidateRanker + full consistency audit | All paths aligned |
| 10 | consec_up≥2 filter | E: +0.35% → +0.53%/trade |
| 11 | Correct-labels retrain triggered | Old model labels were 0.75/1.0, not 1.2/0.7 |
| 12/13 | Web API + paper_trade_unified defaults fixed | 6 params each corrected |
| 14 | XGBoost installed | RF-only → XGB+RF ensemble |
| 15 | Regime score features in ML row | Training/inference gap closed |
| 16/16b | Retrain filters aligned (min_price, min_adv, VIX) | Production-relevant training data |
| 18 | min_risk_reward filter in backtest | Better training label quality |
| 19 | Skip-Thursday filter | Thu WR=50.4% vs 57.4% (z=-3.5, p<0.0002) |
| 25/26 | win_prob removed from alpha numerator | WF HC WR=39.5% anti-predictive |
| 34 | stop_atr_mult 0.7→1.0 (ExitManager) | False stops in pullbacks |
| 38 | Tier thresholds recalibrated | For new formula range without win_prob |
| 41 | Timeout discovery | 98.7% WR on timeout trades |
| 42 | vol_penalty threshold 3%→4% | ATR 3-4% stocks consistently better |
| 43 | UnifiedBrain consistency fixes | Sync with live AlphaEngine values |
| 44 | _ATR_STOP 0.7→1.0 in screener | +0.117%/trade on 1554-trade replay |
| 45 | ~40 portfolio audit fixes across 15 files | Correctness/coherence/safety |
| 46 | Remove min-price/min-adv from training backtest | 420-row retrain → gate fail fixed |

---

*Last updated: 2026-06-02*  
*Source files: tradingagents/portfolio/*, web/api/thematic_*.py, scripts/simulate_rule_based.py, scripts/paper_trade*.py*
