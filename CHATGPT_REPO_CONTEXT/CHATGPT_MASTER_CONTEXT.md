# TradingAgents Master Context for ChatGPT

This document provides a complete overview of the `TradingAgents` repository to guide future architecture, UI redesign, and prompt generation.

## 1. Project Purpose
TradingAgents is an AI-powered algorithmic trading system. It utilizes large language models (LLMs), machine learning (ML), and reinforcement learning (RL) to analyze markets, generate signals, and perform automated or paper trading. It includes a comprehensive backtesting engine, live paper trading integration (e.g., Fidelity, Webull), risk management rules, and a web-based dashboard for monitoring and administration.

## 2. Main Architecture
The backend is split into two primary components:
1. **Web Server (`web/`)**: An API layer (Flask/FastAPI) that serves the static frontend files and exposes routing logic (`web/api/*.py`). It handles authentication, connects to databases (`d1_store.py`, `supabase_store.py`), and translates UI actions into core system commands.
2. **Core Package (`tradingagents/`)**: A modular Python package containing the actual business logic. It includes the AI agents, backtesting engine, data fetching modules, risk management, and ML/RL logic.

## 3. Key Folders
- `tradingagents/`: Core Python backend package containing trading logic and agent definitions.
  - `agents/`: Submodules for analysts, managers, researchers, risk_mgmt, and trader.
  - `llm_clients/`: Client code for LLM interactions (e.g., OpenRouter).
  - `backtesting/` & `rl/`: Core system logic and advanced model pipelines.
- `web/`: The web application layer containing the API routes and frontend dashboard.
  - `static/`: Frontend HTML, CSS, and JS files.
  - `api/`: Web server backend routes.
- `ml_models/` & `rl_models/`: Storage for trained models.
- `scripts/` & `cli/`: Utility and deployment scripts.

## 4. Key Files
- **`main.py`**: Primary entry point for running core trading agents via CLI.
- **`backtest.py`**: Orchestrates backtesting runs against historical data.
- **`run_web.py`**: Starts the web dashboard server.
- **`web/app.py`**: Main application factory/router for the web backend.
- **`tradingagents/agents/schemas.py`**: Defines base agent structures and data schemas.
- **`tradingagents/metrics.py`**: Calculates trading performance metrics.

## 5. Frontend/UI Structure
Located in `web/static/`, built as a standard SPA (HTML/JS/CSS). Interacts with backend purely via REST API calls. 
- **`index.html`**: The main dashboard page structure and markup.
- **`premium-static-ui.css`**: Custom stylesheet overriding defaults.
- **`premium-static-ui.js`**: Core application logic (API calls, charting, DOM updates).
- **DOM Dependencies**: JavaScript relies heavily on existing `<canvas id="...">` elements, table body IDs, and form attributes. **Do not alter these ID bindings.**

## 6. Backend/Python Structure
The backend routing logic lives in `web/api/` (e.g., `paper.py`, `backtest.py`, `fidelity.py`, `settings.py`, `analysis.py`). These routes import modules from the core `tradingagents/` package to execute commands.

## 7. Trading / Agent / LLM Flow
1. **Market Data**: Pulled via `tradingagents/dataflows/`.
2. **Analysis**: Handled by `tradingagents/agents/analysts/` and `screening/`.
3. **Signal Generation**: Passed to `tradingagents/agents/trader/` to generate buy/sell signals.
4. **Execution**: Signals clear `tradingagents/agents/risk_mgmt/` and execute via broker integrations (e.g., `web/api/fidelity.py`).
5. **LLM Usage**: LLM agents read news/filings, construct prompts, route through `tradingagents/llm_clients/`, and output structured data via `schemas.py`.

## 8. Important Configs
- **`.env`**: Environment secrets and API keys.
- **`tradingagents/default_config.py`**: Global system settings, thresholds, and default agent behaviors.
- **`tradingagents/openrouter_usage.py`**: Manages LLM token limits and billing logic.

## 9. UI / Design Problems
The current frontend (`index.html`, `tailwind.min.css`) relies entirely on generic, overused boilerplate.
- **shadcn/ui Default Gravity**: Abstract components lacking unique domain branding.
- **3-Card Feature Grids & Pill Badges**: Cookie-cutter landing-page structures.
- **Generic Copywriting**: Heavy use of vague, hype-driven SaaS copy ("seamless", "unlock", "revolutionize").

## 10. Anti-AI-Design Issues
An automated audit flagged the UI as **OBVIOUSLY AI / TEMPLATE-CODED**.
- **Bad Colors/Surfaces**: Default dark SaaS background, generic `border-white/10` glassmorphism, and unnecessary noise/blobs.
- **Weak Motion**: Lazy `transition-all duration-300` and scroll-reveal tropes applied uniformly without product rationale.
- **Lack of Depth**: Missing focus states, reduced-motion support, and real UI states (loading, empty, offline, error).

## 11. Files Safe to Edit
- `web/static/index.html`
- `web/static/premium-static-ui.css`
- `web/static/premium-static-ui.js`
- `web/api/settings.py`
- `web/api/analysis.py`
- `tradingagents/agents/analysts/*`

## 12. Files Dangerous to Edit
- **Broker Integrations**: `web/api/fidelity.py`, `web/api/webull_portfolio.py`.
- **Core Pipelines**: `tradingagents/agents/__init__.py`, `tradingagents/agents/schemas.py`, `tradingagents/metrics.py`.
- **Risk Management**: `tradingagents/agents/risk_mgmt/*`.

## 13. Files/Folders to Ignore
- `.venv`, `.venv-torch`, `__pycache__`, `.DS_Store`, `.git`
- `dist/`, `build/`, `tradingagents.egg-info/`, `tmp/`, `scratch/`
- `.backtest_cache/`, `backtest_results_*.json`, `backtest_charts_*/`
- Vendor files: `web/static/tailwind.min.css`, `web/static/chart.umd.min.js`, `web/static/chartjs-adapter-date-fns.min.js`, `web/static/chartjs-financial.min.js`, `web/static/marked.min.js`.

## 14. Existing Audit Scores / Results
- **AI Design Risk**: `86.0/100` (Obviously AI/Template-coded)
- **Human Design Score**: `14.0/100`
- Scored perfectly bad (100% cap filled) in Composition, Component Clichés, Palette Tells, Surface Glows, Motion Taste, and Copywriting. 
- Scored `0%` in State Depth, Accessibility Basics, and Design System Maturity.

## 15. Rules for Future Prompts
When proposing or implementing UI/architecture changes:
1. **Preserve Logic**: Do not break `id` attributes or backend endpoints in `premium-static-ui.js`.
2. **Break the Rhythm**: Abandon generic landing-page structures (Hero + 3-Cards). Design dense, terminal-style, or multi-pane data layouts.
3. **Write Authored CSS**: Eliminate generic Tailwind styling strings. Author specific classes in `premium-static-ui.css` using semantic, domain-specific color tokens (`--color-bullish`).
4. **Purposeful States**: Always account for loading, empty, and error states. Ensure accessibility (focus rings, reduced motion).
5. **No AI Tropes**: Strip out generic hype copywriting, glowing blobs, glassmorphism templates, and universally applied `transition-all`.

## 16. Recommended Next Prompts/Tasks
- *"Write a highly detailed, domain-specific CSS architecture for `premium-static-ui.css` that replaces generic Tailwind utility classes with a rigorous trading terminal aesthetic."*
- *"Redesign `index.html` to remove the 3-card grid formula and generic landing page structure, replacing it with a dense, data-first monitoring layout. Ensure all existing JS element IDs are retained."*
- *"Implement full state management in `premium-static-ui.js` and `index.html` to handle loading, offline, and error states elegantly, without using generic animations."*
