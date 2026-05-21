"""Tests for error handling and validation."""

import pytest
from unittest.mock import patch

from tradingagents.graph.trading_graph import TradingAgentsGraph


class TestErrorHandling:
    """Test error handling in TradingAgentsGraph."""

    def test_propagate_invalid_company_name(self):
        """Test that propagate raises ValueError for invalid company name."""
        graph = TradingAgentsGraph()

        with pytest.raises(ValueError, match="Invalid company_name"):
            graph.propagate("", "2024-01-01")

        with pytest.raises(ValueError, match="Invalid company_name"):
            graph.propagate(None, "2024-01-01")

        with pytest.raises(ValueError, match="Invalid company_name"):
            graph.propagate(123, "2024-01-01")

    def test_propagate_invalid_trade_date(self):
        """Test that propagate raises ValueError for invalid trade date."""
        graph = TradingAgentsGraph()

        with pytest.raises(ValueError, match="Invalid trade_date"):
            graph.propagate("AAPL", "")

        with pytest.raises(ValueError, match="Invalid trade_date"):
            graph.propagate("AAPL", None)

        with pytest.raises(ValueError, match="Invalid trade_date"):
            graph.propagate("AAPL", 123)

    def test_propagate_invalid_date_format(self):
        """Test that propagate raises ValueError for invalid date format."""
        graph = TradingAgentsGraph()

        with pytest.raises(ValueError, match="Invalid date format"):
            graph.propagate("AAPL", "2024/01/01")

        with pytest.raises(ValueError, match="Invalid date format"):
            graph.propagate("AAPL", "not-a-date")

    @patch('tradingagents.graph.trading_graph.TradingAgentsGraph._run_graph')
    def test_propagate_runtime_error_handling(self, mock_run_graph):
        """Test that propagate handles runtime errors properly."""
        graph = TradingAgentsGraph()
        mock_run_graph.side_effect = Exception("Test error")

        with pytest.raises(RuntimeError, match="Propagation failed"):
            graph.propagate("AAPL", "2024-01-01")

    @patch('tradingagents.graph.trading_graph.TradingAgentsGraph._run_graph')
    def test_propagate_successful_execution(self, mock_run_graph):
        """Test successful propagation execution."""
        graph = TradingAgentsGraph()
        expected_result = ({"test": "data"}, "BUY")
        mock_run_graph.return_value = expected_result

        result = graph.propagate("AAPL", "2024-01-01")
        assert result == expected_result
        mock_run_graph.assert_called_once_with("AAPL", "2024-01-01")