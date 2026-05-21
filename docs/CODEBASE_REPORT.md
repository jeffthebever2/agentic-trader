# TradingAgents Codebase Report

Generated from two subagent codebase passes plus a local verification pass on 2026-05-04.
Updated for v1.0.0 release on 2026-05-21.

## Project Shape

- `pyproject.toml` defines the `tradingagents` Python package at version `1.0.0` with Python `>=3.10`.
- `tradingagents/` contains the core framework.
- `cli/` contains the Typer/Rich command-line app exposed as the `tradingagents` console script.
- `tests/` contains pytest tests for memory logging, checkpoint resume, model validation, structured agents, signal processing, and ticker handling.
- `scripts/smoke_structured_output.py` is a provider smoke test for structured output.
- `Dockerfile` and `docker-compose.yml` provide container entry points.

## Entry Points

- Installed CLI: `tradingagents`, configured in `pyproject.toml`.
- Source CLI: `python3 -m cli.main analyze`.
- Programmatic example: `main.py`, which constructs `TradingAgentsGraph` and calls `propagate`.
- Core public API: `tradingagents/graph/trading_graph.py`, class `TradingAgentsGraph`.

## Core Architecture

- `tradingagents/graph/trading_graph.py` orchestrates LLM clients, memory logging, tool nodes, LangGraph compilation, checkpointing, propagation, reflection, signal processing, and final state logging.
- `tradingagents/graph/setup.py` builds the LangGraph workflow: selected analysts, bull/bear research debate, research manager, trader, risk debate, portfolio manager, then end.
- `tradingagents/graph/propagation.py` creates the initial graph state and invocation args.
- `tradingagents/graph/checkpointer.py` provides SQLite-backed LangGraph checkpoint/resume support.
- `tradingagents/agents/` contains analyst, researcher, trader, risk-management, and manager agent factories.
- `tradingagents/agents/schemas.py` defines Pydantic structured-output schemas.
- `tradingagents/dataflows/interface.py` routes stock, indicator, fundamental, news, and transaction requests to configured data vendors.
- `tradingagents/llm_clients/factory.py` builds provider-specific LLM clients.

## Verification

- `git status --short` cannot run because this extracted package is not a git repository.
- `pytest -q` cannot run because `pytest` is not installed in the active environment.
- `python` is not available on PATH in this environment, but `python3` is.
- `python3 -m compileall tradingagents cli tests main.py` passed.

## Fixes Made

- Added explicit runtime dependencies for direct imports:
  - `pydantic`, used by `cli/models.py`, `tradingagents/agents/schemas.py`, and `tradingagents/agents/utils/structured.py`.
  - `python-dotenv`, used by `main.py` and `cli/main.py`.
- Added a `dev` optional dependency extra containing `pytest`.

## Remaining Fix Candidates

1. Regenerate `uv.lock`; it appears stale against `pyproject.toml` and records package metadata that does not match version `1.0.0`.
2. Review the Docker Ollama profile. `docker-compose.yml` sets `LLM_PROVIDER=ollama`, but the code path found during exploration does not appear to read `LLM_PROVIDER`.
3. Review README enterprise-provider wording. The README mentions AWS Bedrock, while the inspected LLM factory supports Azure but not Bedrock.
4. Consider whether the CLI should call the same `TradingAgentsGraph.propagate` path as programmatic usage, or intentionally keep its direct streaming path with equivalent memory-log and checkpoint behavior.
