# Unified Short-Hold Portfolio Brain — Design Plan
*Created: 2026-05-26*

---

## 1. Current Signal Sources (Inventory)

| Source | File | Output | Notes |
|--------|------|--------|-------|
| Confirmed-pullback rules | `scripts/paper_trade_today.py::_score_ticker` | `Candidate` (score, entry, stop, target, atr) | Rule-based gate |
| ML model (old bundle) | `scripts/paper_trade_today.py::_apply_ml` | ml_probability, expected_return, large_loss_prob, tbs_prob, timeout_prob | From `ml_models/latest/` |
| ML model (new challenger) | same file, ml_new path | Same schema, different bundle | From `ml_models/latest_new/` |
| Breakout scanner | `tradingagents/screening/breakout_scanner.py::BreakoutScanner` | `BreakoutResult` (score, breakout_type, features) | Already integrated in paper pipeline |
| Market regime engine | `tradingagents/screening/market_regime.py::MarketRegimeEngine` | `MarketRegimeState` (regime, regime_score, crash_risk_score, size_factor, no_trade) | SPY-based |
| AlphaEngine | `tradingagents/portfolio/alpha_engine.py::AlphaEngine` | `AlphaResult` (alpha_score, tier A+/A/B/C, size_mult, full audit) | Already wires all signals together |
| CandidateRanker | `tradingagents/portfolio/candidate_ranker.py::CandidateRanker` | `RankedCandidate` (composite_score) | Subset of AlphaEngine |
| TickerReliabilityTracker | `tradingagents/portfolio/ticker_reliability.py` | reliability score [0,1] | Per-ticker rolling win rate |
| PaperFeedbackTracker | `tradingagents/portfolio/alpha_engine.py::PaperFeedbackTracker` | feedback_mult [0.6,1.2] | Recent paper trade adjustment |
| Long-hold candidates | `build_candidates()` | `candidates_by_strategy["long_hold"]` | max_hold=20 calendar days |
| Pure AI | LLM-based pipeline | `candidates_by_strategy["pure_ai"]` | Not ML-gated |
| Exit manager | `tradingagents/portfolio/exit_manager.py::ExitManager` | exit levels (stop, target, trail, partial) | Used in scan loop |

**Key gap:** No single "unified brain" layer that:
- Accepts candidates from ALL sources per ticker
- Deduplicates (one trade per ticker, not 3)
- Applies a single canonical alpha_score
- Enforces short-hold (horizon_days ≤ 10)
- Produces a ranked pool with full audit trail per decision

---

## 2. New Module: `tradingagents/portfolio/unified_brain.py`

### 2.1 UnifiedCandidate Schema (extends existing Candidate)

```python
@dataclass
class UnifiedCandidate:
    # Identity
    ticker: str
    strategy_sources: list[str]    # all strategies that contributed (e.g. ["algorithm","combined"])
    primary_source: str            # highest-signal source

    # Entry/exit plan
    direction: str                 # "long" (short-selling not supported)
    entry: float
    stop: float
    take_profit: float
    horizon_days: int              # target hold days (1–10 in short-hold mode)
    atr: float

    # ML signals
    confidence: float              # win_probability (calibrated)
    expected_return: float         # from ML expected_return model
    large_loss_probability: float
    target_before_stop_probability: float
    timeout_probability: float

    # Quality signals
    breakout_score: float          # BreakoutScanner.score [0–100]
    regime_score: float            # MarketRegimeState.regime_score [0–1]
    ticker_reliability: float      # TickerReliabilityTracker [0–1]
    liquidity_score: float         # ADV-based [0–1]
    volatility_score: float        # ATR% inverted [0–1]

    # Composite
    alpha_score: float             # computed by UnifiedBrain.score()
    tier: str                      # A+ / A / B / C / NO_TRADE

    # Audit
    reason: str                    # why selected
    rejection_reason: str          # why rejected (empty if accepted)
    score_breakdown: dict          # per-component audit

    # Risk
    risk_dollars: float = 0.0      # sized (entry - stop) * shares
    shares: int = 0
    reward_risk: float = 0.0
```

### 2.2 Merger/Deduper

When multiple strategies produce the same ticker:
1. Collect all per-source scores and ML values
2. Take `max(breakout_score)`, `max(confidence)`, `max(expected_return)`
3. Take `min(large_loss_probability)` (be conservative)
4. Take `max(strategy_sources)` as merged list
5. Average stop, target from contributing sources (or use ATR-anchored values)
6. Mark `primary_source = source_with_highest_alpha`

### 2.3 Alpha Score Formula

```
setup_quality = (1 + breakout_boost) * tbs_prob * (1 - timeout_penalty)
numerator     = confidence * er_boost * regime_score * setup_quality * ticker_reliability
denominator   = 1 + ll_penalty + vol_penalty + corr_penalty + liq_penalty

alpha_score = numerator / denominator * feedback_mult

# Penalties
ll_penalty      = 1.5 * large_loss_probability   (capped at 0.70)
vol_penalty     = max(0, atr_pct - 0.03) * 1.0
corr_penalty    = 0.0–0.30 based on correlation to existing positions
liq_penalty     = max(0, 1 - liquidity_score) * 0.2
```

Short-hold specific adjustments:
- `horizon_days > max_hold_days` → tier = C (soft reject)
- `timeout_prob > 0.60 && horizon_days > 5` → penalty ×1.5 (setup likely to time out)
- Prefer `tbs_prob > 0.55` (target-before-stop for short hold)

### 2.4 Tier Thresholds

| Tier | alpha_score | confidence | regime_score | breakout_score |
|------|-------------|------------|--------------|----------------|
| A+   | ≥ 0.40      | ≥ 0.68     | ≥ 0.70       | ≥ 65           |
| A    | ≥ 0.25      | ≥ 0.60     | ≥ 0.50       | any            |
| B    | ≥ 0.10      | ≥ 0.52     | ≥ 0.40       | any            |
| C    | < 0.10      | any        | any          | any            |
| NO_TRADE | 0      | any        | regime.no_trade | any         |

### 2.5 Portfolio Allocator

```
For each accepted candidate (A+/A, optionally B at min size):
  1. risk_dollars = account_value * risk_pct_per_trade * tier_mult
     - A+: risk_pct * 1.5
     - A:  risk_pct * 1.0
     - B:  risk_pct * 0.4
  2. stop_distance = entry - stop
  3. raw_shares = floor(risk_dollars / stop_distance)
  4. cap_shares = floor(account_value * position_cap_pct / entry)
  5. final_shares = min(raw_shares, cap_shares, adv_cap_shares, settled_cash_shares)
  6. Apply regime_factor: bear→0.5×, neutral→0.75×, crisis→0.0×
  7. Apply vol_factor: VIX > 35→0.5×, VIX > 25→0.75×
  8. Heat check: if total deployed + new position > max_heat → skip
  9. Sector check: if sector already has max_sector_positions → skip
  10. Correlation check: if correlation to existing positions > 0.70 → skip
```

### 2.6 Short-Hold Exit Logic

```python
class ShortHoldExitPlan:
    stop: float              # invalidation level (ATR-based or user override)
    take_profit: float       # expected-return target (ATR * RR or ML-derived)
    max_hold_days: int       # default 10, hard exit regardless of price
    breakeven_trigger: float # move stop to entry after price gains 1 ATR
    trail_mult: float        # trail stop at peak - trail_mult * ATR (default 0.5)
    partial_profit_pct: float  # sell fraction at partial_trigger (default 50%)
    partial_trigger_mult: float  # trigger partial at entry + mult * (target - entry)
    min_rr: float            # reject if (take_profit - entry) / (entry - stop) < 1.5
```

Short-hold specific:
- Max hold = 10 trading days (configurable 1–10)
- Partial take-profit at 50% of move toward target, sell 50%
- Trailing stop activates after breakeven
- Hard exit after `max_hold_days` regardless (avoids becoming a bag holder)

### 2.7 Audit Trail

Every decision (accept OR reject) written to:
- `{output_dir}/unified_brain_audit_{YYYYMMDD}.jsonl`

Schema per line:
```json
{
  "ts": "2026-05-26T10:31:00",
  "ticker": "AAPL",
  "decision": "ACCEPT|REJECT|WATCHLIST",
  "tier": "A",
  "strategy_sources": ["algorithm", "combined"],
  "alpha_score": 0.312,
  "confidence": 0.641,
  "expected_return": 0.028,
  "large_loss_prob": 0.11,
  "breakout_score": 72.0,
  "regime_score": 0.85,
  "ticker_reliability": 0.58,
  "shares": 14,
  "entry": 182.30,
  "stop": 179.80,
  "take_profit": 186.90,
  "risk_dollars": 35.00,
  "reward_risk": 1.84,
  "horizon_days": 5,
  "reason": "A-tier: conf=0.641, breakout=72, regime=bull",
  "rejection_reason": "",
  "score_breakdown": {...}
}
```

---

## 3. Files to Create

### New:
- `tradingagents/portfolio/unified_brain.py` — UnifiedCandidate, UnifiedBrain (scorer, merger, allocator)
- `tradingagents/portfolio/short_hold_exits.py` — ShortHoldExitPlan, ShortHoldExitManager
- `scripts/paper_trade_unified.py` — standalone paper runner using unified brain (does NOT delete paper_trade_today.py)

### Modified:
- `tradingagents/portfolio/__init__.py` — export new classes
- `scripts/improvement_loop.py` — add unified brain audit reporting (optional)

---

## 4. Short-Hold Mode Config

```python
SHORT_HOLD_CONFIG = {
    "max_hold_days": 10,          # hard exit after N trading days
    "min_hold_days": 1,           # minimum before exit allowed
    "horizon_target_days": 3,     # target exit window (for scoring)
    "min_rr": 1.5,                # minimum reward:risk
    "min_confidence": 0.60,       # min ML probability to trade
    "risk_pct_per_trade": 1.0,    # % of account to risk per trade
    "position_cap_pct": 20.0,     # max % of account per position
    "max_heat_pct": 75.0,         # max % of account deployed
    "max_open_positions": 5,
    "max_sector_positions": 2,
    "breakeven_trigger_atr": 1.0, # move stop to entry after 1 ATR gain
    "trail_atr_mult": 0.5,        # trail at peak - 0.5*ATR
    "partial_profit_fraction": 0.5, # sell 50% at partial trigger
    "partial_profit_trigger": 0.5,  # trigger at 50% of move to target
    "adv_cap_pct": 0.01,          # max 1% of ADV
    "reject_long_hold": True,     # reject candidates with horizon > max_hold_days
}
```

---

## 5. Paper/Shadow Mode Integration

`paper_trade_unified.py` runs:
1. Load same ticker universe and price data as paper_trade_today.py
2. Run `build_candidates()` from paper_trade_today.py (existing pipeline, unchanged)
3. Pass all candidates through `UnifiedBrain.process()`
4. Write unified audit JSONL
5. Run paper account using UnifiedBrain decisions (separate account state from existing strategies)
6. Compatible with same safety monitor, account state, and improvement loop

The existing strategies (algorithm, combined, machine_learning, ml_new, long_hold, pure_ai) remain unchanged in paper_trade_today.py.

---

## 6. Validation Plan

After ≥ 30 unified paper trades, compare:
- Unified short-hold paper win rate vs old combined strategy
- Average hold days (unified should be < 5, old combined can be up to 14)
- Risk-adjusted return (expectancy per dollar risked)
- Stop/target hit rates (unified should show more targets hit per hold-day)

Do NOT use old backtest results as proof. Holdout window (2026-05-08 → 2026-05-26) is read-once diagnostic only.

---

## 7. Retraining Note

The unified brain uses the same ML bundle as the current system. No retraining is required for this architecture change. If walk-forward ROC or Brier scores are too weak after the compliance-mandated retrain (see LIVE_TRADING_READINESS_REVIEW.md), run:

```bash
python scripts/retrain_weekly.py \
  --win-label-threshold 0.01 \
  --executed-weight 8.0 \
  --run-walk-forward \
  --calibrate
```

This is already configured in the improvement loop and does not require new config.

---

## 8. Implementation Status

- [x] UNIFIED_PORTFOLIO_BRAIN_PLAN.md created
- [x] `tradingagents/portfolio/unified_brain.py`
- [x] `tradingagents/portfolio/short_hold_exits.py`
- [x] `scripts/paper_trade_unified.py`
- [x] `tradingagents/portfolio/__init__.py` exports updated
