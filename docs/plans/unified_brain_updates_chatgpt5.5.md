# Unified Brain Updates — ChatGPT 5.5 Profitability Focus

**Date:** 2026-05-30  
**Scope:** UnifiedBrain only: `tradingagents/portfolio/unified_brain.py`, `scripts/paper_trade_unified.py`, short-hold exits, model gates, audit feedback, and portfolio construction.  
**Mode:** Read-only planning. No source-code changes implemented in this pass.  
**Target:** Find the highest-leverage path toward 30%+ annualized returns without weakening validation, hiding risk, or fabricating profitability.

---

## Executive Read

The UnifiedBrain is the right place to focus because it is the portfolio-level decision layer: it merges candidates, scores them, assigns tiers, sizes positions, applies heat/cash constraints, and writes an audit trail. That is where edge, risk, and capital deployment meet.

The current UnifiedBrain is not yet a true alpha engine. It is mostly a defensive filter:

- Win probability was disabled because it was anti-predictive out of sample.
- Expected return is not reliable enough to drive sizing.
- Large-loss probability appears to be the strongest ML signal.
- Alpha score is mostly `regime_score × breakout_boost / penalties`.
- Backtest/live exit behavior and portfolio accounting still diverge.

So the path to 30%+ is not “turn the model up.” The path is:

1. Make UnifiedBrain runnable and measurable.
2. Align live behavior with backtest labels.
3. Stop wasting capital through dead caps and dead ranking logic.
4. Use large-loss probability as the primary risk throttle.
5. Add only validated upside signals back into the numerator.
6. Prove each change through nested/purged walk-forward before deployment.

No current evidence proves the stock/ML UnifiedBrain can produce 30%+ yet. A separate leveraged-ETF branch has a documented 30%+ test result, but that is not the same system.

---

## Subagent Findings Used

Three focused read-only subagents inspected:

- UnifiedBrain architecture and integration path.
- Existing alpha logs, completed fixes, and 30%+ claims.
- ML/backtest/training limits and likely profitability blockers.

Key synthesis:

- `UnifiedBrain.process()` is the correct focus point.
- `paper_trade_unified.py` appears to call `brain.process(..., exclude_strategies=...)`, but `UnifiedBrain.process()` does not accept that parameter. This is a likely runtime blocker.
- `allocate()` hardcodes every sector as `"unknown"`, so `max_sector_positions=2` can silently cap the entire book at two positions.
- The large-loss model is useful; the win model is not yet useful.
- UnifiedBrain can create optimistic hybrid candidates by taking best fields across sources that may not belong to the same setup.
- Live exits and backtest labels are not aligned closely enough for trusted profitability numbers.
- Current model gates are too permissive for a 30%+ target.

---

## Current UnifiedBrain Flow

Primary files:

- `tradingagents/portfolio/unified_brain.py`
- `scripts/paper_trade_unified.py`
- `tradingagents/portfolio/short_hold_exits.py`
- `web/api/paper.py`
- `docs/plans/PORTFOLIO_AUDIT_2026-05-30.md`
- `docs/plans/ALPHA_EVOLUTION_LOG.md`

Flow:

1. `paper_trade_unified.py` builds candidates from the existing paper pipeline.
2. `UnifiedBrain.process()` receives candidates grouped by strategy.
3. Candidates are merged by ticker.
4. `_dedup_ticker()` creates one `UnifiedCandidate` per ticker.
5. `score_one()` applies gates and computes alpha/tier.
6. `allocate()` sizes A+/A candidates and rejects the rest.
7. `_process_entries()` opens accepted positions.
8. `ShortHoldExitManager` handles stop, target, partial, trailing, and max-hold exits.
9. Unified audit JSONL records decisions for dashboard and later analysis.

This is the right architecture. The problem is that several pieces are currently misaligned or underpowered.

---

## Why 30%+ Is Not There Yet

### 1. The win model is not an alpha source

Recent reports show near-random or anti-predictive win behavior. High-confidence buckets can underperform the base rate. That is why the code removed win probability from hard gating and tier requirements.

Implication: do not re-enable win-prob gates, A+ size boosts, or probability-driven ranking until walk-forward buckets are monotonic and high-confidence trades beat base rate.

### 2. Large-loss prediction is useful but defensive

The large-loss model appears materially stronger than the win model. That can reduce drawdowns and improve profit factor, but it does not automatically create enough positive expectancy for 30% annualized returns.

Implication: use large-loss probability as the first-order veto/sizing throttle, then add validated upside selection separately.

### 3. UnifiedBrain may be under-deploying capital

The hardcoded sector `"unknown"` means `max_sector_positions=2` can cap the whole book at two positions even when `max_open_positions=5` and `max_heat_pct=75%`.

For a short-hold strategy, 30%+ annualized requires enough turnover and enough deployed capital. A hidden two-position cap can crush CAGR even if per-trade edge improves.

### 4. Alpha score is too thin

Current alpha is mostly:

```text
regime_score × breakout_boost
--------------------------------------
1 + large_loss_penalty + vol_penalty + liquidity_penalty
```

That is sane defensively, but it is not enough for high-return selection because the numerator has little per-trade upside information.

### 5. Backtest/live exits are not identical

Backtest outcome measurement and live UnifiedBrain exits do not fully match. Live uses partials, breakeven, trailing, and max-hold behavior. If labels and simulation do not match those mechanics, the model learns a different game than the account actually trades.

### 6. Threshold search can overfit small samples

Some profitable-looking thresholds are based on small test slices. A 30% target needs nested purged walk-forward threshold selection, not threshold selection on the same period used to judge success.

---

## Highest-Impact Profitability Levers

### Wave 1 — Make UnifiedBrain Actually Tradeable

**Goal:** remove blockers that prevent valid live/paper execution.

1. Fix `paper_trade_unified.py` ↔ `UnifiedBrain.process()` API mismatch.
2. Confirm UnifiedBrain can run one full paper scan with audit output.
3. Add a smoke test that creates sample candidates and verifies:
   - merge works
   - score works
   - allocation works
   - audit writes
   - no unexpected `TypeError`

Profit impact: indirect but mandatory. If the brain path cannot run reliably, no 30% plan matters.

### Wave 2 — Restore Capital Utilization Without Increasing Blind Risk

**Goal:** allow the brain to use capital when independent opportunities exist.

1. Add real sector to `UnifiedCandidate` and positions, or temporarily disable sector cap until real sector data exists.
2. Replace the `"unknown"` sector cap behavior so the whole book is not capped at two positions.
3. Add correlation/concentration checks before increasing from 2 to 4-5 concurrent positions.
4. Report capital utilization in the audit:
   - starting cash
   - settled cash
   - heat before allocation
   - heat after allocation
   - max heat rejected count
   - sector/correlation rejected count

Profit hypothesis: if edge is positive, moving from hidden 2-position cap to diversified 4-5 position exposure can raise CAGR materially. This is one of the few levers that can move annualized return without pretending per-trade edge improved.

### Wave 3 — Align Backtest Labels With UnifiedBrain Exits

**Goal:** make training and validation measure the same mechanics the brain trades.

1. Simulate UnifiedBrain exit rules in backtest:
   - initial stop
   - target
   - partial profit at configured trigger
   - breakeven move
   - trailing stop
   - max hold
   - commissions and slippage
2. Rebuild outcome columns using those same rules.
3. Replace or supplement `_win_label = h_return > 0.5%` with account-relevant labels:
   - net R multiple after costs
   - capital-day adjusted return
   - large-loss event
   - target-before-stop under live exits
   - SPY-relative return

Profit hypothesis: current labels may reward trades that look good in fixed-window return but are weak after capital tie-up, stop behavior, and partial exits. Better labels can turn ML from defensive filtering into useful selection.

### Wave 4 — Treat Large-Loss Probability As The Core Risk Throttle

**Goal:** use the one ML signal that has evidence.

1. Keep win probability disabled until restoration gates are met.
2. Make large-loss probability the primary veto and size throttle.
3. Tune `ll_hard_cap` with purged walk-forward only.
4. Avoid double/triple-penalizing the same large-loss signal across score, rank, and size unless validation proves the compounding helps.
5. Audit each accepted trade with:
   - `large_loss_probability`
   - final size multiplier from LL
   - reason if LL vetoed

Profit hypothesis: better loss avoidance improves geometric growth by reducing deep drawdowns and keeping position sizing viable.

### Wave 5 — Add A Real Upside Numerator

**Goal:** move from “avoid bad trades” to “select better winners.”

Candidate numerator signals to validate independently:

1. `target_before_stop_probability`
2. breakout score
3. regime-specific momentum acceleration
4. relative strength vs SPY
5. volume dry-up / expansion transition
6. setup RR geometry
7. ticker reliability posterior
8. day-of-week and VIX regime interactions

Strict restoration rule:

Only add a signal into alpha numerator if it passes:

- Purged walk-forward ROC or rank IC positive.
- High-score bucket beats base win rate.
- Net return after costs improves.
- Drawdown does not worsen beyond tolerance.
- Effect persists across at least two regimes or is explicitly regime-routed.

Profit hypothesis: 30%+ requires a positive selection signal, not just fewer bad trades.

### Wave 6 — Split The Brain By Regime

**Goal:** stop one unified score from washing out different edges.

Recommended route:

1. Keep one UnifiedBrain allocator.
2. Use regime-specific scoring adapters:
   - normal VIX pullback
   - elevated VIX rebound
   - crash rebound
   - low-vol no-trade or tiny-size mode
3. Each adapter has its own:
   - threshold
   - target/stop preference
   - max hold
   - large-loss cap
   - allowed size factor

Profit hypothesis: pullbacks, rebounds, and breakout continuations likely have different predictors. A single score can average them into mediocrity.

### Wave 7 — Make Threshold Selection Nested

**Goal:** prevent “great backtest, bad live” from threshold overfit.

Required validation:

1. Outer purged walk-forward for final evaluation.
2. Inner purged walk-forward for threshold/tier/size selection.
3. No threshold selected on the final test fold.
4. Report:
   - fold-by-fold CAGR
   - max drawdown
   - profit factor
   - exposure
   - trade count
   - turnover
   - average capital-days
   - high-confidence bucket lift

Deployment gate for 30% target:

```text
Median fold CAGR >= 30%
Worst fold CAGR > 0%
Max drawdown <= 25-30%
Profit factor >= 1.25
Trades per year enough to avoid tiny-sample illusion
High-score bucket beats base rate OOS
```

---

## Concrete 30%+ Roadmap

### Phase 0 — Do Not Tune Yet

Before tuning, fix measurement and execution blockers:

- UnifiedBrain process API mismatch.
- Sector cap `"unknown"` behavior.
- Backtest/live exit mismatch.
- Stop/target geometry consistency.
- Audit fields needed for grader.
- Account-level MTM and drawdown truth.

### Phase 1 — Baseline UnifiedBrain Truth Run

Run one clean baseline:

```text
Universe: all_tickers or documented liquid subset
Window: 2019-present
Evaluation: purged walk-forward
Costs: commission + slippage
Exit model: exact UnifiedBrain short-hold exits
Sizing: current production sizing
Metrics: CAGR, max DD, PF, exposure, capital utilization, trade count
```

This becomes the reference. No change is “good” unless it beats this reference OOS.

### Phase 2 — Capital Utilization Experiment

Test:

- current hidden sector behavior
- real sector cap
- no sector cap but correlation cap
- 3, 4, 5 max positions
- 50%, 65%, 75% max heat

Look for the best CAGR/DD tradeoff, not the highest raw CAGR.

### Phase 3 — Large-Loss First

Sweep large-loss controls:

- hard veto thresholds
- size haircuts
- regime-specific LL caps
- no double-penalty variant

Success means higher geometric return and lower drawdown, not just better win rate.

### Phase 4 — Upside Signal Bakeoff

Each candidate upside signal must be tested alone before combining:

- TBS probability
- breakout score
- relative strength
- momentum acceleration
- RR geometry
- volatility expansion
- regime interaction

Add only signals with stable OOS lift.

### Phase 5 — Regime-Routed Brain

Build a single allocator with multiple scoring profiles:

- low VIX: skip or micro-size
- normal VIX: pullback continuation
- elevated VIX: faster target, tighter max hold
- crash rebound: separate rebound rules

Success means each route has its own OOS report.

### Phase 6 — Deployment Gate

Do not deploy 30% mode unless:

- OOS median CAGR clears 30%.
- OOS drawdown is tolerable.
- Capital utilization is high enough.
- Trade count is sufficient.
- Live/paper audit agrees with simulated behavior.
- Safety halts are stricter, not weaker.

---

## Specific UnifiedBrain Upgrade Ideas

### 1. Candidate Integrity

Current dedup can combine best ML fields from one source with entry/stop/target from another. That can create a candidate that never existed.

Plan:

- Preserve source-level candidate bundles.
- Score each source candidate independently.
- Merge only after each candidate has a coherent score.
- If merging, audit which source supplied each field.

Expected benefit: removes optimistic hybrid candidates and improves live/backtest agreement.

### 2. Sector And Correlation-Aware Allocation

Plan:

- Add `sector` to `UnifiedCandidate`.
- Carry sector into paper `Position`.
- Enforce sector caps only when sector is known.
- Add correlation cap before buy.
- Rank accepted candidates by alpha per unit of correlation risk.

Expected benefit: allows more positions without hidden concentration.

### 3. Remaining-Risk Budget Sizing

Current batch sizing uses full `account_value` for every candidate’s risk dollars.

Plan:

- Track remaining risk budget during allocation.
- Size candidate N from remaining heat/risk capacity.
- Audit planned vs actual risk.

Expected benefit: smoother portfolio heat and less accidental front-loaded risk.

### 4. Large-Loss Sizing Curve

Instead of hard-only veto:

```text
ll_prob <= 0.15: full size
0.15-0.30: 70% size
0.30-0.50: 35-50% size
>0.50: reject
```

Exact breakpoints must be walk-forward selected.

Expected benefit: keeps some positive-expectancy trades while controlling tail risk.

### 5. Continuous VIX Low-Vol Taper

Current VIX low-vol skip is a cliff under 15.

Plan:

- VIX < 15: skip or micro-size
- 15-18: linear taper
- 18-25: normal
- 25-35: elevated-vol profile
- >35: crisis profile or no-trade except validated rebound route

Expected benefit: less threshold noise around VIX 15.

### 6. Score Formula v2

Candidate formula after validation:

```text
alpha =
  regime_route_score
  × validated_upside_signal
  × breakout_or_pullback_quality
  × reliability_multiplier
  × feedback_multiplier
  ÷ (1 + large_loss_penalty + volatility_penalty + liquidity_penalty + correlation_penalty)
```

Important: `validated_upside_signal` must not be win probability unless the new model passes restoration gates.

---

## What Not To Do

Do not:

- Re-enable win probability because it “feels” like ML should help.
- Lower quality gates to pass a retrain.
- Optimize thresholds on the same period used for final reporting.
- Claim 30%+ from tiny trade counts.
- Trust CAGR before exposure and capital-days are reported.
- Increase size before stop fills, MTM drawdown, and live/backtest exits are aligned.
- Deploy a model with high-confidence buckets below base rate.

---

## Success Metrics

For every UnifiedBrain experiment, report:

- CAGR
- total return
- max drawdown
- profit factor
- Sortino
- Calmar
- trade count
- trades/year
- average exposure
- max heat
- average capital-days per trade
- win rate
- average win/loss
- expectancy per trade
- expectancy per capital-day
- SPY-relative return
- fold-by-fold OOS metrics
- high-score bucket lift
- large-loss rate
- stop-hit rate
- target-hit rate
- partial-hit rate
- trailing-stop exit rate

30%+ is not meaningful unless it survives those metrics.

---

## Final Recommendation

Focus all near-term effort on UnifiedBrain, but sequence it like this:

1. **Fix runability and audit truth.**
2. **Fix portfolio utilization.**
3. **Align exits and labels.**
4. **Use large-loss probability as the main ML control.**
5. **Validate upside signals one by one.**
6. **Route scoring by regime.**
7. **Only then pursue a 30%+ deployment profile.**

The fastest credible path to 30%+ is not a bigger model. It is a cleaner UnifiedBrain that can deploy more capital into independent, validated, short-hold edges while cutting tail losses aggressively.

---

## Deep Search Addendum — 10-Subagent Pass

**Date:** 2026-05-30  
**Mode:** Read-only deeper search. No code changes implemented here.  
**Purpose:** Refine the roadmap with concrete blockers and profit levers found by a 10-subagent fanout.

### A. New Priority Stack

The deeper pass changes the immediate priority order:

1. **Make the UnifiedBrain runner complete one real cycle.**
2. **Stop synthetic merged candidates from overstating edge.**
3. **Make paper/live exits equivalent to backtest labels.**
4. **Fix allocator heat/sector/cash/liquidity behavior.**
5. **Keep ML as a risk filter until upside heads prove lift.**
6. **Promote target-geometry mismatch as a top validation blocker.**

### B. Hard Runability Blockers

The UnifiedBrain path appears likely to fail before a valid trade opens:

- `scripts/paper_trade_unified.py` passes `exclude_strategies` into `UnifiedBrain.process()`, but `process()` does not accept that parameter.
- `paper_trade_unified.py` calls `account.buy(ticker, price, shares, now)`, while `PaperAccount.buy()` expects a candidate-like object and reads candidate fields.
- `paper_trade_unified.py` constructs trackers with stale keyword arguments.
- `paper_trade_unified.py` calls `build_candidates()` with an old signature and expects too many return values.
- The web API lists `unified_brain`, but process start/monitor paths are still centered on `paper_trade_today.py`.
- Unified output directories and web status collection use different layout assumptions.

Until these are fixed, 30%+ research on UnifiedBrain is blocked because the primary execution path is not trustworthy.

### C. Candidate Merge Can Create Synthetic Edge

`_dedup_ticker()` currently takes the best field across all source candidates:

- max confidence
- max expected return
- min large-loss probability
- max target-before-stop probability
- min timeout probability
- max breakout score

Then it takes entry/stop/target from a separately selected `best_c`.

That can create a candidate that never existed as a single tradable setup. The low loss probability may come from one source, the high target-before-stop probability from another, and the stop/target geometry from a third. This can inflate backtest expectations and make audit rows impossible to reconstruct.

Roadmap update:

- Score each source candidate atomically first.
- Select one complete source bundle per ticker.
- Treat multi-source agreement as a separate confirmation feature.
- Add audit provenance for every selected field.
- Distinguish independent sources from copied/derived strategy membership.

### D. Allocation Bugs Can Both Reduce CAGR And Raise Risk

Allocation issues discovered:

- Sector is hardcoded to `"unknown"`, so `max_sector_positions=2` can cap the whole book at two names while failing to control real sector concentration.
- `max_heat` is checked before sizing, but the next accepted buy is not clamped to remaining heat. A portfolio at 70% heat with 75% max heat can still accept a 20% position and jump to roughly 90%.
- `adv_cap_pct` is read but no ADV cap is applied because `adv_shares` is hardcoded high.
- Settled-cash checks omit commission.
- B-tier sizing config exists but B-tier candidates are watchlist only in the main process.

Roadmap update:

- Carry real sector into `UnifiedCandidate` and positions, or disable sector caps until sector data exists.
- Clamp shares to remaining heat and remaining cash after estimated costs.
- Wire real ADV/liquidity caps or remove the dead config.
- Decide whether B-tier is tradable based on OOS expectancy, not config leftovers.

### E. Exit Path Is Not Label-Equivalent

UnifiedBrain paper/live exits do not match backtest/training assumptions:

- Paper entries use current sampled price, while exit plans use stale signal `uc.entry`, `uc.stop`, and `uc.take_profit`.
- Backtest checks OHLC path for intraday stop/target touches; paper mostly checks latest sampled close.
- Backtest exits at stop/target levels; paper ignores `result.exit_price` and sells at sampled price.
- Partial exits compute shares-to-sell but call full `account.sell()`, liquidating the whole position.
- Live Unified exits include partials/trailing behavior that fixed stop/target/time labels do not model.
- Hold-period intent is mixed: horizon target 3d, max hold 10d, labels often 10d or sometimes 5d depending training mode.

Roadmap update:

- Fix paper exit mechanics before using paper PnL as evidence.
- Re-anchor exit plans to actual fill price.
- Simulate the same partial/breakeven/trailing policy in backtest labels.
- Store exact exit reason, exit level, sampled price, and whether the event was stop/target/partial/trail/time.

### F. ML Trust Hierarchy

Current model outputs should be treated this way:

| Output | Use In UnifiedBrain | Reason |
|---|---|---|
| `large_loss_probability` | Trust as risk penalty/cap | Strongest evidence; useful ROC/calibration versus other heads |
| `win_probability` | Ignore for positive alpha | Near-random or anti-predictive WF evidence |
| `expected_return` | Audit only | Negative/weak R2 |
| `target_before_stop_probability` | Audit only for now | Near-random ROC in latest evidence |
| `timeout_probability` | Audit only for now | Sparse/unstable and previously anti-predictive |

Roadmap update:

- Do not use win probability, expected return, TBS, or timeout as positive score drivers until walk-forward proves bucket lift.
- Do not aggregate risk with optimistic `min(large_loss_probability)` across sources. Prefer max, median-high, or atomic source selection.
- Tighten production `ll_hard_cap` research around the current evidence band. Existing logs suggest `ll<=0.15` may be powerful but still needs paper/live confirmation.

### G. Updated Evidence From Alpha Logs

The previous roadmap should be refined with newer log evidence:

- Stop geometry has reportedly moved from `0.7 ATR` to `1.0 ATR`; treat that as implemented but still requiring live/paper validation.
- The realized stop-geometry lift was closer to `+0.117%/trade`, not enough to claim it alone gets near 30% annualized.
- Target geometry is now a top blocker: deployed model labels appear closer to `0.75 ATR`, while live target behavior may be `1.2 ATR`; a fresh `1.2/1.0` retrain reportedly failed quality gates.
- Cycle 43 consistency fixes appear already applied: partial trigger `0.833`, vol penalty threshold `0.04`, and tier cap sizing.
- Large-loss gate research has moved toward `ll_max=0.15`, but paper/live validation remains missing.

### H. Revised First Five Implementation Waves

1. **Runner repair wave**
   - Align `UnifiedBrain.process()` signature.
   - Fix tracker construction.
   - Fix `build_candidates()` call.
   - Add candidate adapter for `PaperAccount.buy()`.
   - Make web start/status paths understand the unified runner.

2. **Truthful candidate wave**
   - Remove best-of-everything merge.
   - Score atomic candidates.
   - Add source lineage and field provenance to audit.

3. **Exit-equivalence wave**
   - Re-anchor exits to actual fill.
   - Use `sell_partial()` for partial exits.
   - Use manager-provided exit level.
   - Backtest exact UnifiedBrain exit policy.

4. **Allocator correctness wave**
   - Fix sector behavior.
   - Clamp to remaining heat.
   - Include commission/cash.
   - Wire ADV cap.
   - Decide B-tier tradeability.

5. **ML risk-filter wave**
   - Use `large_loss_probability` conservatively.
   - Keep other ML heads out of positive alpha.
   - Validate `ll_hard_cap` and size haircuts through nested/purged WF.

### I. 30%+ Gate After Deep Search

The 30%+ target should remain locked behind:

- Unified runner completing real paper cycles.
- Exact exit-policy equivalence between backtest and paper/live.
- No synthetic candidate merging.
- OOS CAGR above 30% across purged folds.
- Max drawdown no worse than target tolerance.
- Enough trades/year to avoid tiny-sample illusion.
- Paper/live audit matching simulated PnL mechanics.

The strongest near-term insight is not a new alpha formula. It is that UnifiedBrain must first become mechanically correct and auditable. Once it is measuring the real thing, the most credible profit lever is capital-efficient deployment of low-large-loss, regime-valid setups, not reintroducing weak win-probability signals.

### J. Safety And Valuation Must Become Fail-Closed

The safety pass found that UnifiedBrain could either overstate health or keep holding through dangerous conditions:

- Unified runner uses `position.avg_price`, but positions expose `entry_price`.
- Production drawdown uses `max(starting_cash, account_value)` rather than a persisted high-water mark.
- Unified safety halts skip new entries but do not flatten on portfolio hard-stop conditions.
- Missing/stale prices can value positions at entry cost, hiding losses.
- Drawdown utilities sum unweighted `pnl_pct`, ignoring position size and open PnL.
- Reliability and feedback integrations are silently disabled by API mismatches.

Roadmap update:

- Add shared account valuation with one price snapshot per cycle.
- Persist `peak_equity` and compute drawdown from high-water mark.
- Treat missing open-position prices as fail-closed: halt entries and mark conservatively.
- Include unrealized PnL in daily/weekly/monthly risk checks.
- Split safety responses into `ENTRY_HALT` and `FORCE_FLATTEN`.
- Wrap reliability/feedback APIs so failures are explicit audit events, not swallowed defaults.

### K. Scoring Parity Must Be A First-Class Workstream

The deeper pass confirmed that UnifiedBrain, AlphaEngine, CandidateRanker, backtest, and web scanner can all rank or filter candidates differently:

- AlphaEngine breakout boost and UnifiedBrain breakout boost differ.
- AlphaEngine normalizes volatility penalty; UnifiedBrain uses raw excess ATR.
- AlphaEngine has correlation/liquidity behavior that UnifiedBrain does not match.
- CandidateRanker can reorder/reject after AlphaEngine has already tiered candidates.
- Backtest and scanner still use raw score/ML-pass style sorting, not unified alpha.
- B-tier size differs between AlphaEngine and UnifiedBrain, and UnifiedBrain does not allocate B-tier by default.

Roadmap update:

1. Make `AlphaEngine.evaluate()` or a new shared scorer the single scoring primitive.
2. Have UnifiedBrain adapt candidates into that scorer instead of duplicating constants.
3. Retire CandidateRanker as an execution decision engine or make it call the same scorer.
4. Add a parity fixture: same candidate + regime + reliability + liquidity must produce identical alpha/tier decisions across paper_today, UnifiedBrain, scanner, and backtest parity mode.
5. Keep allocation separate from scoring, but feed it shared tier and risk metadata.

### L. Validation Harness Is Missing For A 30% Proof

Current infrastructure has useful purged walk-forward pieces, but not enough to honestly prove UnifiedBrain 30%+.

Missing or incomplete:

- UnifiedBrain historical replay harness.
- Exact short-hold exit simulator matching partials, breakeven, trailing, max-hold, costs, and slippage.
- Nested purged walk-forward selector/evaluator.
- CPCV implementation over outer-period blocks.
- PSR/DSR implementation with persisted trial count.
- Fold-level report schema for CAGR, max drawdown, profit factor, exposure, heat, capital-days, and trade count.
- Reproducible manifest script for all parameters and data windows.

Roadmap update:

- Do not call any UnifiedBrain result “30% proven” until this harness exists.
- Treat existing threshold search as diagnostic only.
- Keep leveraged-ETF 30% results labeled as a different strategy family.
- Require paper trading of a frozen config before any live-capital 30% claim.

### M. Observability Gaps Hide Whether The Brain Is Working

The web/API pass found that the UI can present `unified_brain` as a strategy even when the actual unified runner is not producing compatible data.

Key gaps:

- Web start/monitor paths launch `paper_trade_today.py`, not `paper_trade_unified.py`.
- Unified runner output path and API expected dated path disagree.
- Audit filename date formats disagree: dashed date versus compact date.
- API collector reads only accepted candidates and drops rejects, watchlist, exits, score breakdown, risk fields, and reasons.
- Candidate history API shape does not match frontend expectations.
- Profitability analytics backend shape does not match frontend expectations.
- Portfolio UI omits stop/target distance, partial state, breakeven state, trailing state, max-hold age, and exit-plan state.
- HIL approvals/rejections lack actor, timestamp, reason, and strategy audit trail.

Roadmap update:

- Add a UnifiedBrain audit timeline panel before optimizing.
- Show accepted, rejected, watchlist, and exit events.
- Show reason codes and score breakdowns.
- Show capital utilization and heat over time.
- Show position progress to stop/target and exit-plan state.
- Make API/frontend response schemas match before relying on dashboard profitability.

### N. Final 10-Agent Priority Order

After the complete fanout, the best order is:

1. **Runner compatibility:** make UnifiedBrain execute one full scan without TypeErrors.
2. **Order adapter:** make UnifiedCandidate compatible with PaperAccount buy/sell telemetry.
3. **Exit truth:** fix partial exits, exit price usage, actual-fill anchoring, and label-equivalent simulation.
4. **Safety truth:** high-water mark drawdown, MTM loss checks, fail-closed missing prices, force-flatten logic.
5. **Candidate truth:** atomic source selection instead of best-field hybrid merging.
6. **Allocator truth:** real sectors, remaining-heat clamp, commission/cash, ADV cap.
7. **Scoring truth:** one shared scorer across AlphaEngine, UnifiedBrain, CandidateRanker, scanner, and backtest parity mode.
8. **ML trust discipline:** only large-loss probability contributes materially until other heads clear walk-forward gates.
9. **Observability:** audit timeline and dashboard schemas that expose why the brain accepted/rejected/exited.
10. **Proof harness:** nested WF + CPCV + PSR/DSR + paper validation before any 30%+ claim.
