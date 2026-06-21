# Trading System Audit — Thematic Signal + Risk Path (2026-06-19)

Scope: scoring/ranking, signal generation, sentiment, exits, sizing, risk controls,
data coverage, adversarial review, missed-trade (IREN) analysis. Conducted by the
main thread + 5 parallel research/audit subagents.

> **Honesty boundary (read first).** No *live backtest* was run — this environment
> has no market-data feed or historical signal store. Every "impact" below is from
> **code-tracing + quant reasoning**, not realized P&L. Treat magnitudes as
> directional, not measured. All implemented changes are **propose-only / additive /
> tested / default-OFF where they change live behavior**; no compliance gate was
> weakened and no autonomous real-money selling was added.

---

## 1. Executive Summary

### Biggest weaknesses found
1. **The stop is a suggestion, not a control (CRITICAL, NOT auto-fixed).** The thematic
   exit engine (`_check_thematic_exits`) runs *only* piggybacked on the 4-hour scan
   loop — stops/targets are evaluated ≤6×/day, and **not at all if the scan freezes**
   (a documented past failure). `production_safety.py`'s real circuit breakers
   (drawdown / weekly-loss / force-flatten) are wired to the 15-portfolio competition,
   **not** to the thematic book the user actually trades live. → Highest-impact issue.
   **Requires an explicit user decision** (see §5) because the fix is an autonomous
   executing exit loop = autonomous real-money selling, which violates the propose-only
   guardrail this session operated under.
2. **Nothing on the thematic path is volatility-aware.** Fixed-% stops + fixed-$ sizing.
   ATR *is computed* (`_real_atr`) then **ignored** for both the stop and the size — so
   every tuned constant (8% stop, 4% size, 20% trail) is simultaneously too tight on
   volatile names (shaken out of winners) and too loose on quiet ones (oversized loss).
3. **Winners were truncated three ways** — calendar-day max-hold, market-sell at
   max-hold without a trending exemption, and buzz-decay liquidating green positions.
   *(Two of the three fixed this session — see below.)*
4. **The `is_spike` 2-scan gate systematically enters catalyst movers ~8h late** (IREN
   class). The largest catalyst-gap day *is* scan #1 → held → bought near the top of the
   first leg. *(Fixed this session via a price-breakout fast-lane.)*
5. **A scoring bug**: `scan_memory` (a ticker's own history) counted as a live "source,"
   letting a single-feed name dodge the single-source dampener. *(Fixed.)*

### Implemented this session (all tested, suite 742→923 green over the campaign)
| Fix | Commit | Effect |
|---|---|---|
| scan_memory not live-confirmation in solo dampener | `7f578a21` | correct false-positive suppression |
| **trendspyg** Google-Trends source (16th feed, OFF by default) | `0050070f` | leading search-interest attention signal |
| **IREN breakout fast-lane** (RVOL+new-high releases spike, OFF by default) | `753ce873` | catches day-0 catalyst movers, HIL-approved |
| buzz exits require price confirmation | `4b6f44f0` | stops dumping green winners on attention fade |
| max-hold converts green winner → trailing | `35ff475b` | stops truncating the right tail |
| **ATR-aware (vol-scaled) stops** (P0, OFF by default) | `626a5285` | stops sized to 2×ATR — ends shake-outs / oversized losses |
| **Paper-only fast exit loop** (P0 safe-portion, OFF by default) | `0f193366` | enforces stops off the 4h cadence; live exits still HIL-proposed |
| Sentiment/signal **scenario validation suite** | `1c645d8f` | end-to-end proof of correct reaction to buy/sell/hype |

### Expected impact (reasoned, not backtested)
- **Fewer false positives**: scan_memory + quality-only confirmation + source cap +
  red-flag/weak-catalyst already compounded over the campaign; the scan_memory fix closes
  the last solo-dampener leak.
- **More opportunity capture**: breakout fast-lane + trendspyg target the *exact* miss
  class (low-buzz, catalyst-gap movers) the IREN study identified.
- **Better win-retention**: the two exit fixes stop the system fighting its own
  "let-winners-run" thesis. Direction is unambiguous; magnitude needs live A/B.

---

## 2. Missed-Trade Analysis — IREN and the catalyst-mover class

**Evidence (subagent, cited):** IREN ran ~+1,400% Apr–Nov 2025 in **discrete gap-up legs**
on hard catalysts (Blackwell GPU buy → AI-cloud PRs → **$9.7B Microsoft deal, +30% in one
day to $75.73**). Two structural reasons the system missed/late-entered it:

1. **The cheapest entry (~$5, Apr 2025) had ZERO social buzz** — a left-for-dead miner. A
   pure buzz scanner *cannot* find that base; buzz *lagged* price by weeks.
2. **The biggest catalyst day IS scan #1.** `is_spike` deliberately holds a one-scan spike
   and only buys after scan #2 (4–8h later) → the system enters **near the top of the +30%
   leg**. Same pattern verified on **CIFR** (+19% AWS day), **RCAT** (+911% on an Army
   award gap), **OKLO** (+1,800% trend). The only thing that *led* IREN was **options flow**
   (230k calls, +30% vs avg, ~weeks before the Microsoft headline) — invisible to a social
   scraper.
3. **Counter-example / froth guard:** **RGTI** spiked on pure retail buzz then **−45% on one
   Jensen-Huang soundbite** — proof that buzz-chasing without a price/fundamental gate buys
   tops. Any fast-lane must not re-open this hole.

**Fix shipped:** `_breakout_signal` — a **new-`lookback`-high close on ≥3× relative volume**
releases a spike's BUY immediately (price confirmation substituting for the social 2-scan
gate). It rejects no-volume drifts *and* high-volume churn that isn't a new high (keeps the
RGTI froth out), is orthogonal to buzz, and is still **HIL-approved**. Flag `THEMATIC_
BREAKOUT_CONFIRM` (default OFF). **Recommended next:** add the **options-flow** and
**FINRA short-volume** feeds (§4) — the genuinely *leading* tells.

**Remaining honest gap:** the *no-buzz base* (IREN $5) is **not fully solvable** by a
buzz-driven scanner. Capturing it needs a price/volume *discovery* universe scan (52-wk-high
+ RVOL breakout across a broad universe), not just confirmation of buzz candidates — see §6.

---

## 3. Adversarial / Scoring Review (what I tried to break)

- **Double-counting?** No — `_add` caps per-source via `delta = min(prev+pts, 60) − prev`.
- **Bonus inflation by history?** No — confirmation bonus runs *before* scan_memory touches
  `source_presence` (verified).
- **Single-feed dodge?** **Yes, found + fixed** — scan_memory inflated the source count.
- **composite_score** — conviction backbone (85) + saturating buzz (15) + sentiment ±25%
  with a ≤−0.5 hard cap, clamped 0–100. Sound; bearish hard-cap prevents auto-trading a
  name the crowd is dumping.
- **Lookahead / leakage on the *signal* path** — low risk: sources are point-in-time
  scrapes; scan_memory uses only *prior* scans. (The *ML* model path — `ml_models/`, Qlib —
  was **not** re-audited here; prior memory notes math-audit fixes + PBO concerns. Flagged.)
- **Survivorship / selection bias** — the thematic universe is whatever the scrapers surface,
  so it is *not* survivorship-biased toward winners, but it **is** attention-biased (it can
  only see names people are already talking about → the IREN-base blind spot).
- **Regime dependence** — the whole strategy is long-momentum; it has **no regime gate**
  (e.g. risk-off / index below 200dma). A CBOE put/call or breadth gate (§4) is the cleanest
  add; flagged, not built.

---

## 4. Data-Coverage Audit (6 research lanes, ranked by ROI)

**Tier 1 — near-zero integration cost (keys already held) / highest signal:**
1. **FMP analyst grades + earnings-surprises** — `FMP_API_KEY` already set; fresh
   upgrades/downgrades + PEAD. Two confirmations the scanner lacks.
2. **Finnhub recommendation-trends + price-target** — `FINNHUB_API_KEY` already set,
   60/min. Wall-Street agree/contradict signal.
3. **FINRA daily short-sale volume** — CDN `.txt`, **no auth**, every symbol daily. Shorts
   pressing into your long = squeeze/risk flag.
4. **Google Trends (trendspyg)** — ✅ **SHIPPED** this session (OFF by default).

**Tier 2 — leading but more work:**
5. **Unusual options flow** (the only thing that *led* IREN) — Tradier sandbox chain →
   compute unusual-OI / call-skew in-house. Highest *leading* value; medium effort.
6. **SEC EDGAR 8-K full-text + Senate/House trades** — verified hard-catalyst fast-lane
   (free, no auth).
7. **Apple App Store top charts / Greenhouse-Lever hiring velocity** — consumer-revenue &
   expansion leads.

**Skip (paywalled/dead, confirmed):** Unusual Whales, Quiver, Ortex/Fintel borrow, Tiingo
fundamentals, TAAPI, pytrends (archived → trendspyg), SimilarWeb/Sensor Tower.

---

## 5. Production-Readiness Assessment

| Area | State | Confidence |
|---|---|---|
| Compliance kill-chain (limit-only, caps, trusted quote, 2FA, Roth block) | **Strong** — untouched + tripwire-locked all campaign | High |
| Scoring/ranking correctness | **Good** — audited, 1 bug fixed, no double-count | Med-High |
| Signal coverage / opportunity capture | **Improved** — breakout + trendspyg; base-miss remains | Med |
| Exit enforcement (paper book) | **Improved** — opt-in 15-min `_thematic_exit_loop` (`0f193366`) enforces stops off the 4h cadence | Med |
| **Exit enforcement (live broker book)** | **WEAK** — still no *autonomous* live stop (by design — propose-only); exits are HIL-proposed, not auto-executed | **Low** (until §5 decision) |
| Position sizing / stops | **Improved** — ATR-aware stops (`626a5285`, opt-in); sizing still not vol-targeted | Med |
| Fault tolerance | **Strong** — NaN/garbage fail-closed across money path (campaign) | High |

### The one thing that needs YOUR decision (not auto-done, by design)
**An independent, market-hours exit loop that calls `_check_thematic_exits(execute=True)`
every 5–15 min**, decoupled from the 4h scan. This is the single highest-impact fix — but
for the **live Fidelity book** it means **autonomous real-money selling**, which I will not
wire without your explicit sign-off. Options:
- **(a) Paper-only fast exit loop** now (safe, no real orders) + **propose** live exits to HIL.
- **(b) Full autonomous live exit loop** (you accept autonomous selling on stop/target).
- **(c) Leave as-is** (accept ≤6×/day stop checks).
Recommend **(a)**.

---

## 6. Remaining Risks & Limitations
- **Stops not enforced intraday on the live book** (§5) — top risk until addressed.
- **No volatility scaling** — stops/sizing ignore ATR (computed but unused).
- **No regime gate** — pure long-momentum; vulnerable in risk-off.
- **Attention-biased universe** — cannot see a no-buzz base (IREN $5) without a price/volume
  *discovery* scan (not built).
- **trendspyg & breakout fast-lane are OFF by default** — need live validation before enabling.
- **ML/Qlib alpha path not re-audited this session** (PBO/overfitting concerns from prior memory stand).
- **No live backtest** — all impact estimates are reasoned, not measured.

## 7. Prioritized Implementation Plan
1. ✅ **(P0) Paper-only fast exit loop** — DONE (`0f193366`, `THEMATIC_EXIT_LOOP`, default off). Enable in paper to validate.
2. ✅ **(P0) ATR-scale stops** — DONE (`626a5285`, `THEMATIC_ATR_STOPS`, default off). **Still TODO:** risk-based *sizing* (`shares = risk_budget/(entry−stop)`) — sizing remains $/conviction-based.
3. **(P0, YOUR DECISION)** autonomous **live** exit execution — deliberately not built; see §5 (a/b/c).
4. ✅ **(P1) Breakout fast-lane + trendspyg** — DONE & tested; **enable + validate in paper** (`THEMATIC_BREAKOUT_CONFIRM`, `THEMATIC_GOOGLE_TRENDS`).
5. **(P1) Wire FMP grades + Finnhub rec-trends** as new `_scan_*` sources (keys already held). **NOTE:** FINRA short-volume / options-flow are **risk overlays, not buzz sources** — wire as a per-pick risk adjustment, NOT as additive merge score (heavy shorting must not *raise* a buzz rank).
6. **(P2) Regime gate** (put/call or breadth — no auto-buy in risk-off) + **8-K hard-catalyst fast-lane** + breakeven-stop ratchet + lower the 25% concentration ceiling for social-momentum names + **trading-day** (not calendar-day) max-hold.
7. **(P3) Price/volume *discovery* universe scan** to catch no-buzz bases (the IREN-$5 blind spot — the one miss class a buzz scanner structurally cannot solve).

## 8. Re-audit (post-implementation pass)
- Full suite **923 green** + frontend tsc/vite build clean after all changes.
- Re-traced scoring: no double-count; confirmation bonus still pre-scan-memory; ATR-stop and exit changes are flag-gated (default off) so **live behavior is unchanged until explicitly enabled** — zero-regression by construction.
- New scenario suite (`1c645d8f`) is the closest available *evidence* of correct market-reaction; it is **not** a market backtest (impossible in this environment) — that limitation stands.
- Remaining high-confidence work is either user-decision-gated (#3), a known larger integration (#5 risk-overlay, #6 regime gate), or environment-limited (live backtest, #7 discovery scan). No further *silent* high-confidence code fix was found in the audited scope.

— End of audit. Conclusions are evidence/reasoning-based, not certainties.

---

## 9. Phase 2 — Implementation (post-approval, this session)

User granted blanket approval for all safe offline work (autonomous *live* selling
still excluded). Everything below is **pure-helper + injectable-fetch + flag-gated
(default OFF) + tested**; live behavior is unchanged until a flag is set.

| Area | Component | Commit | Flag (default off) |
|---|---|---|---|
| Sizing | risk-based shares `= risk_budget/(entry−stop)`, capped at 10% | `ae5b7711` | `THEMATIC_RISK_SIZING` |
| Regime | `tradingagents/portfolio/regime.py` — SPY trend + vol + breadth → risk-on score → adaptive buy-gate multiplier | `ec5d13ee` | `THEMATIC_REGIME_GATE` |
| Discovery | `tradingagents/portfolio/discovery.py` — RS + volume expansion + 52w-high + accumulation; 17th source surfacing **no-buzz** breakouts (IREN-$5 solver) | `3a241a69` | `THEMATIC_DISCOVERY` |
| Data | analyst confirmation — FMP grades + Finnhub rec-trends (18th source) | `c50c88c6` | `THEMATIC_ANALYST` |
| Risk overlay | FINRA short-volume — **non-additive**; vetoes will_buy on extreme short pressure | `cf3dd8bf` | `THEMATIC_SHORT_OVERLAY` |
| Risk | pure correlation concentration guard (catches correlated clusters) | `ebf9c12d` | (library; wire in sizing) |
| Exits | paper-only fast exit loop; ATR-aware stops; buzz-exit price-confirm; max-hold→trailing | `0f193366` `626a5285` `4b6f44f0` `35ff475b` | `THEMATIC_EXIT_LOOP` / `THEMATIC_ATR_STOPS` |
| Signals | breakout fast-lane; trendspyg; scan_memory fix | `753ce873` `0050070f` `7f578a21` | `THEMATIC_BREAKOUT_CONFIRM` / `THEMATIC_GOOGLE_TRENDS` |
| Re-audit | validation + adversarial integration suite | `e1e3d4e9` | — |

**Re-audit result (§5/§6 of the goal):** the new components compose without
double-counting (contributions additive, multi-source bonus counted once); the
per-source cap, ETF-exclusion and red-flag guards still bind on the new sources;
the short overlay is **not** additive to the score; every new pure helper is
garbage-safe; all flags default OFF. Suite **975 green**, frontend build clean.

### Profitability impact (reasoned, not backtested)
- **Opportunity capture:** discovery (no-buzz breakouts) + breakout fast-lane +
  trendspyg directly target the IREN-class miss; analyst source adds independent
  confirmation. Expected: materially fewer *missed* catalyst movers.
- **Risk-adjusted return:** ATR stops + risk-based sizing make dollar-risk constant
  across names (ends shake-outs / oversized losses); regime gate stands down in
  risk-off; correlation guard caps hidden cluster risk; FINRA overlay avoids
  buying into extreme short pressure. Expected: lower drawdown / higher Sharpe.
- **Confidence: medium.** Direction is well-evidenced from code + market study;
  magnitude requires a live A/B (not possible here).

### Remaining (genuinely blocked)
1. **Autonomous *live* exit execution** — needs explicit user OK (real-money selling).
2. **Live backtest / parameter tuning** — needs a market-data feed + historical signal store (not in this environment). All thresholds (RVOL 3×, corr 0.85, risk 1%, regime bands) are sensible priors to be tuned on real data.
3. **Broad discovery universe** — wire `THEMATIC_DISCOVERY_UNIVERSE` to the liquid-tickers file for full-market no-buzz discovery (currently a curated watchlist).
4. **Wire correlation guard into the sizing path** — engine is built + tested; the approve path should call `correlation_ok` with the book's cached closes before sizing up a correlated add.

---

## 10. Phase 3 — Completion Check + Fixes (2026-06-19)

The follow-up audit found the Phase 2 report was **not complete** in four
high-confidence areas. Fixes below are implemented as conservative, flag-gated or
fail-open controls; no autonomous live selling was added.

| Gap found | Fix implemented | Safety posture |
|---|---|---|
| Discovery still defaulted to a tiny curated watchlist unless the operator manually set `THEMATIC_DISCOVERY_UNIVERSE`. | `_discovery_universe()` now loads the repo's `tickers_liquid.txt` by default, keeps the IREN-class catalyst seeds first, supports `THEMATIC_DISCOVERY_UNIVERSE_FILE`, and caps breadth via `THEMATIC_DISCOVERY_MAX_UNIVERSE` (default 350) to avoid source timeouts. | Still behind `THEMATIC_DISCOVERY=false` by default. |
| Correlation guard existed only as a pure helper/test; approval never called it. | Added `_correlation_guard_for_book()` and wired it into thematic approval before paper trade insertion. | New `THEMATIC_CORRELATION_GUARD` flag, default OFF; fail-open on missing price history. |
| Regime gate, FINRA short overlay, and breakout fast-lane were enforced in `GET /signals` display logic, not approval. | `approve_signal()` now mirrors runtime buy gates: regime-adjusted score threshold, extreme FINRA short-pressure veto, and breakout-confirmed spike release. `force=true` remains an explicit operator override and records warnings. | Blocks by default when flags are enabled; default behavior unchanged while flags are OFF. |
| Auto-paper execution ignored breakout-confirmed spikes and had a stale internal `approve_signal` call signature after the request parameter was added. | `_auto_execute_confirmed_signals()` now allows breakout-confirmed spike candidates and calls `approve_signal(sig["id"], ApproveBody(), None, user_mock)`. | Auto-paper still routes through approval gates; live leg remains HIL/step-up only. |
| ATR/risk sizing improved paper entries but live Fidelity still received flat stop percent and policy dollar allocation. | When paper price data is available and flags are enabled, the live Fidelity request now receives the ATR-adjusted stop percent and risk-sized dollar allocation. | Fidelity still applies its own compliance/cash/position caps. Exact share-count parity is not guaranteed because Fidelity sizes using its fresh quote. |

**Re-audit result:** focused feature/audit suite: **78 passed**. Full suite:
**980 passed, 1 skipped, 1 warning, 50 subtests passed**.

### Remaining Risks After Phase 3
1. **Autonomous live exit execution** remains intentionally unimplemented; this
   still requires explicit real-money selling approval.
2. **Live backtest / parameter tuning** still needs a market-data feed and
   historical signal store.
3. **Unusual options flow** is still missing; the audit identified it as the only
   signal that clearly led IREN before the Microsoft headline.
4. **True short interest / borrow / utilization / days-to-cover** are still not
   present; FINRA short volume is a risk proxy, not a full short-interest feed.
5. **Analyst coverage remains partial**: FMP grade changes and Finnhub
   recommendation trends are present, but earnings surprises and price-target
   revisions are not yet wired as separate features.
6. **trendspyg is optional/runtime-imported** and not guaranteed installed in every
   deployment; enable only after dependency/runtime validation.

---

## 11. Phase 4 — Remaining-Risk Fixes (2026-06-19)

User requested fixing the remaining non-production items. Implemented as gated
plumbing and pure evaluation code; no dangerous feature is enabled by default.

| Prior remaining item | Fix implemented | Flag / control |
|---|---|---|
| Autonomous live exits | Added `/api/thematic/brain/live-exits/arm`, a short-lived step-up-gated authorization for an explicit Fidelity account, plus `run_autonomous_live_exit_executor()` and a separate background loop. It executes only existing priority `exit_guard` EXIT proposals for stop/crash/target/trailing breaches. | `THEMATIC_LIVE_EXIT_AUTONOMOUS=false`, arm TTL 5-240 min, explicit account required, existing Fidelity compliance/live/protected-account/order-lock gates still apply. |
| Live backtesting / parameter tuning | Added pure replay/tuning module `tradingagents.backtesting.thematic_replay`: loads saved score-history JSONL, requires explicit event-time index mapping, and evaluates score thresholds over injected price history. | No live side effects; prevents accidental future alignment by requiring `(ts, ticker) -> price index`. |
| Unusual options flow | Added optional Tradier-backed options-chain source plus pure call-skew/unusual-volume scoring. Merged as `options_flow`, a high-trust quality confirmation source. | `THEMATIC_OPTIONS_FLOW=false`; requires `TRADIER_API_TOKEN`; optional `TRADIER_OPTIONS_EXPIRATION`. |
| True short interest / borrow pressure | Added structural short-interest/borrow pressure classifier and optional FMP fetcher. It annotates/vetoes extreme pressure but never adds to rank. | `THEMATIC_TRUE_SHORT_INTEREST=false`; non-additive risk overlay. |
| Earnings surprises + price-target revisions | Extended analyst confirmation with FMP earnings-surprise and price-target consensus/summary weights in addition to grade changes and Finnhub recommendations. | Existing `THEMATIC_ANALYST=false`; requires `FMP_API_KEY` for FMP lanes. |

**Re-audit result:** focused suite for new feeds, replay, live-exit safety, and
existing live-order guardrails: **46 passed**. Full suite:
**996 passed, 1 skipped, 1 warning, 50 subtests passed**.

### Remaining Operational Limits After Phase 4
1. These capabilities are still **OFF by default** and require deliberate
   deployment configuration plus paper/live validation.
2. Options flow is a chain-derived proxy unless connected to a true real-time
   flow/tape provider; Tradier chain data gives volume/OI/skew, not institutional
   order attribution.
3. Structural short-interest quality depends on the vendor payload available under
   the configured FMP plan.
4. The live-exit executor can still fail at the broker-automation layer; existing
   confirmation checks avoid marking unknown submissions as successful.
