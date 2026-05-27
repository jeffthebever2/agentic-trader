# Repository Map: TradingAgents

## What this project is
TradingAgents is an AI-powered algorithmic trading system. It utilizes large language models (LLMs), machine learning (ML), and reinforcement learning (RL) to analyze markets, generate signals, and perform automated or paper trading. It includes a comprehensive backtesting engine, live paper trading integration (e.g., Fidelity, Webull), risk management rules, and a web-based dashboard for monitoring and administration.

## Main Folders and Their Roles
- `tradingagents/`: The core Python backend package containing the trading logic, agent definitions, and AI interactions.
- `web/`: The web application layer containing the API routes and frontend dashboard.
- `ml_models/`: Storage for trained machine learning models used in market prediction.
- `rl_models/`: Storage for reinforcement learning models.
- `tests/`: Test suite for the application.
- `scripts/`: Utility and deployment scripts.
- `cli/`: Command-line interface tools.
- `docs/`: Project documentation.
- `tools/`: Additional tooling and resources.

## Frontend Location
- **Path:** `web/static/`
- Contains HTML, CSS, and JavaScript files for the dashboard UI.

## Backend/Python Package Location
- **Path:** `tradingagents/` (Core trading system and logic)
- **Path:** `web/api/` (Web server backend routes for handling dashboard requests)

## Agent/Trading System Location
- **Path:** `tradingagents/agents/` (Contains submodules for analysts, managers, researchers, risk_mgmt, and trader)
- **Path:** `tradingagents/backtesting/` (Backtesting logic)
- **Path:** `tradingagents/rl/` (Reinforcement learning integration)
- **Path:** `tradingagents/llm_clients/` (Client code for LLM interactions, e.g., OpenRouter)

## Config/Deployment Files
- `.env`, `.env.example`, `.env.enterprise.example`: Environment variables and secrets.
- `docker-compose.yml`, `Dockerfile`: Containerization and deployment configuration.
- `requirements.txt`, `pyproject.toml`, `uv.lock`: Python dependency management.
- `Agentic Trader.spec`: PyInstaller spec file for building executables.
- `tradingagents/default_config.py`, `tradingagents/logging_config.py`: Default application settings.

## Files/Folders to Ignore
- `.venv`, `.venv-torch`, `__pycache__`
- `.DS_Store`, `.git`, `.github`
- `dist/`, `build/`, `tradingagents.egg-info/`
- `tmp/`, `scratch/`, `.backtest_cache/`, `.pytest_cache/`, `.ruff_cache/`
- Generated `backtest_results_*.json` and `backtest_charts_*/` folders.
