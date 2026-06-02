# Private Manager — Desktop Control Panel

**Date:** 2026-05-31  
**Purpose:** Owner-only macOS management console for the Agentic Trader platform  
**Audience:** Sole operator — not a user-facing trading UI

---

## What Was Rebuilt

The `/desktop` Tauri app was rebuilt from a generic 6-page control panel into a comprehensive 8-section private management console. It is entirely separate from the main React web trading app in `/frontend`.

### Before vs. After

| Was | Now |
|---|---|
| Generic dashboard + services tiles | 8 dedicated management sections |
| No authentication | Auth gate: login screen + `/api/auth/me` check |
| No confirmation dialogs | ConfirmModal on all dangerous actions |
| No safe mode | Session-scoped safe mode toggle (disables all writes) |
| Placeholder API client | Centralized query layer with all known endpoints |
| No honest "missing" states | `NotImplemented` component for all undocumented endpoints |
| 5 pages, flat API calls | 8 pages, typed query functions, auth/safety layered |

---

## Folder Structure

```
/desktop/
├── src/
│   ├── api/
│   │   ├── client.ts           Configurable Axios — reads URL + token from Tauri store
│   │   └── queries.ts          ALL management query functions + MISSING endpoint docs
│   ├── components/
│   │   ├── AuthGate.tsx        Login screen — wraps entire app; blocks until /api/auth/me passes
│   │   ├── ConfirmModal.tsx    Confirmation dialog for all risky actions
│   │   ├── MetricCard.tsx      Stat cards with accent ring
│   │   ├── NotImplemented.tsx  Honest placeholder for missing endpoints (shows method/path/body)
│   │   ├── SafeModeBar.tsx     Safe mode warning banner
│   │   ├── ServiceBadge.tsx    Running/stopped service badge
│   │   ├── Sidebar.tsx         Nav with safe-mode toggle + role display
│   │   ├── StateViews.tsx      Loading, ErrorState, Empty
│   │   └── StatusIndicator.tsx StatusDot + StatusBadge (ok/warn/error/running/stopped/unknown)
│   ├── pages/
│   │   ├── Home.tsx            Management Home — aggregated status overview
│   │   ├── AppHealth.tsx       Health checks for all platform layers
│   │   ├── Services.tsx        Service controls with ConfirmModal
│   │   ├── Logs.tsx            Log viewer — source/level/keyword filter
│   │   ├── Deployments.tsx     Git status, runtime info, restart action, deploy placeholders
│   │   ├── ModelManagement.tsx ML status, retrain history, trigger retrain
│   │   ├── DataSources.tsx     Data freshness — real where available, honest placeholders
│   │   └── Settings.tsx        Connection + auth, safe mode, feature flags, CF config
│   ├── store/
│   │   ├── auth.ts             Zustand auth store (user, safeMode, checkAuth)
│   │   └── settings.ts         Zustand settings (apiUrl, token) backed by Tauri plugin-store
│   ├── App.tsx                 Routes + AuthGate + settings/auth load on mount
│   └── main.tsx                React 19 + React Query entry
├── src-tauri/
│   ├── src/main.rs + lib.rs    Tauri 2 entry (store, shell, dialog, notification plugins)
│   ├── icons/                  32x32, 128x128, 128x128@2x, icon.icns, icon.ico
│   ├── Cargo.toml
│   ├── build.rs
│   └── tauri.conf.json         App: Agentic Trader, id: org.agentictrader.desktop
├── index.html
├── vite.config.ts              Port 1420, @shared alias
├── tsconfig.json
└── package.json

/shared/
├── types/index.ts              Shared TS types matching backend shapes
└── constants/index.ts          ENDPOINTS map, defaults
```

---

## Files Changed / Created

| File | Action | Notes |
|---|---|---|
| `desktop/src/pages/Home.tsx` | NEW | Management home — replaces Dashboard |
| `desktop/src/pages/AppHealth.tsx` | NEW | Full health check matrix |
| `desktop/src/pages/Services.tsx` | REPLACED | Added ConfirmModal, paper trader start/stop, safe mode |
| `desktop/src/pages/Logs.tsx` | REPLACED | Added search, level filter, line count selector |
| `desktop/src/pages/Deployments.tsx` | REPLACED | Git status, runtime info, safe restart, deploy placeholders |
| `desktop/src/pages/ModelManagement.tsx` | NEW | ML status, feature importance, retrain history + trigger |
| `desktop/src/pages/DataSources.tsx` | NEW | Data freshness — real + honest placeholders |
| `desktop/src/pages/Settings.tsx` | REPLACED | Auth, safe mode, feature flags, CF config |
| `desktop/src/pages/Dashboard.tsx` | DELETED | Superseded by Home.tsx |
| `desktop/src/pages/SystemHealth.tsx` | DELETED | Superseded by AppHealth.tsx |
| `desktop/src/components/AuthGate.tsx` | NEW | Login screen |
| `desktop/src/components/ConfirmModal.tsx` | NEW | Confirmation dialog |
| `desktop/src/components/NotImplemented.tsx` | NEW | Missing endpoint placeholder |
| `desktop/src/components/SafeModeBar.tsx` | NEW | Safe mode banner |
| `desktop/src/components/StatusIndicator.tsx` | NEW | Status dots and badges |
| `desktop/src/components/Sidebar.tsx` | REPLACED | 8 nav items, safe mode toggle, role display |
| `desktop/src/App.tsx` | REPLACED | AuthGate + 8 routes |
| `desktop/src/api/queries.ts` | REPLACED | Full typed query layer + MISSING endpoint docs |
| `desktop/src/store/auth.ts` | NEW | Auth state |

---

## Commands

### Run desktop (dev mode)
```bash
# Requires backend running at configured URL
cd desktop
npm install          # first time only
npm run tauri:dev    # Vite dev server on :1420 + Tauri window opens
# or from repo root:
npm run desktop:dev
```

### Build release .app
```bash
cd desktop && npm run tauri:build
# Output: desktop/src-tauri/target/release/bundle/macos/Agentic Trader.app
npm run desktop:tauri-build   # from root
```

### Build DMG
```bash
cd desktop && npm run tauri:dmg
# Output: desktop/src-tauri/target/release/bundle/dmg/Agentic Trader_0.1.0_aarch64.dmg
npm run desktop:dmg           # from root
```

### Run web frontend (unchanged)
```bash
cd frontend && npm run dev
npm run web:dev               # from root
```

---

## Management Sections + Backend Endpoints Used

### 1. Management Home (`/`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/health/deep` | none | ✅ |
| `GET /api/system/metrics` | user | ✅ |
| `GET /api/system/services` | user | ✅ |
| `GET /api/ml/status` | none | ✅ |
| `GET /api/admin/audit` | admin | ✅ |

### 2. App Health (`/health`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/health/deep` | none | ✅ |
| `GET /api/system/metrics` | user | ✅ |
| `GET /api/admin/runtime/diagnostics` | admin | ✅ |
| `GET /api/auth/features` | none | ✅ |
| `GET /api/admin/scheduler/status` | admin | ❌ MISSING |
| `GET /api/market/feed/status` | user | ❌ MISSING |

### 3. Services (`/services`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/system/services` | user | ✅ |
| `GET /api/paper/status` | user | ✅ |
| `POST /api/paper/start` | admin | ✅ |
| `POST /api/paper/stop` | admin | ✅ |
| `POST /api/admin/runtime/web/restart` | admin | ✅ |
| `POST /api/admin/runtime/tunnel/start` | admin | ✅ |
| `POST /api/admin/runtime/tunnel/stop` | admin | ✅ |

### 4. Logs (`/logs`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/logs/system` | user | ✅ |
| `GET /api/logs/sources` | user | ✅ |

### 5. Deployments (`/deploy`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/admin/runtime/diagnostics` | admin | ✅ |
| `GET /api/admin/runtime/status` | admin | ✅ |
| `POST /api/admin/runtime/web/restart` | admin | ✅ |
| `GET /api/admin/deploy/history` | admin | ❌ MISSING |
| `POST /api/admin/deploy` | admin | ❌ MISSING |

### 6. AI / Model Management (`/models`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/ml/status` | none | ✅ |
| `GET /api/ml/history` | none | ✅ |
| `POST /api/ml/retrain` | admin | ✅ |
| `GET /api/ml/errors/recent` | admin | ❌ MISSING |

### 7. Data Sources (`/data`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/logs/sources` | user | ✅ |
| `GET /api/ml/status` | none | ✅ |
| `GET /api/paper/status` | user | ✅ |
| `GET /api/market/feed/status` | user | ❌ MISSING |
| `GET /api/data/freshness` | user | ❌ MISSING |

### 8. Settings (`/settings`)
| Endpoint | Auth | Status |
|---|---|---|
| `GET /api/admin/flags` | admin | ✅ |
| `POST /api/admin/flags` | admin | ✅ |
| `GET /api/admin/cloudflare` | admin | ✅ |
| Local Tauri store | n/a | ✅ (encrypted) |

---

## Missing Backend Endpoints

All are documented inline in `desktop/src/api/queries.ts` and shown as `NotImplemented` components in the UI:

| Endpoint | Purpose | Auth |
|---|---|---|
| `GET /api/market/feed/status` | Per-feed freshness/delay | user |
| `GET /api/data/freshness` | All data source timestamps | user |
| `GET /api/admin/errors/recent` | Recent server errors for Home panel | admin |
| `GET /api/admin/deploy/history` | Deployment log (git commit, outcome) | admin |
| `POST /api/admin/deploy` | Safe remote deploy (git pull + rebuild) | admin |
| `GET /api/admin/scheduler/status` | Background job scheduler state | admin |
| `GET /api/ml/errors/recent` | ML inference error log | admin |

---

## Security Protections

| Protection | Implementation |
|---|---|
| Auth gate | App blocked until `GET /api/auth/me` returns 200 |
| Token storage | Tauri `plugin-store` encrypted local file — never localStorage, env, or plaintext |
| No raw secrets displayed | CF config shows `configured: true/false` only; token field is password-type with show/hide |
| Confirmation modals | All risky actions (restart, stop, retrain, tunnel) require `ConfirmModal` |
| Safe mode | Session toggle disables all write actions; persists until manually disabled |
| No arbitrary shell exec | All actions go through typed backend API endpoints; no eval/exec surfaces |
| Input validation | URL field validated before save; auth token stripped of whitespace |
| Admin-only actions | Restart, flag changes, tunnel control, retrain all guarded by `require_admin` on backend |
| Sanitized error responses | Global exception handler in backend returns generic messages, not stack traces |

---

## Build / Lint Results

| Check | Result |
|---|---|
| Desktop TypeScript (`tsc -b`) | ✅ 0 errors |
| Desktop Vite build | ✅ 391KB bundle, 0 warnings |
| Rust `cargo check` | ✅ Finished (Tauri 2.11.2) |
| Web frontend build | ✅ 0 errors (unchanged) |
| Web frontend lint | ✅ 0 lint problems (unchanged) |

---

## Known Blockers

| Blocker | Impact | Fix |
|---|---|---|
| Icons are PIL-generated placeholders | DMG icon is a plain circle | Replace `icons/` with branded 1024×1024 PNG; run `cargo tauri icon` |
| Apple notarization not configured | DMG not distributable via Gatekeeper | Add `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` to Tauri macOS bundle config |
| Admin endpoints require admin CF session | Services/deploy actions return 403 for non-admin | Ensure API token belongs to an admin account |
| `GET /api/paper/system-health` not surfaced | Paper system health detail not shown | Add to AppHealth page once endpoint shape confirmed |

---

## Next Improvements

1. Replace placeholder icons with branded app icon
2. Add Apple notarization env vars for distributable DMG
3. Implement missing backend endpoints: `feed/status`, `data/freshness`, `deploy/history`
4. Add real-time log streaming via WebSocket (`GET /api/ws/logs`)
5. Add system tray icon showing server status (green/red dot)
6. Add desktop notifications for: paper trader down, model gate failed, tunnel offline
7. Add equity curve chart to Model Management (paper trader PnL overlay)
8. Add an "Export Snapshot" button (calls `GET /api/admin/export`)
