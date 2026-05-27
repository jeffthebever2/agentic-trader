# Portfolio Sizing & Exit Plan
**Created:** 2026-05-26  
**Goal:** Risk-adjusted allocation that gives more capital to better candidates, dynamic exits, auditable decisions

---

## 1. Current State Audit

### What exists and works
| Component | File | Status |
|-----------|------|--------|
| `calculate_dynamic_size()` — Kelly + ML confidence + streak + TOD + drawdown + regime + ADV cap | `tradingagents/portfolio/position_sizing.py` | ✅ Solid |
| `_ml_composite_score()` — p × er_boost × tbs × (1−ll×0.5) × (1−to×0.3) | `scripts/paper_trade_today.py` | ⚠ Incomplete |
| Regime sizing factor (bear 0.5×, neutral 0.75×, bull 1.0×) | `paper_trade_today.py` | ✅ |
| Drawdown-adjusted sizing (−10% DD → 0.5× size) | `paper_trade_today.py` | ✅ |
| Streak adjustment (3 losses → 0.5×, win streak → 1.2×) | `position_sizing.py` | ✅ |
| Heat cap (max 80% deployed) | `paper_trade_today.py` | ✅ |
| Sector concentration gate | `paper_trade_today.py` | ✅ |
| VWAP gate | `paper_trade_today.py` | ✅ |
| RVOL gate (< 1.8) | `paper_trade_today.py` | ✅ |
| Sector RS gate (sector 20d return ≥ −5%) | `paper_trade_today.py` | ✅ |
| Portfolio hard stop (max_portfolio_drawdown) | `paper_trade_today.py` | ✅ |
| Daily loss limit | `paper_trade_today.py` | ✅ |
| Scale-in logic | `paper_trade_today.py` | ✅ |
| Trailing stop (ATR-based after breakeven) | `paper_trade_today.py` | ✅ |
| Breakeven stop (move to entry at +1 ATR) | `paper_trade_today.py` | ✅ |
| Partial profit taking | `paper_trade_today.py` | ✅ |
| `CorrelationAnalyzer` | `tradingagents/portfolio/correlation.py` | ✅ exists but not called |
| `DrawdownMonitor` | `tradingagents/portfolio/drawdown.py` | ✅ exists but not called |

### Critical flaws
| Flaw | Severity | Impact |
|------|----------|--------|
| `risk_per_trade_pct = 0.0` default — ATR/dollar-risk sizing path **disabled** | **Critical** | All trades sized equally by % of account, ignoring stop distance |
| `min_risk_reward = 0.6` default — almost no trades filtered | **High** | Accepting 3:5 risk:reward setups (lose more than you win) |
| `_ml_composite_score` ignores volatility penalty (atr_pct), regime quality, ticker reliability | **High** | Low-quality/high-vol candidates rank same as clean setups |
| `large_loss_probability` not enforced as hard cap in `scan_account_once` entry path | **High** | Trade can pass ML gate but still have 40% large-loss probability |
| `expected_return` not used in position sizing | **Medium** | High expected return doesn't translate to larger size |
| No structured per-decision audit log | **Medium** | Cannot audit why a specific size was chosen |
| `CorrelationAnalyzer` never called in live path | **Medium** | Can hold 5 correlated tech stocks simultaneously |

---

## 2. Required Changes

### Priority 1: Enable ATR/dollar-risk sizing (fix the biggest gap)
**File:** `tradingagents/portfolio/position_sizing.py`  
**Change:** Default `risk_per_trade_pct` to read from args, add explicit check; add `expected_return` parameter to boost size for high-expected-return trades; add `large_loss_probability` parameter to cap size.  
**New behavior:** shares = floor(risk_dollars / stop_dist), then cap by max allocation. Blend only when ATR/stop is unavailable.

### Priority 2: Fix min R:R default
**File:** `scripts/paper_trade_today.py`  
**Change:** `--min-risk-reward` default: `0.6` → `1.5`  
**Also:** Enable `--risk-per-trade-pct` default: `0.0` → `1.0`  

### Priority 3: Improve candidate ranking score
**File:** `tradingagents/portfolio/candidate_ranker.py` (new)  
**Formula:**
```
composite = (win_prob * expected_return_boost * tbs * regime_score) 
            / (1.0 + ll * 1.5 + volatility_penalty + correlation_penalty)
```
Where:
- `win_prob`: calibrated ML win probability
- `expected_return_boost = 1.0 + clip(expected_return, -0.5, 3.0)`
- `tbs`: target_before_stop_probability (path quality)
- `regime_score`: 1.0 bull, 0.75 neutral, 0.5 bear
- `ll`: large_loss_probability
- `volatility_penalty = clip(atr_pct / 0.03 - 1.0, 0.0, 2.0)` (ATR > 3% penalized)
- `correlation_penalty`: 0.2 per highly correlated existing position

### Priority 4: `large_loss_probability` hard cap in entry logic
**File:** `scripts/paper_trade_today.py`  
**Change:** In `scan_account_once`, before sizing, check `candidate.large_loss_probability`. If > `--ml-large-loss-max` (default 0.35), reject with audit log entry.

### Priority 5: Add `CandidateRanker` module
**File:** `tradingagents/portfolio/candidate_ranker.py` (new)  
**Purpose:** Standalone ranking with audit trail. Returns ranked list with per-candidate scores and rejection reasons.

### Priority 6: Dynamic exit level calculator
**File:** `tradingagents/portfolio/exit_manager.py` (new)  
**Purpose:** Compute stop/target from ATR, confidence, expected return, and risk constraints.

### Priority 7: Structured sizing audit log
**File:** `scripts/paper_trade_today.py`  
**Change:** After each sizing decision (buy OR skip), log a structured JSON record with all factors used.

---

## 3. Sizing Formula

### Dollar-risk sizing (PRIMARY path — enabled when stop is available)
```python
risk_dollars = portfolio_value * risk_pct / 100.0     # e.g. 1% of $10k = $100
stop_distance = entry_price - stop_price              # e.g. $50.00 - $48.50 = $1.50
shares = floor(risk_dollars / stop_distance)          # $100 / $1.50 = 66 shares
# Cap by max allocation
max_shares_alloc = floor(portfolio_value * max_alloc_pct / entry_price)
# Cap by settled cash
max_shares_cash = floor(settled_cash / entry_price)
# Cap by ADV liquidity (1% of 20-day avg volume)
max_shares_adv = floor(adv * 0.01)
# Final
shares = min(shares, max_shares_alloc, max_shares_cash, max_shares_adv)
```

### Expected return boost (within cap)
```python
# High expected return → allow up to cap_max
# Low/negative expected return → cap at cap_min
er = clip(candidate.expected_return or 0.0, -0.5, 2.0)
er_alloc_boost = 0.0 + er / 4.0  # +0% at er=0, +50% at er=2.0
final_alloc = min(cap_max, cap_min + (cap_max - cap_min) * ml_confidence_t + er_alloc_boost)
```

### Regime sizing multipliers
| Regime | Factor |
|--------|--------|
| bull / uptrend | 1.0 |
| neutral / sideways | 0.75 |
| bear / downtrend | 0.50 |
| unknown | 0.80 |

### Drawdown reduction
| Account Drawdown | Size Factor |
|-----------------|-------------|
| 0 to −5% | 1.0 → 0.75 (linear) |
| −5% to −10% | 0.75 → 0.50 (linear) |
| > −10% | 0.50 (floor) |

---

## 4. Dynamic Exit Levels

### Stop loss (primary: ATR-based)
```python
stop = entry_price - atr * stop_atr_mult  # default mult = 1.0
# Widen for high volatility (atr_pct > 3%), tighten for low volatility
# But risk_dollars = shares * stop_distance stays constant
```

### Take profit (primary: ATR-based, confidence-adjusted)
```python
target = entry_price + atr * target_atr_mult  # default mult = 0.75
# High ML confidence → extend target
if ml_probability > 0.70:
    target = entry_price + atr * (target_atr_mult * 1.3)
# High expected return → extend target
if expected_return is not None and expected_return > 0.02:
    target = max(target, entry_price * (1 + expected_return))
# Minimum R:R constraint
min_rr = 1.5  # configurable
required_target = entry_price + (entry_price - stop) * min_rr
target = max(target, required_target)
```

### Trailing stop (after breakeven)
```python
# Activated after price clears entry + 1 ATR
trail_stop = peak_price - atr * trail_atr_mult  # default 0.5
# Never lower than last stop
```

### Partial take profit
```python
# Sell partial_profit_fraction at partial_profit_pct gain
# Default: sell 33% at +1R, hold rest to target
```

---

## 5. Guardrails

| Guardrail | Trigger | Action |
|-----------|---------|--------|
| Large loss cap | large_loss_probability > 0.35 | Reject trade |
| Min R:R | live_reward/live_risk < 1.5 | Reject trade |
| Max heat | deployed > 80% of account | Stop new entries |
| Portfolio drawdown | account_drawdown < −5% | Reduce all sizes 50% |
| Portfolio hard stop | account_drawdown < −max_dd | Flatten all positions |
| Daily loss | today_pnl < −daily_loss_limit% | Stop new entries today |
| Correlation | >2 existing positions with corr>0.70 | Reduce size or reject |
| Loss streak 3+ | 3 consecutive losses | 50% size until 1 win |
| Bad regime | SPY bear/downtrend | 50% size |
| Earnings blackout | earnings within 3 days | Skip |
| Stale data | signal_date != today | Skip |

---

## 6. Audit Log Format

Per sizing decision, emit:
```json
{
  "type": "SIZING_DECISION",
  "ticker": "AAPL",
  "decision": "BUY" | "SKIP" | "REDUCE",
  "scan_date": "2026-05-26",
  "timestamp": "2026-05-26T10:31:45",
  "inputs": {
    "price": 180.25,
    "stop": 177.50,
    "target": 184.00,
    "atr": 2.75,
    "atr_pct": 0.015,
    "ml_probability": 0.68,
    "large_loss_probability": 0.18,
    "expected_return": 0.032,
    "target_before_stop_probability": 0.45,
    "composite_score": 0.412,
    "spy_regime": "bull",
    "account_value": 10000.00,
    "settled_cash": 7500.00,
    "account_drawdown": -0.02
  },
  "sizing": {
    "risk_pct": 1.0,
    "risk_dollars": 100.00,
    "stop_distance": 2.75,
    "atr_shares": 36,
    "cap_shares": 55,
    "cash_shares": 41,
    "adv_shares": 9999,
    "regime_factor": 1.0,
    "dd_factor": 0.98,
    "streak_factor": 0.85,
    "ml_boost": 1.18,
    "final_shares": 36,
    "final_notional": 6489.00,
    "final_pct_of_account": 0.065,
    "risk_dollars_actual": 99.00,
    "reward_risk_ratio": 1.73
  },
  "rejection_reason": null
}
```

---

## 7. Files Changed

| File | Change | Status |
|------|--------|--------|
| `PORTFOLIO_SIZING_PLAN.md` | This document | ✅ Done |
| `tradingagents/portfolio/candidate_ranker.py` | New: composite ranking with audit trail | ✅ Done |
| `tradingagents/portfolio/exit_manager.py` | New: dynamic stop/target calculator | ✅ Done |
| `tradingagents/portfolio/position_sizing.py` | Add expected_return, large_loss_prob params; ATR dollar-risk primary path; er cap boost | ✅ Done |
| `scripts/paper_trade_today.py` | Fixed defaults (min_rr=1.5, risk_pct=1.0), added large_loss gate, replaced `_ml_composite_score` with `CandidateRanker`, added SIZING_DECISION audit log | ✅ Done |

---

## 8. Test/Backtest Commands

### Validate sizing changes with dry-run paper trade
```bash
python scripts/paper_trade_today.py \
  --once \
  --max-tickers 50 \
  --risk-per-trade-pct 1.0 \
  --min-risk-reward 1.5 \
  --position-cap-pct 20.0 \
  --position-cap-min-pct 5.0 \
  --ml-large-loss-max 0.35 \
  --tickers all_tickers.txt
```

### Validate ranking output (check audit log)
```bash
python -c "
import json
from pathlib import Path
log = Path('paper_accounts/confirmed_pullback/event_log.json')
if log.exists():
    events = json.loads(log.read_text()).get('events', [])
    sizing = [e for e in events if e.get('type') == 'SIZING_DECISION']
    for s in sizing[-5:]:
        print(json.dumps(s, indent=2))
"
```

### Backtest with realistic cost model (verify R:R distribution)
```bash
python backtest.py \
  --tickers all_tickers.txt \
  --start 2025-01-01 \
  --end 2026-05-07 \
  --hold-periods 3 5 10 \
  --primary-hold 3 \
  --account-commission 1.0 \
  --account-slippage-bps 5.0 \
  --target-mult 0.75 \
  --stop-mult 1.0 \
  --threshold 70 \
  --no-charts
```

---

## 9. Success Criteria

- [ ] `risk_per_trade_pct = 1.0` default — shares computed from dollar-risk / stop-distance
- [ ] `min_risk_reward = 1.5` default — no sub-1.5R trades
- [ ] `large_loss_probability > 0.35` → hard reject in entry path
- [ ] Candidates ranked by composite score including regime, atr_pct penalty
- [ ] Structured `SIZING_DECISION` log entry per entry/skip
- [ ] High-expected-return candidates get more capital (within cap)
- [ ] Low-confidence candidates get less capital (above floor)
- [ ] No weakening of existing risk controls (stop, max_positions, heat cap)
