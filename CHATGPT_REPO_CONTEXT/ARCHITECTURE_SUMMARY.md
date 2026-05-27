# Architecture Summary

## Frontend Structure
The frontend is located in `web/static/` and is built as a traditional web application. It interacts with the backend strictly via RESTful API calls to endpoints defined in `web/api/`. The UI provides views for dashboard summaries, active positions, backtest initiation and results, settings, and deep ticker analysis.

## Backend Structure
The backend is split into two primary components:
1. **Web Server (`web/`)**: An API layer (likely Flask or FastAPI) that serves the static frontend files and exposes routing logic (`web/api/*.py`). It handles authentication, connects to databases (`d1_store.py`, `supabase_store.py`), and translates UI actions into core system commands.
2. **Core Package (`tradingagents/`)**: A modular Python package containing the actual business logic. It includes the AI agents, backtesting engine, data fetching modules, risk management, and ML/RL logic.

## Data Flow
1. **Market Data Integration:** The system pulls historical or live market data (price, volume, news) using data flows defined in `tradingagents/dataflows/`.
2. **Analysis:** The data is passed to `tradingagents/agents/analysts/` and `tradingagents/screening/` for technical, fundamental, and sentiment analysis.
3. **Signal Generation:** Analyst agents feed data to the `tradingagents/agents/trader/` modules, which generate buy/sell signals.
4. **Execution:** Signals pass through `tradingagents/agents/risk_mgmt/` to ensure they comply with risk rules. Approved signals are sent to broker integrations (e.g., `web/api/fidelity.py`, `web/api/paper.py`) for execution.
5. **Persistence:** Results and portfolio states are saved to databases or cache files.

## AI/LLM Flow
The system relies heavily on LLMs for qualitative analysis (e.g., reading news, interpreting SEC filings). 
- Agents construct prompts based on market data.
- Prompts are routed through `tradingagents/llm_clients/` (via OpenRouter or direct APIs).
- The LLM responses are parsed, structured (often using `schemas.py`), and converted into actionable metrics (e.g., a sentiment score of 1-10) which are then consumed by the trading logic.

## Trading / Research Flow
- **Research:** Runs continuously or on-demand, scanning the market, analyzing news (`news_impact_filter.py`), and filtering tickers.
- **Backtesting:** Users can run historical simulations (`backtest.py` or via UI). This flow uses historical data and bypasses live brokers, simulating fills and tracking metrics (`tradingagents/backtest_analyzer.py`).
- **Paper/Live Trading:** Runs in real-time. Connects to live data feeds, updates portfolio state, and logs virtual or actual trades.

## Where UI Connects to Backend
The UI makes HTTP requests to endpoints mapped in `web/app.py`, which delegates to the specific route files in `web/api/` (e.g., `web/api/backtest.py`, `web/api/paper.py`, `web/api/market.py`). These API functions then import and call the necessary modules from the `tradingagents` package.

## Where Settings / Config Live
- **Global / System Configuration:** `tradingagents/default_config.py` and `tradingagents/logging_config.py`.
- **Environment Secrets:** `.env` file (API keys, database URLs).
- **User/Dashboard Settings:** Handled via `web/api/settings.py` and stored in the application database (Supabase/D1).
