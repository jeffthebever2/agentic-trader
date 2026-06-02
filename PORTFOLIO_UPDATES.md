# Thematic Portfolio — Feature Update

**Date:** 2026-05-31  
**Route:** `/app/thematic`  
**Purpose:** Owner-curated high-conviction portfolio grouped by long-term investment theme

---

## What Was Added

### Backend: `web/api/thematic_portfolio.py` (NEW)

Full CRUD + scoring API for per-user thematic portfolio storage.

**Data storage:** `tmp/thematic_portfolio_{user_hash}.json` per user (same pattern as existing portfolio.py)

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/thematic/portfolio` | user | Full enriched portfolio with prices, scores, theme groupings, summary |
| `POST` | `/api/thematic/portfolio/position` | user | Add position |
| `PUT` | `/api/thematic/portfolio/position/{ticker}` | user | Edit position |
| `DELETE` | `/api/thematic/portfolio/position/{ticker}` | user | Remove position |
| `GET` | `/api/thematic/portfolio/themes` | user | List themes |
| `POST` | `/api/thematic/portfolio/themes/{key}` | user | Add custom theme |
| `POST` | `/api/thematic/portfolio/notes` | user | Save portfolio notes |
| `GET` | `/api/thematic/portfolio/score/{ticker}` | user | Score a single ticker |
| `GET` | `/api/thematic/portfolio/defaults` | user | Default themes, category/risk options |

### Frontend: `frontend/src/pages/ThematicPortfolio/index.tsx` (NEW)

Full page with 3 views (Theme grouping, Grid, Table) and full CRUD modals.

---

## Data Model

### Position
```json
{
  "ticker": "NVDA",
  "name": "NVIDIA Corporation",
  "theme": "ai_infrastructure",
  "entry_price": 450.00,
  "shares": 10,
  "conviction": 9,
  "risk_level": "medium",
  "category": "core",
  "thesis": "Why we own it",
  "catalyst": "Main upcoming catalyst",
  "thesis_bull": "What needs to happen",
  "thesis_bear": "What would break the thesis",
  "risk_warning": "Key risk",
  "review_date": "2026-09-01",
  "tags": ["AI", "semis"],
  "added_at": "2026-05-31T00:00:00Z"
}
```

### Computed fields (returned by API, not stored)
- `current_price` — from yfinance (15-min delayed)
- `gain_pct`, `gain_usd`, `market_value`
- `scores` — 10-dimension scoring object
- `theme_name`, `theme_color`, `theme_emoji`

### Scoring dimensions (all 0–10)
| Score | How calculated |
|---|---|
| `theme_score` | Premium themes (AI leaders, infra, HBM, DC power) = 8, others = 6 |
| `catalyst_score` | 8 if catalyst text exists, 4 if empty |
| `momentum_score` | Based on % above entry price |
| `fundamental_score` | **Placeholder 6.0** — needs live earnings/growth data |
| `supply_chain_score` | 8 if supply-chain theme, 5 otherwise |
| `social_score` | **Placeholder 5.0** — needs news/sentiment API |
| `entry_quality` | 10 at entry, decreases as chase % grows |
| `risk_score` | Inverse of risk level |
| `chase_risk` | Higher = more extended above entry |
| `final_score` | Weighted composite of above |

### Default Themes
- 🧠 AI Leaders
- ⚡ AI Infrastructure
- 🔗 Optical Networking
- 💾 Memory / HBM
- 🔋 Data Center Power
- ☢️ Nuclear / Energy
- 🚀 Space & Defense
- ⚛️ Quantum / Future Compute
- ⛏️ Critical Minerals
- 🏭 Reshoring / Industrial
- 💳 Fintech / Consumer
- 🔬 Future Tech / Biotech

### Categories
`core` · `growth` · `satellite` · `speculative` · `watchlist` · `avoid`

### Risk Levels
`low` · `medium` · `high` · `very_high`

---

## UI Features

| Feature | Status |
|---|---|
| Portfolio dashboard with summary cards | ✅ |
| Total market value, gain/loss | ✅ (requires entry_price + shares) |
| Best winner / worst loser | ✅ |
| Theme allocation bar chart | ✅ |
| By-Theme grouped view | ✅ |
| Grid card view | ✅ |
| Table view with all fields | ✅ |
| Search by ticker/thesis/catalyst | ✅ |
| Filter by theme/category/risk | ✅ |
| Add position modal with full form | ✅ |
| Edit position | ✅ |
| Remove with confirmation | ✅ |
| Position cards with expand/collapse | ✅ |
| Score bars (per dimension) | ✅ |
| Thesis / bull / bear sections | ✅ |
| Conviction slider | ✅ |
| Loading / error / empty states | ✅ |
| Demo disclaimer on prices | ✅ |
| Portfolio scoring | ✅ |

---

## Demo vs Real Data

| Data | Source | Notes |
|---|---|---|
| Current prices | yfinance (15-min delayed) | Clearly labeled as delayed. Not real-time. |
| Entry price, shares | User input | Manually entered. Not from broker. |
| Thesis/catalyst/notes | User input | Fully editable. |
| Fundamental score | **Placeholder 6.0** | Needs earnings, P/E, revenue growth from financial API |
| Social/news score | **Placeholder 5.0** | Needs news/sentiment feed (e.g. Benzinga, NewsAPI) |
| Portfolio value | Calculated from user inputs | Accurate only if entry_price + shares are filled |

**No real broker connection.** No buy/sell execution. No position sync from Fidelity/broker.

---

## Missing Backend / Data Connections

| Feature | What's Needed | Endpoint Needed |
|---|---|---|
| Real-time prices | WebSocket or 1-min data | `GET /api/market/quotes?tickers=...` (exists in market.py) |
| Fundamental score | P/E, EPS growth, revenue, margins | `GET /api/market/fundamentals/{ticker}` |
| Social/news score | Recent news sentiment | `GET /api/market/sentiment/{ticker}` |
| Broker sync | Import positions from Fidelity/Webull | Link to existing fidelity.py / webull_portfolio.py |
| Performance history | Track portfolio value over time | Store daily snapshots; `GET /api/thematic/portfolio/history` |
| Export | CSV/PDF export | `GET /api/thematic/portfolio/export` |
| Alerts | Notify on thesis-breaker triggers | Event-driven; needs alert engine |

---

## Files Changed

| File | Change |
|---|---|
| `web/api/thematic_portfolio.py` | NEW — full CRUD + scoring backend |
| `web/app.py` | + import + include_router for thematic_router |
| `frontend/src/pages/ThematicPortfolio/index.tsx` | NEW — full page |
| `frontend/src/App.tsx` | + lazy import + `/thematic` route |
| `frontend/src/components/layout/Sidebar.tsx` | + `Thematic Portfolio` nav item |

---

## Build / Lint Status

- Frontend build: ✅ 0 TypeScript errors (2552 modules)
- Frontend lint: ✅ 0 lint problems
- Backend compile: ✅ `py_compile` clean
- Existing features: ✅ Unchanged

---

## Next Improvements

1. **Wire up real-time quotes** — existing `/api/market/quotes` already works; add auto-refresh to portfolio cards
2. **Fundamental score** — add P/E, EPS growth, revenue data from yfinance `info` dict (free)
3. **Performance history** — store daily snapshots in `tmp/thematic_history_{hash}.jsonl`
4. **Broker sync** — one-click import from webull_portfolio.py or fidelity.py into thematic positions
5. **Export** — CSV download of all positions + scores
6. **Alerts** — "review date reached" and "position down >20%" notifications
7. **Closed ideas** — archive removed positions with outcome notes (what worked / failed)
8. **Allocation chart** — pie chart using existing recharts (already in frontend deps)
