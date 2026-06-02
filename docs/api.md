# TradingAgents API Documentation

Two distinct APIs live in this project:
1. **Python SDK** — `TradingAgentsGraph` for multi-agent LLM analysis
2. **Web API** — FastAPI backend for paper trading, thematic portfolios, live trading, ML, and admin

---

## Python SDK

### TradingAgentsGraph

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph

ta = TradingAgentsGraph(
    selected_analysts=["market", "social", "news", "fundamentals"],
    debug=False,
    config=None,      # uses DEFAULT_CONFIG if None
    callbacks=None,   # LangChain callback handlers
)
state, decision = ta.propagate("AAPL", "2024-01-15")
```

**`propagate(company_name, trade_date)`** returns `(state_dict, decision_string)`.

**Configuration keys:** `llm_provider`, `deep_think_llm`, `quick_think_llm`, `max_debate_rounds`, `data_cache_dir`, `checkpoint_enabled`, `starting_cash`, `portfolio_state_path`, `trade_log_path`.

**Data providers:** `yfinance` (free), `alpha_vantage` (paid), `fmp` (paid), `sec` (EDGAR filings).

**Metrics:**
```python
from tradingagents.metrics import get_metrics
summary = get_metrics().get_summary()
```

---

## Web API

Base URL: `http://localhost:8000` (default). All endpoints require authentication unless noted.

Auth: Cloudflare Access JWT (production) or session cookie (dev). Manager endpoints additionally require `X-Manager-Key` header.

---

### Auth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/me` | Current user info |
| POST | `/api/auth/login` | Login (returns session) |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/2fa/status` | 2FA enrollment status |
| POST | `/api/auth/2fa/enroll` | Enroll TOTP |
| POST | `/api/auth/2fa/verify` | Verify TOTP code |
| POST | `/api/auth/step-up` | Step-up auth for sensitive actions |

---

### Market Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/market/quote?symbol=X` | Live quote |
| GET | `/api/market/quote-detail?symbol=X` | Fundamentals, 52-week range, analyst target |
| GET | `/api/market/history?symbol=X&period=1y` | OHLCV history |
| GET | `/api/market/news?symbol=X` | Latest news for symbol |
| GET | `/api/market/regime` | Current market regime state |
| GET | `/api/market/watchlist` | User watchlist |
| POST | `/api/market/watchlist` | Add to watchlist |
| DELETE | `/api/market/watchlist/{symbol}` | Remove from watchlist |
| GET | `/api/market/opportunities` | Top screener opportunities |

---

### Paper Trading

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/paper/start` | Start paper trading session |
| POST | `/api/paper/stop` | Stop running session |
| GET | `/api/paper/status` | Session status + config |
| GET | `/api/paper/summary` | Portfolio summary + P&L |
| GET | `/api/paper/positions` | Open positions |
| GET | `/api/paper/candidates` | Current scan candidates |
| GET | `/api/paper/events` | Event log (last N entries) |
| GET | `/api/paper/hil/pending` | Pending HIL approvals |
| POST | `/api/paper/hil/approve` | Approve HIL trade |
| POST | `/api/paper/hil/reject` | Reject HIL trade |
| GET | `/api/paper/settings` | HIL + paper settings |
| POST | `/api/paper/settings` | Update settings |

**Default config (POST /api/paper/start body):**
```json
{
  "target_mult": 1.2,
  "stop_mult": 1.0,
  "ml_probability_threshold": 0.55,
  "ml_large_loss_max": 0.50,
  "min_risk_reward": 1.15,
  "skip_vix_low_vol": true,
  "skip_extended_bounce": true,
  "skip_thursday": true
}
```

---

### Thematic Portfolio (Manual Conviction)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/thematic/portfolio` | Full portfolio with live prices + scores |
| POST | `/api/thematic/portfolio/position` | Add position |
| PUT | `/api/thematic/portfolio/position/{ticker}` | Edit position |
| DELETE | `/api/thematic/portfolio/position/{ticker}` | Remove position |
| GET | `/api/thematic/portfolio/themes` | User's themes |
| POST | `/api/thematic/portfolio/themes/{theme_key}` | Add/update theme |
| POST | `/api/thematic/portfolio/notes` | Save portfolio notes |
| GET | `/api/thematic/portfolio/score/{ticker}` | Score single position |
| GET | `/api/thematic/portfolio/defaults` | Themes, categories, risk levels for dropdowns |
| POST | `/api/thematic/trade` | Inject paper trade with R:R gate + conviction scaling |

**`POST /api/thematic/trade` body:**
```json
{
  "ticker": "NVDA",
  "dollar_amount": 500,    // scales by conviction from portfolio
  "entry_price": null,     // null = fetch live price
  "stop_pct": 5.0,
  "target_pct": 10.0       // auto-widened if R:R < min_rr
}
```

**Response includes:** `rr`, `conviction`, `atr`, `warnings[]` (if R:R was auto-widened).

---

### Thematic Auto (AI-Picked Signals)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/thematic/auto/scan` | Trigger fresh scan (async) |
| GET | `/api/thematic/auto/status` | Scan status |
| GET | `/api/thematic/auto/signals` | Pending signal queue |
| POST | `/api/thematic/auto/signals/{id}/approve` | Approve signal → paper trade |
| POST | `/api/thematic/auto/signals/{id}/skip` | Skip signal |
| GET | `/api/thematic/auto/trending` | Raw trending tickers (no AI) |
| GET | `/api/thematic/auto/exit-check` | Dry-run: positions that would exit |
| POST | `/api/thematic/auto/exit-check` | Execute exits (stop/target/hold/buzz) |
| GET | `/api/thematic/auto/exit-log` | Last 50 exit records |
| GET | `/api/thematic/auto/score-history?limit=20` | Last N scan score snapshots |
| GET | `/api/thematic/auto/brave-usage` | Brave Search monthly usage |
| GET | `/api/thematic/auto/hil-settings` | HIL settings |
| POST | `/api/thematic/auto/hil-settings` | Update HIL settings |
| GET | `/api/thematic/auto/twitter-status` | Twitter API availability |

**HIL settings body:**
```json
{
  "enabled": true,
  "dollar_amount": 500,
  "auto_trade_paper": false,
  "min_rr": 1.5,
  "max_portfolio_heat": 80.0,
  "daily_loss_limit_pct": 3.0,
  "conviction_scale": true,
  "sms_notify": false
}
```

**`POST /api/thematic/auto/signals/{id}/approve` body:**
```json
{
  "dollar_amount": 500,
  "stop_pct": null,      // null = use signal's stop
  "target_pct": null,    // null = use signal's target
  "fidelity_trade": false,
  "execute_fidelity": false
}
```

---

### Portfolio (Manual Tracker)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Portfolio with live prices + P&L |
| POST | `/api/portfolio/positions` | Add position (deducted from cash) |
| DELETE | `/api/portfolio/positions/{ticker}` | Remove (cash returned at current price) |
| GET | `/api/portfolio/history` | Trade history log |
| GET | `/api/portfolio/paper-trades` | Paper trades from account file |

---

### ML

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ml/summary` | Model stats: ROC, Brier, feature count, training date |
| GET | `/api/ml/feature-importance` | Top features |
| GET | `/api/ml/score/{ticker}` | Score single ticker with deployed model |
| POST | `/api/ml/retrain` | Trigger retrain pipeline (admin) |
| GET | `/api/ml/retrain/status` | Retrain job status |

---

### Scanner

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/scanner/status` | Scan status |
| POST | `/api/scanner/scan` | Trigger breakout scan (async) |
| POST | `/api/scanner/scan/sync` | Synchronous scan (blocks) |
| GET | `/api/scanner/results` | Latest scan results |
| GET | `/api/scanner/candidates` | Current candidate list |

---

### Analysis (LLM Multi-Agent)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/analyze` | Start async LLM analysis for ticker+date |
| POST | `/api/analyze/sync` | Synchronous analysis (blocks) |
| GET | `/api/analyze/status/{job_id}` | Analysis job status |
| GET | `/api/analyze/result/{job_id}` | Analysis result |

---

### Backtest

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/backtest/run` | Start backtest job |
| GET | `/api/backtest/status/{job_id}` | Job status |
| GET | `/api/backtest/results/{job_id}` | Results |
| GET | `/api/backtest/list` | Recent backtest runs |

---

### Fidelity (Live Trading)

Requires `LIVE_TRADING_ENABLED=true` AND `LIVE_TRADING_HARD_BLOCKED=False` in compliance.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fidelity/status` | Session status |
| POST | `/api/fidelity/login` | Start Playwright session |
| POST | `/api/fidelity/logout` | Close session |
| GET | `/api/fidelity/portfolio` | Live Fidelity positions |
| GET | `/api/fidelity/cash` | Scraped cash balance |
| POST | `/api/fidelity/trade` | Place market/limit order |
| POST | `/api/fidelity/thematic-trade` | Thematic conviction trade |
| GET | `/api/fidelity/orders` | Order history |
| POST | `/api/fidelity/cancel/{order_id}` | Cancel order |

---

### History

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/history/trades` | All closed trades |
| GET | `/api/history/performance` | Aggregate P&L, WR, Sharpe |
| GET | `/api/history/by-strategy` | P&L broken down by strategy |

---

### Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | User settings (authenticated) |
| POST | `/api/settings` | Update settings |
| GET | `/api/settings/defaults` | Default config values |

---

### Admin

All require admin role.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | User list |
| POST | `/api/admin/users` | Create user |
| DELETE | `/api/admin/users/{email}` | Delete user |
| GET | `/api/admin/flags` | Feature flags |
| POST | `/api/admin/flags` | Update feature flags |
| GET | `/api/admin/health` | Deep health check |
| GET | `/api/admin/logs` | System logs |
| POST | `/api/admin/view-as` | Impersonate user (manager key required) |
| GET | `/api/admin/paper/runs` | All paper trading run summaries |

---

### Logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/logs/server` | Server log tail |
| GET | `/api/logs/paper` | Paper trading log tail |
| GET | `/api/logs/retrain` | ML retrain log tail |

---

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/system/info` | Version, uptime, config |
| GET | `/api/health` | Basic health check |
| GET | `/ws` | WebSocket for real-time updates |

---

### WebSocket Events

Connect to `/ws` with auth cookie/token. Server pushes JSON events:

```json
{"type": "paper_update", "data": {...}}
{"type": "signal_new", "data": {...}}
{"type": "exit_executed", "data": {...}}
{"type": "scan_complete", "data": {...}}
{"type": "price_alert", "data": {"symbol": "NVDA", "price": 950.0}}
```
