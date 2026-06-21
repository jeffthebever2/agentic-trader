"""Wiring of the portfolio-aware sizer into the thematic approve path.

Exercises web.api.thematic_auto._portfolio_aware_dollar end-to-end with the
network bits (sectors, closes) mocked, so the integration is deterministic.
"""
import asyncio
import json

import pytest

import web.api.thematic_auto as ta


def _series(n=120, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


@pytest.fixture
def paper_state(tmp_path, monkeypatch):
    state = {
        "cash": 100_000.0,
        "positions": {
            "NVDA": {"entry_price": 100.0, "shares": 100},  # $10k
            "AMD":  {"entry_price": 50.0, "shares": 100},   # $5k
        },
    }
    f = tmp_path / "thematic_state.json"
    f.write_text(json.dumps(state))
    import web.api.thematic_portfolio as tp
    monkeypatch.setattr(tp, "PAPER_STATE_FILE", f)
    return f


def test_portfolio_aware_dollar_success(monkeypatch, paper_state):
    monkeypatch.setattr(ta, "_sector_of", lambda t: {"MU": "Technology", "NVDA": "Technology", "AMD": "Technology"}.get(t.upper()))
    monkeypatch.setattr(ta, "_closes_map", lambda tickers, period="6mo": {t.upper(): _series() for t in tickers})
    sig = {"ticker": "MU", "conviction": 8, "score": 85}
    dollars, info = asyncio.run(
        ta._portfolio_aware_dollar("u@x.com", sig, 125_000.0, target_pct=40, stop_pct=10, hil={})
    )
    assert info is not None
    assert "binding_constraint" in info and "factors" in info
    # MU is Technology and the book already holds 12% Tech (NVDA 8% + AMD 4% of 125k);
    # sizing must respect the 30% sector cap and 10% per-position cap.
    assert dollars <= 125_000 * 0.10 + 1


def test_portfolio_aware_dollar_no_account_value(monkeypatch, paper_state):
    sig = {"ticker": "MU", "conviction": 8, "score": 85}
    dollars, info = asyncio.run(
        ta._portfolio_aware_dollar("u@x.com", sig, 0.0, target_pct=40, stop_pct=10, hil={})
    )
    assert dollars is None and info is None  # caller falls back to legacy adaptive


def test_portfolio_aware_dollar_degrades_when_enrichment_fails(monkeypatch, paper_state):
    # Sectors + closes unavailable → neutral vol/corr/sector factors, still sizes.
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ta, "_sector_of", _boom)
    monkeypatch.setattr(ta, "_closes_map", _boom)
    sig = {"ticker": "MU", "conviction": 8, "score": 85}
    dollars, info = asyncio.run(
        ta._portfolio_aware_dollar("u@x.com", sig, 125_000.0, target_pct=40, stop_pct=10, hil={})
    )
    assert info is not None          # graceful: still produced a size
    assert dollars > 0
