# Unified Alpha Engine — Design Plan
*Created: 2026-05-26*

---

## 1. Current State Map

### Candidate flow (paper_trade_today.py)
```
build_candidates()
  ↓  screener.py: StockScreener → ScreenResult (score, entry, stop, target, atr)
  ↓  _apply_ml(): ML bundle → ml_probability, expected_return, large_loss_prob, tbs_prob
  ↓  _score_ticker(): gate checks → Candidate
  ↓  candidates.sort(key=_ml_composite_score)
  ↓  CandidateRanker.rank() → RankedCandidate (composite_score, rejected, rejection_reason)

scan_account_once()
  ↓  SafeTradeGuard.check() → halt?
  ↓  MarketRegimeEngine → no_trade / regime_size_factor
  ↓  for each candidate in ranked order:
       - price gate (entry proximity)
       - earnings blackout
       - sector RS filter
       - sector concentration gate
       - CandidateRanker hard rejection
       - heat cap
       → PositionSizer.calculate_dynamic_size()
       → account.buy()
```

### Component inventory

| Component | File | What it does | Gaps |
|-----------|------|--------------|------|
| `_ml_composite_score()` | paper_trade_today.py:3117 | Simple sort key: p × er × tbs × (1-ll) × (1-to) | No breakout, no regime, no reliability |
| `CandidateRanker.score_one()` | candidate_ranker.py | win_prob × er_boost × tbs / (1 + ll_pen + vol_pen + to_pen) × reliability | No breakout, no correlation, no tier |
| `PositionSizer.calculate_dynamic_size()` | position_sizing.py | Kelly + ML + streak + ToD + drawdown + regime + ATR risk + ADV cap | Not tier-aware |
| `ExitManager.calculate()` | exit_manager.py | ATR stop/target + confidence extension + ER anchor + min RR + trail + partial | No breakout invalidation level |
| `TickerReliabilityTracker` | ticker_reliability.py | Per-ticker blended win rate from paper trades | Not wired to aggression reduction |
| `SafeTradeGuard` | safe_trade_guard.py | Crisis VIX / regime / drawdown / drift / WR floor halt | No retrain recommendation |
| `CorrelationAnalyzer` | correlation.py | Live correlation matrix check | NOT called in scan_account_once |
| `MarketRegimeEngine` | screening/market_regime.py | Probabilistic regime state | size_factor + no_trade wired; max_open_trades wired |
| `BreakoutScanner` | screening/breakout_scanner.py | 4-component 0-100 score + breakout type | **Not used in paper_trade_today.py** |

### Critical gaps
1. **No unified alpha_score** — CandidateRanker.composite_score ≠ final sort key in paper_trade_today; `_ml_composite_score` is used for sort and misses breakout + regime + reliability
2. **No candidate tiers** — binary pass/fail; all passing candidates get same treatment
3. **Breakout score never enters live sizing** — BreakoutScanner exists but is not wired to paper trading
4. **CorrelationAnalyzer not called** in scan loop (imported but only used in build_candidates to filter)
5. **Paper feedback loop is one-way** — drift detected in SafeTradeGuard but does not reduce aggression or fire retrain recommendation
6. **Audit trail incomplete** — CANDIDATE_EVALUATED event missing; rejection reasons not always structured
7. **ExitManager not aware of breakout invalidation level** from BreakoutScanner

---

## 2. Unified Alpha Score Formula

```
              win_prob × er_boost × tbs_prob × regime_score × breakout_boost
alpha_score = ─────────────────────────────────────────────────────────────── × ticker_rel × feedback_mult
              1 + ll_penalty + vol_penalty + timeout_penalty + corr_penalty + liquidity_penalty
```

### Factor definitions

| Factor | Source | Formula | Range |
|--------|--------|---------|-------|
| `win_prob` | ML model calibrated | `ml_probability` clipped [0,1] | [0, 1] |
| `er_boost` | ML model | `1 + clip(expected_return, -0.5, 3.0)` | [0.5, 4.0] |
| `tbs_prob` | ML model | `target_before_stop_probability` | [0, 1] |
| `regime_score` | MarketRegimeEngine | `.regime_score` [0,1] | [0, 1] |
| `breakout_boost` | BreakoutScanner or signal | `1 + clip(breakout_score/100, 0, 1) * 0.5` = [1.0, 1.5] | [1.0, 1.5] |
| `ll_penalty` | ML model | `large_loss_probability × 1.5` | [0, 1.5] |
| `vol_penalty` | ATR/price | `max(0, atr_pct - 0.03) / 0.03 × 1.0` | [0, ∞) |
| `timeout_penalty` | ML model | `timeout_probability × 0.3` | [0, 0.3] |
| `corr_penalty` | CorrelationAnalyzer | `0.15` if high correlation else `0` | {0, 0.15} |
| `liquidity_penalty` | ADV | `0.2` if adv < min_adv else `0` | {0, 0.2} |
| `ticker_rel` | TickerReliabilityTracker | size_multiplier(score), [0.5, 1.1] | [0.5, 1.1] |
| `feedback_mult` | PaperFeedbackTracker | drift/streak aggression scalar, [0.5, 1.0] | [0.5, 1.0] |

### Candidate tiers

| Tier | alpha_score | win_prob | Extra conditions | Action | Size mult |
|------|-------------|----------|------------------|--------|-----------|
| A+   | ≥ 0.40      | ≥ 0.68   | regime_score ≥ 0.70, breakout_score ≥ 65 | Full size × 1.5 (cap hard) | 1.5× |
| A    | ≥ 0.25      | ≥ 0.60   | —                | Full size × 1.0 | 1.0× |
| B    | ≥ 0.12      | ≥ 0.52   | —                | Half size, watchlist | 0.5× |
| C    | < 0.12      | < 0.52   | —                | Reject | 0× |
| No-trade | any    | any      | regime.no_trade or crash_risk > 0.70 | Block all entries | 0× |

---

## 3. Architecture: New File

### `tradingagents/portfolio/alpha_engine.py`

```python
class AlphaEngine:
    """Unified candidate evaluation: alpha_score, tier, size_mult, audit."""

    def evaluate(
        self,
        candidate: Any,             # Candidate
        regime_state: Optional[Any],# MarketRegimeState
        ticker_reliability: float,  # from TickerReliabilityTracker
        feedback_mult: float,       # from PaperFeedbackTracker
        breakout_score: float,      # 0-100 from BreakoutScanner or 0 if unknown
        is_correlated: bool,        # from CorrelationAnalyzer
        adv: Optional[float],       # 20d avg daily volume × price
        min_adv_dollars: float,
    ) -> AlphaResult:
        ...

    def tier(self, alpha_score: float, win_prob: float, regime_score: float,
             breakout_score: float) -> str:
        ...

@dataclass
class AlphaResult:
    ticker: str
    alpha_score: float
    tier: str            # "A+", "A", "B", "C", "NO_TRADE"
    size_mult: float     # [0, 1.5] — multiplied against base size
    rejected: bool
    rejection_reason: str
    # breakdown
    win_prob: float
    er_boost: float
    tbs_prob: float
    regime_score: float
    breakout_boost: float
    ll_penalty: float
    vol_penalty: float
    timeout_penalty: float
    corr_penalty: float
    liquidity_penalty: float
    ticker_reliability: float
    feedback_mult: float
    numerator: float
    denominator: float
    audit: Dict[str, float]
```

---

## 4. Paper Feedback Tracker

### `PaperFeedbackTracker` (in alpha_engine.py or standalone)

Tracks predicted probability vs actual outcomes rolling window:

```python
class PaperFeedbackTracker:
    """Monitor model drift and provide aggression scalar."""

    def record(self, ticker: str, predicted_prob: float, won: bool, timestamp: str) -> None:
        ...

    def drift_score(self, n: int = 30) -> float:
        """Mean predicted prob - actual win rate over last N closed trades. >0 = overconfident."""
        ...

    def aggression_mult(self) -> float:
        """[0.5, 1.0] scalar. 1.0 = model on target; decays when drift > threshold."""
        ...

    def retrain_recommended(self) -> bool:
        """True when drift has been sustained for > N trades."""
        ...
```

Storage: JSON sidecar in paper account state dir `feedback_tracker.json`.

---

## 5. Integration Points

### paper_trade_today.py changes

**`build_candidates()`**:
- After ML scoring, call `BreakoutScanner.score_one_from_signals()` or use existing `score` if breakout mode to populate `candidate.breakout_score`
- Populate `candidate.alpha_tier` and `candidate.alpha_score` using AlphaEngine

**`scan_account_once()`**:
- Replace `_ml_composite_score` sort key with `alpha_score` (already computed on Candidate)
- Replace CandidateRanker hard rejection with AlphaEngine tier rejection
- Pass `tier_size_mult` into `PositionSizer.calculate_dynamic_size()` as `tier_factor`
- Emit `CANDIDATE_EVALUATED` event for every candidate (selected + rejected)
- Replace correlation check with one that uses cached data (not live download per-candidate)

**`PositionSizer.calculate_dynamic_size()`**:
- Add `tier_factor: float = 1.0` param
- Apply as additional multiplier after regime_factor but before ATR cap

**`ExitManager.calculate()`**:
- Add `invalidation_level: Optional[float] = None` param from BreakoutScanner
- If invalidation_level provided and tighter than ATR stop, use it

### Candidate dataclass additions
```python
alpha_score: float | None = None
alpha_tier: str = "C"
breakout_score: float = 0.0
```

---

## 6. Audit Trail Events

Every candidate evaluated emits `CANDIDATE_EVALUATED`:
```json
{
  "type": "CANDIDATE_EVALUATED",
  "ticker": "AAPL",
  "scan_date": "2026-05-26",
  "timestamp": "...",
  "alpha_score": 0.382,
  "tier": "A",
  "size_mult": 1.0,
  "rejected": false,
  "rejection_reason": "",
  "win_prob": 0.65,
  "expected_return": 0.042,
  "large_loss_probability": 0.08,
  "tbs_prob": 0.61,
  "timeout_probability": 0.22,
  "breakout_score": 72,
  "breakout_boost": 1.36,
  "regime": "bull",
  "regime_score": 0.95,
  "crash_risk_score": 0.02,
  "ticker_reliability": 0.70,
  "feedback_mult": 0.95,
  "ll_penalty": 0.12,
  "vol_penalty": 0.0,
  "corr_penalty": 0.0,
  "liquidity_penalty": 0.0,
  "numerator": 0.51,
  "denominator": 1.12
}
```

SIZING_DECISION (already exists) — add `tier`, `alpha_score`, `tier_size_mult`.

---

## 7. Retrain Recommendation

When `PaperFeedbackTracker.retrain_recommended()` is True:
- Log `RETRAIN_RECOMMENDED` event
- Print warning on dashboard
- No automatic retraining

Retrain command:
```bash
python scripts/retrain_weekly.py \
  --tickers all_tickers.txt \
  --months 84 \
  --hold 3 \
  --executed-weight 20 \
  --min-roc 0.56 \
  --max-brier 0.24

# After retrain, validate on walk-forward:
python scripts/validation_report.py
```

---

## 8. Files to Create / Modify

### New:
- [x] `tradingagents/portfolio/alpha_engine.py` — AlphaEngine, AlphaResult, PaperFeedbackTracker, TIER_SIZE_MULT

### Modified:
- [x] `scripts/paper_trade_today.py`:
  - Candidate dataclass: +alpha_score, +alpha_tier, +breakout_score
  - build_candidates(): AlphaEngine.evaluate() for all candidates; alpha_score used as sort key
  - scan_account_once(): tier_factor in PositionSizer call; CANDIDATE_EVALUATED audit event (all candidates reaching sizing); tier C early-exit with SKIP log
  - sell(): PaperFeedbackTracker.record() on each closed trade; RETRAIN_RECOMMENDED when drift sustained
- [x] `tradingagents/portfolio/position_sizing.py`: +tier_factor param; applied in both ATR-risk and pct-of-account paths
- [x] `tradingagents/portfolio/exit_manager.py`: +invalidation_level param; tighter of ATR stop vs invalidation wins
- [x] `tradingagents/portfolio/__init__.py`: exports AlphaEngine, AlphaResult, CandidateRanker, ExitManager, ExitLevels, PaperFeedbackTracker, RankedCandidate, TIER_SIZE_MULT, TickerReliabilityTracker
- `tradingagents/screening/__init__.py`: already exports BreakoutScanner (no change needed)

---

## 9. Anti-Cheating Checklist

- [x] alpha_score formula does not use any holdout-period outcomes as inputs (all inputs are ML outputs + market data + ticker history)
- [x] PaperFeedbackTracker only reads from live account trades (state_path = feedback_tracker.json in paper account dir)
- [x] Tier thresholds set by judgment, not optimized on holdout period (TIER_THRESHOLDS hard-coded)
- [x] breakout_score from BreakoutScanner is rule-based, not trained on holdout
- [x] No weakening of risk controls (stop distances unchanged; ExitManager only tightens via invalidation_level, never widens)
- [x] Correlation penalty adds caution, never removes it

---

## 10. Success Criteria

- Every candidate decision logged with structured alpha_score + tier + reason
- A+ candidates get ≥ 50% more size than B candidates (within caps)
- Model drift detected and aggression reduced before next scan
- Zero cases where size exceeds cap_max or heat limit
- Walk-forward validation shows tier A+ win rate > tier A > tier B
