from pathlib import Path


from tradingagents.dataflows.cache import DataCache
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.metrics import get_metrics, timed_operation


def test_data_cache_set_and_get(tmp_path: Path):
    cache = DataCache(tmp_path, max_age_hours=1)
    cache_key = "sample_key"
    cache.set(cache_key, {"value": 123})

    assert cache.get(cache_key) == {"value": 123}


def test_route_to_vendor_uses_cache(tmp_path: Path, monkeypatch):
    # Configure data cache and use a dummy vendor implementation.
    set_config({
        "data_cache_dir": str(tmp_path),
        "data_cache_enabled": True,
        "data_cache_ttl_hours": 24,
        "data_vendors": {"core_stock_apis": "yfinance"},
    })

    # Reset the cache module singleton to avoid stale state across tests.
    import tradingagents.dataflows.cache as cache_module

    cache_module._cache_instance = None

    # Replace the vendor implementation with a simple mock.
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_stock_data", {"yfinance": lambda *args, **kwargs: "cached-result"})

    first_result = interface.route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2024-01-02")
    assert first_result == "cached-result"

    # Change the vendor implementation and verify the cached result is returned instead.
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_stock_data", {"yfinance": lambda *args, **kwargs: "changed-result"})
    second_result = interface.route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2024-01-02")
    assert second_result == "cached-result"


def test_timed_operation_records_metrics():
    metrics = get_metrics()
    metrics.reset()

    with timed_operation("test_operation"):
        pass

    summary = metrics.get_summary()
    assert summary.get("test_operation_count") == 1
    assert summary.get("test_operation_total") is not None
    assert summary.get("test_operation_avg") is not None
