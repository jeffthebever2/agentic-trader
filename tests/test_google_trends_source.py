"""trendspyg / Google-Trends source. Rising search interest is a leading attention
signal. The live scrape can't run in tests, so the momentum math is a pure helper
and the source fetch is injectable. Source is OFF unless THEMATIC_GOOGLE_TRENDS."""
import asyncio

import web.api.thematic_auto as t


# ── _trends_momentum (pure) ─────────────────────────────────────────────────
def test_rising_interest_scores_positive():
    # clear acceleration: baseline ~20, recent ~80 → strong rise
    assert t._trends_momentum([20, 20, 25, 30, 60, 80]) > 0


def test_flat_interest_scores_zero():
    assert t._trends_momentum([50, 50, 50, 50]) == 0


def test_declining_interest_scores_zero():
    assert t._trends_momentum([80, 70, 40, 20]) == 0


def test_noise_floor_rejected():
    # rising but tiny absolute interest (< floor) → ignore
    assert t._trends_momentum([1, 1, 2, 5, 8, 10]) == 0


def test_rose_from_zero_scored():
    assert t._trends_momentum([0, 0, 0, 0, 40, 60]) > 0


def test_too_short_series_zero():
    assert t._trends_momentum([90, 95]) == 0
    assert t._trends_momentum([]) == 0


def test_momentum_is_bounded():
    assert t._trends_momentum([1, 1, 1, 1, 100, 100]) <= 8


# ── _google_trends_tickers (flag + injected fetch) ──────────────────────────
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_GOOGLE_TRENDS", raising=False)
    out = asyncio.run(t._google_trends_tickers(fetch=lambda q: [10, 20, 40, 80]))
    assert out == {}


def test_enabled_returns_rising_tickers(monkeypatch):
    monkeypatch.setenv("THEMATIC_GOOGLE_TRENDS", "true")
    series = {"IREN stock": [10, 10, 20, 80], "NVDA stock": [50, 50, 50, 50]}
    out = asyncio.run(t._google_trends_tickers(
        fetch=lambda q: series.get(q, []),
        terms={"IREN": "IREN stock", "NVDA": "NVDA stock"},
    ))
    assert out.get("IREN", 0) > 0     # rising → included
    assert "NVDA" not in out          # flat → excluded


def test_fetch_failure_is_graceful(monkeypatch):
    monkeypatch.setenv("THEMATIC_GOOGLE_TRENDS", "true")
    def _boom(q):
        raise RuntimeError("blocked")
    out = asyncio.run(t._google_trends_tickers(fetch=_boom, terms={"X": "X stock"}))
    assert out == {}                  # never raises


# ── merge integration ───────────────────────────────────────────────────────
def test_merge_accepts_google_trends(monkeypatch):
    async def _identity(tickers):
        return {x.upper() for x in tickers}
    monkeypatch.setattr(t, "_validate_tickers", _identity)
    monkeypatch.setattr(t, "_get_historical_scores", lambda n_scans=5: {})
    ranked, bd = asyncio.run(t._merge_signals(
        {"NVDA": 8}, {}, [], None, google_trends={"NVDA": 5},
    ))
    assert "google_trends" in bd["NVDA"]
    # google_trends is a quality source → with reddit it earns the confirmation bonus
    assert bd["NVDA"].get("multi_source_bonus", 0) > 0
