import pytest
import pandas as pd

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


@pytest.mark.unit
def test_load_ohlcv_redownloads_invalid_cache_and_passes_timeout(monkeypatch, tmp_path):
    from tradingagents.dataflows import stockstats_utils

    monkeypatch.setattr(
        stockstats_utils,
        "get_config",
        lambda: {"data_cache_dir": str(tmp_path)},
    )
    monkeypatch.setenv("YFINANCE_TIMEOUT_SECONDS", "7")

    # Simulate a bad partial cache from a previous failed Yahoo response.
    today = pd.Timestamp.today()
    start = (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    bad_cache = tmp_path / f"AAPL-YFin-data-{start}-{end}.csv"
    bad_cache.write_text("not_date,not_close\nbroken,1\n", encoding="utf-8")

    captured = {}

    def fake_download(*args, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-18", "2026-05-19"]),
                "Open": [10.0, 11.0],
                "High": [11.0, 12.0],
                "Low": [9.0, 10.0],
                "Close": [10.5, 11.5],
                "Volume": [1000, 1200],
            }
        )

    monkeypatch.setattr(stockstats_utils.yf, "download", fake_download)

    data = stockstats_utils.load_ohlcv("AAPL", "2026-05-19")

    assert len(data) == 2
    assert captured["timeout"] == 7.0
    assert captured["progress"] is False


@pytest.mark.unit
def test_load_ohlcv_empty_yahoo_response_fails_fast(monkeypatch, tmp_path):
    from tradingagents.dataflows import stockstats_utils

    monkeypatch.setattr(
        stockstats_utils,
        "get_config",
        lambda: {"data_cache_dir": str(tmp_path)},
    )
    monkeypatch.setattr(stockstats_utils.yf, "download", lambda *a, **k: pd.DataFrame())

    with pytest.raises(ValueError, match="No market data returned"):
        stockstats_utils.load_ohlcv("NOPE", "2026-05-19")
