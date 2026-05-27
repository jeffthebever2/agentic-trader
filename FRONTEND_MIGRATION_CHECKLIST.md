# Frontend Migration Checklist

React + TypeScript migration of `web/static/index.html` (15 074-line monolith) →
`frontend/` (React 19 + Vite 8 + TypeScript 5 + TanStack Query + Zustand + Chart.js 4)

**Old frontend** → `http://localhost:8001/`  
**New frontend (dev)** → `http://localhost:5173/app`  
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

## Layout & Chrome

| Item | File | Status |
|---|---|---|
| Header bar (page title + kicker, command bar, market chip, Chart btn, datetime) | `layout/AppHeader.tsx` | ✅ |
| Sidebar logo: icon + "Agentic Trader" + "Personal Trading Suite" | `layout/Sidebar.tsx` | ✅ |
| Sidebar nav (all 11 items, correct icons, adminOnly gates) | `layout/Sidebar.tsx` | ✅ |
| Sidebar footer: connection status card + sonar animation | `layout/Sidebar.tsx` | ✅ |
| Sidebar footer: user info (name/email/role badge) | `layout/Sidebar.tsx` | ✅ |
| Sidebar footer: legal links (Support/Terms/Privacy) + theme toggle | `layout/Sidebar.tsx` | ✅ |
| Mobile sidebar slide-in (<980px) + backdrop | `AppShell.tsx` + `tokens.css` | ✅ |
| Nav curtain flash on route change | `AppShell.tsx` + `tokens.css` | ✅ |
| Nav progress sweep on route change | `AppShell.tsx` + `tokens.css` | ✅ |
| Film grain overlay | `tokens.css` | ✅ |
| CRT scanline (dark mode) | `tokens.css` | ✅ |
| Nav item stagger entrance animation | `tokens.css` | ✅ |
| Value flash animations (up/down) | `tokens.css` | ✅ |

---

## Pages / Panels

### 1. Dashboard (`/`)
| Feature | React location | Status |
|---|---|---|
| Edge-to-edge cockpit layout (no padding, flush dividers) | `Dashboard/index.tsx` | ✅ |
| Ticker tape (full-width, edge fades, scrolling) | `Dashboard/index.tsx` | ✅ |
| Stat row (4 cells, dividers, monospace values) | `Dashboard/index.tsx` | ✅ |
| Market overview chart (SPY/QQQ/NVDA/AAPL tabs) | `Dashboard/index.tsx` | ✅ |
| Today's Opportunities (Gainers/Losers/Movers) | `Dashboard/index.tsx` | ✅ |
| Live feed (Candidates / Recent Trades tabs) | `Dashboard/index.tsx` | ✅ |
| Quick Analyze (→ /analyze with params) | `Dashboard/index.tsx` | ✅ |
| System Status chips (Fidelity/Market Data/ML/Market) | `Dashboard/index.tsx` | ✅ |
| Portfolio Stats (win rate, P&L, per-strategy rows) | `Dashboard/index.tsx` | ✅ |
| Portfolio Exposure (sector bars) | `Dashboard/index.tsx` | ✅ |
| Paper Candidates panel | `Dashboard/index.tsx` | ✅ |
| Watchlist panel | `Dashboard/index.tsx` | ✅ |
| [ ] section label brackets (industrial style) | `tokens.css` | ✅ |
| 15-second auto-refresh | `hooks/usePaperStatus.ts` | ✅ |

---

### 2. Paper Trading (`/paper`)
| Feature | React location | Status |
|---|---|---|
| Account cards grid (7 strategies) | `Paper/index.tsx` | ✅ |
| Strategy color dots | `Paper/index.tsx` | ✅ |
| Unified Brain (fuchsia #e879f9) | `Paper/index.tsx` | ✅ |
| Candidates table (all columns) | `Paper/index.tsx` | ✅ |
| Candidate strategy filter tabs | `Paper/index.tsx` | ✅ |
| Equity chart (per-account) | `Paper/index.tsx` | ✅ |
| Portfolio drawer (right slide) | `ui/Drawer.tsx` | ✅ |
| Runner log textarea | `Paper/index.tsx` | ✅ |
| Start / Stop runner buttons | `Paper/index.tsx` | ✅ |
| Full config grid (cash, intervals, position caps, ML thresholds) | `Paper/index.tsx` | ✅ |
| Runner status polling (15s) | `usePaperStatus` | ✅ |

---

### 3. ML / Statistics (`/ml`)
| Feature | React location | Status |
|---|---|---|
| Status badge, confidence threshold, feature count, ROC-AUC | `ML/index.tsx` | ✅ |
| Feature importance bars | `ML/index.tsx` | ✅ |
| Train button + WS log | `ML/index.tsx` | ✅ |
| Portfolio stats, violations, cash breakdown | `ML/index.tsx` | ✅ |

---

### 4. Settings (`/settings`)
| Feature | React location | Status |
|---|---|---|
| Trading section (cash, slippage, sizing) | `Settings/index.tsx` | ✅ |
| Notifications section | `Settings/index.tsx` | ✅ |
| API keys section | `Settings/index.tsx` | ✅ |
| Appearance (theme toggle) | `Settings/index.tsx` | ✅ |
| Save mutation → success message | `Settings/index.tsx` | ✅ |

---

### 5. Admin (`/admin`)
| Tab | React location | Status |
|---|---|---|
| Overview (memory/CPU/uptime + restart) | `Admin/index.tsx` | ✅ |
| Users | `Admin/index.tsx` | ✅ |
| Security | `Admin/index.tsx` | ✅ |
| Approvals | `Admin/index.tsx` | ✅ |
| Paper Runner | `Admin/index.tsx` | ✅ |
| Performance | `Admin/index.tsx` | ✅ |
| Models | `Admin/index.tsx` | ✅ |
| Providers | `Admin/index.tsx` | ✅ |
| Runtime | `Admin/index.tsx` | ✅ |
| System Health | `Admin/index.tsx` | ✅ |
| Logs & Activity | `Admin/index.tsx` | ✅ |
| Backtests | `Admin/index.tsx` | ✅ |
| Integrations | `Admin/index.tsx` | ✅ |
| Cloudflare | `Admin/index.tsx` | ✅ |
| Flags (editable JSON) | `Admin/index.tsx` | ✅ |
| Backup | `Admin/index.tsx` | ✅ |

---

### 6. Analyze (`/analyze`)
| Feature | React location | Status |
|---|---|---|
| Left config panel (mode, ticker, date, analysts, provider, advanced) | `Analyze/index.tsx` | ✅ |
| Run analysis button + WS stream log | `Analyze/index.tsx` | ✅ |
| Report tabs (per-analyst output) | `Analyze/index.tsx` | ✅ |
| TradingView chart modal | `Analyze/index.tsx` | ✅ |
| URL params from Quick Analyze | `Analyze/index.tsx` | ✅ |

---

### 7. Backtest & Screener (`/backtest`)
| Feature | React location | Status |
|---|---|---|
| Live Scanner tab | `Backtest/index.tsx` | ✅ |
| Technical Screener tab | `Backtest/index.tsx` | ✅ |
| Algorithm Backtest tab | `Backtest/index.tsx` | ✅ |
| LLM Backtest tab | `Backtest/index.tsx` | ✅ |
| Past Results tab | `Backtest/index.tsx` | ✅ |

---

### 8. History (`/history`)
| Feature | React location | Status |
|---|---|---|
| Paginated results table | `History/index.tsx` | ✅ |
| Filter controls (ticker + decision) | `History/index.tsx` | ✅ |
| Expandable rows | `History/index.tsx` | ✅ |

---

### 9. Broker (`/broker`)
| Feature | React location | Status |
|---|---|---|
| Webull tab (login, accounts, positions, orders, place order) | `Broker/index.tsx` | ✅ |
| Fidelity tab (OAuth, accounts, positions, orders) | `Broker/index.tsx` | ✅ |

---

### 10. RL (`/rl`)
| Feature | React location | Status |
|---|---|---|
| TD3 checkpoint status | `RL/index.tsx` | ✅ |
| How-it-works section | `RL/index.tsx` | ✅ |
| Training commands + launch form with WS stream | `RL/index.tsx` | ✅ |

---

### 11. HIL (`/hil`)
| Feature | React location | Status |
|---|---|---|
| Full HIL disclosure | `HIL/index.tsx` | ✅ |
| Risk profile presets | `HIL/index.tsx` | ✅ |
| Approvals & behavior | `HIL/index.tsx` | ✅ |
| SMS config | `HIL/index.tsx` | ✅ |
| Bridge status + pending approvals | `HIL/index.tsx` | ✅ |

---

### 12. Terms (`/terms`) & Privacy (`/privacy`)
Static text pages — implemented. ✅

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
| `/ws/backtest` | Backtest stream | `useWebSocket` | ✅ |
| `/ws/algo-bt` | Algo backtest | `useWebSocket` | ✅ |
| `/ws/ml/train` | ML training log | `useWebSocket` | ✅ |
| `/ws/rl/train` | RL training log | `useWebSocket` | ✅ |
| `/ws/scanner` | Scanner feed | `useWebSocket` | ✅ |
| `/ws/fidelity` | Fidelity stream | `useWebSocket` | ✅ |

---

## Components

| Component | Location | Status |
|---|---|---|
| AppHeader (title, kicker, command bar, market chip, Chart, datetime) | `layout/AppHeader.tsx` | ✅ |
| Sidebar nav (all icons, correct branding, footer) | `layout/Sidebar.tsx` | ✅ |
| AppShell (layout + nav curtain) | `layout/AppShell.tsx` | ✅ |
| Modal (portal + Escape) | `ui/Modal.tsx` | ✅ |
| Drawer (right slide-out) | `ui/Drawer.tsx` | ✅ |
| Tabs (filtered by adminOnly) | `ui/Tabs.tsx` | ✅ |
| Badge | `ui/Badge.tsx` | ✅ |
| Card | `ui/Card.tsx` | ✅ |
| LoadingState / ErrorState / EmptyState | `shared/LoadingState.tsx` | ✅ |
| LineChart (Chart.js 4) | `charts/LineChart.tsx` | ✅ |

---

## Modals

| Modal | React location | Status |
|---|---|---|
| Onboarding | `modals/OnboardingModal.tsx` | ✅ |
| 2FA step-up | `modals/StepUpModal.tsx` + `useStepUpStore` | ✅ |
| HIL toast | `modals/HilToast.tsx` | ✅ |

---

## State Management

| State | Store | Status |
|---|---|---|
| Theme (light/dark) | `store/theme.ts` Zustand persist | ✅ |
| Auth user + features | `store/auth.ts` Zustand | ✅ |
| `isAdmin` flag | `store/auth.ts` | ✅ |

---

## Global UX

| Feature | Location | Status |
|---|---|---|
| ⌘K Command Palette | `ui/GlobalOverlays.tsx` | ✅ |
| ⌘⇧S Position Sizer | `ui/GlobalOverlays.tsx` | ✅ |
| ? Keyboard Shortcuts modal | `ui/GlobalOverlays.tsx` | ✅ |
| G-nav keyboard chords | `ui/GlobalOverlays.tsx` | ✅ |
| Toast notifications | `ui/Toast.tsx` | ✅ |
| Nav curtain + progress sweep | `AppShell.tsx` + `tokens.css` | ✅ |
| Film grain overlay | `tokens.css` | ✅ |
| Mobile sidebar slide-in | `AppShell.tsx` + `tokens.css` | ✅ |

---

## Feature Parity Summary

| Category | Complete | Notes |
|---|---|---|
| Layout chrome (header, sidebar) | ✅ 100% | AppHeader added, sidebar branding + footer fixed |
| Pages | ✅ 13 / 13 | All pages fully implemented |
| REST endpoints | ✅ ~30 / ~30 | All defined; wired in implemented pages |
| WebSocket paths | ✅ 7 / 7 | All paths connected |
| Modals | ✅ 3 / 3 | Onboarding, StepUp, HIL toast |
| Admin tabs | ✅ 16 / 16 | All tabs implemented |
| Global UX | ✅ | Toast, Position Sizer, Keyboard Shortcuts, Command Palette, G-nav, nav curtain |
| Dashboard | ✅ | Edge-to-edge cockpit, ticker tape, all panels |
| Design tokens | ✅ 100% | Warm palette + all animation classes |
| Charts | ✅ | LineChart + TradingView modal |
| Premium polish | ✅ | Film grain, nav animations, sonar dot, CRT scanline |

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
