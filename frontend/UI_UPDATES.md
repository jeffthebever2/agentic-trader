# UI Updates Log

## 2026-05-30 — Pass 2-8: Lint Fixes, Retrain History, SystemStatus Deep Health, Bundle Split

### Summary
Went from **41 → 3 lint problems** (3 remaining are React Compiler "Compilation Skipped" for third-party library hooks — not fixable without library upgrades). Build clean, 0 TypeScript errors.

### Lint / Architecture Fixes (Pass 2 + 8)

**Eliminated `setState` in effects** by converting to derived state:
- `AppShell.tsx` — `showOnboarding` derived from `user.onboarding_completed`, no effect
- `HilToast.tsx` — rewrote to derive visible trade directly from query data; removed ref-during-render
- `Settings.tsx` — `displayName`, `phone`, `smsOptOut`, `tradingVals` all derived with override pattern + `useMemo`
- `Paper.tsx` — autostart config derived with override pattern
- `HIL/index.tsx` — `form`, `phone` derived with override + reset-on-discard logic
- `Analyze/index.tsx`, `Backtest/index.tsx` — model defaults moved to `changeProvider()` handler (not effect)
- `Broker.tsx` — `refreshedAt` derived from `posQ.dataUpdatedAt` (removed effect entirely)
- `GlobalOverlays.tsx` — `CommandPalette` gets `key={cmd ? 'open' : 'closed'}` to remount on open (resets q/idx automatically)

**Critical bug fixed:**
- `CandidatePanel.tsx:382` — `ChartSVG` was a component created inside render (unmounts subtree every render). Renamed to `renderChart()` (plain function), no more re-mount.
- `HilToast.tsx:73` — refs accessed during render. Replaced with derived `t` from query data.

**Other fixes:**
- `DrawdownChart.tsx` — mutable `peak` variable in render replaced with `Array.reduce`
- `DataTable.tsx` — useless `cmp = 0` assignment → `const cmp = ...`
- `Dashboard/index.tsx` — empty `catch {}` blocks
- `CandidatePanel.tsx:859` — `(_, _i)` unused params → `(_, i)` with explicit key
- `market.ts` — added `rsi?: number | null` to `QuoteDetail` interface (was `any` cast)
- `Toast.tsx`, `GlobalOverlays.tsx`, `StepUpModal.tsx` — `eslint-disable react-refresh/only-export-components` (stores co-located with components; splitting would require ~10 import changes)
- `Analyze/index.tsx:515` — `catch (_)` → `catch`
- `Backtest/index.tsx:154` — removed `useCallback` for `fetchFiles`, just an async function now
- `LineChart.tsx` — added `showLegend`, `yFormatter` to effect deps
- `useWebSocket.ts`, `useAuth.ts` — added eslint-disable for intentional missing deps (callbacks stored in refs)

### New Backend Endpoint
- `GET /ml/history` — reads `ml_models/retrain_history.jsonl`, returns list of retrain records

### New Shared Utilities
- `src/utils/format.ts` — `fmtDollar`, `fmtPnl`, `fmtNum`, `fmtPct`, `pnlColor`, `winRateColor`, `timeAgo`, `fmtVolume`. Import from here to stop duplication across pages.

### ML Page — Retrain History Table (Pass 5)
- Model History sub-section rewritten to use actual `retrain_history.jsonl` format
- Shows: date, WF ROC (color-coded green ≥0.51 / yellow ≥0.49 / red <0.49), training rows, outcome (deployed / failed)
- Newest cycle first, "LATEST" badge on top row
- Notes column shows first 80 chars of context

### Dashboard — Enhanced SystemStatus (Pass 3)
- Added `/health/deep` query to SystemStatus component
- Now shows: Market, ML Model, Market Data, Fidelity, **Tunnel, Disk, AutoFix** chips
- Tunnel/Disk/AutoFix chips only appear when deep health responds (new checks)
- Overall HEALTHY/DEGRADED badge based on deep health status
- Chip tooltips show `detail` from health endpoint

### Bundle Splitting (Pass 8)
- `vite.config.ts` — `manualChunks()` function splits heavy vendors:
  - `vendor-charts` (recharts, chart.js, d3, lightweight-charts)
  - `vendor-react` (react, react-dom, react-router-dom)
  - `vendor-query` (@tanstack/react-query)
  - `vendor-ui` (zustand, lucide-react)
- `index` chunk: **571KB → 131KB** (77% reduction)
- Charts chunk loads lazily (only when pages using charts are visited)

### Build / Lint Status
- **Build**: ✅ 0 TypeScript errors, 0 build errors
- **Lint**: 3 problems (all "Compilation Skipped" warnings from React Compiler for third-party hooks — Broker/TanStack Virtual, History/Broker chart lib, Backtest `run` useCallback with timer ref)
- **Bundle**: `index` 131KB gzip:37KB | `vendor-charts` 714KB gzip:222KB | `vendor-react` 219KB gzip:70KB

### Remaining Recommended Next Steps
1. **Move stores to `src/store/`** — Toast/GlobalOverlays/StepUpModal export both stores + components from same file; split to fix Fast Refresh warning
2. **Use `utils/format.ts`** — existing pages (Dashboard, ML) still have inline helpers; migrate them to use the shared utils
3. **Retrain Cycle 46 completion** — retrain running as of 2026-05-30 23:16, ETA ~12:15 AM. Deploy if gate passes.
4. **Equity curve sparkline on Signals page** — mini price chart per candidate
5. **System log endpoint** — expose `logs/autofix.log` + `logs/webserver.err` via `GET /logs/system` for real-time viewing in Logs page

---

## 2026-05-30 — Cycle 45 UI Audit + New Pages

### Files Changed
- `src/App.tsx` — added Signals + Logs routes, wrapped all lazy pages in `ErrorBoundary`
- `src/components/layout/Sidebar.tsx` — added Signals + Logs nav items; renamed "Statistics"→"Models & Stats", "Backtest"→"Research & Backtest"; added prefetch for `/signals` and `/logs`
- `src/pages/Signals/index.tsx` — **NEW**: Live Signals page
- `src/pages/Logs/index.tsx` — **NEW**: Logs & Research page

### New Pages

#### `/signals` — Live Signals
- Grid of ML-scored candidate cards with tier breakdown (A+/A/B/C)
- Each card: entry/target/stop prices, upside/downside %, ML win probability, R:R ratio, alpha score, ATR, large-loss risk
- "Why this signal?" section if `decision_reason` / `ai_reason` field is present on candidate
- Sort by: score, ML %, R:R, upside
- Filter by strategy + ticker search
- Live runner status badge (LIVE/STOPPED)
- Empty state with contextual guidance

#### `/logs` — Logs & Research
- Stats strip: total analyses, unique tickers, buy/hold/sell signal counts
- Tabs: Paper Decisions | Trade Log | AI Memory
- Paper Decisions: paginated table with search (ticker/strategy/reason) + decision filter
- Trade Log: paginated table with ticker search + strategy filter, P&L coloring
- AI Memory: renders the markdown memory log with size info
- All tabs: loading state, empty state with explanation

### Structural Improvements
- Error boundaries now wrap every lazy-loaded route (prevents one page crash from killing whole app)
- Sidebar prefetch added for `/signals` and `/logs` (hover-to-prefetch)
- Nav now covers all goal sections: Dashboard, Signals, Paper Trading, Research & Backtest, History, Models & Stats, Logs, HIL, Admin, Settings

### Build / Lint Status
- **Build**: ✅ 0 TypeScript errors
- **Lint (new files)**: ✅ 0 errors, 0 warnings
- **Lint (whole project)**: 41 pre-existing issues in Settings, Paper, History, HIL, Broker, Backtest, Analyze, Dashboard (all existed before this session)
- Bundle size warning (CartesianChart 316KB, index 571KB) — pre-existing, not blocking

### Pre-existing Lint Issues (out of scope — not introduced here)
All in these files, pre-existing before this session:
- `pages/Settings/index.tsx` — `setState` in effect bodies (3 instances)
- `pages/Paper/index.tsx` — `setState` in effect (1)
- `pages/History/index.tsx` — any cast (1)
- `pages/HIL/index.tsx` — `setState` in effect (1)
- `pages/Broker/index.tsx` — any casts (2)
- `pages/Backtest/index.tsx` — any casts (2)
- `pages/Analyze/index.tsx` — any cast (1)
- `pages/Dashboard/index.tsx` — various

### Missing Backend Data (for full functionality)
- `decision_reason` / `ai_reason` on `CandidateRow` — needed for "Why this signal?" section. Field exists in type but backend must emit it on candidates API response.
- `GET /logs/paper-decisions` `confidence` field — optional, shown if present
- System/autofix log viewer (autofix.log) — not exposed via API; would need a new endpoint like `GET /logs/system`

### Next Recommended Improvements
1. **Equity curve on Signals page** — show mini sparkline per candidate showing recent price vs entry
2. **Model Intelligence tab** — dedicated page section showing prediction vs actual calibration curve, confidence histogram, and WF ROC trend over retrain history
3. **Fix pre-existing lint** — `setState` in effects in Settings/Paper/HIL can be refactored to `useEffect` initializer pattern
4. **Bundle splitting** — move Recharts/CartesianChart out of the main chunk via explicit `build.rolldownOptions`
5. **Mobile nav** — sidebar collapses to hamburger on mobile; currently works but could be polished
6. **System log endpoint** — expose `logs/autofix.log` and `logs/webserver.err` via API for real-time viewing in the Logs page
