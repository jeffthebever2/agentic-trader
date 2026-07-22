import os

from tradingagents.config import env_bool

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "data_cache_enabled": env_bool("TRADINGAGENTS_DATA_CACHE_ENABLED", True),
    "data_cache_ttl_hours": int(os.getenv("TRADINGAGENTS_DATA_CACHE_TTL_HOURS", "24")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    "structured_memory_dir": os.getenv("TRADINGAGENTS_STRUCTURED_MEMORY_DIR", os.path.join(_TRADINGAGENTS_HOME, "memory")),
    "portfolio_state_path": os.getenv("TRADINGAGENTS_PORTFOLIO_STATE_PATH", os.path.join(_TRADINGAGENTS_HOME, "portfolio", "positions.json")),
    "trade_log_path": os.getenv("TRADINGAGENTS_TRADE_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "logs", "trade_results.jsonl")),
    "paper_decision_log_path": os.getenv("TRADINGAGENTS_PAPER_DECISION_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "logs", "paper_decisions.jsonl")),
    "alt_data_config_path": os.getenv("TRADINGAGENTS_ALT_DATA_CONFIG", os.path.join(_TRADINGAGENTS_HOME, "alt_data_sources.json")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # Paper portfolio/risk settings
    "starting_cash": float(os.getenv("TRADINGAGENTS_STARTING_CASH", "100000")),
    "max_positions": int(os.getenv("TRADINGAGENTS_MAX_POSITIONS", "10")),
    "max_position_size": float(os.getenv("TRADINGAGENTS_MAX_POSITION_SIZE", "0.05")),
    "max_sector_exposure": float(os.getenv("TRADINGAGENTS_MAX_SECTOR_EXPOSURE", "0.30")),
    "max_daily_loss": float(os.getenv("TRADINGAGENTS_MAX_DAILY_LOSS", "-0.05")),
    "max_monthly_loss": float(os.getenv("TRADINGAGENTS_MAX_MONTHLY_LOSS", "-0.15")),
    "paper_trading_enabled": env_bool("TRADINGAGENTS_PAPER_TRADING_ENABLED", True),
    "live_broker_enabled": False,
    # Quota-protected paid/free-tier enrichers
    "fmp_enabled": env_bool("TRADINGAGENTS_FMP_ENABLED", True),
    "fmp_daily_limit": int(os.getenv("TRADINGAGENTS_FMP_DAILY_LIMIT", "250")),
    "fmp_reserve_calls": int(os.getenv("TRADINGAGENTS_FMP_RESERVE_CALLS", "25")),
    # LLM settings
    "llm_provider": os.getenv("LLM_PROVIDER", "cloudflare"),
    "deep_think_llm": os.getenv("CLOUDFLARE_DEFAULT_DEEP_MODEL", "@cf/openai/gpt-oss-120b"),
    "quick_think_llm": os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "sec,yfinance,fmp",  # Options: sec, fmp, alpha_vantage, yfinance
        "news_data": "duckduckgo,yfinance,alpha_vantage",  # Options: duckduckgo, fmp, alpha_vantage, yfinance
        "social_data": "social,duckduckgo",
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # RL / TD3 settings
    # Path to a trained TD3 checkpoint directory (produced by scripts/train_rl_agent.py).
    # When the directory exists and contains meta.json, the RL signal is injected into
    # the execution context automatically. Set to None to disable.
    "rl_checkpoint_dir": os.getenv("TRADINGAGENTS_RL_CHECKPOINT", "rl_models/td3_checkpoint"),
    "rl_device": os.getenv("TRADINGAGENTS_RL_DEVICE", "cpu"),
}
