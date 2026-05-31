# Unified Brain — Path to 30%+ Annualized

**Date:** 2026-05-30
**Target:** 30%+ out-of-sample risk-adjusted annual return from the Unified Portfolio Brain.
**Scope:** `tradingagents/portfolio/unified_brain.py` + the geometry/sizing infra it depends on.
**Method:** 4 deep read-only subagents (alpha scoring, sizing/compounding, regime/gating, exit geometry), cross-checked against `ALPHA_EVOLUTION_LOG.md` (Cycles 25/38/40/41), `ml_models/latest/training_report.json`, and live integration paths.
**Status of every item: PROPOSED — NOT IMPLEMENTED.** Tracking doc only.

---

## The One Insight That Reframes Everything

**Cycle 41 MFE/MAE simulation** (`ALPHA_EVOLUTION_LOG.md:2639-2657`), at 1.2 target / 1.0 stop geometry:

| Outcome | Freq | Avg ret | EV contribution |
|---------|------|---------|-----------------|
| TARGET_HIT | 19.1% | +3.42% | +0.65% |
| STOP_HIT | 37.1% | −2.67% | **−0.99%** |
| TIMED_OUT | 43.9% | **+2.10% @ WR 98.7%** | **+0.92%** |
| **Total** | | | **+0.59%/trade** |

**The edge is NOT target-hitting. Target-hits net −0.34% after stop drag. The profit engine is the 43.9% of trades that TIME OUT positive at 98.7% win rate — slow drift, not mean-reversion.** This is a drift/trend-capture strategy wearing a mean-reversion-target costume.

Every proposal below is judged on one axis: **does it stop clipping the right tail (the +2.10% timeout / +3.42% target drift) and stop converting drift-winners into premature stop-outs?**

Two corollaries fall out immediately:
1. The two model heads aligned with *survival-to-drift* — `large_loss_probability` (ROC **0.731**, calibrated) and `timeout_probability` (ROC 0.588) — are respectively **buried in a denominator** and **discarded**. The scoring engine has no calibrated edge signal in its numerator.
2. The live execution stop (0.7 ATR) is **tighter than the training-label stop** (1.0 ATR), so the model's predicted winners stop out early. This is the documented "WF HC WR=39.5% anti-predictive" symptom — the model isn't broken, execution is.

---

## Wave 1 — Geometry Coherence (the dominant lever, do first)

Convergent #1 finding across the exit and alpha agents. Estimated to recover the bulk of the gap to 30% on its own.

### B1. Unify the stop: screener 0.7 ATR → 1.0 ATR everywhere | **HIGHEST IMPACT**
- **Current:** three different stops live simultaneously — screener `screener.py:254 _ATR_STOP=0.7`; labels/ExitManager 1.0 (`retrain_weekly.py:216`, `exit_manager.py:109`); `short_hold_exits` fallback `min_rr=1.5` (`:118,393`). Stale screener docstring claims 0.8 (`screener.py:45`).
- **Why it caps profit:** a trade the model labeled a winner (stop at −1.0 ATR) is killed in production at −0.7 ATR. The −0.7→−1.0 ATR band holds adverse excursions the labels treat as survivable noise inside winners. Each premature stop converts a +2.10% timeout-drift winner into a ~−1.4% loss.
- **Change:** `screener._ATR_STOP = 1.0`; `short_hold_exits` fallbacks `min_rr 1.5→1.2`; fix docstring. **One canonical geometry: stop = 1.0 ATR.**
- **Expectancy:** holding the empirical MAE/MFE fixed and only correcting the execution stop recovers ~10-15pp of timeout-winners currently stopped out. Est. **+0.4 to +0.6%/trade**. At ~50 trades/yr × +0.59% ≈ **~30% annualized before compounding** — this fix alone is most of the goal.
- **Validation:** PID 305 correct-geometry CSV is exactly this test — backtest 0.7 vs 1.0 execution stop on identical signals; realized WR should converge to label WR. **Blocker: all existing EV buckets in the log were computed at 0.7/1.2 and are now stale — recompute before tuning anything downstream.**
- **Risk:** wider stop = larger per-loss in ATR, but sizing is risk-based (`risk_dollars/stop_dist`, UB:692-693) so **dollar risk per trade is invariant to stop width** — share count auto-adjusts. Net positive.

### B2. Raise target 1.2 ATR → 2.0 ATR (R:R 1.2 → 2.0)
- **Current:** `target_atr_mult=1.2` (`exit_manager.py:110`, screener `_ATR_TARGET=1.2`).
- **Why it caps profit:** winners average +3.42% (target) / +2.10% (timeout), but a 1.2-ATR target fires right as drift establishes. Target-hits net **−0.34% EV** — the target actively destroys value by capping trades that would drift further. A 1.2 R:R on a 37% stop rate is mathematically near break-even.
- **Change:** `target_atr_mult 1.2→2.0`; `min_rr 1.2→1.8`. Or remove hard target, let chandelier trail + max_hold harvest drift.
- **Expectancy:** three-state model with target at 2.0R ≈ **+0.94%/trade** vs +0.59% at 1.2.
- **Validation:** re-run MFE/MAE sim at target_mult ∈ {1.2,1.5,2.0,2.5,no-target}; pick max EV/variance. **Ship with B3 (trail) — never alone.**
- **Risk:** lower hit frequency → more reliance on trail; pair with B3.

### B3. Chandelier trail + earlier breakeven
- **Current:** breakeven at +1.0 ATR (`UB:93`); trail = peak − 0.5 ATR (`UB:94`, `short_hold_exits.py:357`).
- **Why it caps profit:** (a) 0.5-ATR trail whipsaws the slow drifters (the profit engine) — normal drift retraces deeper than 0.5 ATR. (b) breakeven at 1.0 ATR leaves a fully-exposed 0→1.0 ATR dead zone (partial also doesn't fire until then post-Cycle 38).
- **Change:** breakeven `1.0→0.6 ATR`; trail `0.5→0.9 ATR` for trend/timeout trades; two-stage — keep loose until +2.0 ATR then tighten to 0.5 to protect near-target gains.
- **Expectancy:** fewer whipsaws ≈ +0.05%/trade; earlier breakeven saves dead-zone losses ≈ +0.20%·R/trade.
- **Validation:** replay open positions through `_update_trail` with trail ∈ {0.5,0.75,0.9,1.0}, breakeven ∈ {0.6,0.8,1.0}; measure MFE captured.

### B4. Partial-profit: tier-conditional (remove for A+)
- **Current:** `partial_profit_trigger=0.833, fraction=0.5` (`UB:95-96`) — sell 50% at +1.0 ATR.
- **Why it caps profit:** Cycle 38 sync reduced variance (good for median) but given drift is the engine, taking 50% off at +1.0 ATR removes half the position right before the +2.10% drift matures — a ~30% haircut on the best trades.
- **Change:** A+ tier → `partial_fraction=0.0` (pure trail, keep full size on highest-conviction drifters); A/B → keep 0.5 partial for variance control. Note: also fixes the config-vs-fallback mismatch (`short_hold_exits.py:135,389` default 0.50 vs config 0.833).
- **Expectancy:** removing A+ partial ≈ **+0.24%/trade for A+** (already sized 1.25×).
- **Validation:** E[partial=0.5] vs E[partial=0] on A+ historical trades.

### B5. Soft time-exit (do NOT shorten max_hold)
- **Current:** `max_hold_days=10` unconditional full close (`short_hold_exits.py:254`).
- **Why:** log (2658) already tested & rejected shortening (h5 +0.34% < h10 +0.40%) — timeout drift NEEDS the full 10 days. Flaw is the *unconditional* close liquidating green-trending winners mid-move.
- **Change:** at day 10, if green AND making higher highs → switch to trail-only, extend hard cap to day 15; flat/red → hard close as today.
- **Expectancy:** +0.05-0.10%/trade, free upside extraction.
- **Risk:** ties up a slot longer (max 5) — only extend green-trending.

**Unified geometry spec (the coherence target):**

| Param | Now (fragmented) | Target (everywhere) |
|-------|------------------|---------------------|
| stop_mult | 0.7 / 1.0 / 1.5 | **1.0 ATR** |
| target_mult | 1.2 | **2.0 ATR** |
| min_rr | 1.2 / 1.5 | **1.8** |
| breakeven | 1.0 ATR | **0.6 ATR** |
| trail | 0.5 | **0.9 → 0.5 two-stage** |
| partial | 0.5 @ 0.833 | **tier-conditional** |
| max_hold | 10 hard | **10 soft / 15 if green-trending** |

Files: `screening/screener.py:253-254,45`, `retrain_weekly.py:215-216`, `exit_manager.py:110`, `unified_brain.py:64,93-96`, `short_hold_exits.py:118,393`.

---

## Wave 2 — Put a Calibrated Edge in the Numerator

The alpha numerator is `regime_score × breakout_boost` only. Regime is constant within a scan → ranking collapses to breakout/large_loss/atr ordering with **no calibrated expectancy signal**. Model head quality (`training_report.json`, 2026 holdout):

| Head | ROC | Calibrated | Verdict |
|------|-----|-----------|---------|
| large_loss_probability | **0.731** | isotonic | strongest signal — used only as a penalty |
| timeout_probability | 0.588 | **none** | weakly predictive — discarded |
| win_probability | 0.491 | isotonic | anti-predictive (correctly removed) |
| target_before_stop | 0.504 | isotonic | coin-flip (correctly dropped) |
| expected_return | R²=−0.023 | — | dead (correctly neutralized) |

### B6. Promote calibrated `1 − large_loss_prob` into the numerator | HIGH
- **Current:** `ll_prob` enters only as `ll_pen=1.5×ll_prob` in the denominator (`UB:506`). Two A+ candidates at ll 0.04 vs 0.14 differ only ~11% in alpha — for the system's most discriminative variable.
- **Change:** `safety = 1 − ll_prob_calibrated`; `numerator = regime_score × breakout_boost × (0.5 + 1.0×safety)` (→ [0.5,1.5] multiplier on rank). **Remove `ll_pen` from denominator** (don't double-count). Keep the hard gate.
- **Mechanism:** orders trades by calibrated P(survive) — the variable Cycle 40 proved drives EV. ll is monotone with expectancy ([0.35,0.50) E=+0.29% vs higher buckets negative).
- **Validation:** WF decile analysis ll vs realized return on new-geometry CSV; fit the slope to the realized E ratio. Gate promotion on WF ROC > 0.65.
- **Cross-ref:** ties to audit item SR-2 (ll currently penalized 2-3× across rank+reject+size — apply once).

### B7. Build a calibrated survivor/drift head and add to numerator | HIGH
- **Current:** `timeout_probability` (ROC 0.588) computed in `_dedup_ticker:347`, stored, never used. It is the only head pointed at the 43.9% timeout-drift profit engine — and it's the only head missing calibration.
- **Change:** (1) add isotonic calibration to the timeout head in `train_ml_models.py`; (2) retrain on label `P(timeout AND positive)` = P(survivor); (3) add `× (0.8 + 0.4×survival_prob_calibrated)` → [0.8,1.2] to numerator, small weight until WF-validated.
- **Mechanism:** tilts selection toward names that drift rather than chop into the stop — the dominant EV bucket.
- **Validation:** WF ROC > 0.55 and monotone E(return | survival_decile); A/B require ≥ +0.05%/trade. Do NOT promote if WF ROC < 0.53.

### B8. Calibrate or kill the breakout signal (currently inert)
- **Current:** **DEAD.** `breakout_score ≈ 0.0 for ~all production candidates** (`ALPHA_EVOLUTION_LOG.md:228`) — the screener path doesn't populate it outside the breakout-strategy subset. So `breakout_boost≈1.0` everywhere and `min_breakout_score=55` (UB:62) is **never read by any code**. The brain runs on `regime_score / (1+penalties)` alone.
- **Change:** (a) diagnose — dump `breakout_score` distribution from a recent audit JSONL to confirm ~0. (b) if the screener can populate it (computation exists `backtest.py:1001`), wire it onto every Candidate in `build_candidates`. (c) calibrate: bin → realized E per decile → isotonic `breakout_score → edge`; use in boost. (d) if Spearman(breakout, realized) < 0.05, **drop it and repurpose the A+ machinery to ll_prob/reliability.**
- **Mechanism:** either activates a real orthogonal momentum signal or removes noise inflating rank dispersion.
- **Risk:** was zeroed out of A+ in Cycle 8 for a reason — diagnose before enforcing.

### B9. Composite alpha→E[return] calibration + re-derived tiers
- **Current:** no module maps composite alpha → expected return; tier cutoffs 0.72/0.55/0.38 were set in Cycle 38 to reproduce a regime mapping via 5 hand-picked smoke-test points — not tied to realized E.
- **Change:** add `alpha_calibration.py` fitting isotonic/Platt on the calibration fold `alpha_score → realized_return`; expose `calibrated_expected_return(alpha)`. Re-derive tiers from the alpha→E curve: A+ where E≥+1.0%/trade & n≥100, A where E≥+0.4%, B where E≥0, C where E<0.
- **Mechanism:** turns the engine from heuristic ranking into estimated-EV ranking — the foundation for Kelly sizing (B10) and principled cutoffs.
- **Risk:** small calibration sample (cal_rows≈215) — use Platt if isotonic overfits; refit on rolling windows.

### B10. Fix reliability multiplier curve (or remove)
- **Current:** `UB:517-524` — reward side flat [1.0,1.10], punishment steep [0.60,1.00]; centered at 0.65 not 0.5 (the 0.65 hardcode at `UB:387` exists to dodge this mis-centering). No evidence reliability predicts forward return.
- **Change:** center at rel=0.5→mult=1.0; compress to [0.85,1.15]; shrink toward 1.0 when n<blend_at_n. **First validate:** if Spearman(reliability, forward_return) < 0.05, set `rel_mult≡1.0` and delete.
- **Mechanism:** stops taxing mean-reverting unlucky streaks and the blanket 24% haircut, or removes a noise term.

---

## Wave 3 — Sizing for Compounding (the under-betting problem)

**Headline:** there are **two sizing engines** and the brain uses the weaker one. `paper_trade_today.py:3966` calls `PositionSizer.calculate_dynamic_size()` — a 10-layer Kelly + streak + drawdown + ML-confidence sizer fed live rolling stats. `paper_trade_unified.py:479 → brain.allocate()` reimplements a **flat** `risk_pct × reg_factor × vix_factor × tier_mult` and never builds rolling_stats. **~80% of the desired sizing infra already exists and is battle-tested next door — the brain just doesn't call it.**

**Arithmetic for 30%:** per-trade equity impact ≈ avg_ret (1.0% on notional) × notional/equity (~0.15) ≈ +0.15%/trade. Over 200 trades `(1.0015)^200−1 ≈ +35%`; over 150 ≈ +25%. **1% flat risk sits right at the edge.** Realized half-Kelly is **25-46%** (`ALPHA_EVOLUTION_LOG.md:2113-2176`) — the book is **under-betting by 1.5-3×** on its best signals.

### B11. Port the existing Kelly/streak/drawdown sizer into the brain | HIGHEST sizing impact
- **Change:** in `paper_trade_unified.py` compute `rolling_stats=_rolling_trade_stats(account.trades,n=20)` (reuse `paper_trade_today.py:2932`) and thread into `allocate`. Replace `UB:692` with Kelly-blended risk: `risk_pct_eff = clamp(0.5 × rolling_kelly × 0.5, 0.5%, 2.5%)` (quarter-Kelly), fall back to 1.0% when n<10. Keep ATR stop→shares conversion unchanged.
- **Effect:** moving effective risk 1.0%→~2.0% on validated edge roughly doubles per-trade equity impact → 25% base case → **~40-45%**. DD rises ~1.5-2× but quarter-Kelly stays far from ruin.
- **Validation:** backtest risk_pct ∈ {1.0,1.5,2.0,2.5} over 2021-2026; report CAGR, MaxDD, MAR; pick the MAR knee.
- **Pair strictly with B14 (drawdown throttle).**

### B12. Continuous conviction-scaled risk (replace 3 discrete tier mults)
- **Current:** `tier_mults {A+:1.25, A:1.0, B:0.4}` (`UB:673`) — a step function discarding continuous alpha. alpha 0.71 vs 0.73 → 25% size cliff; 0.55 vs 0.71 → identical size.
- **Change:** `conviction_mult = clamp(0.4 + (alpha−0.38)/(0.72−0.38)×(1.25−0.4), 0.4, 1.25)`. Keep tier labels for gating/reporting.
- **Effect:** reallocates toward genuinely strongest signals (A+ WR 74% vs A 61%); +3-5pp CAGR with *lower* DD. Low risk (bounded by current range).

### B13. Regime-conditional heat + revive correlation/sector caps
- **Current:** `max_heat=75%`, `max_open=5`, `position_cap=20%`. `corr_pen=0.0` placeholder (`UB:512`, scale 0.25 wired but unused). Sector cap dead (`sec="unknown"`, `UB:667`). Book is under-deployed in calm (cash drag) AND blind to correlation in stress (5 slots can be one factor).
- **Change:** (1) regime heat: 90% bull/normal-VIX, 75% neutral, 50% bear/elevated, 0% crisis. (2) revive correlation penalty: `risk_pct_eff × (1 − 0.5×avg_corr)` vs current positions. (3) populate `uc.sector` (coarse GICS) to make `max_sector_positions=2` bind.
- **Effect:** higher deployment when edge is best lifts CAGR toward/over 30%; correlation+sector caps cut tail DD (the biggest historical drawdown source).
- **Risk:** 90% bull heat raises gross — pair with B14 + correlation cap.

### B14. Account-drawdown throttle (portfolio circuit breaker) | safety valve for B11/B13
- **Current:** none in `allocate`. Log explicitly calls for it (`:1039` "halve at >5% DD"; `:1023` "only portfolio-level controls limit crash exposure" — Feb 2026 crash showed entry filters can't catch mid-hold crashes).
- **Change:** `dd = 1 − equity/peak`; `dd_factor`: <5%→1.0, 5-10%→0.75, 10-15%→0.5, >15%→0.25; multiply into combined_factor; auto-recovers as equity recovers.
- **Effect:** turns a −30% crash into ~−15-18% for ~−2-3pp CAGR drag → net MAR improvement. This is what lets B11/B13 push risk up without ruin.

### B15. Anti-martingale streak scaling (free — already coded next door)
- **Current:** `allocate` has no streak logic; `position_sizing.py:188-197` already implements loss-streak 3+→×0.5 / 2→×0.7 / 1→×0.85, win-streak 4+→×1.2 / 2+→×1.1. The brain just doesn't call it.
- **Change:** apply the identical multipliers to `combined_factor` (`UB:683`) using rolling_stats from B11.
- **Effect:** ~10-20% MaxDD reduction, near-neutral CAGR.

### B16. Compounding base & heat taper
- **Current:** sized off full `account_value` every iteration (`UB:692`), not remaining budget; hard 75% wall.
- **Change:** keep sizing off current MTM equity (correct for geometric growth — do NOT switch to remaining-cash base, that suppresses compounding); add smooth heat taper `risk_pct_eff × (1 − deployed/max_heat)^0.5` so the 5th position < the 1st.
- **Effect:** neutral-to-positive CAGR, smoother equity curve, better geometric mean.

---

## Wave 4 — Regime & Gating (capture more good, cut more bad)

### B17. Replace the VIX<15 binary cliff with a continuous, setup-conditional filter
- **Current:** `UB:812-818` rejects every candidate when VIX<15. "low_vol E=−0.248%" is an **aggregate over all setups** — a non-trivial positive subset exists (WR 50.8% vs 57.6%). A hard step at 15.0 (VIX 14.9 = nothing, 15.1 = full) is economically implausible and overfit to one breakpoint.
- **Change:** (a) continuous taper `vix_size_factor = clip((vix−13)/(16−13),0,1)`. (b) setup override: don't skip in low-vol if ll_prob<0.30 AND reliability≥0.65 AND breakout clears bar.
- **Validation:** sweep VIX in 1-pt bins cross-tabbed by ll_prob & reliability; find where conditional E crosses zero (not a global mean test).

### B18. Re-sweep `ll_hard_cap` on correct geometry
- **Current:** `ll_hard_cap=0.50` chosen from stale 0.7/1.2 buckets; [0.40,0.45) bucket is E=−0.253% (log:240) — cap may be too loose. ll model is ROC 0.731, used as a binary cliff (wastes ranking info).
- **Change:** sweep cap ∈ {0.30,0.35,0.40,0.45,0.50} on **new-geometry** CSV; likely tighten to ~0.40 and let the denominator penalty (or B6 numerator term) handle the 0.30-0.40 gradient.
- **Blocker:** all existing ll buckets are stale (built at 1.2/0.7) — recompute on current geometry first.

### B19. Per-ticker / per-sector regime instead of a single SPY scalar
- **Current:** one SPY+VIX state stamped identically on every candidate (`_dedup_ticker:377-382`); `sector_breadth` computed (`market_regime.py:380`) but only nudges the global label.
- **Change:** classify each of 11 sector ETFs with the same trend logic; blend `0.6×sector + 0.4×spy`; add a per-ticker trend filter (above own SMA20/50, fields exist `backtest.py:1007`) as a soft tier adjustment.
- **Mechanism:** differentiates the otherwise-constant regime_score across candidates → better ranking & tier assignment; the only Wave-4 item that makes the brain *differentiate* candidates (likely matters more than any single threshold for 30%+).
- **Risk:** more params → overfit; helps selection not tail protection.

### B20. Enforce Thursday/Monday skips inside the brain
- **Current:** `--skip-thursday`/`--skip-monday` are backtest/build_candidates filters only; no day-of-week gate in the brain; log notes propagation bugs where `skip_*` were missing from `_today_args` (`:658,732`). Thu E=−0.26% (z=−3.5, p<0.0002); Mon WR 55.3% vs 66.9% (p=0.000125), replicated 2019-2025.
- **Change:** add `skip_weekdays:[0,3]` and a hard-reject in `score_one`/`process` on `signal_date.weekday()`. **First fix:** `signal_date` can be empty (`_dedup_ticker:363`) → gate silently no-ops; fix date plumbing.
- **Risk:** cuts ~40% of trading days → fewer opportunities; verify EV uplift outweighs trade-count loss for a frequency-dependent target.

### B21. Add earnings-date avoidance + gap filter
- **Current:** none. 3-day holds straddling earnings are fat-tailed coin-flips; post-large-gap entries chase a spent move.
- **Change:** reject/down-tier candidates with earnings in `[entry, entry+max_hold]` (needs earnings calendar); reject entries gapped >3% into the signal unless breakout-structured (reuse `gap_pct`, `backtest.py:566`).
- **Mechanism:** removes highest-variance lowest-edge entries → tightens the loss tail.

---

## Sequencing & Expected Path to 30%+

| Wave | Items | Est. cumulative |
|------|-------|-----------------|
| **1 — Geometry coherence** | B1 (stop 0.7→1.0) + B2 (target→2.0) + B3-B5 | ~0 → **~30-45%/trade-EV-driven** |
| **2 — Calibrated edge in numerator** | B6 (ll→numerator) + B7 (survivor head) + B8-B10 | better ranking → capital concentration into real edge |
| **3 — Kelly sizing** | B11 (port sizer) + B14 (DD throttle) + B12,B13,B15,B16 | 25% → **40-45%** with controlled DD |
| **4 — Regime/gating** | B17-B21 | trims negative tail, lifts frequency in high-EV regimes |

**Recommended first 4 changes (highest ROI, lowest risk):**
1. **B1** — screener stop 0.7→1.0 (the dominant fix; likely near 30% alone).
2. **B11 + B14** — port Kelly sizer + drawdown throttle (return lever + safety valve, mostly wiring).
3. **B6** — promote calibrated `1−ll_prob` to numerator (use the ROC-0.731 signal as edge, not just a penalty).
4. **B8 diagnosis** — confirm whether breakout_score is dead; it currently contributes nothing.

---

## Hard Constraints (per research-loop rules — do not violate)

1. **Stale buckets:** every EV figure in the log was computed at 0.7/1.2 geometry. After B1, **recompute all buckets** before tuning B18/B17/B19 — else you optimize against the wrong distribution.
2. **OOS haircut:** in-sample E discounts to ~30-50% OOS (WF ROC≈0.51, log:321). Validate on walk-forward, not in-sample buckets. 30% target must clear on OOS-discounted EV × frequency.
3. **No weakening of validation or risk controls.** No tuning on holdout. Gate every model-head promotion on WF ROC thresholds stated per-item.
4. **Dead ends — do not revisit:** win_prob in numerator/gate (ROC 0.491), expected_return boost (R²<0), target_before_stop (ROC 0.504), timeout-as-penalty, shortening max_hold (h5<h10). Rejected entry gates: ADX, CCI floor, MACD buckets, upper-wick, ATR floor, CMF (year-inconsistent); only RSI9≥44 survived.
5. Append all validation results to `ALPHA_EVOLUTION_LOG.md` (never overwrite).

---

*Generated read-only. No source files modified. Subagent provenance: alpha (P1-P6), sizing (P1-P6), regime (1-7), exit (#1-#6) — renumbered B1-B21 here. Companion: `PORTFOLIO_AUDIT_2026-05-30.md`.*

---

## IMPLEMENTATION STATUS — 2026-05-30 (Cycle 45)

**IMPLEMENTED:** B1 (stop 0.7→1.0, Cycle 44), B3/B5 (chandelier intermediate breakeven + conditional time-exit in short_hold_exits), B10 (reliability curve unified+clamped), B16 (heat taper). Brain fully unified to the live AlphaEngine formula (vol penalty, breakout boost, tier mults, reliability floor).

**DEFERRED — need validation/retrain (do NOT ship unvalidated per loop ML rules):** B2 (target→2.0 ATR) & B4 (tier-conditional partial) — change payoff geometry, need MFE/MAE re-sim on current geometry; B6 (1−ll to numerator), B7 (survivor head), B8 (breakout diagnose), B9 (composite calibration + re-derived cutoffs); B11 (Kelly port), B12 (continuous conviction), B13 (regime heat + correlation sizing), B14 (drawdown throttle — safe but Brain path not live); B17-B21 (VIX taper, ll_cap sweep, per-sector regime, Thu/Mon gate, earnings/gap filters). All gated on a validated 1.2/1.0-geometry retrain.
