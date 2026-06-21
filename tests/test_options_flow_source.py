"""Optional unusual-options-flow source. It is a confirmation source, not an
order path, and stays off unless THEMATIC_OPTIONS_FLOW is enabled."""
import asyncio

import web.api.thematic_auto as t


def test_options_flow_weight_call_skew():
    chain = [
        {"option_type": "call", "volume": 1200, "open_interest": 400},
        {"option_type": "call", "volume": 900, "open_interest": 300},
        {"option_type": "put", "volume": 250, "open_interest": 500},
    ]
    assert t._options_flow_weight(chain) >= 6


def test_options_flow_weight_ignores_weak_chain():
    assert t._options_flow_weight([{"option_type": "call", "volume": 100, "open_interest": 1000}]) == 0
    assert t._options_flow_weight([]) == 0


def test_options_flow_source_default_off(monkeypatch):
    monkeypatch.delenv("THEMATIC_OPTIONS_FLOW", raising=False)
    out = asyncio.run(t._options_flow_tickers(
        universe=["IREN"],
        fetch_chain=lambda tk: [{"option_type": "call", "volume": 2000, "open_interest": 100}],
    ))
    assert out == {}


def test_options_flow_source_enabled(monkeypatch):
    monkeypatch.setenv("THEMATIC_OPTIONS_FLOW", "true")
    out = asyncio.run(t._options_flow_tickers(
        universe=["IREN"],
        fetch_chain=lambda tk: [{"option_type": "call", "volume": 2000, "open_interest": 100}],
    ))
    assert out.get("IREN", 0) > 0


def test_options_flow_merges_as_quality(monkeypatch):
    async def _identity(tickers):
        return {x.upper() for x in tickers}

    monkeypatch.setattr(t, "_validate_tickers", _identity)
    monkeypatch.setattr(t, "_get_historical_scores", lambda n_scans=5: {})
    ranked, bd = asyncio.run(t._merge_signals({}, {}, [], None, options_flow={"IREN": 6}))
    assert "IREN" in dict(ranked)
    assert "single_source_dampener" not in bd["IREN"]
