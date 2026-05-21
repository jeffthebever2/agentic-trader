"""Performance monitoring and metrics collection."""

import time
from contextlib import contextmanager
from typing import Dict, Any, Optional
from collections import defaultdict

from tradingagents.logging_config import get_logger

logger = get_logger(__name__)


class MetricsCollector:
    """Collect and report performance metrics."""

    def __init__(self):
        self.metrics: Dict[str, list] = defaultdict(list)
        self.counters: Dict[str, int] = defaultdict(int)

    def record_timing(self, operation: str, duration: float) -> None:
        """Record the duration of an operation."""
        self.metrics[operation].append(duration)
        logger.debug("Recorded timing for %s: %.3f seconds", operation, duration)

    def increment_counter(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        self.counters[name] += value

    def get_average_time(self, operation: str) -> Optional[float]:
        """Get the average time for an operation."""
        times = self.metrics.get(operation, [])
        return sum(times) / len(times) if times else None

    def get_total_count(self, name: str) -> int:
        """Get the total count for a counter."""
        return self.counters.get(name, 0)

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all metrics."""
        summary = {}
        for operation, times in self.metrics.items():
            if times:
                summary[f"{operation}_avg"] = sum(times) / len(times)
                summary[f"{operation}_count"] = len(times)
                summary[f"{operation}_total"] = sum(times)

        summary.update(self.counters)
        return summary

    def reset(self) -> None:
        """Reset all metrics."""
        self.metrics.clear()
        self.counters.clear()


# Global metrics instance
_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector."""
    return _metrics


@contextmanager
def timed_operation(operation_name: str):
    """Context manager to time an operation."""
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        get_metrics().record_timing(operation_name, duration)


def record_api_call(provider: str, endpoint: str, success: bool = True) -> None:
    """Record an API call for monitoring."""
    counter_name = f"api_call_{provider}_{endpoint}"
    if success:
        counter_name += "_success"
    else:
        counter_name += "_error"
    get_metrics().increment_counter(counter_name)


def record_llm_call(model: str, tokens: Optional[int] = None) -> None:
    """Record an LLM API call."""
    get_metrics().increment_counter(f"llm_call_{model}")
    if tokens:
        get_metrics().increment_counter(f"llm_tokens_{model}", tokens)