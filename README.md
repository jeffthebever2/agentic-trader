# Agentic Trader

A production algorithmic stock-trading system: ML candidate scanning, a 15-portfolio
paper-trading competition, Qlib alpha research, a FastAPI + React dashboard, and
**real-money broker execution** (Fidelity / Webull) behind a human-in-the-loop (HIL)
approval flow.

> ⚠️ **This system can place real orders with real money.** Live execution is OFF by
> default and guarded by a multi-layer compliance kill-chain plus per-trade step-up 2FA.
> Read [`SECURITY.md`](SECURITY.md) before enabling anything live.

- Python package: **`tradingagents/`**
- Dashboard backend: **`web/`** (FastAPI, served on `127.0.0.1:8001`)
- Dashboard frontend: **`frontend/`** (React + Vite + TypeScript → built into `web/static/dist`)
- Agent/developer guide: [`CLAUDE.md`](CLAUDE.md) · Deep-dive docs: [`docs/`](docs/)

---

## Architecture

```
            Market data (yfinance · Fidelity · trusted quote gateway)
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                            ▼
  Candidate scanner          Thematic scanner            Holdings Brain
  (breakout / pullback)      (~15 social/news feeds)     (reads REAL broker
        │                          │                      holdings, assesses
        ▼                          ▼                      hold/trim/add/exit)
  ML win-probability         AI pick + buzz/intent             │
  (XGBoost bundle)           scoring → signals                 │
  + Qlib alpha factors            │                            │
        │                          │                            │
        ▼                          ▼                            ▼
  UnifiedBrain  ───────────►  HIL approval queue  ◄─────────────┘
  (sizes / gates)            (human approves in dashboard or via SMS link)
        │                          │
        ▼                          ▼
  15 paper portfolios        Compliance kill-chain  →  LIVE broker order
  (competition leaderboard)  (tradingagents/compliance.py)   (Fidelity / Webull,
        │                     limit-only · 10% cap · $50k ·    LIMIT only)
        ▼                     trusted fresh quote · step-up 2FA
  FastAPI + React dashboard  ──────────────────────────────────►
```

Nothing trades autonomously. Every background loop is **propose-only** — orders require
an explicit human approval that passes the compliance gates.

---

## Quick Start (development)

```bash
# 1. Python env (two venvs exist: .venv main, .venv-torch for RL/torch)
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[web,dev]'        # deps come from pyproject.toml (uv.lock pins them);
                                   # `uv sync` also works. NOTE: requirements.txt is a stub.

# 2. Configure
cp .env.example .env               # fill in keys; live trading stays OFF by default

# 3. Frontend (only needed if you change the UI)
cd frontend && npm install && npm run build   # → web/static/dist (served live)

# 4. Run
./start.sh web                     # dashboard only → http://localhost:8001/app
./start.sh all                     # dashboard + paper-trading competition
```

Dashboard (SPA): **http://localhost:8001/app**
Legacy portfolio leaderboard (server-rendered): **http://localhost:8001/portfolios**

### Windows (one-click)

On Windows, skip the steps above — run the launcher from the repo root:

```bat
start.bat            :: set up venv + deps + .env + UI, then start the dashboard
start.bat doctor     :: check what's installed / wrong (changes nothing)
start.bat stop       :: stop the dashboard
start.bat help       :: all commands (paper, train, retrain, all, ...)
```

Double-click `start.bat`, or run it in Command Prompt / PowerShell. The first run creates
`.venv`, installs dependencies, copies `.env`, and builds the UI (a few minutes); after
that it starts in seconds and opens the dashboard in your browser. On any failure it
prints plain-English `[FAIL]` lines saying exactly what to fix. Requires Python 3.10+ on
PATH (tick *Add python.exe to PATH* when installing); Node 18+ only if the UI isn't
already built. The brains live in `scripts/windows_launcher.py`.

---

## Commands

```bash
# Dev process control (foreground)
./start.sh web        # FastAPI dashboard (run_web.py → uvicorn :8001)
./start.sh paper      # candidate engine (scripts/paper_trade_today.py)
./start.sh all        # web + paper together
./start.sh train      # full pipeline: ML + HMM + Qlib + validation (resumable)
./start.sh retrain    # weekly model refresh
./start.sh status     # what's running + model health
./start.sh logs       # tail latest logs
./start.sh stop       # kill managed processes

# Frontend (strict — must be type-clean to ship)
cd frontend && npm run build     # tsc -b && vite build → web/static/dist
cd frontend && npm run dev       # vite dev server (:5173, proxies /api → :8001)

# Tests (pytest; testpaths=tests, pythonpath=".")
python3 -m pytest                # full suite
python3 -m pytest tests/test_holdings_brain.py -q
python3 -m pytest -m unit        # markers: unit | integration | smoke
```

Full command reference (CLIs, health checks, production ops): [`docs/COMMANDS.md`](docs/COMMANDS.md).

### Production runtime (launchd)

In production the system runs as launchd services
(`~/Library/LaunchAgents/org.agentictrader.*.plist`): `webserver` (run_web.py → uvicorn
on `127.0.0.1:8001`), `papertrader` (`scripts/paper_trade_unified.py`, 15-min loop),
`tunnel` (cloudflared), `autofix`, `logrotate`. After editing backend code or `.env`,
reload the running server:

```bash
launchctl kickstart -k gui/$(id -u)/org.agentictrader.webserver
```

Frontend changes are pure static — `npm run build` then hard-refresh the browser.

---

## Subsystems

| Subsystem | Where | What it does |
|-----------|-------|--------------|
| **ML + portfolio pipeline** | `scripts/paper_trade_*.py`, `tradingagents/portfolio/`, `ml_models/` | Builds candidates → XGBoost win-probability + Qlib factors → `UnifiedBrain` sizes/gates → 15 competing paper accounts. |
| **15-portfolio competition** | `tradingagents/portfolios/`, `web/api/portfolios.py` | Same candidate stream, 15 different risk/sizing/hold configs, ranked live at `/portfolios`. |
| **Thematic system** | `web/api/thematic_auto.py`, `thematic_portfolio.py` | Scrapes ~15 social/news sources → AI pick + buzz/tweet-intent scoring → HIL signals → paper (and optional live). |
| **Holdings Brain** | `tradingagents/portfolio/holdings_brain.py`, `web/api/holdings_brain.py` | Reads real broker holdings (incl. pre-existing), proposes hold/trim/add/exit via HIL. Never touches protected (Roth/retirement) accounts. |
| **Live execution + compliance** | `tradingagents/compliance.py`, `web/api/fidelity.py`, `webull_portfolio.py` | Every live order passes the kill-chain. Fidelity = Playwright automation; Webull = `fidelity-api`/webull lib. |
| **Performance tracker** | `web/api/performance.py`, `frontend/src/pages/Performance/` | Deposit-adjusted P&L, cash-flow ledger, daily auto-capture from real broker data. |
| **Web dashboard** | `web/app.py`, `web/api/*.py`, `frontend/` | FastAPI backend (25 routers) + React SPA under `/app`. |

---

## Repository layout

```
tradingagents/        Core Python package
  compliance.py         Live-order kill-chain (the safety floor — never weaken)
  portfolio/            UnifiedBrain, Holdings Brain, sizers, exit managers
  portfolios/           15-portfolio competition framework
  qlib_integration/     Qlib alpha-factor pipeline
  screening/            Candidate + thematic scanners, tweet-intent
  data/                 quote_gateway.py (trusted execution quotes)
  ml/                   ML training + calibration

web/                  FastAPI dashboard backend
  app.py                App + startup background loops (all propose-only)
  api/*.py              25 routers (prefix /api): fidelity, webull, thematic,
                        holdings_brain, performance, portfolios, twofa, …
  static/dist/          Built React SPA (output of frontend build)

frontend/             React + Vite + TypeScript SPA (basename /app)
  src/pages/            Dashboard, Broker, HIL, Thematic, Performance, ML, …

scripts/              Paper-trade engines, training, validation, ops
ml_models/            Deployed model artifacts (latest/, stock_universe/, hmm_regime/)
docs/                 Reference docs + plans/ (audits & roadmaps)
tests/                pytest suite
```

---

## Configuration

Copy `.env.example` → `.env`. Key variables:

| Variable | Purpose |
|----------|---------|
| `WEB_HOST` | Bind host (default `127.0.0.1`). *Note: the bind port is hardcoded `8001`.* |
| `LIVE_TRADING_ENABLED` | Master live toggle (default `false`). Even when `true`, `LIVE_TRADING_HARD_BLOCKED` in source must also be off. |
| `FIDELITY_*` / Webull creds | Brokerage connection (only needed for live trading). |
| `FMP_API_KEY` | Trusted execution-quote source (gates live orders). |
| `THEMATIC_AUTO_SCAN` | Enable the 4-hour thematic scan loop. |
| `HOLDINGS_BRAIN_ENABLED` | Enable Holdings Brain proposal loops. |
| `STEP_UP_SECRET` | Secret for per-trade step-up 2FA (set a real value in production). |

The `.env` file is sensitive and has been clobbered before — **append, don't overwrite;
back it up first.**

---

## Safety & compliance

Live order execution is protected by independent layers (see [`SECURITY.md`](SECURITY.md)
for the full model):

- **Two master kill-switches** — `LIVE_TRADING_HARD_BLOCKED` (source constant) and
  `LIVE_TRADING_ENABLED` (`.env`, read fresh per call).
- **Compliance kill-chain** (`validate_live_order`) — LIMIT only (no market/short/margin/
  options), ≤10% of account per position, ≤$50k/order, and a **trusted, fresh** execution
  quote (`PreTradeGate`).
- **Per-trade step-up 2FA** on every order endpoint (TOTP / passcode / passkey).
- **Protected accounts** — Roth/retirement/non-equity accounts are never traded.
- **Propose-only loops** — background scanners queue HIL proposals; only the explicitly
  armed autonomous-live-exit loop can place an order without a fresh click, and it
  requires a step-up arm record.

---

## Requirements

- Python ≥ 3.10 (production runs 3.14)
- Node.js 18+ (frontend build only)
- Qlib 0.9.8+ (for `--include-qlib-features`)

```bash
pip install -e '.[web,dev]'      # or: uv sync
```
