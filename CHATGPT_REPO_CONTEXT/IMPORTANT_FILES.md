# Important Files

The following are the most important files in the repository for understanding the core functionality.

1. **`main.py`**
   - **Purpose:** Primary entry point for running the core trading agents via CLI.
   - **Why it matters:** Initializes the system, parses arguments, and kicks off the trading lifecycle.
   - **Safe to edit:** Maybe
   - **What could break:** Command-line usage, job scheduling, or top-level execution.

2. **`backtest.py`** (Root)
   - **Purpose:** Orchestrates backtesting runs.
   - **Why it matters:** Core logic for testing strategies against historical data before deploying to paper/live.
   - **Safe to edit:** Maybe
   - **What could break:** The entire backtesting pipeline and evaluation metrics.

3. **`run_web.py`**
   - **Purpose:** Starts the web dashboard server.
   - **Why it matters:** It's the entry point for the UI and backend API.
   - **Safe to edit:** Yes
   - **What could break:** Server startup, port binding.

4. **`web/app.py`**
   - **Purpose:** The main application factory/router for the web backend.
   - **Why it matters:** Hooks up all the blueprints from `web/api/` and serves the frontend.
   - **Safe to edit:** Maybe
   - **What could break:** Routing, middleware, or web application startup.

5. **`web/api/paper.py`**
   - **Purpose:** API routes for paper trading.
   - **Why it matters:** Connects the UI to paper trading execution and tracking.
   - **Safe to edit:** Maybe
   - **What could break:** Paper trading operations and dashboard syncing.

6. **`web/api/backtest.py`**
   - **Purpose:** API routes for triggering and viewing backtests from the UI.
   - **Why it matters:** Allows the frontend to start backtests and stream results.
   - **Safe to edit:** Yes
   - **What could break:** UI backtest integration.

7. **`web/api/fidelity.py`** / **`web/api/webull_portfolio.py`**
   - **Purpose:** Integration with specific broker APIs.
   - **Why it matters:** Handles live account connection, portfolio fetching, and trade execution.
   - **Safe to edit:** No (unless careful)
   - **What could break:** Broker authentication and live/paper order execution.

8. **`tradingagents/default_config.py`**
   - **Purpose:** Contains all default configuration values.
   - **Why it matters:** Central source of truth for settings, thresholds, and limits.
   - **Safe to edit:** Yes (with caution)
   - **What could break:** Global agent behavior, risk limits.

9. **`tradingagents/agents/__init__.py`** & **`tradingagents/agents/schemas.py`**
   - **Purpose:** Defines the base agent structures and data schemas.
   - **Why it matters:** All specific agents inherit or use these data structures.
   - **Safe to edit:** No
   - **What could break:** Serialization, validation, and inter-agent communication.

10. **`tradingagents/agents/trader/` (files within)**
    - **Purpose:** The actual execution agents that decide when and what to buy/sell.
    - **Why it matters:** Contains the core trading logic and rules.
    - **Safe to edit:** Maybe
    - **What could break:** Trading decisions, position sizing, profitability.

11. **`tradingagents/agents/analysts/` (files within)**
    - **Purpose:** Agents responsible for technical, fundamental, and sentiment analysis.
    - **Why it matters:** They feed signals to the trader agents.
    - **Safe to edit:** Yes
    - **What could break:** Signal generation quality.

12. **`tradingagents/agents/risk_mgmt/` (files within)**
    - **Purpose:** Enforces risk limits, stop losses, and exposure.
    - **Why it matters:** Protects the portfolio from severe drawdowns.
    - **Safe to edit:** No
    - **What could break:** Risk controls, leading to unbounded losses.

13. **`tradingagents/agents/news_impact_filter.py`**
    - **Purpose:** Analyzes news sentiment and impact on specific assets.
    - **Why it matters:** Filters out assets with adverse news or prioritizes high-catalyst stocks.
    - **Safe to edit:** Yes
    - **What could break:** Sentiment scoring.

14. **`tradingagents/llm_clients/` (files within)**
    - **Purpose:** Interfaces with external LLM providers (e.g., OpenAI, Anthropic, OpenRouter).
    - **Why it matters:** The brain of the qualitative analysis relies on these.
    - **Safe to edit:** Maybe
    - **What could break:** LLM connectivity, prompt handling, token limits.

15. **`tradingagents/rl/`** & **`web/api/rl.py`**
    - **Purpose:** Reinforcement learning environment and logic.
    - **Why it matters:** Experimental or advanced models learning to trade dynamically.
    - **Safe to edit:** Maybe
    - **What could break:** Model training, inference, and RL pipelines.

16. **`web/api/analysis.py`**
    - **Purpose:** Web routes for triggering deep analysis of tickers.
    - **Why it matters:** Used by the UI to show detailed ticker reports.
    - **Safe to edit:** Yes
    - **What could break:** The analysis page on the dashboard.

17. **`web/auth.py`** & **`web/api/auth_routes.py`**
    - **Purpose:** Handles user authentication, login, and sessions for the web app.
    - **Why it matters:** Secures the dashboard.
    - **Safe to edit:** No
    - **What could break:** User logins, security vulnerabilities.

18. **`web/api/settings.py`**
    - **Purpose:** Manages user or system settings from the UI.
    - **Why it matters:** Persists configuration changes.
    - **Safe to edit:** Yes
    - **What could break:** UI settings updates.

19. **`tradingagents/openrouter_usage.py`**
    - **Purpose:** Tracks and manages API usage and costs for OpenRouter.
    - **Why it matters:** Prevents massive API bills and rate limiting.
    - **Safe to edit:** Maybe
    - **What could break:** Cost tracking and billing limits.

20. **`tradingagents/metrics.py`**
    - **Purpose:** Calculates trading performance metrics (Sharpe, Sortino, Drawdown).
    - **Why it matters:** Used universally to judge strategy success.
    - **Safe to edit:** No
    - **What could break:** Mathematical errors in performance reporting.

*(Note: Additional important files include `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `pyproject.toml`, which define the deployment and dependency environments.)*
