# Agentic Trader — Command Reference

Accurate as of 2026-06-21. Environment: **macOS / zsh**.

Run everything from the project root using **absolute paths** where possible.
Don't `cd` inside compound bash commands (triggers permission prompts).

> **Canonical web entrypoint is `run_web.py`.** Anything you remember about
> `web/start.py` is dead — ignore it.

---

## Quick reference

| Goal | Command |
|------|---------|
| Web dashboard (dev) | `./start.sh web` → http://localhost:8001 |
| Candidate/paper engine (dev) | `./start.sh paper` |
| Web + paper together | `./start.sh all` |
| What's running | `./start.sh status` |
| Tail logs | `./start.sh logs [name]` |
| Stop dev procs | `./start.sh stop` |
| Full training pipeline | `./start.sh train` |
| Weekly retrain | `./start.sh retrain` |
| Portfolio leaderboard (CLI) | `./start.sh portfolios` |
| Model health | `./start.sh model` |
| **Apply backend / `.env` change (prod)** | `launchctl kickstart -k gui/$(id -u)/org.agentictrader.webserver` |
| Frontend build | `cd frontend && npm run build` |
| Full test suite | `python3 -m pytest` |
| Health check | `curl -s localhost:8001/health` |

---

## Environment & dependencies

Two virtualenvs exist:

- **`.venv`** — main env (web, ML, trading).
- **`.venv-torch`** — RL / torch work.

Production launchd services run under **Python 3.14**
(`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14`).

Dependencies are declared in **`pyproject.toml`** (locked by `uv.lock`).
**`requirements.txt` is a no-op stub (just `.`)** — do **not** `pip install -r requirements.txt`.

```bash
# Preferred (uv, uses uv.lock):
uv sync --extra web --extra dev

# Or with pip (editable install):
python3 -m pip install -e .
python3 -m pip install -e '.[web,dev]'      # include web + dev extras
```

Optional extra groups (from `pyproject.toml`): `web`, `dev`, `media`.

First-time setup:

```bash
python3 -m venv .venv && source .venv/bin/activate
uv sync --extra web --extra dev      # or: pip install -e '.[web,dev]'
cp .env.example .env                 # fill in your keys
# NOTE: .env is sensitive and has been clobbered before — append, don't overwrite. Back it up first.
```

---

## Dev / foreground process control — `./start.sh`

`start.sh` runs services in the **foreground / background for local dev**.
It resolves Python from `.venv/bin/python3` if present, else system `python3`.

| Command | What it runs |
|---------|--------------|
| `./start.sh web` | `run_web.py` — FastAPI dashboard → http://localhost:8001 (leaderboard at `/portfolios`) |
| `./start.sh paper` | `scripts/paper_trade_today.py` — candidate / paper-trading engine |
| `./start.sh all` | `run_web.py` + `scripts/paper_trade_today.py` (both backgrounded, PIDs tracked) |
| `./start.sh train` | `scripts/train_everything.py --tickers all_tickers.txt --include-qlib-features --profile safe` (ML + HMM + Qlib + validation, 30–90 min, resumable via `--resume`) |
| `./start.sh retrain` | `scripts/retrain_weekly.py --tickers all_tickers.txt --include-qlib-features` (fastest model refresh) |
| `./start.sh status` | Managed PIDs + port-8001 check + deployed-model health |
| `./start.sh logs [name]` | `tail -f logs/<name>.log`; with no name, tails the most recent `logs/*.log` |
| `./start.sh stop` | Kills managed procs **and** anything bound to port 8001 |
| `./start.sh portfolios` (alias `report`) | `scripts/portfolio_report.py` — leaderboard CLI |
| `./start.sh model` (alias `model-status`) | `scripts/check_retrain_status.py` — model health + retrain history |
| `./start.sh` (bare) / `--help` / `-h` | Prints help |

Extra args pass straight through, e.g.:

```bash
./start.sh train --resume
./start.sh logs web
```

---

## Production runtime — launchd (NOT `start.sh`)

In production the system runs as **launchd** services, defined in
`~/Library/LaunchAgents/org.agentictrader.*.plist`:

| Service | Process |
|---------|---------|
| `webserver` | `run_web.py` → uvicorn on `127.0.0.1:8001` |
| `papertrader` | `scripts/paper_trade_unified.py` (15-min loop) |
| `tunnel` | `cloudflared` |
| `autofix` | self-heal watcher |
| `logrotate` | log rotation |

Logs land in `logs/*.log` and `logs/*.err`.

### Apply a backend / `.env` change

The running server loaded its **code and `.env` at startup**, so edits aren't live
until you reload the process:

```bash
launchctl kickstart -k gui/$(id -u)/org.agentictrader.webserver
```

- `kickstart -k` **restarts the plist's process** (picks up edited Python + `.env`).
- It does **NOT** reload an edited `.plist`. Changing the plist itself needs a full
  **unload + load** (`launchctl bootout` then `bootstrap`).

Other services kickstart the same way, e.g.:

```bash
launchctl kickstart -k gui/$(id -u)/org.agentictrader.papertrader
```

### Edit → see-it loop

- **Frontend** changes are pure static (`npm run build` → `web/static/dist`, served
  live) → just **hard-refresh the browser**. No kickstart needed.
- **Backend / `.env`** changes → **kickstart the webserver** (above).

---

## Frontend (React / Vite / TypeScript)

The trading UI lives entirely in **`frontend/`**. The build is strict — `tsc -b`
must be type-clean (any implicit-`any` / untyped code fails the build).

> The root `package.json` (chalk/vitest "orchestrator") is an unrelated tool —
> **ignore it**.

```bash
cd frontend
npm run build     # tsc -b && vite build → outputs to web/static/dist (served live → hard-refresh)
npm run dev       # vite dev server on :5173, proxies /api → :8001
npm run lint
```

---

## Tests (pytest)

Config lives in `pyproject.toml` `[tool.pytest.ini_options]`
(`testpaths=tests`, `pythonpath="."`, `--strict-markers`).

```bash
python3 -m pytest                              # full suite
python3 -m pytest tests/test_holdings_brain.py -q   # one file
python3 -m pytest tests/test_x.py::test_name        # one test
```

Markers:

```bash
python3 -m pytest -m unit
python3 -m pytest -m integration
python3 -m pytest -m smoke
```

---

## CLIs

### `ta` — operator console (`ta = "ta:app"`)

Installed as a console script via `pip install -e .`. Subcommand groups:

```bash
ta paper status        # paper: status / start / stop
ta ml status           # ml: retrain / status / train / predict
ta hil pending         # hil: pending / approve / reject
ta server              # server controls
ta backtest            # backtests
ta db                  # db utilities
ta --help              # full command tree
```

### `agentic-restore` — fresh-machine bring-up (`agentic-restore = "cli.restore_runtime:main"`)

Checks deps, restores optional local ML/data artifacts, starts the web app + tunnel,
prints troubleshooting logs.

```bash
agentic-restore doctor        # diagnostics
agentic-restore start --restart
agentic-restore start --restart --quick-tunnel   # temporary Cloudflare Quick Tunnel
agentic-restore stop
agentic-restore bundle-data --output ~/Desktop/agentic-trader-artifacts.tar.gz
agentic-restore all --artifact-tar ~/Desktop/agentic-trader-artifacts.tar.gz --install --restart
```

Git intentionally omits large local folders (`ml_models/`, `rl_models/`,
`.backtest_cache/`, `backtest_index.db`); `bundle-data` packages them for transfer.

---

## Health checks

```bash
curl -s localhost:8001/health          # liveness
curl -s localhost:8001/health/deep     # deep check (deps / data / market-hours aware)
curl -s localhost:8001/api/paper/status | head -c 500   # paper engine status

./start.sh status                      # dev: procs + port 8001 + model health
ps aux | rg "run_web.py|paper_trade_(today|unified).py"  # is it running?
lsof -ti:8001                          # what's on the web port
```

---

## Gotchas

- **`run_web.py` is the entrypoint** — not `web/start.py`.
- **`WEB_PORT` does nothing** — the uvicorn bind is hardcoded to **8001**. Only
  `WEB_HOST` is honored.
- **`requirements.txt` is a stub** — install via `uv sync` / `pip install -e .`.
- **`kickstart` reloads the process, not the plist** — editing a `.plist` needs
  unload + load.
- Frontend `tsc -b` is strict — type errors block the build.
- Don't `cd` inside compound bash commands here (permission prompts) — use absolute
  paths.
- `.env` is sensitive and was clobbered once — **append, don't overwrite; back it up first.**
