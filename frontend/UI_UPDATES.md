# UI Updates Log

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
