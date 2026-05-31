# Portfolio Audit — Discrepancies & Profitability Improvements

**Date:** 2026-05-30
**Scope:** All 19 modules in `tradingagents/portfolio/` + integration paths (`scripts/paper_trade_today.py`, `paper_trade_unified.py`, `backtest.py`, `screening/screener.py`)
**Method:** 4 parallel read-only subagents, one per subsystem
**Status of every item below: PROPOSED — NOT IMPLEMENTED.** This is a tracking doc only.

Severity = profitability impact. Each item: `ID | Severity | Category | Location | Problem → Proposed fix`.
Category: **DISC** = discrepancy/bug/dead code/mismatch; **IMP** = profitability improvement.

---

## 0. Top Priority (cross-cutting, fix first)

These corrupt the ground-truth PnL and the risk circuit-breakers that every other module trusts. Fix before tuning anything else.

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **TP-1** | HIGH | **Live stop geometry 0.7 ATR vs labels/exits 1.0 ATR** (`screener.py:254 _ATR_STOP=0.7`). Model learns P(target-before-stop) under 1.0-ATR stop; live positions stop ~30% tighter → predicted winners become premature stop-outs. Root cause of WR≈40% / anti-predictive HC bucket. | Set `screener._ATR_STOP=1.0`; reconcile RR comment `unified_brain.py:64`. Standardize 1.0/1.2 everywhere. (E-1) |
| **TP-2** | HIGH | **Drawdown peak = max(start, current), not running high-water mark** (`production_safety.py:493`). After a profitable run the −12% halt never fires. | Persist running HWM in state; compute DD from it. (V-17) |
| **TP-3** | HIGH | **Loss limits & circuit breakers ignore open-position MTM** (`drawdown.py:67`, `production_safety.py:481`). Hold losers open → daily-loss halt bypassed exactly during selloffs. | Include unrealized MTM of open positions in daily/weekly/monthly PnL. (E-7, V-18) |
| **TP-4** | HIGH | **Stops fill at re-fetched live price, not stop level** (`state.py:253,291`; `execute_sell`). Records better-than-stop exits → inflates win rate / avg return in the log the whole reliability stack grades on. | Fill stops gap-aware at `min(open, stop_loss)`, targets symmetric; explicit slippage. (V-26) |
| **TP-5** | HIGH | **`state.py` re-fetches live yfinance price on every valuation** → inconsistent intra-decision prices, network-fail returns `entry_price` (positions look flat, no stops trigger). Garbage-in for all downstream PnL. | One price snapshot per cycle passed into all valuation methods; treat fetch failure as skip/halt. (V-25) |

---

## 1. Brain ↔ Engine Divergence (two scoring functions that disagree)

`unified_brain.py` (UB) and `alpha_engine.py` (AE) share tier cutoffs (0.72/0.55/0.38) but compute **different** alpha scores and sizes for identical inputs → live-vs-backtest edge divergence.

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **BE-1** | HIGH | **Vol penalty 25× off.** UB:509 uses raw excess ATR (`*scale`); AE:320 normalizes by threshold (`/thr*scale`). 2% excess → UB adds 0.02, AE adds 0.50. Brain applies almost no vol penalty. | Adopt AE normalized form in UB:509; re-tune tier thresholds. |
| **BE-2** | HIGH | **Breakout boost cap differs:** AE:175 `0.5` vs UB:104 `0.30`. Biggest discretionary lever in the (now 2-factor) numerator set 67% higher in Engine. | Unify `breakout_max_boost`; re-derive tiers. |
| **BE-3** | MED | **B-tier size mult differs:** AE:58 `0.50` vs UB:78 `0.40`. Backtest over-sizes B vs live. | Single source of truth for tier multipliers. |
| **BE-4** | MED | **Reliability floor differs:** UB:524 `0.60` vs AE:352 `0.50`. Brain under-penalizes worst tickers. | Unify floor (0.50) + band endpoints. |

**Action:** factor the alpha-score + tier-size math into one shared function imported by both modules.

---

## 2. Dead Logic on the Live Path (carefully built, never runs)

Biggest single class of profitability leak: the production ATR sizing path discards most adjustment layers.

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **DL-1** | HIGH | **ATR sizing path ignores ML confidence, streak, time-of-day, daily-lock, regime, ER cap** (`position_sizing.py:248-264`). Uses `cap_max` not `cap_max_effective`; `base_pct` (which carries steps 2-6) is discarded whenever ATR/stop>0. Live size = pure vol-target × tier × large_loss only. | Carry the composite multiplier (ml, streak, tod, daily-lock, regime) into `risk_dollars` on the ATR path. (F5) |
| **DL-2** | MED-HIGH | **Allocation weights computed, never applied** (`candidate_ranker.py:337` / `paper_trade_today.py:3692`). `_alloc_weights` never read again; docstring claims it multiplies base size. Rank ordering has zero effect on capital. | Pass `alloc_weight[ticker]` into `calculate_dynamic_size`, or delete + fix docstring. (F1) |
| **DL-3** | HIGH | **Correlation cap never invoked on live entry path.** `CorrelationAnalyzer` wired only in `trading_graph.py`, absent from `paper_trade_today.py` entry loop. Book can accumulate correlated names uncapped. | Call `check_concentration_risk(account,ticker)` before buy; cache matrix per scan (it does live `yf.download(1y)` per check). (F8) |
| **DL-4** | HIGH | **Sector cap is dead — sector hardcoded `"unknown"`** (`unified_brain.py:667`). Every fill increments `sector_counts["unknown"]`; `max_sector=2` → silently caps whole book at 2 positions, defeating `max_open_positions=5`. | Plumb real sector onto `UnifiedCandidate`/`Position`, or disable gate until sectors exist. (UB-7) |
| **DL-5** | HIGH | **DriftDetector high-conf-failure / WF-gap computed but never blocks trading** (`drift_detector.py:144`). `ProductionSafety` reads a separate `ml_drift.json` field instead. Most informative drift signal is reporting-only. | Wire `DriftDetector.check().has_drift` into `ProductionSafetyMonitor.check_all`. (V-15) |
| **DL-6** | HIGH | **`tbs_prob` (documented core preference) computed but unused** (`alpha_engine.py:237`). Numerator is only `regime×breakout`; no per-trade probability of success enters the decision. | Validate tbs WF ROC independently; if >0.5 add to numerator/gate. (AE-5) |
| **DL-7** | MED | **`min_model_confidence_floor=0.52` configured, never enforced** (`production_safety.py:55`). `check_all` accepts `candidates` param and ignores it. | If no candidate ≥ floor, warn/halt; or remove dead key. (V-21) |
| **DL-8** | MED | **`high_vol_adjustments` 2-day max-hold override never wired** (`safe_trade_guard.py:180`). Elevated-VIX positions ride full 10-day clock. | Clamp `plan.max_hold_days` to override when VIX elevated. (E-10) |
| **DL-9** | MED | **Regime/calibration alerts reporting-only** (`reliability_stats.py:241`). `REGIME_COLLAPSE` (WR<0.35) keeps trading. | Feed alerts into ProductionSafety as warn/halt. (V-27) |

---

## 3. Grader / Calibration Measuring Constants (reports look real, measure nothing)

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **GC-1** | HIGH | **BUY event missing graded fields** (`paper_trade_today.py:739`): no `large_loss_probability`, `alpha_tier`, `alpha_score`, `breakout_score`, `model_version`, `regime_at_entry`. Grader silently defaults → every trade lands tier `"C"` / version `"unknown"`. `by_tier`/`by_model_version` slices meaningless. | Emit fields on BUY; or grade off SELL where present. (V-3) |
| **GC-2** | HIGH | **Large-loss & stop/target tracks dead** — SELL never emits `max_drawdown_pct`/`max_adverse_pct`/`stop_hit`/`target_hit` (`paper_trade_today.py:1004`). `actual_large_loss` always 0 → ll head always "correct". | Emit MAE + explicit stop/target booleans on SELL. (V-1) |
| **GC-3** | MED | **`exit_reason` literal mismatch.** Grader matches `"stop"`/`"target"` (`prediction_grader.py:181`); emitters use `"STOP_LOSS"`/`"TAKE_PROFIT"`. `stop_rate`/`target_rate` → 0. | Normalize casefold + map to canonical enum. (V-2) |
| **GC-4** | MED | **SELL carries `regime_at_entry`/`alpha_tier`/`ll_prob` but grader reads only BUY** (`prediction_grader.py:164`). Forces defaults despite correct values existing. | `buy_ev.get(k, sell_ev.get(k, default))`. (V-4) |
| **GC-5** | MED | **`pnl_pct` magnitude heuristic** `if abs>2.0: /100` (`prediction_grader.py:170`). A real +250% winner silently → 0.025. | Standardize units at source; remove guess or use explicit `pnl_units`. (V-5) |
| **GC-6** | MED | **trade_id day-granular** (`prediction_grader.py:329`) → two same-day round-trips collapse, 2nd dropped (survivorship bias against chase trades). | Include HH:MM:SS / UUID in trade_id. (V-6) |
| **GC-7** | MED | **Calibration = first-moment only** `|avg_prob − win_rate|` (`reliability_stats.py:57`). Constant-0.6 predictor with zero discrimination looks perfect; hides anti-predictive HC bucket. | Per-bucket reliability check + monotonicity alert (HC WR must exceed LC WR). (V-10) |
| **GC-8** | MED | **Win-prediction threshold 0.60 ≠ live entry 0.52** (`prediction_grader.py:197`). Mislabels the 0.52–0.60 marginal band. | Pass live operating threshold into grader. (V-11) |

---

## 4. Threshold / Constant Mismatches (config vs fallback drift — latent landmines)

Masked today only because full config dicts are spread in; a partial override re-activates anti-predictive gates.

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **TC-1** | MED | **Confidence/win_prob fallbacks still encode old anti-predictive gates** (`unified_brain.py:478,536,543,548`): fallbacks 0.58/0.66/0.60/0.52 while config sets 0.0. | Set fallbacks 0.0, or drop the clauses. (UB-1) |
| **TC-2** | MED | **`ll_hard_cap` fallback 0.35 vs config 0.50** (UB:484 vs 63). 0.35 rejects the documented-profitable 0.35-0.50 bucket. | Align fallback to 0.50. (UB-2) |
| **TC-3** | LOW-MED | **`vol_penalty_atr_threshold` fallback 0.03 vs tuned 0.04** (UB:508 vs 99). | Fallback 0.04. (UB-3) |
| **TC-4** | MED | **`min_rr` fallback 1.5 vs config 1.2** (`short_hold_exits.py:118,393`; `unified_brain.py:472`). If config key dropped, targets jump to 1.5R, breaking trained 1.2 geometry. | All fallbacks 1.2. (E-3) |
| **TC-5** | LOW-MED | **`partial_profit_trigger` fallback 0.50 vs config 0.833** (`short_hold_exits.py:135,389`). Fallback scalps winners at 50% of target. | Default 0.833 in both factories. (E-4) |
| **TC-6** | MED | **PSI thresholds inconsistent:** deploy fails ≥0.25 (`feature_monitor.py:41`) but runtime drift fails >0.20 (`drift_detector.py:80`, `production_safety.py:64`). Clean deploy can instantly trip runtime halt. | Single shared PSI constant (warn 0.10 / act 0.20 / hard-fail 0.25). (V-13) |
| **TC-7** | LOW-MED | **Drift windows/thresholds inconsistent:** live acts at 10 trades / drift>0.15; `DriftDetector` needs ≥15 / 0.08 cal / 0.20 PSI (`drift_detector.py:81` vs `paper_trade_today.py:1059`). | Unify window + thresholds, one drift source of truth. (V-28) |

---

## 5. Sizing / Ranking Math Errors

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **SR-1** | MED | **tier_factor applied 2-3×** (`position_sizing.py:240,252,258`). Risk_dollars×tier + tier cap + base_pct×tier; ATR path ignores base_pct so tier applied 2× there, 1× on fallback. B-tier ≈0.25× effective vs intended 0.5×. | Apply tier_factor exactly once per path; unit-test effective risk = `risk_pct × tier`. (F4) |
| **SR-2** | MED | **large_loss_prob penalizes 2-3×** (`candidate_ranker.py:228` denominator + 200 hard-reject + `position_sizing.py:280` size haircut). The one trusted signal is compounded. | Apply large_loss once (prefer sizing haircut); justify any compounding with calibration. (F11) |
| **SR-3** | HIGH | **Ranking numerator = regime only** (`candidate_ranker.py:237`). Within one scan `reg_score` is identical for all candidates → ranking collapses to ordering by large_loss/atr/reliability. Near-zero correlation with realized edge. | Re-introduce a validated edge signal (tbs or recalibrated win_prob) once WF ROC>0.55, or rule-based edge proxy (RR×momentum). (F2) |
| **SR-4** | MED | **ATR sizer ignores risk_reward/target** (`position_sizing.py:244`). Sizes 1.2-RR and 4.0-RR trades identically. | Scale `risk_dollars` by clipped RR factor; assert RR ≥ floor. (F6) |
| **SR-5** | MED | **min_win_prob gated AND declared anti-predictive** (`candidate_ranker.py:113` off, but `paper_trade_today.py:3685` passes `min_win_prob≈0.55`). Contradictory: actively filters on a signal called anti-predictive. | One source of truth: keep gate off everywhere, or restore to both gate+numerator if WF shows predictiveness. (F3) |
| **SR-6** | HIGH | **Reliability hardcoded 0.65 on one score path** (`paper_trade_today.py:2400`). Pins `rel_mult=1.0`; "stop trading losing names" protection disabled there. | Pass real `_tracker.get_score`; handle small-sample inside tracker. (V-8) |
| **SR-7** | MED | **`min_risk_reward` gate bypassed when RR==0** (`alpha_engine.py:304`: `and risk_reward>0`). Malformed candidates (no valid stop/target) skip the filter. | Reject when `risk_reward<=0` OR `<min`. (AE-1) |
| **SR-8** | MED | **Kelly `adjusted_win_rate = win_rate × confidence`** (`position_sizing.py:28`) double-discounts p → returns 0 for most setups; optimistic 5:1 default (`avg_win .10/avg_loss .02`) over-bets unproven names; possible 100× unit error on avg_win/loss. | Confidence as fractional-Kelly multiplier on output; conservative no-history default; verify units. (F9) |
| **SR-9** | LOW-MED | **`vol_penalty` unbounded in ranker** (`candidate_ranker.py:231`) despite "all inputs clipped" invariant; ATR=0.30 shrinks composite ~10×. | Clip vol_penalty (e.g. ≤2-3) or move vol handling into sizer. (F12) |
| **SR-10** | MED | **Correlation cap off-by-one + strict `>`** (`correlation.py:41`, `max_high_corr=2`). Permits a 3-way correlated cluster. | Decide semantics; use `>=` if "at most N correlated." (F7) |

---

## 6. Exit Logic

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **E-1** | HIGH | See **TP-1** (screener 0.7 ATR stop vs 1.0 labels). | — |
| **E-2** | MED | **Two parallel exit engines** — `ExitManager` (docstring says "live") bypassed; live uses `ShortHoldExitManager` with screener stop verbatim. Divergent defaults, silent drift. | Route short-hold stop/target through one source of truth, or delete ExitManager. |
| **E-3/E-4** | MED | Fallback constant mismatches → see **TC-4, TC-5**. | — |
| **E-5** | LOW-MED | **Partial vs breakeven use different day-gates** (`short_hold_exits.py:290` partial gated `min_hold_days=1`, but trail/breakeven activate day-0). Fast movers treated inconsistently. | Allow partial day-0, or gate trail by same `min_hold_days`. |
| **E-6** | MED | **Backtest intrabar resolution optimistic** (`backtest.py:1440`): ambiguous bar (hit target+stop) resolved by close-vs-midpoint; live resolves stop-first. Inflates backtest WR vs live. | Resolve ambiguous bars stop-first in `measure_outcome`. |
| **E-7** | MED-HIGH | See **TP-3** (`drawdown.py` realized-only). | — |
| **E-8** | MED | **`drawdown.py` sums per-trade pnl_pct additively, no equity weighting** (`drawdown.py:73`). `max_daily_loss=-0.05` is not a true −5% account move. | Weight each pnl_pct by capital fraction, or log account-level pnl. |
| **E-9** | MED | **`max_hold_days=10` time-exit cuts winners** (`short_hold_exits.py:254`); model ER is a 3-day estimate, day-10 survivors are slow winners liquidated mid-move. | If green & above trail, convert to trail-only; force-close only losers/flat at limit. |
| **E-10** | MED | See **DL-8** (high-vol 2-day override unwired). | — |
| **E-11** | MED | **Confidence extension widens target to 1.56 ATR** (`exit_manager.py:210`) but labels cap target at 1.2 ATR. Lowers hit-rate on best trades. (Moot while ExitManager bypassed.) | Cap extension at trained geometry, or retrain with confidence-conditional target ladder. |
| **E-12** | LOW-MED | **No profit locked below +1 ATR** (`short_hold_exits.py:189`). Run to +0.9 ATR then reverse → full give-back + stop at original loss. | Intermediate breakeven move at +0.5-0.6 ATR. |

---

## 7. Safety Gates — too loose / too strict / stale

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **V-17** | HIGH | See **TP-2** (drawdown peak not HWM). | — |
| **V-18** | MED-HIGH | See **TP-3** (closed-trade-only PnL). | — |
| **V-20** | HIGH | **One stale ticker in random sample of 20 halts whole book** (`production_safety.py:266,628`). `critical = stale_count>0`; random sampling → nondeterministic "no-trade days". | Halt only if fraction stale > threshold (e.g. 20%); exclude offending ticker; seed/deterministic sampling. |
| **V-22** | HIGH | **ROC<0.52 only warns, no halt** (`production_safety.py:397`). Near-random model trades live (deployed WF ROC=0.5121). Direct profitability leak. | Hard ROC halt floor; require WF-HC WR > overall before HC sizing. |
| **V-16** | LOW-MED | **Model age naive-local `now` vs UTC `created_at`** (`production_safety.py:337`); negative age never halts. | Parse tz-aware UTC; clamp age ≥0. |
| **V-19** | LOW | **Weekly PnL string-compares dates** (`production_safety.py:488`). Fragile to non-ISO. | Parse to date objects. |
| **V-23** | MED | **Calibration staleness threshold tied to model age +15 (≥60d)** (`production_safety.py:362`). Probs decay faster than relevance. | Independent shorter calibration-staleness (20-30d) that downgrades confidence sizing. |
| **V-29** | LOW-MED | **Abnormal-move (25%+) only warns** (`production_safety.py:637`). Likely corporate-action/bad-split → corrupt features/stops. | Exclude abnormal-move tickers from cycle's candidates. |

---

## 8. Calibration / Reliability Robustness

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **CR-1** | MED | **PSI noisy at small n; n<10 returns "stable" (not "unknown")** (`feature_monitor.py:55`). Passes drifted model when data scarce; false halts when noisy. | Require ≥50 prod samples; return `insufficient` below; fewer bins (5) for small n. |
| **CR-2** | MED | **`drift_detector._check_psi` treats WR-drift as PSI** (`drift_detector.py:211`). Live writes `drift=|pred_wr−actual_wr|`; compared to 0.20 PSI threshold and labeled PSI_DRIFT. Mislabels calibration drift as feature drift. | Read WR drift into `calibration_drift`; keep PSI for feature distributions only. |
| **CR-3** | LOW-MED | **Reliability blend linear, jumpy at small n** (`ticker_reliability.py:83`): n=2 losses → score ~0.40 (penalty edge). | Beta-Binomial posterior mean with 0.5 prior. |
| **CR-4** | MED | **Two divergent reliability→multiplier curves** (`candidate_ranker.py:255` inline reward 1.15 vs `ticker_reliability.py:93` reward 1.10) + `rel_clipped` dead code. Risk of double-counting reliability. | Delete `rel_clipped`; converge on `TickerReliabilityTracker.size_multiplier`; apply in one stage only. |

---

## 9. Dead Config / Clarity (low impact)

| ID | Sev | Problem | Fix |
|----|-----|---------|-----|
| **DC-1** | LOW | `er_clip_max=3.0` never used in Brain (`unified_brain.py:105`); ER neutralized (R²=0.012). | Remove or comment. |
| **DC-2** | LOW | `er_boost=1.0`, `timeout_penalty=0.0` permanently neutralized but occupy formula structure (`alpha_engine.py:369,381`). Alpha ≈ `regime×breakout / (1+1.5·large_loss)`. | Confirm corr/liq penalties actually wired (`min_adv_dollars` defaults 0 → liquidity gate never fires — real drawdown-control gap). |
| **DC-3** | LOW-MED | **VIX low-vol skip is a hard binary cliff at 15.0** (`unified_brain.py:812`). Fragile to noise around threshold. | Continuous size taper VIX 15-18; sensitivity-sweep the breakpoint. (UB-8) |
| **DC-4** | MED | **Risk sized off full `account_value` for every trade in batch** (`unified_brain.py:692`), not remaining budget. Nth trade still risks 1% of total; aggregate heat front-loaded. | Size off remaining risk budget or current equity; document aggregate target. (UB-9) |
| **DC-5** | LOW-MED | **`calculate_kelly_size` ignores `portfolio_value`; rolling Kelly inert in production** (`position_sizing.py:15`). System is de-facto vol-target, not Kelly, despite naming. | Remove unused param + document, or wire Kelly into `risk_per_trade_pct`. (F10) |
| **DC-6** | MED | **`state.py` fallback stop −2% / target +10%** (`state.py:241`) — fixed 5:1, ignores ATR, inconsistent with 1.2/1.0 ATR geometry. | Derive fallback from ATR, or refuse to open without explicit levels. (V-24) |

---

## Suggested Sequencing

1. **Wave 1 — ground truth & breakers (TP-1…TP-5, V-20, V-22):** until PnL log and circuit-breakers are correct, no metric can be trusted and no tuning is safe. These also directly address the WR≈40% root cause.
2. **Wave 2 — wake the dead logic (DL-1…DL-9):** turn on the sizing/ranking/correlation/drift layers that already exist but don't run. Largest latent edge with least new code.
3. **Wave 3 — fix the measurement (GC-1…GC-8, CR-1…CR-4):** make grader/calibration measure real values so Wave 4 decisions are data-driven.
4. **Wave 4 — unify scoring (BE-1…BE-4) + re-tune (SR-1…SR-10, TC-*):** single scoring/sizing function, then re-derive thresholds against it.
5. **Wave 5 — exit refinements (E-5,E-6,E-9,E-11,E-12) + clarity (DC-*).**

**Validation discipline (per research-loop rules):** every change validated walk-forward, no tuning on holdout, no weakening of validation/risk controls. Append results to `ALPHA_EVOLUTION_LOG.md`.

---

*Generated read-only. No source files modified. Subagent source IDs in parentheses (UB/AE = brain/engine, F = ranking/sizing, E = exits, V = validation).*

---

## IMPLEMENTATION STATUS — 2026-05-30 (Cycle 45)

**IMPLEMENTED (all bug / dead-control / coherence findings):**
TP-1 (stop 0.7→1.0, Cycle 44), TP-2/V-17 (HWM drawdown), TP-3/E-7/V-18 (open MTM in breakers), TP-4/V-26 (gap-aware fills), V-24/DC-6 (ATR fallback levels),
BE-1/BE-2/BE-3/BE-4 (Brain unified to live Engine), DL-1/F5 (sizer safety layers on ATR path), DL-4/UB-7 (sector cap), DC-4 (heat taper),
AE-1/SR-7 (R:R leak), AE-3 (rel clamp), SR-8/F9 (Kelly confidence + prior), SR-9/F12 (vol clip), SR-10/F7 (correlation off-by-one), CR-4 (dead code),
TC-1..TC-6 (config/threshold mismatches), E-3/E-4/E-9/E-11/E-12 (exits), E-8 (equity-weighted pnl),
V-16/V-19/V-20/V-21/V-22/V-23 (safety gates), CR-1/CR-2 (PSI), GC-2/GC-3/GC-4/GC-5/GC-6 (grader telemetry).

**DEFERRED (require walk-forward validation / retrain / live run — see ALPHA_EVOLUTION_LOG Cycle 45 Remaining):**
Target-geometry retrain; SR-3/F2 + B6/B7 (numerator edge promotion); F1/DL-2 (alloc-weight wiring into live sizer); DL-3/F8 (correlation call on entry path); DL-5/V-15 (DriftDetector→halt); DL-8 (high-vol max_hold override); DL-6/AE-5 (tbs_prob); GC-1 (MAE emit) / GC-7 (bucket calibration); SR-1/F4 reassessed as not-a-bug; SR-5/F3 win_prob gate kept deliberately.
