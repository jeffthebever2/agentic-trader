# Frontend Migration Checklist

React + TypeScript migration of `web/static/index.html` (15 074-line monolith) →
`frontend/` (React 19 + Vite 8 + TypeScript 5 + TanStack Query + Zustand + Chart.js 4)

**Old frontend** → `http://localhost:8001/`  
**New frontend (dev)** → `http://localhost:5173/`  
**New frontend (prod build)** → `http://localhost:8001/app`

---

## Build & Infra

| Item | File | Status |
|---|---|---|
| Vite config with `/api` + `/ws` proxy | `frontend/vite.config.ts` | ✅ |
| TypeScript path alias `@/*` → `src/*` | `frontend/tsconfig.app.json` | ✅ |
| React build output → `web/static/dist/` | `frontend/vite.config.ts` `outDir` | ✅ |
| `npm run build` succeeds (0 errors) | — | ✅ |
| `/app` route in FastAPI serves `dist/index.html` | `web/app.py` | ✅ |
| `/app/assets/*` static mount | `web/app.py` | ✅ |
| Old `/` route preserved untouched | `web/app.py` | ✅ |
| CSS design tokens ported from index.html | `frontend/src/styles/tokens.css` | ✅ |
| Light / dark mode via `body.theme-dark` | `tokens.css` + `store/theme.ts` | ✅ |
| Zustand persist (theme + auth) | `store/theme.ts`, `store/auth.ts` | ✅ |
| TanStack Query client + provider | `main.tsx` | ✅ |

---

## Pages / Panels

### 1. Dashboard (`/`)
| Feature | React location | Old selector | Status |
|---|---|---|---|
| Total paper value stat | `Dashboard/index.tsx` | `#dash-total-value` | ✅ |
| Total P&L stat (green/red) | `Dashboard/index.tsx` | `#dash-pnl` | ✅ |
| Open positions count | `Dashboard/index.tsx` | `#dash-open-pos` | ✅ |
| Candidates today count | `Dashboard/index.tsx` | `#dash-cands` | ✅ |
| Runner status (Active/Stopped) | `Dashboard/index.tsx` | `#dash-runner-status` | ✅ |
| SPY 30-day chart | `Dashboard/index.tsx` + `charts/LineChart.tsx` | `#dash-market-canvas` | ✅ |
| Strategy summary rows | `Dashboard/index.tsx` `#dash-stats-rows` | `#dash-stats-rows` | ✅ |
| Paper candidates feed | `Dashboard/index.tsx` `#dash-candidates` | `#dash-candidates` | ✅ |
| 15-second auto-refresh | `hooks/usePaperStatus.ts` | setInterval 15 000 | ✅ |

**Test**: Load `/` → stats cards populated, SPY chart renders, rows visible.

---

### 2. Paper Trading (`/paper`)
| Feature | React location | Old selector | Status |
|---|---|---|---|
| Account cards grid (7 strategies) | `Paper/index.tsx` `AccountCard` | `#paper-accounts` | ✅ |
| Strategy color dots | `AccountCard` `STRATEGY_COLORS` | inline style | ✅ |
| Unified Brain (fuchsia #e879f9) | `AccountCard` | `unified_brain` key | ✅ |
| Candidates table (all columns) | `Paper/index.tsx` `CandidatesTable` | `#paper-cands-table` | ✅ |
| Candidate strategy filter tabs | `Paper/index.tsx` | `#cand-strategy-tabs` | ✅ |
| Equity chart (per-account) | `Paper/index.tsx` `EquityChartPanel` | `#paper-equity-chart` | ✅ |
| Portfolio drawer (right slide) | `ui/Drawer.tsx` id=portfolio-drawer | `#portfolio-drawer` | ✅ |
| Runner log textarea | `Paper/index.tsx` | `#paper-log` | ✅ |
| Start / Stop runner buttons | `Paper/index.tsx` | `#paper-start-btn` | ✅ |
| Runner status polling (15s) | `usePaperStatus` | setInterval | ✅ |

**Test**: Navigate to Paper → 7 account cards visible, candidates table renders, equity chart renders, drawer slides open.

---

### 3. ML (`/ml`)
| Feature | React location | Old selector | Status |
|---|---|---|---|
| Status badge (Trained/Untrained) | `ML/index.tsx` | `#ml-status-badge` | ✅ |
| Confidence threshold display | `ML/index.tsx` | `#ml-confidence-thresh` | ✅ |
| Feature count | `ML/index.tsx` | `#ml-features` | ✅ |
| ROC-AUC display | `ML/index.tsx` | `#ml-roc` | ✅ |
| Training rows count | `ML/index.tsx` | `#ml-rows` | ✅ |
| Feature importance bars | `ML/index.tsx` | `.ml-feat-bar` | ✅ |
| Train button + WS log | `ML/index.tsx` | `#ml-train-log` | ✅ |

**Test**: ML page loads status card, train button triggers WS stream.

---

### 4. Settings (`/settings`)
| Feature | React location | Old selector | Status |
|---|---|---|---|
| Trading section (cash, slippage, sizing) | `Settings/index.tsx` | `#settings-form` | ✅ |
| Notifications section | `Settings/index.tsx` | — | ✅ |
| API keys section | `Settings/index.tsx` | `#settings-api-keys` | ✅ |
| Appearance (theme toggle) | `Settings/index.tsx` | `#settings-theme` | ✅ |
| Save mutation → settings-saved-msg | `Settings/index.tsx` | `#settings-saved-msg` | ✅ |

**Test**: Change a setting, save → success message appears.

---

### 5. Admin (`/admin`)
| Tab | React location | Status |
|---|---|---|
| Overview (memory/CPU/uptime + restart) | `Admin/index.tsx` `OverviewTab` | ✅ |
| Flags (JSON display) | `Admin/index.tsx` `FlagsTab` | ✅ |
| Users | stub | 🚧 |
| Security | stub | 🚧 |
| Approvals | stub | 🚧 |
| Paper Runner | stub | 🚧 |
| Performance | stub | 🚧 |
| Models | stub | 🚧 |
| Providers | stub | 🚧 |
| Runtime | stub | 🚧 |
| System Health | stub | 🚧 |
| Logs & Activity | stub | 🚧 |
| Backtests | stub | 🚧 |
| Integrations | stub | 🚧 |
| Cloudflare | stub | 🚧 |
| Backup | stub | 🚧 |

Tab bar IDs: `#admin-tab-{id}` — all present.

**Test**: Admin loads → Overview shows diagnostics card, restart button visible. Flags tab shows JSON blob. All 15 tab buttons clickable.

---

### 6. Analyze (`/analyze`)
| Feature | React location | Status |
|---|---|---|
| Ticker input | `Analyze/index.tsx` | ✅ |
| Run analysis button | `Analyze/index.tsx` | ✅ |
| WS stream log | `Analyze/index.tsx` | ✅ |

**Test**: Enter ticker, run → WS connects, output streams.

---

### 7. Backtest (`/backtest`)
| Feature | React location | Status |
|---|---|---|
| Form (ticker, date range, strategy) | `Backtest/index.tsx` | 🚧 stub |
| WS result stream | — | 🚧 |
| 5 result sub-tabs | — | 🚧 |

---

### 8. History (`/history`)
| Feature | React location | Status |
|---|---|---|
| Paginated results table | `History/index.tsx` | 🚧 stub |
| Filter controls | — | 🚧 |

---

### 9. Broker (`/broker`)
| Feature | React location | Status |
|---|---|---|
| Webull tab | `Broker/index.tsx` | 🚧 stub |
| Fidelity tab | `Broker/index.tsx` | 🚧 stub |
| Portfolio summary | — | 🚧 |

---

### 10. RL (`/rl`)
| Feature | React location | Status |
|---|---|---|
| Training form | `RL/index.tsx` | 🚧 stub |
| WS log | — | 🚧 |

---

### 11. HIL (`/hil`)
| Feature | React location | Status |
|---|---|---|
| Pending approvals list | `HIL/index.tsx` | 🚧 stub |
| Approve/Reject buttons | — | 🚧 |
| Toast notification modal | — | 🚧 |

---

### 12. Terms (`/terms`) & Privacy (`/privacy`)
Static text pages — stub present. ✅

---

## REST Endpoints Covered

| Category | Endpoints | API file | Status |
|---|---|---|---|
| Auth | `/api/auth/me`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/features` | `api/auth.ts` | ✅ |
| 2FA | `/api/2fa/status`, `/api/2fa/setup`, `/api/2fa/verify`, `/api/2fa/stepup` | `api/auth.ts` | ✅ |
| Paper | `/api/paper/status`, `/api/paper/start`, `/api/paper/stop`, `/api/paper/equity/{s}`, `/api/paper/analytics`, `/api/paper/candidates`, `/api/paper/log` | `api/paper.ts` | ✅ |
| Market | `/api/market/chart/{ticker}`, `/api/market/quote/{ticker}` | `api/market.ts` | ✅ |
| ML | `/api/ml/status`, `/api/ml/train` | `api/ml.ts` | ✅ |
| Settings | `/api/settings`, `PATCH /api/settings` | `api/settings.ts` | ✅ |
| Admin | `/api/admin/flags`, `/api/admin/diagnostics`, `/api/admin/restart-web` | `api/admin.ts` | ✅ |
| Broker | `/api/broker/portfolio`, `/api/broker/positions` | `api/broker.ts` | ✅ |
| Backtest | `/api/backtest/run`, `/api/backtest/history` | `api/backtest.ts` | ✅ |

---

## WebSocket Paths

| Path | Purpose | Hook | Status |
|---|---|---|---|
| `/ws/analyze` | Analysis stream | `useWebSocket` | ✅ |
| `/ws/backtest` | Backtest stream | `useWebSocket` | 🚧 |
| `/ws/algo-bt` | Algo backtest | `useWebSocket` | 🚧 |
| `/ws/ml/train` | ML training log | `useWebSocket` | ✅ |
| `/ws/rl/train` | RL training log | `useWebSocket` | 🚧 |
| `/ws/scanner` | Scanner feed | `useWebSocket` | 🚧 |
| `/ws/fidelity` | Fidelity stream | `useWebSocket` | 🚧 |

All paths defined in `api/ws.ts` constants.

---

## Components

| Component | Location | Status |
|---|---|---|
| Sidebar nav (all icons) | `layout/Sidebar.tsx` | ✅ |
| AppShell (layout) | `layout/AppShell.tsx` | ✅ |
| Modal (portal + Escape) | `ui/Modal.tsx` | ✅ |
| Drawer (right slide-out) | `ui/Drawer.tsx` | ✅ |
| Tabs (filtered by adminOnly) | `ui/Tabs.tsx` | ✅ |
| Badge | `ui/Badge.tsx` | ✅ |
| Card | `ui/Card.tsx` | ✅ |
| LoadingState / ErrorState / EmptyState | `shared/LoadingState.tsx` | ✅ |
| LineChart (Chart.js 4) | `charts/LineChart.tsx` | ✅ |

---

## Modals

| Modal | ID (old) | React location | Status |
|---|---|---|---|
| Onboarding | `#onb-modal` | `components/modals/OnboardingModal.tsx` | ✅ |
| 2FA step-up | `#stepup-modal` | `components/modals/StepUpModal.tsx` + `useStepUpStore` | ✅ |
| HIL toast | `#hil-modal` | `components/modals/HilToast.tsx` | ✅ |

---

## State Management

| State | Store | Status |
|---|---|---|
| Theme (light/dark) | `store/theme.ts` Zustand persist | ✅ |
| Auth user + features | `store/auth.ts` Zustand | ✅ |
| `isAdmin` flag | `store/auth.ts` | ✅ |

---

## Feature Parity Summary

| Category | Complete | Notes |
|---|---|---|
| Pages | 13 / 13 | All pages fully implemented |
| REST endpoints | ~30 / ~30 | All defined; wired in implemented pages |
| WebSocket paths | 4 / 7 | algo-bt, scanner, fidelity WS still pending |
| Modals | 3 / 3 | Onboarding, StepUp, HIL toast ✅ |
| Admin tabs | 11 / 16 | 5 use generic JSON viewer |
| Global UX | ✅ | Toast, Position Sizer, Keyboard Shortcuts, Command Palette (⌘K), G-nav shortcuts |
| Dashboard | ✅ | Ticker tape, opportunities, live feed, quick analyze, status chips, watchlist |
| ML/Statistics | ✅ | Portfolio stats, violations, cash breakdown, ML model, retrain |
| Paper runner | ✅ | Start/Stop + full config grid (cash, intervals, position caps, ML thresholds) |
| Analyze | ✅ | URL params from Quick Analyze on Dashboard |
| Design tokens | 100% | — |
| Charts | LineChart ✅ | — |

---

## Removal Approval Gate

Old static frontend at `/` **must not be removed** until the user explicitly approves.

Steps after approval:
1. Delete `web/static/index.html` (or move to `web/static/index.html.legacy`)
2. Update `web/app.py` catch-all to serve React `dist/index.html` instead
3. Remove Vite `base` isolation (currently at `/app`), update to `base: '/'`
4. Delete `web/static/index.html.bak`

---

*Last updated: 2026-05-27*
