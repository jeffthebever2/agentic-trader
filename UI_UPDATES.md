# UI Updates — Agentic Trader Desktop Control Panel

**Date:** 2026-05-31  
**Scope:** Add `/desktop` Tauri + React macOS control panel + `/shared` types layer

---

## 1. Repo Structure Created

```
/
├── frontend/           existing React web UI (unchanged)
├── web/                existing FastAPI backend (unchanged)
├── desktop/            NEW — Tauri + React macOS desktop app
│   ├── src/
│   │   ├── api/
│   │   │   ├── client.ts       configurable Axios client (reads apiUrl + token from store)
│   │   │   └── queries.ts      typed query functions for all backend endpoints
│   │   ├── components/
│   │   │   ├── Sidebar.tsx     nav sidebar (Dashboard/Services/Logs/Deployments/Health/Settings)
│   │   │   ├── MetricCard.tsx  stat card with accent ring + dot
│   │   │   ├── ServiceBadge.tsx  running/stopped badge with detail text
│   │   │   └── StateViews.tsx  Loading, ErrorState, Empty components
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx   metrics grid + services overview + ML stats
│   │   │   ├── Services.tsx    service badges + quick actions (restart/tunnel)
│   │   │   ├── Logs.tsx        log tail viewer (source+lines selector, 30s refresh)
│   │   │   ├── Deployments.tsx git status + runtime info + retrain trigger
│   │   │   ├── SystemHealth.tsx full metrics + progress bars + API health matrix
│   │   │   └── Settings.tsx    API base URL + auth token (persisted in Tauri store)
│   │   ├── store/
│   │   │   └── settings.ts     Zustand store backed by @tauri-apps/plugin-store
│   │   ├── App.tsx             BrowserRouter + Routes + settings load on mount
│   │   ├── main.tsx            React 19 + QueryClientProvider entry
│   │   └── index.css           Tailwind v4 + dark scrollbar
│   ├── src-tauri/
│   │   ├── src/
│   │   │   ├── main.rs         Tauri entry
│   │   │   └── lib.rs          plugins: store, shell, dialog, notification
│   │   ├── icons/              32x32.png, 128x128.png, 128x128@2x.png, icon.icns, icon.ico
│   │   ├── Cargo.toml          tauri 2 + tauri-plugin-store/shell/dialog/notification
│   │   ├── build.rs
│   │   └── tauri.conf.json     app: Agentic Trader, identifier: org.agentictrader.desktop
│   ├── index.html
│   ├── vite.config.ts          port 1420, base /, shared alias @shared → ../shared
│   ├── tsconfig.json           strict, ES2021, @shared path alias
│   └── package.json            npm scripts: dev/build/tauri:dev/tauri:build/tauri:dmg
│
├── shared/             NEW — types and constants shared between web + desktop
│   ├── types/index.ts  SystemMetrics, SystemServices, DeepHealth, Logs, ML, etc.
│   └── constants/index.ts  ENDPOINTS map, DEFAULT_API_BASE_URL, LOG_SOURCES
│
└── package.json        NEW — root workspace with web:dev, desktop:dev, desktop:dmg scripts
```

---

## 2. Files Changed / Created

| File | Status | Notes |
|---|---|---|
| `desktop/` | NEW (entire directory) | Tauri 2 + React 19 + Vite + Tailwind v4 |
| `shared/types/index.ts` | NEW | Shared TS types matching backend response shapes |
| `shared/constants/index.ts` | NEW | Endpoint map + defaults |
| `package.json` (root) | NEW | Workspace scripts |
| `frontend/` | UNCHANGED | Web build still clean (0 errors, 0 lint) |
| `web/` | UNCHANGED | Backend untouched |

---

## 3. Commands

### Run web frontend (existing)
```bash
cd frontend && npm run dev
# or from root:
npm run web:dev
```

### Run desktop (dev mode)
```bash
cd desktop
npm install       # first time only
npm run tauri:dev # launches Vite on :1420 + Tauri window
# or from root:
npm run desktop:dev
```

### Build desktop (release .app)
```bash
cd desktop && npm run tauri:build
# or from root:
npm run desktop:tauri-build
```

### Build DMG
```bash
cd desktop && npm run tauri:dmg
# or from root:
npm run desktop:dmg
# Output: desktop/src-tauri/target/release/bundle/dmg/Agentic Trader_0.1.0_aarch64.dmg
```

---

## 4. Desktop Pages

| Page | Route | Backend endpoints used |
|---|---|---|
| Dashboard | `/` | `GET /api/system/metrics`, `GET /api/system/services`, `GET /api/health/deep` |
| Services | `/services` | `GET /api/system/services`, `POST /api/admin/runtime/web/restart`, `POST /api/admin/runtime/tunnel/start`, `POST /api/admin/runtime/tunnel/stop` |
| Logs | `/logs` | `GET /api/logs/system`, `GET /api/logs/sources` |
| Deployments | `/deployments` | `GET /api/admin/runtime/diagnostics`, `POST /api/ml/retrain` |
| System Health | `/health` | `GET /api/system/metrics`, `GET /api/health/deep`, `GET /api/admin/runtime/status` |
| Settings | `/settings` | Tauri store only (local) |

All pages have Loading, Error, and Empty states. All queries use React Query with auto-refetch intervals.

---

## 5. Auth / Security

- API base URL and auth token stored in **Tauri plugin-store** (encrypted local file) — never hardcoded
- Token sent as `Authorization: Bearer …` header when set
- Default: rely on Cloudflare Access session cookies (`withCredentials: true`)
- Settings page: show/hide token toggle, security notes inline
- No secrets in source code, no `.env` files, no hardcoded values

---

## 6. Missing / Placeholder Backend Endpoints

These endpoints are called by the desktop but may require admin auth or may not exist yet:

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/api/system/metrics` | user | ✅ Implemented in `web/api/system.py` |
| `GET` | `/api/system/services` | user | ✅ Implemented in `web/api/system.py` |
| `GET` | `/api/logs/system` | user | ✅ Implemented in `web/api/logs.py` |
| `GET` | `/api/logs/sources` | user | ✅ Implemented in `web/api/logs.py` |
| `POST` | `/api/ml/retrain` | admin | ✅ Implemented in `web/api/system.py` |
| `POST` | `/api/admin/runtime/web/restart` | admin | ✅ In `web/api/admin.py` |
| `POST` | `/api/admin/runtime/tunnel/start` | admin | ✅ In `web/api/admin.py` |
| `POST` | `/api/admin/runtime/tunnel/stop` | admin | ✅ In `web/api/admin.py` |
| `GET` | `/api/admin/runtime/diagnostics` | admin | ✅ In `web/api/admin.py` |
| `GET` | `/api/admin/runtime/status` | admin | ✅ In `web/api/admin.py` |

**All desktop endpoints exist in the backend.** Desktop app will show ErrorState gracefully if any are unreachable (auth failure, network, etc.).

---

## 7. Known Blockers

| Blocker | Impact | Resolution |
|---|---|---|
| Icons are PIL-generated placeholders | DMG icon is a plain circle — not branded | Replace `desktop/src-tauri/icons/` with proper 1024×1024 app icon and run `cargo tauri icon icon.png` to regenerate all sizes |
| `notarization` not configured | DMG can't be distributed via Gatekeeper without Apple notarization | Add `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` env vars to `tauri.conf.json` bundle.macOS for distribution |
| Tauri dev window needs backend running | Desktop fetches live data from backend | Start `python run_web.py` before `npm run tauri:dev` |
| Admin endpoints require admin session | Services restart/tunnel actions require admin CF Access token | Use Settings page to enter a valid token, or authenticate via CF Access browser flow |
| WebSocket log streaming not implemented | Logs page polls HTTP every 30s | Add `GET /api/ws/logs` WebSocket endpoint in backend for real-time streaming |

---

## 8. Build Results

- **Desktop TypeScript:** ✅ 0 errors (`tsc -b`)
- **Desktop Vite build:** ✅ 355KB main bundle (1874 modules)
- **Desktop Rust (cargo check):** ✅ Compiles clean (Tauri 2.11.2)
- **Web frontend:** ✅ Unchanged — 0 lint errors, 0 TS errors
- **Web backend:** ✅ Unchanged

---

## 9. Next Improvements

1. Replace placeholder icons with branded 1024×1024 PNG, run `cargo tauri icon`
2. Add Apple notarization config for distributable DMG
3. Add real-time log streaming via WebSocket in Logs page
4. Add equity curve chart to Dashboard (paper trader PnL)
5. Add trade history table to Dashboard
6. Add ML training progress WebSocket view in Deployments
7. Add system tray icon with quick status (CPU, tunnel, paper trader)
8. Add desktop notifications for paper trader alerts
