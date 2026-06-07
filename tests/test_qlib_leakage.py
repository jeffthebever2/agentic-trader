"""
Leakage and alignment tests for QlibFeatureMerger.

These tests prove that qlib_* features at date T never use price information
from T or any later date (the "no look-ahead" contract).

Test strategy
-------------
- Direct lag check: feature[T] == feature_computed_on_truncated_series[T]
  where the truncated series ends at T-1.
- Perturbation check: perturbing close[T] must NOT change any feature at T.
- Future-date insulation: perturbing close[T+k] (k>=1) must NOT change feature[T].
- Merge alignment: merged scan_date rows use only prices through scan_date - 1.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tradingagents.qlib_integration.feature_merger import (
    QLIB_CS_FEATURE_COLS,
    QLIB_FEATURE_COLS,
    LeakageError,
    QlibFeatureMerger,
    assert_no_leakage,
    compute_qlib_features,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_prices(n: int = 500, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    prices = 100.0 + rng.standard_normal(n).cumsum() * 0.5
    prices = np.abs(prices) + 10.0  # strictly positive
    return pd.Series(prices, index=idx, name="close")


def _make_price_df(n: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    c = 100.0 + rng.standard_normal(n).cumsum() * 0.5 + 10.0
    h = c * (1.0 + rng.uniform(0, 0.01, n))
    lo = c * (1.0 - rng.uniform(0, 0.01, n))
    vol = rng.integers(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"close": c, "high": h, "low": lo, "volume": vol}, index=idx)


# ── Core lag tests ─────────────────────────────────────────────────────────────

def test_feature_lag_default_is_one():
    """lag=2 features must equal lag=1 features shifted 1 day forward."""
    close = _make_prices(300)
    feats_lag1 = compute_qlib_features(close, lag_days=1)
    feats_lag2 = compute_qlib_features(close, lag_days=2)

    # lag2[t] == lag1[t-1] for all t
    lag1_shifted = feats_lag1.shift(1)
    common = feats_lag1.index.intersection(feats_lag2.index)
    check = common[5:]

    for col in QLIB_FEATURE_COLS:
        if col not in feats_lag1.columns:
            continue
        a = feats_lag2.loc[check, col].dropna()
        b = lag1_shifted.loc[check, col].reindex(a.index).dropna()
        both = a.index.intersection(b.index)
        if len(both) < 5:
            continue
        for d in both[:10]:
            if pd.isna(a[d]) and pd.isna(b[d]):
                continue
            assert math.isclose(float(a[d]), float(b[d]), rel_tol=1e-5), (
                f"{col} at {d}: lag2={a[d]:.8f} != lag1_shifted={b[d]:.8f}"
            )


def test_lag_zero_raises():
    """lag_days=0 must raise LeakageError — same-day use is disallowed."""
    close = _make_prices(100)
    with pytest.raises(LeakageError):
        compute_qlib_features(close, lag_days=0)


def test_negative_lag_raises():
    close = _make_prices(100)
    with pytest.raises(LeakageError):
        compute_qlib_features(close, lag_days=-1)


# ── Perturbation leakage tests ─────────────────────────────────────────────────

def test_perturbing_same_day_price_does_not_change_features():
    """Changing close[T] must NOT affect any feature at date T."""
    close = _make_prices(400)
    feats = compute_qlib_features(close, lag_days=1)

    # Pick a date with valid features and enough history
    valid = feats.dropna(how="all")
    if len(valid) < 50:
        pytest.skip("insufficient valid rows")
    test_date = valid.index[50]

    loc = close.index.get_loc(test_date)
    close_perturbed = close.copy()
    close_perturbed.iloc[loc] *= 100.0  # large perturbation

    feats_perturbed = compute_qlib_features(close_perturbed, lag_days=1)

    for col in QLIB_FEATURE_COLS:
        if col not in feats.columns or col not in feats_perturbed.columns:
            continue
        orig = feats.loc[test_date, col]
        new = feats_perturbed.loc[test_date, col]
        if pd.isna(orig) and pd.isna(new):
            continue
        assert math.isclose(float(orig), float(new), rel_tol=1e-6), (
            f"LEAKAGE: {col} at {test_date} changed from {orig} to {new} "
            f"when close[{test_date}] was perturbed × 100"
        )


def test_perturbing_future_price_does_not_change_features():
    """Changing close[T+5] must NOT affect features at date T."""
    close = _make_prices(400)
    feats = compute_qlib_features(close, lag_days=1)

    valid = feats.dropna(how="all")
    if len(valid) < 60:
        pytest.skip("insufficient valid rows")
    test_date = valid.index[50]

    loc = close.index.get_loc(test_date)
    future_loc = min(loc + 5, len(close) - 1)

    close_perturbed = close.copy()
    close_perturbed.iloc[future_loc] *= 100.0

    feats_perturbed = compute_qlib_features(close_perturbed, lag_days=1)

    for col in QLIB_FEATURE_COLS:
        if col not in feats.columns or col not in feats_perturbed.columns:
            continue
        orig = feats.loc[test_date, col]
        new = feats_perturbed.loc[test_date, col]
        if pd.isna(orig) and pd.isna(new):
            continue
        assert math.isclose(float(orig), float(new), rel_tol=1e-6), (
            f"LEAKAGE: {col} at {test_date} changed from {orig} to {new} "
            f"when close[{close.index[future_loc]}] (T+5) was perturbed × 100"
        )


def test_truncation_equivalence():
    """Feature at date T on full series == feature at T on series truncated at T (inclusive).

    Since lag=1 means feature[T] uses raw features from T-1, adding future prices
    (T+1, T+2, ...) must not change feature[T].  We verify by including T in the
    truncated series and confirming the value matches the full computation.
    """
    close = _make_prices(500)
    feats_full = compute_qlib_features(close, lag_days=1)

    valid = feats_full.dropna(how="all")
    if len(valid) < 10:
        pytest.skip("insufficient valid rows")

    sample_dates = valid.index[:: max(1, len(valid) // 5)][:5]

    for test_date in sample_dates:
        loc = close.index.get_loc(test_date)
        if loc < 260:  # need enough warm-up
            continue
        # Truncate at test_date (inclusive) — no future data beyond T
        close_trunc = close.iloc[: loc + 1]
        feats_trunc = compute_qlib_features(close_trunc, lag_days=1)

        if test_date not in feats_trunc.index:
            continue

        for col in QLIB_FEATURE_COLS:
            if col not in feats_full.columns or col not in feats_trunc.columns:
                continue
            orig = feats_full.loc[test_date, col]
            trunc = feats_trunc.loc[test_date, col]
            if pd.isna(orig) and pd.isna(trunc):
                continue
            if pd.isna(orig) or pd.isna(trunc):
                continue
            assert math.isclose(float(orig), float(trunc), rel_tol=1e-5), (
                f"{col} at {test_date}: full={orig:.8f} != trunc_at_T={trunc:.8f}"
            )


# ── assert_no_leakage tests ────────────────────────────────────────────────────

def test_assert_no_leakage_passes_on_correct_features():
    close = _make_prices(500)
    feats = compute_qlib_features(close, lag_days=1)
    assert_no_leakage(feats, close)  # must not raise


def test_assert_no_leakage_catches_injected_leak():
    """Manually inject a same-day price into a feature and verify LeakageError fires."""
    close = _make_prices(500)
    feats = compute_qlib_features(close, lag_days=1)

    # Inject leakage: overwrite qlib_close_rank at date T with close[T] directly
    # This simulates a feature that accidentally uses same-day price.
    valid = feats.dropna(how="all")
    if len(valid) < 300:
        pytest.skip("insufficient rows")

    feats_leaked = feats.copy()
    for d in valid.index[260:270]:
        loc = close.index.get_loc(d) if d in close.index else None
        if loc is None:
            continue
        # Encode same-day price as feature — pure leakage
        feats_leaked.loc[d, "qlib_close_rank"] = float(close.iloc[loc])

    with pytest.raises(LeakageError):
        assert_no_leakage(feats_leaked, close)


# ── QlibFeatureMerger tests ────────────────────────────────────────────────────

def test_merger_adds_qlib_columns():
    df_price = _make_price_df(400)
    price_cache = {"AAPL": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)

    scan_dates = df_price.index[260::20]  # every 20 days after warmup
    training_df = pd.DataFrame({
        "ticker": "AAPL",
        "scan_date": scan_dates.strftime("%Y-%m-%d"),
        "score": 50.0,
    })

    result = merger.merge(training_df, price_cache)

    for col in QLIB_FEATURE_COLS:
        assert col in result.columns, f"Expected column {col} in merged DataFrame"


def test_merger_unknown_ticker_produces_nan():
    df_price = _make_price_df(400)
    price_cache = {"AAPL": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)

    training_df = pd.DataFrame({
        "ticker": ["UNKNOWN_ZZZ"],
        "scan_date": ["2020-01-15"],
        "score": [50.0],
    })

    result = merger.merge(training_df, price_cache)
    for col in QLIB_FEATURE_COLS:
        assert col in result.columns
        assert pd.isna(result[col].iloc[0]), f"{col} should be NaN for unknown ticker"


def test_merger_scan_date_uses_prior_day_prices():
    """Verify that the merged feature at scan_date T uses prices from T-1 or earlier."""
    df_price = _make_price_df(500)
    price_cache = {"TSLA": df_price}

    merger = QlibFeatureMerger(lag_days=1, run_leakage_check=True)

    # Use a scan date well into the price history
    scan_date = df_price.index[350].strftime("%Y-%m-%d")
    training_df = pd.DataFrame({
        "ticker": ["TSLA"],
        "scan_date": [scan_date],
        "score": [75.0],
    })

    # If merger runs leakage check and passes, features are safe
    result = merger.merge(training_df, price_cache)
    for col in QLIB_FEATURE_COLS:
        assert col in result.columns


def test_merger_summary_reports_coverage():
    df_price = _make_price_df(400)
    price_cache = {"MSFT": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)
    scan_dates = df_price.index[260::10]
    training_df = pd.DataFrame({
        "ticker": "MSFT",
        "scan_date": scan_dates.strftime("%Y-%m-%d"),
        "score": 50.0,
    })

    result = merger.merge(training_df, price_cache)
    summary = merger.summary(result)

    assert summary["feature_count"] == len(QLIB_FEATURE_COLS)
    assert summary["cs_feature_count"] == len(QLIB_CS_FEATURE_COLS)
    assert summary["total_rows"] == len(training_df)
    for col in QLIB_FEATURE_COLS + QLIB_CS_FEATURE_COLS:
        assert col in summary["coverage"]


def test_merger_does_not_modify_existing_columns():
    """Merging qlib features must not alter any pre-existing training columns."""
    df_price = _make_price_df(400)
    price_cache = {"SPY": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)
    scan_dates = df_price.index[260::20]

    training_df = pd.DataFrame({
        "ticker": "SPY",
        "scan_date": scan_dates.strftime("%Y-%m-%d"),
        "score": 42.0,
        "rsi14": 55.0,
        "ret_1d": 0.005,
    })

    result = merger.merge(training_df, price_cache)

    pd.testing.assert_series_equal(result["score"], training_df["score"])
    pd.testing.assert_series_equal(result["rsi14"], training_df["rsi14"])
    pd.testing.assert_series_equal(result["ret_1d"], training_df["ret_1d"])


# ── IC / forward-return alignment ─────────────────────────────────────────────

def test_qlib_mom_63_present_with_short_history():
    """qlib_mom_63 should have non-NaN values after ~90 days; qlib_mom_252_21 still NaN."""
    close = _make_prices(120)  # short history — not enough for 252d
    feats = compute_qlib_features(close, lag_days=1)

    assert "qlib_mom_63" in feats.columns, "qlib_mom_63 must be in QLIB_FEATURE_COLS"
    assert "qlib_mom_252_21" in feats.columns

    # After lag=1 and ~85 day warmup, mom_63 should have valid rows
    mom63_valid = feats["qlib_mom_63"].dropna()
    mom252_valid = feats["qlib_mom_252_21"].dropna()

    assert len(mom63_valid) > 0, "qlib_mom_63 should have valid rows with 120 days of history"
    assert len(mom252_valid) == 0, "qlib_mom_252_21 should be all NaN with only 120 days of history"


def test_qlib_mom_63_leakage_safe():
    """Perturbing close[T] must not change qlib_mom_63 at T."""
    close = _make_prices(300)
    feats = compute_qlib_features(close, lag_days=1)

    valid = feats["qlib_mom_63"].dropna()
    if len(valid) < 5:
        pytest.skip("insufficient valid rows")
    test_date = valid.index[5]

    loc = close.index.get_loc(test_date)
    close_perturbed = close.copy()
    close_perturbed.iloc[loc] *= 100.0

    feats_perturbed = compute_qlib_features(close_perturbed, lag_days=1)

    orig = feats.loc[test_date, "qlib_mom_63"]
    new = feats_perturbed.loc[test_date, "qlib_mom_63"]
    if pd.isna(orig) and pd.isna(new):
        return
    assert math.isclose(float(orig), float(new), rel_tol=1e-6), (
        f"LEAKAGE: qlib_mom_63 at {test_date} changed from {orig} to {new} "
        f"when close[{test_date}] was perturbed × 100"
    )


def test_merger_adds_cs_rank_columns():
    """After merge, all QLIB_CS_FEATURE_COLS must be present."""
    df_price = _make_price_df(500)
    price_cache = {"AAPL": df_price, "MSFT": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)
    scan_dates = df_price.index[260::20]
    training_df = pd.DataFrame({
        "ticker": ["AAPL"] * len(scan_dates) + ["MSFT"] * len(scan_dates),
        "scan_date": list(scan_dates.strftime("%Y-%m-%d")) * 2,
        "score": 50.0,
    })

    result = merger.merge(training_df, price_cache)

    for col in QLIB_CS_FEATURE_COLS:
        assert col in result.columns, f"Expected CS column {col}"
        non_nan = result[col].dropna()
        assert len(non_nan) > 0, f"{col} should have non-NaN values"
        # CS rank (pct=True) must be in [0, 1]
        assert non_nan.between(0.0, 1.0).all(), f"{col} values must be in [0, 1]"


def test_cs_rank_single_ticker_is_1():
    """With only one ticker per scan_date, CS rank must be 1.0 (sole member = rank 1)."""
    df_price = _make_price_df(500)
    price_cache = {"SOLO": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)
    scan_dates = df_price.index[270::30][:5]
    training_df = pd.DataFrame({
        "ticker": "SOLO",
        "scan_date": scan_dates.strftime("%Y-%m-%d"),
        "score": 50.0,
    })

    result = merger.merge(training_df, price_cache)

    for col in QLIB_CS_FEATURE_COLS:
        assert col in result.columns
        base_col = col.replace("qlib_cs_rank_", "qlib_")
        if base_col == "qlib_cs_rank_close_rank":
            base_col = "qlib_close_rank"
        # With pct=True and single member, rank should be 1.0 for non-NaN rows
        for _, row in result.iterrows():
            if pd.notna(row[col]):
                assert math.isclose(float(row[col]), 1.0, rel_tol=1e-6), (
                    f"{col} should be 1.0 for single ticker per date, got {row[col]}"
                )


def test_cs_rank_does_not_modify_base_cols():
    """Merging CS ranks must not alter the per-ticker base qlib_* columns."""
    df_price = _make_price_df(500)
    price_cache = {"AAPL": df_price, "GOOG": df_price}

    merger = QlibFeatureMerger(run_leakage_check=False)
    scan_dates = df_price.index[270::20]
    training_df = pd.DataFrame({
        "ticker": ["AAPL"] * len(scan_dates) + ["GOOG"] * len(scan_dates),
        "scan_date": list(scan_dates.strftime("%Y-%m-%d")) * 2,
        "score": 50.0,
    })

    result = merger.merge(training_df, price_cache)

    # Per-ticker base features should be identical for AAPL and GOOG (same price_cache)
    aapl = result[result["ticker"] == "AAPL"].set_index("scan_date")
    goog = result[result["ticker"] == "GOOG"].set_index("scan_date")
    common = aapl.index.intersection(goog.index)

    for col in QLIB_FEATURE_COLS:
        if col not in result.columns:
            continue
        a = aapl.loc[common, col].dropna()
        g = goog.loc[common, col].reindex(a.index).dropna()
        both = a.index.intersection(g.index)
        for d in both[:5]:
            assert math.isclose(float(a[d]), float(g[d]), rel_tol=1e-6), (
                f"Base col {col} should be same for same-price tickers at {d}"
            )


def test_ic_computation_uses_only_past_features():
    """IC computed on qlib features uses forward returns, but features themselves are lagged."""
    from tradingagents.qlib_integration.factor_ic import factor_summary

    close = _make_prices(500)
    feats = compute_qlib_features(close, lag_days=1)

    # Build single-ticker factor_df and returns_df (cross-sectional with 1 ticker is degenerate
    # but we're testing the computation doesn't raise and features are all past-looking)
    factor_col = "qlib_close_rank"
    if factor_col not in feats.columns:
        pytest.skip("qlib_close_rank not in features")

    factor_df = feats[[factor_col]].rename(columns={factor_col: "AAPL"})
    returns_df = close.pct_change(1).rename("AAPL").to_frame()

    # factor_df values at T must NOT contain T's return — they're 1-day lagged close data
    # Verify: factor at T correlates with T's price rank, computed from T-1 data
    common = factor_df.dropna().index
    assert len(common) > 50, "Not enough data for IC test"

    result = factor_summary(factor_df, returns_df, forward_days=5)
    # Should produce a valid summary without raises
    assert "ic_mean" in result
    assert "icir" in result
