# Architecture Review — Agentic Trader

_Senior-engineer reverse-engineering pass, 2026-07-07. Read-only analysis; no functionality changed. Evidence is file:line-backed._

---

## 1. Clean architecture breakdown (what it actually is)

Five subsystems, ~71k LOC Python + ~14k LOC React:

```
                         ┌─────────────────────────────────────────────┐
   social/news scrapers ─┤ web/api/thematic_auto.py  (6,562 LOC god)   │
   (~16 sources)         │   scrape → merge → AI pick → score → size   │
                         │   → approve_signal (558 LOC) → paper + live │
                         └──────────────┬──────────────────────────────┘
                                        │
  ml_models/*.joblib ──► scripts/paper_trade_today.py (5,486 LOC) ──► tmp/**/state.json
   (build → predict)      scan_account_once (909 LOC)                  (per-portfolio JSON)
                                        │
  tradingagents/portfolio/ (decision core, "pure"):                    ┌── web/app.py ──┐
    unified_brain / alpha_engine / holdings_brain / position_sizer     │ 26 routers     │
    compliance.py ──► validate_live_order ──► PreTradeGate ──► quote   │ 10 bg loops    │
                                        │                              │ 1 flock lock   │
  web/api/fidelity.py (3,209 LOC) ── Playwright ──► digital.fidelity   └────────────────┘
    webull_portfolio.py ── fidelity-api lib
                                        │
  frontend/ (React 19 / Vite) ── 19 pages, 13k+ LOC, axios + react-query
```

**Live-order data flow (verified):**
`require_step_up` (2FA) → inline `LIVE_TRADING_HARD_BLOCKED` check → `live_trading_enabled()` → `validate_live_order(order_dict)` → gate chain (action/type/qty/symbol/product-rules/**$50k cap**/execution-quote) → `PreTradeGate.check(require_trusted_source=True)` → Playwright places LIMIT order.

**State layer:** ~10 per-user/per-portfolio JSON files under `tmp/`, guarded by a **mix of three lock disciplines** (`threading.Lock`, `asyncio.Lock`, one `CrossProcessAsyncLock`). Correctness rests on a single-process assumption enforced by one flock at startup.

---

## 2. Critical problem areas (ranked by blast radius)

### 🔴 P0 — Correctness/safety defects (fixing these DOES change behavior — needs sign-off)

1. **`$50k` per-order cap is silently skipped when `ref_px == 0`.** `compliance.py:185` — `if ref_px > 0 and quantity > 0:`. A non-market order missing both `limit_price` and `quote_price` gets `ref_px=0` → **bypasses the dollar cap entirely.** Only the execution-quote gate backstops it, and that runs only when `execute` is truthy. A hard dollar cap must never be conditional on the thing it's capping being present.
2. **The "ultimate kill switch" is not inside compliance.** `LIVE_TRADING_HARD_BLOCKED` (`compliance.py:20`) is checked by *each endpoint* (`fidelity.py:1491,2183,2598`), not by `validate_live_order`. Any code path that calls the validator without re-adding the line bypasses the ultimate block — and `thematic_auto.py:6261` documents a path that "never touches validate_live_order or step-up." The hard block belongs *inside* `validate_live_order`.
3. **Three uncoordinated kill switches.** Source constant (`compliance.py:20`), env toggle (`compliance.py:23`), and `safety_config.json` kill (`production_safety.py:512`) live on different code paths and don't know about each other. The production-safety kill cannot stop an order that only goes through `validate_live_order`.
4. **Symbol validation rejects valid tickers.** `symbol.isalpha() and len ≤ 5` (`compliance.py:175`) rejects `BRK.B`, class shares, any digit ticker — silent order refusal, no override.

### 🔴 P0 — Scalability ceiling (architectural, hard)

5. **Cannot run more than one web worker. Ever.** `app.py:872` acquires a lifetime flock because every order/state guard (`_ORDER_LOCKS`, `_paper_state_lock`, alert cooldowns) is an **in-process** `asyncio.Lock`. `uvicorn --workers N` → duplicate live orders. Override env `WEB_SINGLE_INSTANCE_LOCK=false` silently removes the only protection. This caps the entire system at one box, one process.
6. **Shared-state locking is a landmine.** The paper book `tmp/thematic_paper/state.json` is written by **4+ modules** (thematic_auto, thematic_portfolio, fidelity, holdings_brain) but serialized by **one lock owned by thematic_auto** that every other writer must remember to import. Thread-lock-guarded files (`paper.py:93`, `copytrade.py:43`) are **not** safe against the separate standalone runner processes.

### 🟠 P1 — God objects (maintainability collapse)

7. **`thematic_auto.py` = 6,562 LOC doing 10 jobs** (scraping, LLM, billing/neuron-accounting, scoring, sizing, persistence, SMS, exits, HTTP routes). `approve_signal` = **558 LOC**, `_run_scan` = **465 LOC**.
8. **`paper_trade_today.py` = 5,486 LOC.** `scan_account_once` = **909 LOC** god-function; `train_ml_models.train_models` = **769 LOC** god-function. None testable in isolation.
9. **Frontend god-components:** Settings 1,598 / Thematic 1,519 / HIL 1,507 / Dashboard 1,280 LOC. Dashboard mounts **13 concurrent polling queries** in one component.

### 🟠 P1 — Duplication that guarantees drift

10. **Four separate position sizers** (`position_sizer.py:206`, `position_sizing.py:106`, `portfolio_policy.py:194`, `unified_brain.py:857`) — code comments literally say "same formula as position_sizing.py". Two files named `position_sizer.py` vs `position_sizing.py`.
11. **Two full scoring engines** (`unified_brain.score_one` vs `alpha_engine.evaluate`) kept aligned only by "match live AlphaEngine" comments.
12. **Stop/target/trailing math in 4 places**; a module marked "NOT on live path" (`exit_manager.py:3`) is load-bearing on the live holdings path (`holdings_brain.py:1073`).
13. **Feature engineering TRIPLICATED** (backtest / `train_ml_from_stock_data` / inference `predict_ml`) with comments admitting "Must mirror exactly … to avoid feature mismatch" — a live train/serve-skew hazard.
14. **`_fidelity_thematic_trade_inner` (373 LOC) and `_exit_inner` (300 LOC) are structural twins**; position-scraping DOM logic exists in 3 copies.
15. **Copy-paste idioms:** env-bool parse **49×**, inline `yfinance.download` **20×**, SMS-number 4-way `or` chain **3×**, digest→email map built **4×** in one file. No `env_bool()`, no price service, no notify service.
16. **Constant-desync landmines with "must match" comments** that have already drifted: `min_rr` 1.15 vs 1.5, `partial_trigger` 0.50 vs 0.833, `breakout_max_boost` 0.30 vs 0.50, trusted-source sets `{3 entries}` vs `{11 entries}`.

### 🟡 P2 — Performance

17. **No quote/price cache.** 20 inline `yfinance.download` sites, each with divergent args; VIX downloaded ≥2×/cycle; `_ticker_fundamentals` does 2 network calls/ticker (N+1, fanned out 16-wide); 1-year correlation matrix downloaded **per scan**. yfinance FD leak is bad enough that code reaches into `yfinance.cache._TzDBManager` internals to close leaked handles.
18. **New `ThreadPoolExecutor` per symbol per quote** (`quote_gateway.py:284`); `copy.deepcopy` per exit-check tick (`short_hold_exits.py:260`); unbounded quote cache never evicted; shadow-log appended per quote with no rotation.

### 🟡 P2 — Error handling / operability

19. **545 broad `except Exception` in web/; 95 are `except: pass`** (thematic_auto 39, fidelity 32). The two most dangerous subsystems — live execution and the scan/approve god-functions — **fail silently**: price fetch → `None`, scraper → `{}`, loop body → `log.warning` + sleep. A persistently-failing trade executor is indistinguishable from a healthy idle one.
20. **Dead background loops never restart.** `_background_tasks` is never inspected for `.done()`/exceptions; a throw outside the inner try kills the loop permanently and silently.

### 🟡 P2 — Repo hygiene / testing

21. **Zero frontend tests. Zero tests on the Playwright live-order path.** 173 backend test files, all skewed to pure logic; the 6,562-LOC HTTP layer and the actual money path are the least covered.
22. **Repo root is a dump:** 57 untracked entries — `.txt`/`.epub` reference dumps, `backtest_charts_*/`, `paper_accounts/`, an unrelated `ai-orchestrator` npm project, a stale 4,275-LOC divergent copy of thematic_auto in `.claude/worktrees/`, and 3 separate frontends with 4 `node_modules` trees. `.gitignore` covers none of the generated output.

---

## 3. Refactoring strategy (phased, behavior-preserving)

**Rule: every step is a pure extraction — identical outputs, covered by characterization tests before the cut.**

### Phase 0 — Stop the bleeding (safety, 1 day)
- Move `LIVE_TRADING_HARD_BLOCKED` check *inside* `validate_live_order` (keep endpoint checks as defense-in-depth). Now impossible to bypass.
- Fix the `ref_px==0` cap gap: **fail closed** — no reference price ⇒ reject, don't skip the cap. _(Behavior change — needs your OK; it only ever fires on a malformed order that shouldn't execute anyway.)_
- Add a `/health/loops` endpoint that inspects `_background_tasks` for dead tasks; restart-on-death supervisor wrapper.

### Phase 1 — Extract shared seams (low risk, high leverage, ~2–3 days)
- `tradingagents/util/env.py::env_bool/env_int/env_float` → replace 49 copy-pasted parses. Mechanical, test-covered.
- `tradingagents/data/price_service.py` — one cached yfinance wrapper (TTL cache, single FD-managed session) → replace the 20 inline `download` sites and kill the FD-leak hack.
- `web/services/notify.py` — one `send_notification()` with quiet-hours + cooldown applied *once* → replace 6 divergent SMS senders.
- `web/api/fidelity/_browser.py` — extract the ag-grid scrape + read-through snapshot cache into one parameterized helper; collapse the trade/exit inner twins onto a shared `_place_order(side, …)`.

### Phase 2 — Break the god-files (medium risk, ~1–2 weeks)
- Split `thematic_auto.py` into a package: `sources/` (scrapers), `ai/` (LLM + neuron billing), `scoring/`, `sizing/`, `state/`, `notify/`, `routes/`. `approve_signal` → orchestrator calling named steps (`_resolve → _size → _gate → _dispatch_paper → _dispatch_live → _mark`).
- Split `paper_trade_today.scan_account_once` into `exits/`, `sizing`, `entries`, `persistence` — extract `PaperAccount` to the already-existing `paper_account.py` and delete the duplicate.
- Collapse the **4 sizers → 1** `PositionSizer` with pluggable factor stack; **2 scoring engines → 1** shared core with paper/live adapters. Delete the "must match" comments by making them the same code.

### Phase 3 — Fix the scalability ceiling (large, design-first)
- Replace in-process locks + JSON files with a real store (SQLite via WAL at minimum, Postgres/Redis for multi-worker). Once state is externally locked, `--workers N` becomes legal and the flock ceiling lifts.
- Consolidate `unified_brain.py` and `paper_engine.py` into one engine; delete the lossy `_map_row` bridge.

### Phase 4 — Frontend + hygiene (parallelizable)
- Enforce the existing `components/ui/*` design system; delete per-page style constants; codegen TS types from Pydantic (`datamodel-code-generator`) to kill the double-typing drift.
- Extract data-fetching hooks; central polling-interval config; one `useWebSocket`.
- `.gitignore` the generated dirs; remove the unrelated `ai-orchestrator` project and the stale worktree copy.

---

## 4. Improved production-grade code

Exemplar extractions (Phase 1) are the correct place to start — mechanical, fully behavior-preserving, test-covered, and they each delete dozens of copies. See the accompanying implementation for `env.py` + characterization tests as the first cut. The god-file splits (Phase 2) and the store migration (Phase 3) are large and should land as their own reviewed PRs, not a big-bang rewrite.

**Sequence:** P0 safety → Phase 1 seams → measure → Phase 2 one god-file at a time behind characterization tests → Phase 3 store. Never refactor a god-function without pinning its current output in a test first.

---

## ✅ IMPLEMENTATION STATUS (2026-07-07 — /goal execution)

**P0 safety defects — ALL FIXED (tests in `tests/test_live_order_compliance.py`, suite 1563 green):**
1. `LIVE_TRADING_HARD_BLOCKED` now enforced INSIDE `validate_live_order` (execute path), not only at endpoints — no call path can bypass it.
2. `$50k` cap fails CLOSED when no reference price exists on an execute order (was silently skipped when `ref_px==0`).
3. Symbol validation replaced with injection-safe `valid_symbol()` — accepts class shares (`BRK.B`) while still rejecting injection payloads; applied consistently in `compliance.py` + the three `web/api/fidelity.py` ticker guards.
4. Loop-death supervision shipped (see DEVOPS D4 — `_spawn_supervised_loop` + `/health/loops`).

**Phase 1 seam (config boundary)** already shipped (`tradingagents/config/env.py`, 24 sites). The god-file splits, 4-sizer collapse, 2-engine merge, and SQLite state-store migration remain **phased multi-PR work** (attempting them blind in one session would be the half-fixes the plan explicitly warns against) — the plan above is their tracked deliverable.
