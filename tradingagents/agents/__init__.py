"""Lazy exports for TradingAgents agent factories.

Keeping this package import-light lets pure utility modules such as memory,
portfolio risk checks, and execution helpers run without importing the full LLM
provider stack.
"""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "AgentState": "tradingagents.agents.utils.agent_states",
    "InvestDebateState": "tradingagents.agents.utils.agent_states",
    "RiskDebateState": "tradingagents.agents.utils.agent_states",
    "create_msg_delete": "tradingagents.agents.utils.agent_utils",
    "create_bear_researcher": "tradingagents.agents.researchers.bear_researcher",
    "create_bull_researcher": "tradingagents.agents.researchers.bull_researcher",
    "create_research_manager": "tradingagents.agents.managers.research_manager",
    "create_fundamentals_analyst": "tradingagents.agents.analysts.fundamentals_analyst",
    "create_market_analyst": "tradingagents.agents.analysts.market_analyst",
    "create_neutral_debator": "tradingagents.agents.risk_mgmt.neutral_debator",
    "create_news_analyst": "tradingagents.agents.analysts.news_analyst",
    "create_aggressive_debator": "tradingagents.agents.risk_mgmt.aggressive_debator",
    "create_portfolio_manager": "tradingagents.agents.managers.portfolio_manager",
    "create_conservative_debator": "tradingagents.agents.risk_mgmt.conservative_debator",
    "create_social_media_analyst": "tradingagents.agents.analysts.social_media_analyst",
    "create_trader": "tradingagents.agents.trader.trader",
}


__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    value = getattr(module, name)
    globals()[name] = value
    return value
