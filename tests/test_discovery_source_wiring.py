"""Discovery source wiring into the scan: flag-gated, injectable, and a discovery
breakout surfaces a NO-BUZZ ticker into the ranked signals (the IREN-$5 solver) —
high-trust-solo so it isn't dampened for lacking social confirmation."""
import asyncio

import web.api.thematic_auto as t


def _breakout_bars():
    closes = [10 + 0.2 * i for i in range(120)]
    highs = [c + 0.1 for c in closes]
    vols = [1e6] * 120
    vols[-1] = 4e6
    return {"highs": highs, "closes": closes, "volumes": vols}


def test_discovery_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_DISCOVERY", raising=False)
    out = asyncio.run(t._discovery_tickers(
        fetch_bars=lambda tk: _breakout_bars(),
        fetch_bench=lambda: [400.0] * 120,
        universe=["IREN"],
    ))
    assert out == {}


def test_discovery_surfaces_breakout(monkeypatch):
    monkeypatch.setenv("THEMATIC_DISCOVERY", "true")
    out = asyncio.run(t._discovery_tickers(
        fetch_bars=lambda tk: _breakout_bars(),
        fetch_bench=lambda: [400.0] * 120,    # flat benchmark → strong RS
        universe=["IREN"],
    ))
    assert out.get("IREN", 0) >= 3


def test_discovery_fetch_failure_graceful(monkeypatch):
    monkeypatch.setenv("THEMATIC_DISCOVERY", "true")
    def _boom(tk):
        raise RuntimeError("net")
    out = asyncio.run(t._discovery_tickers(fetch_bars=_boom, fetch_bench=lambda: [], universe=["X"]))
    assert out == {}


def test_no_buzz_discovery_name_ranks_without_dampener(monkeypatch):
    async def _identity(tickers):
        return {x.upper() for x in tickers}
    monkeypatch.setattr(t, "_validate_tickers", _identity)
    monkeypatch.setattr(t, "_get_historical_scores", lambda n_scans=5: {})
    # discovery is the ONLY source for NEWNAME (no social buzz at all)
    ranked, bd = asyncio.run(t._merge_signals(
        {}, {}, [], None, discovery={"NEWNAME": 6},
    ))
    tickers = dict(ranked)
    assert "NEWNAME" in tickers                      # surfaced despite zero buzz
    assert "single_source_dampener" not in bd.get("NEWNAME", {})  # high-trust solo
