import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog


@pytest.mark.unit
def test_structured_memory_records_accuracy(tmp_path):
    log = TradingMemoryLog({
        "memory_log_path": str(tmp_path / "memory.md"),
        "structured_memory_dir": str(tmp_path),
    })

    log.store_decision("AAPL", "2026-01-01", "Rating: Buy\nConfidence: 70%")
    log.log_outcome("AAPL", "2026-01-01", "2026-01-10", 100, 110, "TAKE_PROFIT")
    log.log_outcome("AAPL", "2026-02-01", "2026-02-10", 100, 95, "STOP_LOSS")

    stats = log.get_decision_accuracy("AAPL")["AAPL"]
    assert stats["total_trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 0.5
    assert "profit factor" in log.get_performance_context("AAPL")


@pytest.mark.unit
def test_past_context_includes_structured_performance_without_markdown_entries(tmp_path):
    log = TradingMemoryLog({
        "memory_log_path": str(tmp_path / "memory.md"),
        "structured_memory_dir": str(tmp_path),
    })
    log.log_outcome("MSFT", "2026-01-01", "2026-01-10", 100, 105, "TAKE_PROFIT")

    context = log.get_past_context("MSFT")
    assert "Past structured performance" in context
