"""Configuration boundary for the trading system.

This package is the single place environment/config parsing lives. Application
and domain code should read config through these typed accessors instead of
calling ``os.getenv`` and re-implementing string coercion at every call site.
"""
from tradingagents.config.env import (
    env_bool,
    env_int,
    env_float,
    env_str,
    env_list,
)

__all__ = ["env_bool", "env_int", "env_float", "env_str", "env_list"]
