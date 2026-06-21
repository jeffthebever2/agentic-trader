# Agentic Trader — Web API Reference

Router-level reference for the FastAPI backend (`web/`). This is a **router map**, not an
exhaustive per-endpoint catalog — it lists each router's prefix, purpose, and key endpoints.
For exact request/response shapes, read the router source in `web/api/*.py`.

## Conventions

| | |
|---|---|
| **Base URL** | `http://127.0.0.1:8001` (uvicorn, localhost-only; public access via Cloudflare tunnel) |
| **SPA** | Served under base path `/app` (React `BrowserRouter basename="/app"`) |
| **API prefix** | Every router in `web/api/` is mounted with `prefix="/api"`, so all paths below are under `/api/...` — **except `portfolios.py`, which self-declares `/api/portfolios/...`** |
| **Auth** | Cloudflare Access JWT (production) or session cookie (dev). User identity resolved per-request. There is **no** `POST /api/auth/login` / `/logout` — auth is handled by the Cloudflare edge, not the app. |
| **WebSockets** | Per-feature, not multiplexed. No single `/ws`. See [WebSockets](#websockets). |

`web/api/fidelity_trade.py` is **not a mounted router** — it is just a Pydantic request model used
by the Fidelity endpoints.

### Live-order compliance kill-chain

Order-placing endpoints (`POST /api/fidelity/trade`, `/api/fidelity/thematic-trade`,
`/api/fidelity/thematic-exit`, and the Holdings-Brain approve route) route through
`tradingagents/compliance.py::validate_live_order` **and** require per-trade step-up 2FA. Gates:

- **Limit orders only** — no market / short / margin / options.
- **`MAX_POSITION_PCT_OF_ACCOUNT` = 10%** of account per position.
- **$50k per order** cap.
- **Trusted, fresh execution quote** required (`PreTradeGate`, `require_trusted_source=True`;
  trusted sources = finnhub / twelve_data / fmp). yfinance is untrusted for execution.
- Two master kill-switches: `LIVE_TRADING_HARD_BLOCKED` (source constant) and
  `LIVE_TRADING_ENABLED` (`.env`).

**Never weaken these gates — real money flows through here.**

## Health (not under the `/api`-prefix rule)

Both bare and `/api`-prefixed forms exist:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/api/health` | Liveness |
| GET | `/health/deep`, `/api/health/deep` | Deep health (deps, market-hours aware) |

## WebSockets

| Path | Purpose |
|---|---|
| `/api/ws/analyze` | Streaming LLM multi-agent analysis |
| `/api/ws/backtest` | Strategy backtest progress |
| `/api/ws/algo-backtest` | Algo backtest progress |
| `/api/ws/scanner/scan` | Live scan progress |
| `/api/ws/ml-train` | ML training progress |
| `/api/ws/rl-train` | RL training progress |
| `/api/ws/fidelity-auth` | Fidelity Playwright login (TOTP pause handshake) |

---

## Routers

All paths below are relative to `/api` unless shown otherwise.

### auth_routes.py — `/auth/*`
Current-user context and user administration (login itself is Cloudflare's job).

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/me` | Current user identity |
| GET | `/auth/features` | Feature flags visible to this user |
| GET | `/auth/users` | List users (admin) |
| PUT | `/auth/users/{email}/role` | Change a user's role (admin) |
| DELETE | `/auth/users/{email}` | Remove a user (admin) |

### twofa_routes.py — `/auth/2fa/*`
TOTP / passcode / passkey enrollment and **per-trade step-up** challenges.

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/2fa/status` | Enrollment + method status |
| POST | `/auth/2fa/totp/enroll`, `/totp/activate`, `/totp/disable` | TOTP lifecycle |
| GET | `/auth/2fa/totp/qr` | TOTP enrollment QR |
| POST | `/auth/2fa/passcode/set`, `/passcode/disable` | Trading-passcode lifecycle |
| POST | `/auth/2fa/method` | Set preferred step-up method |
| POST | `/auth/2fa/step-up/totp`, `/step-up/passcode`, `/step-up/email`, `/step-up/email/send` | Step-up via TOTP / passcode / email |
| POST | `/auth/2fa/step-up/passkey/begin`, `/step-up/passkey/complete` | Step-up via passkey |
| POST | `/auth/2fa/passkey/register/begin`, `/passkey/register/complete` | Register a passkey |
| DELETE | `/auth/2fa/passkey/{id}` | Remove a passkey |

### market.py — `/market/*`
Quotes, charts, news, watchlist, and the trusted-quote gateway.

| Method | Path | Purpose |
|---|---|---|
| GET | `/market/quotes` | Batch quotes |
| GET | `/market/quote-detail` | Single-symbol detail (fundamentals, 52w, target) |
| GET | `/market/chart` | OHLCV chart data |
| GET | `/market/sparklines` | Compact sparkline series |
| GET | `/market/sp500-list` | S&P 500 constituents |
| GET | `/market/news-summary` | News summary for a symbol |
| GET | `/market/watchlist` | User watchlist |
| GET | `/market/trade-chart.png` | Rendered TradingView-style trade chart (cached PNG) |
| GET | `/market/gateway-quote` | Trusted-source quote via `quote_gateway` |
| GET | `/market/gateway-health` | Quote-gateway provider health |

### analysis.py — `/analyze`
Multi-agent LLM analysis (WebSocket-driven, not job-poll).

| Method | Path | Purpose |
|---|---|---|
| POST | `/analyze` | Kick off an analysis |
| WS | `/ws/analyze` | Stream the run |

### scanner.py — `/scanner/*`
Breakout/pullback candidate screening.

| Method | Path | Purpose |
|---|---|---|
| POST | `/scanner/screen` | Run a screen |
| GET | `/scanner/ticker-files` | Available ticker universe files |
| GET | `/scanner/tickers` | Tickers in a given file |
| WS | `/ws/scanner/scan` | Stream scan progress |

### backtest.py — `/backtest/*`
Backtest runs and result artifacts.

| Method | Path | Purpose |
|---|---|---|
| GET | `/backtest/results` | List result files |
| GET | `/backtest/results/{filename}` | Fetch one result file |
| POST | `/backtest/screen` | Screen within a backtest |
| WS | `/ws/backtest`, `/ws/algo-backtest` | Stream backtest / algo-backtest |

### ml.py — `/ml/*`
Deployed-model status and training. (Note: `POST /ml/retrain` lives in **system.py**.)

| Method | Path | Purpose |
|---|---|---|
| GET | `/ml/status` | Deployed model stats (ROC, Brier, features, date) |
| GET | `/ml/readiness` | Whether a usable model is loaded |
| GET | `/ml/history` | Past training runs |
| WS | `/ws/ml-train` | Stream a training run |

### rl.py — `/rl/*`
Reinforcement-learning training (`.venv-torch`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/rl/status` | RL training status |
| WS | `/ws/rl-train` | Stream an RL training run |

### paper.py — `/paper/*`
The 15-portfolio paper competition, HIL queue, and SMS/MMS approval bridge.

| Method | Path | Purpose |
|---|---|---|
| GET | `/paper/analytics` | Paper analytics |
| GET | `/paper/equity` | Equity curve |
| GET | `/paper/system-health` | Paper-loop health |
| GET | `/paper/quotes` | Quotes for paper positions |
| GET | `/paper/hil/pending` | Pending HIL approvals |
| POST | `/paper/hil/resolve` | Resolve a pending item (single endpoint — not approve/reject) |
| GET | `/approve` | SMS approve deep-link target |
| — | `/paper/sms/*`, `/paper/mms/test` | SMS/MMS notify + inbound + test |

### portfolio.py — `/portfolio/*`
Single manual portfolio tracker.

| Method | Path | Purpose |
|---|---|---|
| GET | `/portfolio/positions` | Positions with live prices + P&L |

### portfolios.py — `/api/portfolios/*` (self-prefixed)
The 15-portfolio competition leaderboard (note: declares its own `/api/portfolios` prefix).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/portfolios/leaderboard` | Leaderboard |
| GET | `/api/portfolios/leaderboard/groups` | Leaderboard grouped |
| GET | `/api/portfolios/model-health` | Deployed-model health summary |
| GET | `/api/portfolios/groups` | Portfolio groups |
| GET | `/api/portfolios/{name}` | One portfolio's detail |
| GET | `/api/portfolios/{name}/config` | One portfolio's config |

### thematic_auto.py — `/thematic/auto/*`
Social-momentum scanner (scrapes ~15 sources → AI pick → pending signals).

| Method | Path | Purpose |
|---|---|---|
| POST | `/thematic/auto/scan` | Trigger a scan |
| GET | `/thematic/auto/status` | Scan status / last run |
| GET | `/thematic/auto/twitter-status` | Twitter-source availability |

### thematic_portfolio.py — `/thematic/portfolio/*`
Manual-conviction thematic book (position CRUD).

| Method | Path | Purpose |
|---|---|---|
| GET / POST / PUT / DELETE | `/thematic/portfolio/...` | Thematic position create / read / update / delete |

### holdings_brain.py — `/thematic/brain/*`
AI management of **existing real broker holdings** — propose-only HIL; approval routes through the
compliance-gated Fidelity endpoints.

| Method | Path | Purpose |
|---|---|---|
| GET | `/thematic/brain/holdings` | Real holdings the brain sees |
| POST | `/thematic/brain/assess` | Run an assessment cycle |
| GET | `/thematic/brain/proposals` | Queued HIL proposals |
| POST | `/thematic/brain/proposals/{id}/approve` | Approve a proposal (**kill-chain + step-up**) |
| POST | `/thematic/brain/proposals/{id}/skip` | Skip a proposal |
| POST | `/thematic/brain/live-exits/arm` | Arm live-exit guard |

### fidelity.py — `/fidelity/*`
**Live broker execution** via Playwright (drives digital.fidelity.com). LIMIT orders only;
every order passes the compliance kill-chain + step-up 2FA.

| Method | Path | Purpose |
|---|---|---|
| GET | `/fidelity/status` | Session status |
| POST | `/fidelity/logout` | Close session |
| GET | `/fidelity/positions` | Live positions (stale-while-revalidate cache; `?refresh=1` forces) |
| GET | `/fidelity/summary` | Account summary |
| GET | `/fidelity/accounts` | Account list |
| GET | `/fidelity/screenshot` | Current-page screenshot |
| POST | `/fidelity/trade` | Place a LIMIT order (**kill-chain + step-up**) |
| POST | `/fidelity/thematic-trade` | Thematic entry (**kill-chain + step-up**) |
| POST | `/fidelity/thematic-exit` | Thematic exit (**kill-chain + step-up**) |
| GET | `/fidelity/thematic-sync` | Reconcile thematic paper ↔ broker |
| GET | `/fidelity/trade-log` | Order/trade log |
| WS | `/ws/fidelity-auth` | Playwright login handshake |
| — | `/fidelity/debug-html`, `/debug-grid`, `/debug-trade` | Scrape-debug helpers |

### webull_portfolio.py — `/webull/*`
Webull (`fidelity-api`/webull lib) — read primitives + order routes. No thematic-execution bridge yet.

| Method | Path | Purpose |
|---|---|---|
| GET | `/webull/status` | Session status |
| POST | `/webull/request-mfa`, `/login`, `/trade-pin`, `/logout`, `/refresh` | Auth lifecycle |
| GET | `/webull/account`, `/positions`, `/orders` | Account / positions / orders |
| POST | `/webull/orders` | Place an order |
| DELETE | `/webull/orders/{order_id}` | Cancel an order |

### performance.py — `/performance/*`
Deposit-adjusted P&L tracker over real Fidelity holdings (per-user append-only snapshots).

| Method | Path | Purpose |
|---|---|---|
| GET | `/performance/summary` | Hero P&L summary |
| GET | `/performance/history` | Snapshot history |
| GET | `/performance/day/{date}` | One day's snapshot |
| GET | `/performance/positions` | Holdings detail |
| GET | `/performance/validate` | Validation report |
| GET | `/performance/synclog` | Sync-log entries |
| GET | `/performance/export` | Export CSV/JSON |
| POST | `/performance/sync` | Capture a fresh snapshot |
| GET / POST / DELETE | `/performance/cashflows` | Deposit/withdrawal ledger |

### history.py — `/history/*`
Closed-trade history and stats.

| Method | Path | Purpose |
|---|---|---|
| GET | `/history` | Trade history |
| GET | `/history/stats` | Aggregate stats |
| GET | `/history/tickers` | Tickers traded |
| GET | `/history/{ticker}/{date}` | One trade record |

### logs.py — `/logs/*`
Operational log/feed tails.

| Method | Path | Purpose |
|---|---|---|
| GET | `/logs/stats` | Log stats |
| GET | `/logs/memory` | Scan-memory feed |
| GET | `/logs/paper-decisions` | Paper decision log |
| GET | `/logs/trades` | Trade log |
| GET | `/logs/system` | System log |
| GET | `/logs/sources` | Source-scrape log |
| GET | `/logs/daily-audit` | Daily audit feed |

### settings.py — `/settings`
`.env`-backed user/runtime settings.

| Method | Path | Purpose |
|---|---|---|
| GET | `/settings` | Read settings |
| POST | `/settings` | Update settings |

### live_verification.py — `/live/verification`
Live-readiness verification snapshot.

| Method | Path | Purpose |
|---|---|---|
| GET | `/live/verification` | Live-trading verification status |

### cloudflare_ai.py — `/cloudflare-ai/*`
Free-LLM (Cloudflare Workers AI / OpenRouter fallback) status + test.

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/cloudflare-ai/...` | Free-LLM status / test |

### system.py — `/system/*` (+ `/ml/retrain`)
Host/service metrics and the retrain trigger.

| Method | Path | Purpose |
|---|---|---|
| GET | `/system/metrics` | Host metrics |
| GET | `/system/services` | Service status |
| POST | `/ml/retrain` | Trigger the retrain pipeline |

### admin.py — `/admin/*`
Admin: audit, feature flags, runtime control, export.

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/audit` | Audit log |
| GET / POST | `/admin/flags` | Read / set feature flags |
| GET | `/admin/cloudflare` | Cloudflare config view |
| GET | `/admin/runtime/status` | Runtime status |
| GET | `/admin/runtime/diagnostics` | Runtime diagnostics |
| POST | `/admin/runtime/web/restart` | Restart the web service |
| POST | `/admin/runtime/tunnel/start`, `/runtime/tunnel/stop` | Tunnel control |
| GET | `/admin/export` | Data export |
