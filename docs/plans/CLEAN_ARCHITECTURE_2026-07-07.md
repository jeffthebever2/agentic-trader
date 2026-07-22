# Clean Architecture Migration — Agentic Trader

_Senior-architect restructuring plan, 2026-07-07. Behavior-preserving only. First slice (config boundary) already shipped + tested._

---

## Guiding principle

You do **not** big-bang rebuild a 71k-LOC live-money system. You establish the **target layering and boundaries**, then migrate one vertical slice at a time behind characterization tests. Each slice must be provably output-identical before the cut. This document is the target; the `config/` boundary is the first slice, already landed (37 tests pin `env_bool` to the exact legacy idiom; full suite 1551 green).

---

## The four layers (dependency rule: inner never imports outer)

```
┌─────────────────────────────────────────────────────────────────────┐
│  INTERFACE / DELIVERY        web/api/*  ·  frontend/  ·  scripts CLIs │  ← FastAPI routers, React, argparse
│  (thin: parse request → call application → serialize response)       │
├─────────────────────────────────────────────────────────────────────┤
│  APPLICATION / USE-CASES     web/services/*  ·  application/*         │  ← orchestration: approve_signal, run_scan,
│  (workflow: gate → size → dispatch → notify; no HTTP, no Playwright) │     copytrade.reconcile, holdings-brain cycle
├─────────────────────────────────────────────────────────────────────┤
│  DOMAIN / CORE               tradingagents/portfolio/  ·  compliance  │  ← pure decision logic: sizing, scoring,
│  (pure, sync, network-free; the safety floor; deterministic)         │     exits, validate_live_order, reconcile
├─────────────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE              tradingagents/config/  ·  data/          │  ← env, price service, quote gateway,
│  (env, brokers, market data, persistence, SMS — all I/O lives here)  │     Fidelity/Webull adapters, state store, SMS
└─────────────────────────────────────────────────────────────────────┘
```

**Today's violations (from the architecture review):**
- Domain does I/O: `correlation.py`, `state.py`, `prediction_grader.py` import `yfinance`. → must move behind an infrastructure `PriceService`.
- Delivery does domain + infra: `approve_signal` (558 LOC) parses the request, sizes, gates, drives Playwright, writes state, and sends SMS in one function. → split across the three inner layers.
- Application logic lives inside a 6,562-LOC delivery module (`thematic_auto.py`). → extract to `web/services/`.
- Infrastructure is inlined everywhere: 24→ now-centralized env reads (done), 20 inline `yfinance` calls, 6 SMS senders (still to do).

---

## Target folder structure

```
tradingagents/
  config/                     ← INFRA: the config boundary  [SHIPPED]
    __init__.py               env_bool/int/float/str/list re-exported
    env.py                    typed env accessors (one truthy dialect)
    settings.py               (next) frozen dataclass of all flags, validated at boot
  data/                       ← INFRA: market data + persistence adapters
    price_service.py          (next) ONE cached, batched yfinance wrapper — kills 20 call sites + FD leak
    quote_gateway.py          existing multi-provider trusted-quote (move here)
    store/                    (phase 3) SQLite-backed state store replacing tmp/*.json
  domain/                     ← DOMAIN: pure decision core (rename of today's portfolio/)
    sizing.py                 ONE PositionSizer (collapses the 4 sizers)
    scoring.py                ONE scoring core (collapses unified_brain/alpha_engine twins)
    exits.py                  ONE stop/target/trailing engine (collapses the 4 copies)
    reconcile.py              copytrade_reconcile (already pure — exemplar)
    compliance.py             validate_live_order + HARD_BLOCK moved INSIDE it
  brokers/                    ← INFRA: execution adapters (protocol-driven)
    base.py                   Broker Protocol: place_order/exit/positions/balances
    fidelity.py               Playwright adapter (extract from web/api/fidelity.py)
    webull.py                 fidelity-api adapter

web/
  services/                   ← APPLICATION: use-cases (no HTTP/Playwright/LLM inline)
    thematic/                 scan, merge, ai_pick, approve — extracted from thematic_auto.py
    copytrade.py              reconcile+execute orchestration (already close to clean)
    holdings_brain.py         brain cycle orchestration
    notify.py                 (next) ONE send_notification(): quiet-hours + cooldown applied once
    scheduler.py              (next) ONE loop-runner replacing 10 copy-pasted while-True bodies
  api/                        ← DELIVERY: thin routers only (parse → service → serialize)
    thematic_auto.py          shrinks from 6,562 → routes only (~500 LOC)
    fidelity.py               shrinks to route handlers; browser logic → brokers/fidelity.py
  app.py                      composition root: wire routers + start scheduler (no logic)

frontend/src/
  shared/
    poll.ts                   (next) visibility-gated polling policy
    api/                      typed client + CODEGEN types from Pydantic (kills double-typing)
  features/<feature>/         page split into hooks/ + components/ (kills 1500-LOC god-pages)
  components/ui/              the EXISTING design system — enforce it, delete per-page styles
```

---

## What shipped in this pass (slice 1 of the migration)

**The config/infrastructure boundary.** `tradingagents/config/env.py` with `env_bool/env_int/env_float/env_str/env_list`.

- Replaced the copy-pasted coercion idiom at **24 call sites** across `thematic_auto.py`, `fidelity.py`, `holdings_brain.py`, `app.py` with `env_bool(...)`.
- **Provably behavior-preserving:** `test_config_env.py` parametrizes `env_bool` against the exact legacy expression `os.getenv(X, d).strip().lower() in ("1","true","yes","on")` over every input × both defaults — 32 equivalence assertions, all green.
- **Deliberately did NOT touch** the ~22 strict `== "true"` sites: `env_bool` is permissive, so migrating them would make `FLAG=yes` newly truthy — a behavior change. Those migrate separately, by choice, not by sweep. This is the discipline the "don't change behavior" constraint demands.
- Full suite: **1551 passed** (was 1514 + 37 new), zero regressions.

Why this slice first: config is the innermost infrastructure dependency — every other layer reads it. Establishing one typed boundary is the prerequisite for a `Settings` object (validated once at boot) that the domain and application layers receive by injection instead of reaching into `os.environ` from 348 scattered sites.

---

## Migration order (each slice = its own reviewed PR, tests-first)

1. **`config/` boundary** — DONE. Next: `config/settings.py` frozen dataclass, injected at composition root.
2. **`data/price_service.py`** — one cached batched market-data adapter; migrate the 20 `yfinance` sites; move `correlation/state/prediction_grader` off direct yfinance (fixes the domain-does-I/O violation + FD leak).
3. **`web/services/notify.py`** + **`scheduler.py`** — collapse 6 SMS senders and 10 loop bodies; `app.py` becomes a real composition root.
4. **Collapse duplicated domain** — 4 sizers→1, 2 scoring engines→1, 4 exit engines→1, behind characterization tests that pin current numeric output. Delete the "must match X" comments by making them the same code.
5. **Split `thematic_auto.py`** — extract application logic to `web/services/thematic/`; router keeps HTTP only. Same for `paper_trade_today.scan_account_once` (909 LOC).
6. **`brokers/` protocol** — extract Playwright/webull to adapters behind a `Broker` Protocol; collapse the trade/exit inner twins.
7. **Frontend** — codegen types, `poll()` policy, split god-pages into `features/`, enforce `components/ui/`.
8. **`data/store/`** — SQLite WAL state store; only then does the single-worker ceiling lift.

**Non-negotiable rule for every slice:** pin current output in a characterization test → extract → prove identical → cut. Never refactor a god-function without the pin first.
