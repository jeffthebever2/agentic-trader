import pytest

from tradingagents.dataflows import interface


@pytest.mark.unit
def test_route_to_vendor_falls_back_after_provider_exception(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("temporary outage")

    def healthy(*args, **kwargs):
        return "healthy provider result"

    monkeypatch.setattr(
        interface,
        "get_config",
        lambda: {
            "data_vendors": {"news_data": "broken,healthy"},
            "tool_vendors": {},
        },
    )
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_news", {"broken": broken, "healthy": healthy})

    result = interface.route_to_vendor("get_news", "AAPL", "2026-01-01", "2026-01-02")

    assert result == "healthy provider result"


@pytest.mark.unit
def test_route_to_vendor_returns_degraded_report_when_every_provider_fails(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("request failed: https://example.test?apikey=secret123&symbol=AAPL")

    monkeypatch.setattr(
        interface,
        "get_config",
        lambda: {
            "data_vendors": {"news_data": "broken"},
            "tool_vendors": {},
        },
    )
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_news", {"broken": broken})

    result = interface.route_to_vendor("get_news", "AAPL", "2026-01-01", "2026-01-02")

    assert "Data Source Degraded" in result
    assert "Continue the analysis" in result
    assert "apikey=[REDACTED]" in result
    assert "secret123" not in result
