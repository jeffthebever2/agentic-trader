import logging
import re

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .duckduckgo_search import (
    get_news_duckduckgo,
    get_global_news_duckduckgo,
)
from .sec_fundamentals import (
    get_fundamentals as get_sec_fundamentals,
    get_balance_sheet as get_sec_balance_sheet,
    get_cashflow as get_sec_cashflow,
    get_income_statement as get_sec_income_statement,
)
from .fmp import (
    get_fundamentals as get_fmp_fundamentals,
    get_balance_sheet as get_fmp_balance_sheet,
    get_cashflow as get_fmp_cashflow,
    get_income_statement as get_fmp_income_statement,
    get_insider_transactions as get_fmp_insider_transactions,
    get_news as get_fmp_news,
)
from .social_signals import get_social_sentiment as get_social_sentiment_signals
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError

# Configuration and routing logic
from .config import get_config
from tradingagents.dataflows.cache import get_cache
from tradingagents.metrics import get_metrics, record_api_call

logger = logging.getLogger(__name__)

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "social_data": {
        "description": "Retail/social sentiment data",
        "tools": [
            "get_social_sentiment",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "duckduckgo",
    "sec",
    "fmp",
    "social",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "sec": get_sec_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "fmp": get_fmp_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "sec": get_sec_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "fmp": get_fmp_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "sec": get_sec_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "fmp": get_fmp_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "sec": get_sec_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "fmp": get_fmp_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "duckduckgo": get_news_duckduckgo,
        "alpha_vantage": get_alpha_vantage_news,
        "fmp": get_fmp_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "duckduckgo": get_global_news_duckduckgo,
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "fmp": get_fmp_insider_transactions,
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
    "get_social_sentiment": {
        "social": get_social_sentiment_signals,
        "duckduckgo": get_social_sentiment_signals,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def _build_cache_key(method: str, args: tuple, kwargs: dict, vendor_config: str = "") -> str:
    """Build a stable cache key for a dataflow call."""
    import hashlib
    import json

    payload = {
        "method": method,
        "vendor_config": vendor_config,
        "args": args,
        "kwargs": kwargs,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    config = get_config()
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    cache_enabled = config.get("data_cache_enabled", True)
    cache_ttl = config.get("data_cache_ttl_hours", 24)
    cache_instance = get_cache(cache_ttl) if cache_enabled else None
    cache_key = _build_cache_key(method, args, kwargs, vendor_config) if cache_instance else None

    if cache_instance is not None:
        cached_value = cache_instance.get(cache_key)
        if cached_value is not None:
            get_metrics().increment_counter("data_cache_hit")
            record_api_call("cache", method, True)
            logger.debug("Cache hit for %s", method)
            return cached_value

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    errors = []
    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)
            if _is_weak_result(result):
                errors.append(f"{vendor}: weak result")
                continue
            record_api_call(vendor, method, True)
            if cache_instance is not None and result is not None:
                cache_instance.set(cache_key, result)
                get_metrics().increment_counter("data_cache_miss")
            return result
        except AlphaVantageRateLimitError:
            errors.append(f"{vendor}: rate limited")
            record_api_call(vendor, method, False)
            continue  # Only rate limits trigger fallback
        except Exception as exc:
            errors.append(f"{vendor}: {_sanitize_error(exc)}")
            record_api_call(vendor, method, False)
            continue

    logger.warning("All data vendors failed for %s: %s", method, "; ".join(errors))
    if cache_instance is not None:
        get_metrics().increment_counter("data_cache_miss")
    return _degraded_result(method, category, fallback_vendors, errors, *args, **kwargs)


def _is_weak_result(result) -> bool:
    """Return True for empty/error result strings that should trigger fallback."""
    if result is None:
        return True
    if isinstance(result, (list, dict)):
        if not result:
            return True
        if isinstance(result, dict) and result.get("error"):
            return True
        return False
    if not isinstance(result, str):
        return False
    stripped = result.strip()
    if not stripped:
        return True
    weak_prefixes = (
        "error ",
        "error fetching",
        "error retrieving",
        "no data",
        "no news",
        "no social",
        "no sec",
        "no fmp",
        "fmp skipped",
        "duckduckgo search unavailable",
        "yt-dlp unavailable",
    )
    return stripped.lower().startswith(weak_prefixes)


def _degraded_result(
    method: str,
    category: str,
    vendors: list[str],
    errors: list[str],
    *args,
    **kwargs,
) -> str:
    """Return a non-throwing report when every configured provider fails."""
    compact_args = ", ".join(str(arg) for arg in args[:3])
    if kwargs:
        compact_args = f"{compact_args}; kwargs={{{', '.join(sorted(kwargs))}}}" if compact_args else f"kwargs={{{', '.join(sorted(kwargs))}}}"
    tried = ", ".join(vendors) if vendors else "none"
    details = "\n".join(f"- {err}" for err in errors) if errors else "- No compatible vendors configured"
    return (
        "## Data Source Degraded\n\n"
        f"- Tool: `{method}`\n"
        f"- Category: `{category}`\n"
        f"- Request: {compact_args or 'n/a'}\n"
        f"- Providers tried: {tried}\n\n"
        "Every configured provider failed or returned empty data. Continue the analysis using other "
        "available evidence, reduce confidence, and avoid aggressive position sizing until this data "
        "is available again.\n\n"
        "Provider diagnostics:\n"
        f"{details}"
    )


def _sanitize_error(exc: Exception | str) -> str:
    """Redact common secret-bearing query params and keep diagnostics compact."""
    text = str(exc) or exc.__class__.__name__
    replacements = (
        (r"(?i)(api[_-]?key=)[^&\s]+", r"\1[REDACTED]"),
        (r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]"),
        (r"(?i)(token=)[^&\s]+", r"\1[REDACTED]"),
        (r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
        (r"sk-[A-Za-z0-9][A-Za-z0-9_-]+", "sk-[REDACTED]"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text[:500]
