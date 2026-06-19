"""_real_atr must always return a finite, non-negative value — it feeds the stop
distance, and a NaN/0/negative price (or a NaN ATR from thin data) would yield a
garbage protective stop."""
import math

import yfinance as yf

import web.api.thematic_auto as t


def test_fallback_is_two_percent_when_download_fails(monkeypatch):
    # Force the except path → 2% of price fallback.
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(yf, "download", _boom)
    assert t._real_atr("NVDA", 100.0) == 2.0


def test_nonfinite_or_nonpositive_price_returns_zero(monkeypatch):
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    for px in (float("nan"), float("inf"), 0.0, -10.0, None):
        out = t._real_atr("NVDA", px)
        assert math.isfinite(out) and out >= 0.0
        assert out == 0.0


def test_result_always_finite(monkeypatch):
    monkeypatch.setattr(yf, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    for px in (1.0, 50.0, 9999.0, float("nan")):
        assert math.isfinite(t._real_atr("X", px))
