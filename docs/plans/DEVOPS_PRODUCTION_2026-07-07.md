# Production Deployment Plan — Agentic Trader

_Senior-DevOps assessment, 2026-07-07. Grounded in what this system actually is, not a generic cloud template._

---

## The one constraint that shapes everything

**This service cannot be horizontally scaled.** Order/state locks are in-process `asyncio.Lock`; state is JSON files in `tmp/`; the Fidelity path holds a stateful Playwright browser session on disk (`.fidelity_session_*.json`) that **cannot run true-headless** (needs a real Chrome). Startup takes a `flock` and *refuses to boot beside a second instance* (`web/app.py:884`).

Therefore:
- **No `--workers N`. No K8s `replicas > 1`. No HPA.** A second replica = duplicate live orders. This is the single most important production fact.
- The correct topology is **one node, one process, stateful, behind the tunnel** — and make *that* bulletproof (restart, monitor, back up). Scaling is a *state-externalization project* (SQLite→Postgres/Redis), not a deployment knob. Until then, "optimize scaling" means vertical + fast restart, not replicas.

The existing `deploy/systemd/agentictrader-web.service` already encodes this correctly (workers=1, `Restart=always`, `LimitNOFILE=8192` for the yfinance fd leak, single-instance note). **That is the real, sound deployment path.**

---

## 🔴 What's broken today (the DevOps findings)

### D1 — CI is false-green: it tests the WRONG project.
`.github/workflows/ci.yml` runs `npm ci / npm run typecheck / npm run build / npm test` at the **repo root**. Root `package.json` is the unrelated `ai-orchestrator` CLI (per CLAUDE.md + arch review). So CI:
- never runs the **1551 pytest tests** (the money-path logic),
- never builds the real UI (`frontend/`),
- runs vitest on an unrelated tool and reports green.
**Every merge is unverified.** This is the highest-priority DevOps fix — a green check that means nothing is worse than no check.

### D2 — Docker artifacts deploy the wrong app.
`Dockerfile` `ENTRYPOINT ["tradingagents"]` runs the orchestrator CLI, not `run_web.py`/uvicorn. It installs no Playwright browser, doesn't build `frontend/`, doesn't expose 8001. `docker-compose.yml` bind-mounts `.env` (secrets into build context) and ships an `ollama` profile — generic template, not this system. **The container cannot run the trading app.**

### D3 — No log rotation → unbounded growth + secrets-in-tracebacks at rest.
systemd `StandardOutput=append:.../webserver.log` with no `logrotate`. Security audit L4 flagged the same. Tracebacks (which can carry request data) accumulate forever.

### D4 — Background loops die silently, no supervision.
Arch review: `_background_tasks` (10 loops incl. the trade/exit executors) are never inspected for `.done()`/exceptions. A dead scan/exit loop looks identical to a healthy idle one. `Restart=always` only catches full-process death, not a single dead task.

### D5 — No automated backup of state or secrets.
`tmp/*.json` (paper books, holdings-brain, signals), `.fidelity_session_*`, `.env` — all live only on the node. A disk loss = total state loss, re-auth, and lost audit trail.

---

## Infrastructure architecture (target)

```
                    ┌──────────────── Cloudflare ────────────────┐
   users ───TLS───► │  Access (IdP/JWT)  →  Tunnel (outbound)    │
                    └───────────────────────┬────────────────────┘
                                            │ cloudflared (systemd)
                    ┌───────────────────────▼────────────────────┐
                    │  Single node (Linux VM, User=trader)        │
                    │                                             │
                    │  systemd:                                   │
                    │   • agentictrader-web    (uvicorn :8001 lo) │  ← workers=1, flock
                    │   • agentictrader-tunnel (cloudflared)      │
                    │   • agentictrader-paperportfolios           │
                    │   • agentictrader-exitguard                 │
                    │   • logrotate.timer, backup.timer  (ADD)    │
                    │                                             │
                    │  state (persistent disk, 0600):             │
                    │   tmp/*.json · .fidelity_session_* · .env   │
                    │   ml_models/latest/*.joblib                 │
                    │                                             │
                    │  Chromium (Playwright, headed/xvfb)         │
                    └─────────────────────────────────────────────┘
                    node_exporter + healthcheck → Prometheus/Grafana or Uptime-Kuma (external)
```

**Why VM + systemd over K8s/containers here:** the broker path needs a persistent, headed Chrome + on-disk session that survives restarts and can't be a fresh ephemeral pod. Containerizing is possible (xvfb + persistent volume + `replicas: 1` + `strategy: Recreate`) but buys nothing for a single-instance stateful app and adds a browser-in-container failure surface. **Recommend: stay on systemd-on-VM.** Reserve K8s for after the state store is externalized.

---

## CI/CD pipeline (corrected)

Replace `ci.yml` with a pipeline that tests the actual system:

```yaml
name: CI
on: { push: { branches: [main] }, pull_request: {} }
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5           # you ship uv.lock — use it
      - run: uv sync --frozen
      - run: uv run ruff check .              # lint (add ruff)
      - run: uv run pytest -q -m "not integration"   # the 1551 real tests
  frontend:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: frontend } }   # NOT root
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
      - run: npm run build                    # tsc -b (strict) + vite build
  # codeql.yml stays (SAST). Add: gitleaks secret scan, pip-audit/npm audit.
```

**Deploy workflow (single-node, zero-fancy, low-downtime):**
1. CI green on `main` → tag.
2. On the node (SSH deploy or a pull-based `deploy.timer`): `git fetch && checkout tag` → `uv sync --frozen` → `cd frontend && npm ci && npm run build` (static, served live — no server restart needed for UI) → `systemctl kickstart` the web unit only for backend/.env changes.
3. **Health gate:** post-restart, poll `/health` for 200 within 30s; if it fails, `git checkout` previous tag + kickstart (scripted rollback). systemd `Restart=always` covers crash-restart.
4. Frontend is pure static → most deploys are zero-downtime (hard-refresh only). Backend restart is ~seconds (flock releases on exit).

---

## Docker (only if you insist on containers)

A *correct* Dockerfile for this app (not the CLI): base `mcr.microsoft.com/playwright/python:v1.4x` (ships Chromium + deps), build `frontend/` in a node stage, copy `web/static/dist`, run `uvicorn ... --workers 1` on `127.0.0.1:8001`, mount a **named volume** for `tmp/` + session files, inject secrets via `--env-file` at *run* (never `COPY .env`). Compose: `deploy: { replicas: 1 }`, `restart: unless-stopped`, healthcheck on `/health`. K8s equivalent: `Deployment replicas:1`, `strategy: Recreate` (never RollingUpdate — two pods = double orders), RWO PVC, `readinessProbe`/`livenessProbe: /health`, secrets via Sealed-Secrets/External-Secrets, NetworkPolicy origin-only. **All of it gated on `replicas: 1` forever until state is externalized.**

---

## Monitoring & logging strategy

- **Health:** `/health` (liveness — keep it minimal/unauth) + `/health/deep` (readiness — **move behind admin/monitoring token**, security M6). Add a `/health/loops` endpoint that reports each background task's `.done()`/last-tick (fixes D4). Livenessprobe/systemd watchdog restarts on failure.
- **Metrics:** `prometheus-fastapi-instrumentator` for request rate/latency/errors; `node_exporter` for CPU/mem/disk/fd-count (watch fd count — the yfinance leak). Grafana or Uptime-Kuma (external host, so it can alert when the node is down).
- **Alerting (money-path first):** page on — web `/health` down > 60s, any background loop dead, a live order rejected by compliance spike, fd count climbing, disk > 85%, cert/tunnel down, `LIVE_TRADING_ENABLED=true` + no successful scan in N min. Route to the existing SMS (Sendblue) + email.
- **Logging:** add `logrotate` (daily, 14-day, compress, `copytruncate`) for `logs/*.log|*.err` (D3). Structured JSON logs + a request-id. Access log already strips query params (good). Ship to a log store (Loki/CloudWatch) so a node loss doesn't lose the audit trail.
- **Audit:** `tmp/admin_audit.jsonl` → ship + back up; chmod 0600 (security M7).

---

## Reliability / downtime reduction

- **Backups (D5):** `backup.timer` → nightly encrypted snapshot of `tmp/*.json`, `.fidelity_session_*`, `.env`, `ml_models/latest/` to off-node storage (restic/rclone → S3/B2). Test restore quarterly.
- **Loop supervision (D4):** wrap each background loop so an unhandled exception restarts the task (not just the process) + emits an alert; report status via `/health/loops`.
- **Graceful shutdown:** `_shutdown` cancels tasks — ensure in-flight orders finish or are journaled before exit; systemd `TimeoutStopSec` tuned.
- **Secrets:** `chmod 600 .env*` (security H3), inject via systemd `EnvironmentFile=` (already not in unit `Environment=` — good), or move to a secret manager; rotate `MANAGER_API_KEY`/`STEP_UP_SECRET`.
- **Config drift:** the paper-portfolios service is defined in BOTH systemd and launchd — pick one per host; don't hand-sync.

---

## Production deployment checklist

**Security (from the 2026-07-07 audit — do these first):**
- [ ] `CF_ACCESS_REQUIRED=true`; localhost bypass gated on peer IP not Host header (C1)
- [ ] `chmod 600 .env .env.bak*`; prune stale backups (H3)
- [ ] `GET /settings` → `require_admin` + `{set:bool}`; SENDBLUE secrets → SENSITIVE_KEYS (H1/H2)
- [ ] remove/harden `X-Manager-Key` (H4); constrain `forwarded_allow_ips` to cloudflared/loopback (H5)
- [ ] step-up gate on `/copytrade/sync` auto-execute (H6)

**CI/CD:**
- [ ] Fix `ci.yml` to run pytest + `frontend/` build (D1); add ruff, gitleaks, pip-audit
- [ ] Rewrite or delete the wrong-app `Dockerfile`/`docker-compose` (D2)
- [ ] Scripted health-gated deploy + rollback

**Runtime:**
- [ ] `LIVE_TRADING_HARD_BLOCKED` / `LIVE_TRADING_ENABLED` set intentionally; verify single-instance flock active
- [ ] `logrotate` for `logs/*` (D3); metrics + `/health/loops` + external uptime alerting (D4)
- [ ] nightly encrypted off-node backup of state + secrets + models (D5); restore tested
- [ ] Playwright Chromium installed on node; Fidelity session seeded; keepalive loop verified
- [ ] node_exporter watching fd count (yfinance leak), disk, mem

**Never:**
- [ ] ❌ `--workers N`, ❌ K8s `replicas>1`, ❌ RollingUpdate, ❌ autoscaling — until state is externalized to a shared store.

---

## ✅ IMPLEMENTATION STATUS (2026-07-07 — /goal execution)

- **D1 (false-green CI) — FIXED.** `.github/workflows/ci.yml` rewritten: a `backend` job (`pip install -e .` + `pytest -m "not integration"` — the real 1563-test suite), a `frontend` job (`working-directory: frontend`, `npm ci && npm run build` strict tsc), and a `secrets-scan` gitleaks job. No longer runs the unrelated root `ai-orchestrator`.
- **D3 (log rotation) — ADDED.** `deploy/logrotate/agentictrader` (daily, 14-day, compress, copytruncate, `create 0640 trader`).
- **D4 (silent loop death) — FIXED.** `web/app.py` `_spawn_supervised_loop` wraps all 10 background loops with crash-restart + CRITICAL logging; new admin-gated `GET /health/loops` probe reports per-loop liveness. Restart behavior unit-verified.
- **D5 (no backups) — ADDED.** `scripts/backup_state.sh` (GPG-AES256 encrypted tar of tmp/ + .env + sessions + model, rclone off-node push, retention prune) + `deploy/systemd/agentictrader-backup.{service,timer}` (nightly 02:30, Persistent).
- **Repo hygiene — FIXED.** `.gitignore` now covers `backtest_charts_*/`, `paper_accounts/`, `Fidelity API/`, `*.epub`, `old_results/`, and the large reference `.txt`/`.md` dumps. Verified via `git check-ignore`.
- **D2 (wrong-app Dockerfile) — documented, not rewritten.** Rewriting the container to a Playwright base + uvicorn is specified above; left as a deliberate choice since the recommendation is **stay on systemd-on-VM** (the container buys nothing for a single-instance stateful broker app). Not deleted to avoid removing something the user may repurpose.
