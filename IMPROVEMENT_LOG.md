# TradingAgents Frontend — Improvement Log

**Date:** 2026-05-27  
**Version:** 1.0  
**Scope:** React 19 + Vite 8 frontend (`frontend/src/`). Identified via full audit against static HTML reference and Flask/FastAPI backend endpoint catalog (~80+ endpoints, 18 route files). Prioritized P0 (broken/missing parity) → P1 (high value, unused endpoints) → P2 (polish/UX).

---

## 1. Dashboard

**Current state:** 4 chart symbols hardcoded (`AAPL`, `MSFT`, `GOOGL`, `NVDA`). No news panel. No quote detail. No portfolio summary. No regime indicator.

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Watchlist stored in user prefs; symbols editable from dashboard | `GET/POST /settings` |
| P1 | News panel below charts; top headlines per symbol | `GET /market/news?symbol=X` |
| P1 | Click chart → quote detail drawer (fundamentals, 52-week range, analyst target) | `GET /market/quote-detail?symbol=X` |
| P1 | Portfolio equity curve widget: daily P&L sparkline | `GET /paper/summary` or `/fidelity/summary` |
| P1 | Market regime badge (Trending / Ranging / Volatile) | `GET /market/regime` |
| P2 | Auto-refresh toggle with configurable interval (30s / 1m / 5m) | — |
| P2 | Price alerts toast: if a watched symbol crosses a threshold, flash HIL-style amber toast | WebSocket `/ws` |

---

## 2. Admin

**Current state:** 11 real tabs implemented. 5 tabs are JSON-only stubs: Analytics, Approvals, Security, History (admin), Paper Runner.

### 2a. Analytics Tab (stub → real)

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Wire ML summary stats: total signals, win rate, last model accuracy | `GET /ml/summary` or `/admin/ml` |
| P1 | Paper trading P&L aggregate across all runs | `GET /paper/summary` |
| P1 | Per-user signal acceptance rate chart | `/admin/analytics` if present, else derive from `/hil/history` |

### 2b. Approvals Tab (stub → real)

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Show pending HIL trade approvals with Approve/Reject buttons | `GET /hil/pending`, `POST /hil/approve`, `POST /hil/reject` |
| P0 | Auto-refresh every 30s or subscribe via WebSocket | `GET /hil/pending` polling |
| P1 | Approval history log (last 50) | `GET /hil/history` |

### 2c. Security Tab (stub → real)

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | List users with 2FA status (TOTP enabled, passkey count) | `GET /admin/users` — fields: `totp_enabled`, `passkeys` |
| P1 | Per-user login event audit log (last login timestamp) | `GET /admin/users` — field: `last_login_notice_at` |
| P1 | Force-disable 2FA for a user (admin recovery flow) | `POST /admin/users/{email}/reset-2fa` if endpoint exists |
| P2 | Session token listing / revocation | `GET /admin/sessions` if endpoint exists |

### 2d. History Tab — Admin (stub → real)

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Aggregate history across all tickers/dates | `GET /history` (admin scope) |
| P1 | Date filter + CSV export button | Same endpoint with query params |

### 2e. Paper Runner Tab (stub → real)

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Start/Stop paper run with config (capital, tickers) | `POST /paper/run`, `POST /paper/stop` |
| P0 | Live status display (running / idle / error) | `GET /paper/status` |
| P1 | Log streaming for active run | `GET /paper/logs` or WebSocket |

---

## 3. History

**Current state:** Shows trade history list. No drill-down. No export. No date filter. No pagination.

| Priority | Improvement | Backend hook |
|---|---|---|
| P0 | Date range filter (from / to) controls | `GET /history?from=X&to=Y` |
| P1 | Click row → detail drawer: full agent report, reasoning, signal breakdown | `GET /history/{ticker}/{date}` (exists, unused) |
| P1 | Export button → CSV download | Client-side serialize, or `GET /history?format=csv` |
| P1 | P&L summary row: total trades, win rate, net gain shown above table | Derive from existing data |
| P2 | Paginate or virtualize rows (TanStack Virtual) for large datasets | — |
| P2 | Filter by outcome (win / loss / flat) | Client-side filter |

---

## 4. Paper Trading

**Current state:** Shows current paper positions and recent signals. Missing history, manual overrides, and regime display.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | Historical candidates view: past signals that were considered but not executed | `GET /paper/candidates-history` (exists, unused) |
| P1 | Manual force-close button per position | `POST /paper/close/{ticker}` if endpoint exists |
| P1 | Regime indicator on run result card | `GET /market/regime` |
| P1 | Configurable auto-refresh interval (currently hardcoded) | — |
| P2 | Paper run history: list past runs with start/end time, final P&L | `GET /paper/runs` if endpoint exists |

---

## 5. ML

**Current state:** Training controls, model status cards, evaluation metrics. Missing feature importance, model comparison, batch retrain.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | Feature importance viewer per model (bar chart of top features) | `GET /ml/features` or per-model detail endpoint |
| P1 | "Retrain all models" batch button with progress indicator | `POST /ml/train` for each model type |
| P1 | Model comparison table: current vs previous accuracy, precision, recall | Model metadata from `/ml/models` or similar |
| P1 | Last-trained timestamp displayed prominently on each model card | Field in model status response |
| P2 | Training log stream (live output during training) | WebSocket or SSE |
| P2 | Prediction confidence distribution histogram | `/ml/predict` response data |

---

## 6. Analyze (Signal Scanner)

**Current state:** Single-ticker analysis with result display. No bulk scan, no export.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | Bulk scan: run analysis against full watchlist in sequence, show table of results | `POST /analyze` per ticker in loop |
| P1 | Export scan results to CSV | Client-side from scan table |
| P2 | Cloudflare AI quick-test button (surfaces the `/cloudflare-ai/test` endpoint) | `POST /cloudflare-ai/test` (currently unused in React) |
| P2 | Signal strength bar / color coding in results table | Derive from existing `signal` response field |
| P2 | Historical signal accuracy for the analyzed ticker (hit rate on past signals) | Cross-reference `/history` data |

---

## 7. Broker / Fidelity

**Current state:** Shows positions table and account summary. Connection test is in Settings page only.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | Connection status badge on Broker page itself (not just Settings) | `GET /fidelity/status` |
| P1 | Live P&L column on positions table (unrealized gain/loss vs cost basis) | `GET /fidelity/positions` — compute client-side |
| P1 | Positions last-refreshed timestamp + manual refresh button | same endpoint |
| P1 | Order history tab: recent executed orders | `GET /fidelity/orders` if endpoint exists |
| P2 | Account allocation pie chart (position sizing visualization) | Derive from positions data |

---

## 8. HIL (Human-In-the-Loop)

**Current state:** Functionally complete after recent fixes. Minor UX gaps remain.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | Pending approvals list auto-refreshes (poll every 30s or WebSocket) | `GET /hil/pending` |
| P1 | SMS test-send button available from HIL page (currently Settings only) | `POST /auth/me/test-sms` |
| P2 | Approval history log visible from HIL page (last 20) | `GET /hil/history` |
| P2 | Per-approval: show full signal reasoning that triggered it | Detail from pending approval record |

---

## 9. Settings

**Current state:** API keys, Compliance, Data Paths, Fidelity connection test. Mostly complete after recent additions.

| Priority | Improvement | Backend hook |
|---|---|---|
| P1 | "Test key" button for Zhipu API and FMP API (ping and confirm valid) | `GET /cloudflare-ai/test`, `GET /market/fmp-test` if they exist |
| P1 | `CF_ACCESS_BOOTSTRAP_ADMIN` env explanation in Admin Keys section | Docs only (static UI) |
| P2 | Show current Cloudflare tunnel status (connected / disconnected) | `GET /admin/cloudflare` if endpoint exists |

---

## 10. Cross-Cutting Improvements

These apply to multiple pages:

| Priority | Improvement | Where |
|---|---|---|
| P0 | Empty states: replace bare `[]` renders with illustrated "no data yet" states | History, Paper, Admin stubs |
| P0 | Loading skeletons: replace spinners with content-shaped skeleton rows | All data tables |
| P1 | WebSocket subscription: live signal/approval events pushed to relevant pages without polling | `ws.ts` hook (`useWebSocket`) — expand event types |
| P1 | Toast for background events: new HIL approval request pops amber toast regardless of current page | `GlobalOverlays.tsx` + `HilToast.tsx` |
| P1 | Error boundary per-page: each route wrapped so one page crash doesn't kill entire app | `ErrorBoundary.tsx` — add to each page route in `App.tsx` |
| P2 | Keyboard shortcuts: `r` to refresh current page, `/` to focus search, `esc` to close drawer/modal | Global `useEffect` keydown |
| P2 | Page-level breadcrumb: show current section in header (Dashboard > Analysis > AAPL) | `AppShell.tsx` header slot |

---

## Implementation Order (Recommended)

```
Phase 1 (P0 — parity/broken stubs)
  Admin: Approvals tab wired to /hil/pending
  Admin: Paper Runner tab wired to /paper/run + /paper/status
  History: date range filter + detail drawer (/history/{ticker}/{date})
  Dashboard: watchlist editable (stored in settings)
  Empty states + loading skeletons across all pages

Phase 2 (P1 — high-value unused endpoints)
  Dashboard: news panel, quote detail drawer, regime badge
  Admin: Security tab (user 2FA status)
  History: CSV export, P&L summary row
  Paper: candidates-history view
  ML: feature importance, model comparison table
  Analyze: bulk watchlist scan

Phase 3 (P2 — polish)
  Auto-refresh intervals configurable
  Keyboard shortcuts
  Breadcrumb in header
  Per-page error boundaries
  Settings: key test buttons
```

---

*Log generated 2026-05-27. Update each entry as improvements land.*
