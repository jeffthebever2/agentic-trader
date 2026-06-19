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

### Implemented this session (all tested, suite 742→908 green over the campaign)
| Fix | Commit | Effect |
|---|---|---|
| scan_memory not live-confirmation in solo dampener | `7f578a21` | correct false-positive suppression |
| **trendspyg** Google-Trends source (16th feed, OFF by default) | `0050070f` | leading search-interest attention signal |
| **IREN breakout fast-lane** (RVOL+new-high releases spike, OFF by default) | `753ce873` | catches day-0 catalyst movers, HIL-approved |
| buzz exits require price confirmation | `4b6f44f0` | stops dumping green winners on attention fade |
| max-hold converts green winner → trailing | `35ff475b` | stops truncating the right tail |

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
| **Exit enforcement (live path)** | **WEAK** — stops checked ≤6×/day, no intraday loop, no portfolio breaker on the live book | **Low** |
| Position sizing | **Weak** — not vol-adjusted; ATR ignored | Low-Med |
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
1. **(P0) Paper-only fast exit loop** (5–15 min, market-hours) → enforces stops; propose live exits. *[your decision]*
2. **(P0) ATR-scale stops + risk-based sizing** — `stop = entry − k·ATR`; `shares = risk_budget /(entry−stop)`. ATR already fetched.
3. **(P1) Enable + validate** breakout fast-lane (`THEMATIC_BREAKOUT_CONFIRM`) and trendspyg (`THEMATIC_GOOGLE_TRENDS`) in paper first.
4. **(P1) Wire FMP grades + Finnhub rec-trends + FINRA short-volume** as new `_scan_*` sources.
5. **(P2) Options-flow feed** (Tradier) + **8-K hard-catalyst fast-lane** + **regime gate** (put/call or breadth).
6. **(P2) Trading-day max-hold** + breakeven-stop ratchet + lower the 25% concentration ceiling for social-momentum names.
7. **(P3) Price/volume *discovery* universe scan** to catch no-buzz bases (the IREN-$5 blind spot).

— End of audit. Conclusions are evidence/reasoning-based, not certainties.
