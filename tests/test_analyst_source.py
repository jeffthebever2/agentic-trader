"""Analyst confirmation source (FMP grades + Finnhub recommendation trends). Pure
weight helpers + flag-gated combined source. Bullish upgrades/skew add; downgrades
cancel. Keys already held; live fetch graceful without them."""
import asyncio

import web.api.thematic_auto as t


# ── pure weight helpers ──────────────────────────────────────────────────────
def test_fmp_grade_weight_net_upgrades():
    assert t._fmp_grade_weight([{"action": "upgrade"}, {"action": "upgrade"}]) == 4
    assert t._fmp_grade_weight([{"action": "upgrade"}, {"action": "downgrade"}]) == 0
    assert t._fmp_grade_weight([{"action": "downgrade"}]) == 0   # never negative
    assert t._fmp_grade_weight([]) == 0
    assert t._fmp_grade_weight([{"action": "upgrade"}] * 10) == 6  # capped


def test_recommendation_weight_skew():
    bull = {"strongBuy": 10, "buy": 5, "hold": 2, "sell": 0, "strongSell": 0}
    bear = {"strongBuy": 0, "buy": 0, "hold": 2, "sell": 5, "strongSell": 10}
    assert t._recommendation_weight(bull) > 0
    assert t._recommendation_weight(bear) == 0    # net bearish floors at 0
    assert t._recommendation_weight({}) == 0
    assert t._recommendation_weight({"hold": 5}) == 0


# ── flag-gated combined source ───────────────────────────────────────────────
def test_analyst_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_ANALYST", raising=False)
    out = asyncio.run(t._analyst_tickers(
        universe=["NVDA"],
        fetch_grades=lambda tk: [{"action": "upgrade"}],
        fetch_recs=lambda tk: {"strongBuy": 10},
    ))
    assert out == {}


def test_analyst_combines_sources(monkeypatch):
    monkeypatch.setenv("THEMATIC_ANALYST", "true")
    out = asyncio.run(t._analyst_tickers(
        universe=["NVDA", "MEH"],
        fetch_grades=lambda tk: [{"action": "upgrade"}] if tk == "NVDA" else [],
        fetch_recs=lambda tk: {"strongBuy": 8, "buy": 2} if tk == "NVDA" else {"sell": 5},
    ))
    assert out.get("NVDA", 0) > 0
    assert "MEH" not in out          # net non-bullish → not surfaced


def test_analyst_fetch_failure_graceful(monkeypatch):
    monkeypatch.setenv("THEMATIC_ANALYST", "true")
    def _boom(tk): raise RuntimeError("net")
    out = asyncio.run(t._analyst_tickers(universe=["X"], fetch_grades=_boom, fetch_recs=_boom))
    assert out == {}


def test_analyst_feeds_merge_as_quality(monkeypatch):
    async def _identity(tickers): return {x.upper() for x in tickers}
    monkeypatch.setattr(t, "_validate_tickers", _identity)
    monkeypatch.setattr(t, "_get_historical_scores", lambda n_scans=5: {})
    ranked, bd = asyncio.run(t._merge_signals({"NVDA": 8}, {}, [], None, analyst={"NVDA": 5}))
    assert "analyst" in bd["NVDA"]
    assert bd["NVDA"].get("multi_source_bonus", 0) > 0   # quality confirmation
