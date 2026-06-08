"""Tests for tradingagents.qlib_integration"""
import numpy as np
import pandas as pd
import pytest
from tradingagents.qlib_integration.adapter import QlibDataAdapter
from tradingagents.qlib_integration.engine import QlibResearchEngine
from tradingagents.qlib_integration.smoke import smoke_test


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(n=60, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = 100 + rng.standard_normal(n).cumsum()
    return pd.DataFrame({
        "Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
        "Close": prices, "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    }, index=idx)


# ── QlibDataAdapter ───────────────────────────────────────────────────────────

def test_normalize_ohlcv_flat():
    adapter = QlibDataAdapter()
    raw = _make_ohlcv()
    norm = adapter.normalize_ohlcv(raw)
    assert "close" in norm.columns
    assert "open" in norm.columns
    assert "volume" in norm.columns
    assert len(norm) == 60


def test_normalize_ohlcv_empty():
    adapter = QlibDataAdapter()
    norm = adapter.normalize_ohlcv(pd.DataFrame())
    assert norm.empty


def test_normalize_ohlcv_preserves_length():
    adapter = QlibDataAdapter()
    raw = _make_ohlcv(n=100)
    norm = adapter.normalize_ohlcv(raw)
    assert len(norm) == 100


def test_extract_alpha_features_returns_dict():
    adapter = QlibDataAdapter()
    raw = _make_ohlcv(n=60)
    norm = adapter.normalize_ohlcv(raw)
    features = adapter.extract_alpha_features(norm, ["AAPL"])
    assert "AAPL" in features
    df = features["AAPL"]
    assert "ret_1d" in df.columns
    assert "ret_5d" in df.columns
    assert "vol_20d" in df.columns
    assert "rsi_14" in df.columns


def test_extract_alpha_features_empty_df():
    adapter = QlibDataAdapter()
    features = adapter.extract_alpha_features(pd.DataFrame(columns=["close"]))
    assert features == {}


def test_extract_alpha_features_too_short():
    adapter = QlibDataAdapter()
    raw = _make_ohlcv(n=10)  # < 20 required for vol_20d
    norm = adapter.normalize_ohlcv(raw)
    features = adapter.extract_alpha_features(norm, ["X"])
    assert features == {}


def test_normalize_multiindex():
    adapter = QlibDataAdapter()
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    rng = np.random.default_rng(0)
    prices = 100 + rng.standard_normal(30).cumsum()
    raw = pd.DataFrame({
        ("Close", "AAPL"): prices,
        ("Open", "AAPL"): prices * 0.99,
        ("High", "AAPL"): prices * 1.01,
        ("Low", "AAPL"): prices * 0.98,
        ("Volume", "AAPL"): np.ones(30) * 1e6,
    }, index=idx)
    raw.columns = pd.MultiIndex.from_tuples(raw.columns)
    norm = adapter.normalize_ohlcv(raw)
    assert "close" in norm.columns


# ── QlibResearchEngine ────────────────────────────────────────────────────────

def test_engine_qlib_available():
    engine = QlibResearchEngine()
    assert engine._qlib_available is True


def test_run_tournament_empty_tickers():
    engine = QlibResearchEngine()
    result = engine.run_tournament([], "2024-01-01", "2024-06-01")
    # No crash, returns TournamentResult with notes
    assert result.run_at
    assert result.tickers == []


def test_run_wf_models_basic():
    """Test walk-forward with synthetic in-memory data (no network)."""
    rng = np.random.default_rng(0)
    n = 200
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "ret_1d": rng.normal(0.001, 0.02, n),
        "ret_5d": rng.normal(0.005, 0.04, n),
        "vol_20d": rng.uniform(0.01, 0.03, n),
        "rsi_14": rng.uniform(30, 70, n),
    }, index=idx)
    engine = QlibResearchEngine(n_splits=3)
    results = engine._run_wf_models(df)
    assert isinstance(results, list)
    # Should have at least one model (may have few OOS samples — check graceful)
    for r in results:
        assert 0 <= r.wf_roc <= 1


# ── smoke_test ────────────────────────────────────────────────────────────────

def test_smoke_test_passes():
    result = smoke_test()
    assert result["qlib_installed"] is True
    assert result["adapter_ok"] is True
    assert result["engine_ok"] is True
    assert result["errors"] == []
    assert result["qlib_version"] is not None


def test_smoke_test_returns_dict_keys():
    result = smoke_test()
    required = {"qlib_installed", "qlib_version", "adapter_ok", "engine_ok", "errors"}
    assert required <= set(result.keys())
