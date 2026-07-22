# Performance Review — Agentic Trader

_Senior performance-engineer pass, 2026-07-07. Read-only analysis; no code changed. Evidence is file:line-backed._

---

## Load-profile reality check

This is **not** a millions-of-users web service. `app.py:872` acquires a lifetime flock because all order/state locks are in-process `asyncio.Lock` — the system is **capped at one process, one box, effectively one user**. There is no request-throughput problem to solve. The real costs are:

1. **Redundant network I/O** — the system re-downloads market data it already has.
2. **Frontend polling churn** — many timers, no visibility gating.
3. **Hot-loop allocation** — per-tick object churn in the exit engine.

Optimize those. Anything framed as "web scale" is misdirected here.

---

## Bottleneck breakdown (ranked by actual wall-clock / memory cost)

| # | Bottleneck | Evidence (file:line) | Why it costs |
|---|---|---|---|
| 1 | No price/quote cache; 20 inline `yfinance.download` | 20 sites; VIX ≥2×/cycle (`paper_trade_today.py:2348`, `4648`); 1-yr correlation matrix **per scan** (`4434`) | Each scan re-downloads existing data. Dominant wall-clock. |
| 2 | N+1 fundamentals | 2 calls/ticker (`3497`,`3509`); `earnings_near` re-fetches cached calendar (`3423`); fanned 16-wide (`3536`) | 2× network per candidate. |
| 3 | yfinance FD leak | reaches into `yfinance.cache._TzDBManager` internals (`1619`) | Errno 24; symptom of per-ticker `Ticker()` churn. |
| 4 | `ThreadPoolExecutor` per symbol per quote | `quote_gateway.py:284`; serial `get_quotes` (`274`) | Thread create/destroy churn, no batching. |
| 5 | `copy.deepcopy` per exit tick | `short_hold_exits.py:260`, per-position per-cycle (`381`) | Full object alloc every hot-loop tick. |
| 6 | Unbounded cache + log | quote cache never evicted (`quote_gateway.py:254`); shadow-log appended per quote, no rotation (`359`) | Slow memory growth — the real "leak." |
| 7 | Frontend polling storm | Dashboard ~13 `useQuery`; `fidelity` polled in 2 sub-components (`Dashboard:194,791`); 35 `refetchInterval` total; none visibility-gated | Network + re-render even when tab backgrounded. |
| 8 | Render waste | 40 memo guards in 14k LOC; 1,280-LOC Dashboard re-renders wholesale on any of 13 query ticks; inline style objects re-allocated per render | Jank on data-heavy pages. |
| 9 | Per-order object churn | `import PreTradeGate` inside `validate_live_order` per call (`compliance.py:220`); fresh `ExitManager` per holding ×2 (`holdings_brain.py:499,1073`) | Minor but pure waste on the money path. |
| 10 | State write amplification | `account.save()` called ~6× inside one `scan_account_once` (`3778–4500`) | Repeated full-JSON serialization per scan. |

---

## Optimization strategies

### A. Collapse market-data I/O into one cached, batched service (fixes #1, #2, #3)
- One `PriceService` with a TTL cache keyed by (symbols, period, interval), backed by a **single batched** `yf.download(universe, …)` instead of per-ticker `yf.Ticker().info`/`.calendar`. Batching is what removes the N+1 **and** the FD leak (`Ticker()` is what leaks sqlite handles).
- Bound the cache (LRU, ~256 entries). Fetch the universe once per scan; derive VIX/correlation/fundamentals from the cached frame.

### B. Kill hot-loop allocation (fixes #5, #4, #9)
- Replace `copy.deepcopy(self.plan)` with `dataclasses.replace(plan, field=…)` — copies only changed fields.
- One reused `ThreadPoolExecutor` (module-level, bounded workers) instead of one-per-quote; batch `get_quotes` into a single fan-out.
- Hoist `from … import PreTradeGate` to module scope; construct `ExitManager` once and reuse.

### C. Frontend: visibility-gated polling + memoization (fixes #7, #8)
- One `poll(speed)` helper returning `refetchInterval: () => visible ? ms : false` + `refetchIntervalInBackground: false`. Backgrounded tab → zero polling.
- Share `queryKey` for identical endpoints so react-query dedupes the double `fidelity` fetch to one network call.
- `React.memo` the Dashboard sub-panels so a `tape` tick doesn't re-render the `history` chart. Hoist inline style objects to module scope (they're re-allocated every render today).

### D. Bound everything that grows (fixes #6)
- LRU/TTL on the quote cache; rotate the shadow-log at N MB; cap background-loop error/usage counters.

### E. Reduce write amplification (fixes #10)
- Batch the per-scan `account.save()` calls to one write at end of `scan_account_once` (guard correctness: still save before any await that could be cancelled).

---

## Scalability recommendations (honest)

- **The single-process ceiling is the only true scale limit — and it's a state-store migration, not a tuning job.** JSON files + in-process locks → SQLite WAL (single box, safe) → Postgres/Redis (multi-worker). Only then is `uvicorn --workers N` legal. Do not micro-optimize around this.
- **Batch, don't parallelize harder.** The quote path spawns a thread pool per symbol; the fix is one batched fetch, not more threads.
- **Keep heavy work off the request path.** Endpoints should read cached snapshots only (the Fidelity snapshot-cache pattern is correct — extend it to price data). Scans stay in background loops.
- **Add supervision.** Background loops die silently today (`_background_tasks` never inspected). A dead scan loop looks identical to a healthy idle one — add restart-on-death + a `/health/loops` probe. (Operability, but it's what prevents a silent perf cliff.)

---

## Suggested sequence (all behavior-preserving; characterization-test each before cutting)
1. `PriceService` + migrate VIX / correlation / fundamentals call sites (biggest win).
2. Hot-loop allocs (`deepcopy` → `replace`, pooled executor, module-scope imports).
3. Frontend `poll()` helper + query dedupe + `React.memo` Dashboard.
4. Bound caches/logs; batch `account.save()`.
5. (Separate track) state-store migration to lift the worker ceiling.
