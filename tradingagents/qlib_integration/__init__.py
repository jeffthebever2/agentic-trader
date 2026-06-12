"""Qlib integration layer for TradingAgents.

Provides:
  - QlibDataAdapter   — converts yfinance/raw OHLCV data into qlib-compatible format
  - QlibResearchEngine — runs qlib alpha factors and model tournament
  - smoke_test()       — verifies qlib is installed and functional

Qlib is Microsoft's Quantitative Investment Library (pyqlib 0.9.8+).
Install: pip install "pyqlib @ git+https://github.com/microsoft/qlib.git"
"""
from tradingagents.qlib_integration.adapter import QlibDataAdapter
from tradingagents.qlib_integration.engine import QlibResearchEngine
from tradingagents.qlib_integration.smoke import smoke_test

__all__ = ["QlibDataAdapter", "QlibResearchEngine", "smoke_test"]
