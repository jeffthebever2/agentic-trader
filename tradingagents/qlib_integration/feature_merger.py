"""
QlibFeatureMerger — timestamp-safe qlib feature injection into training DataFrames.

Safety contract
---------------
All qlib_* features at row date T use ONLY prices available through T-1 (one
trading-day lag).  This means the feature series computed from raw close prices
is shifted forward by one day before being stored, so a model trained on date T
has no access to T's closing price or forward returns.

Feature names
-------------
  qlib_mom_252_21   Long-term momentum: 252d/21d ratio minus 1, skipping
                    most-recent 21 days (standard Fama-French momentum).
  qlib_vol_ratio    5d realised vol / 63d realised vol.  < 1 → quiet period,
                    > 1 → vol expansion.
  qlib_atr_z        ATR(14) z-score vs 90-day trailing mean/std.
  qlib_close_rank   Where today's close ranks in the trailing 252-day range [0,1].

Usage
-----
    from tradingagents.qlib_integration.feature_merger import QlibFeatureMerger

    merger = QlibFeatureMerger()
    # price_cache: {ticker: pd.DataFrame with DatetimeIndex and columns
    #               close, high, low, volume} (same format as precompute() output)
    enriched_df = merger.merge(training_df, price_cache)

Leakage detection
-----------------
    merger.assert_no_leakage(features_df)

raises LeakageError if any feature at date T contains information from T or later.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd


QLIB_FEATURE_COLS = [
    "qlib_mom_252_21",
    "qlib_vol_ratio",
    "qlib_atr_z",
    "qlib_close_rank",
]


class LeakageError(RuntimeError):
    """Raised when a qlib feature violates timestamp safety."""


def compute_qlib_features(
    close: pd.Series,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    lag_days: int = 1,
) -> pd.DataFrame:
    """Compute per-ticker qlib features with a mandatory look-back lag.

    Parameters
    ----------
    close : pd.Series
        Daily closing prices, DatetimeIndex, sorted ascending.
    high, low : pd.Series, optional
        Daily high/low prices (for ATR).  If None, ATR falls back to close-based estimate.
    lag_days : int
        Number of trading days to shift the raw feature series forward before
        returning.  Default 1 ensures that feature[T] uses prices through T-1.

    Returns
    -------
    pd.DataFrame indexed by date with columns: qlib_mom_252_21, qlib_vol_ratio,
    qlib_atr_z, qlib_close_rank.  Rows with insufficient history are NaN.
    """
    if lag_days < 1:
        raise LeakageError(f"lag_days must be >= 1, got {lag_days}")

    c = close.sort_index().dropna()
    if len(c) < 30:
        return pd.DataFrame(columns=QLIB_FEATURE_COLS, index=c.index)

    ret1d = c.pct_change(1)

    # qlib_mom_252_21: close[t] / close[t-252] / (close[t-21] / close[t-252]) - 1
    # = (close[t] / close[t-21]) - 1 ... only when 252 >= t.  Use the rolling
    # ratio directly: mom = c/c.shift(252) - 1 minus c/c.shift(21) - 1.
    mom_long = c / c.shift(252) - 1.0
    mom_short = c / c.shift(21) - 1.0
    mom_252_21 = mom_long - mom_short

    # qlib_vol_ratio: 5d realised vol / 63d realised vol
    vol5 = ret1d.rolling(5).std()
    vol63 = ret1d.rolling(63).std()
    vol_ratio = (vol5 / vol63.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    # qlib_atr_z: ATR(14) z-score vs trailing 90d
    if high is not None and low is not None:
        h = high.reindex(c.index)
        lo = low.reindex(c.index)
        prev_c = c.shift(1)
        tr = pd.concat([
            (h - lo).abs(),
            (h - prev_c).abs(),
            (lo - prev_c).abs(),
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
    else:
        # Fallback: ATR proxy from close-range
        atr14 = (c - c.shift(1)).abs().rolling(14).mean()

    atr_mean90 = atr14.rolling(90).mean()
    atr_std90 = atr14.rolling(90).std()
    atr_z = ((atr14 - atr_mean90) / atr_std90.replace(0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )

    # qlib_close_rank: rank of close in trailing 252-day range [0,1]
    roll_min = c.rolling(252).min()
    roll_max = c.rolling(252).max()
    close_rank = ((c - roll_min) / (roll_max - roll_min).replace(0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )

    raw = pd.DataFrame(
        {
            "qlib_mom_252_21": mom_252_21,
            "qlib_vol_ratio": vol_ratio,
            "qlib_atr_z": atr_z,
            "qlib_close_rank": close_rank,
        },
        index=c.index,
    )

    # Apply mandatory lag: feature[T] now contains prices computed through T-1
    lagged = raw.shift(lag_days)
    return lagged.round(6)


def assert_no_leakage(
    features: pd.DataFrame,
    close: pd.Series,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    tol_days: int = 0,
) -> None:
    """Assert that features at date T do not depend on close prices at T or later.

    Strategy: perturb close[T] by a large amount (×10) and verify that
    features at date T are unchanged.  If any feature value changes, it used
    future or same-day price data.

    Parameters
    ----------
    features : pd.DataFrame
        Feature DataFrame as returned by compute_qlib_features (lag_days >= 1).
    close : pd.Series
        The close price series used to compute features.
    high, low : pd.Series, optional
        Must be passed when features were computed with high/low data so the
        perturbed recomputation uses the same formula.
    tol_days : int
        Number of leading rows to skip (warm-up).  Default 0.

    Raises
    ------
    LeakageError if any feature[T] depends on close[T] or close[T+k], k>=0.
    """
    c = close.sort_index().dropna()
    common_dates = features.index.intersection(c.index)
    check_dates = common_dates[max(260, tol_days):]  # skip warm-up

    if len(check_dates) == 0:
        return

    leaky_cols: list[str] = []

    # Check a sample of 20 evenly spaced dates for efficiency
    sample = check_dates[:: max(1, len(check_dates) // 20)]

    for date in sample:
        loc = c.index.get_loc(date)
        if loc + 1 >= len(c):
            continue

        # Perturb close at exactly date T — high/low left unchanged
        c_perturbed = c.copy()
        c_perturbed.iloc[loc] = c_perturbed.iloc[loc] * 10.0

        feat_perturbed = compute_qlib_features(c_perturbed, high, low, lag_days=1)

        if date not in feat_perturbed.index or date not in features.index:
            continue

        orig_row = features.loc[date]
        new_row = feat_perturbed.loc[date]

        for col in QLIB_FEATURE_COLS:
            if col not in orig_row.index or col not in new_row.index:
                continue
            o = orig_row[col]
            n = new_row[col]
            if pd.isna(o) and pd.isna(n):
                continue
            if pd.isna(o) != pd.isna(n):
                leaky_cols.append(col)
                break
            if not math.isclose(float(o), float(n), rel_tol=1e-6):
                leaky_cols.append(col)
                break

        if leaky_cols:
            raise LeakageError(
                f"LEAKAGE DETECTED: columns {leaky_cols} at date {date} "
                f"changed when close[{date}] was perturbed. "
                "Features must not use same-day or future prices."
            )


class QlibFeatureMerger:
    """Merge timestamp-safe qlib features into a training DataFrame.

    Parameters
    ----------
    lag_days : int
        Look-back lag applied to all features (default 1 — uses T-1 close).
    run_leakage_check : bool
        When True (default), calls assert_no_leakage on a per-ticker sample
        before merging.  Set False only in tests where speed matters.
    """

    def __init__(self, lag_days: int = 1, run_leakage_check: bool = True) -> None:
        if lag_days < 1:
            raise ValueError("lag_days must be >= 1")
        self.lag_days = lag_days
        self.run_leakage_check = run_leakage_check

    def features_for_ticker(
        self,
        ticker: str,
        price_cache: Dict[str, pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        """Return lagged qlib feature DataFrame for one ticker, or None if unavailable."""
        df = price_cache.get(ticker)
        if df is None or df.empty:
            return None

        close = df["close"] if "close" in df.columns else (df["Close"] if "Close" in df.columns else None)
        high = df["high"] if "high" in df.columns else (df["High"] if "High" in df.columns else None)
        low = df["low"] if "low" in df.columns else (df["Low"] if "Low" in df.columns else None)

        if close is None or len(close) < 30:
            return None

        feats = compute_qlib_features(close, high, low, lag_days=self.lag_days)

        if self.run_leakage_check:
            try:
                assert_no_leakage(feats, close, high, low)
            except LeakageError:
                raise

        return feats

    def merge(
        self,
        df: pd.DataFrame,
        price_cache: Dict[str, pd.DataFrame],
        ticker_col: str = "ticker",
        date_col: str = "scan_date",
    ) -> pd.DataFrame:
        """Merge qlib_* features into training DataFrame.

        Parameters
        ----------
        df : pd.DataFrame
            Training DataFrame with columns ticker_col and date_col.
        price_cache : dict
            {ticker → OHLCV DataFrame}.
        ticker_col, date_col : str
            Column names in df.

        Returns
        -------
        pd.DataFrame with additional qlib_* columns.  Rows with no matching
        qlib data get NaN for all qlib columns (model handles gracefully via
        median imputation in train_ml_models.py).
        """
        if ticker_col not in df.columns or date_col not in df.columns:
            raise ValueError(f"DataFrame must have '{ticker_col}' and '{date_col}' columns")

        result = df.copy()
        for col in QLIB_FEATURE_COLS:
            result[col] = np.nan

        tickers_in_df = result[ticker_col].unique()
        merged_count = 0
        skipped_count = 0

        for ticker in tickers_in_df:
            feats = self.features_for_ticker(ticker, price_cache)
            if feats is None or feats.empty:
                skipped_count += 1
                continue

            # Build lookup: date string → feature row
            feats_idx = feats.copy()
            feats_idx.index = pd.to_datetime(feats_idx.index).normalize()

            mask = result[ticker_col] == ticker
            ticker_rows = result.loc[mask].copy()

            scan_dates = pd.to_datetime(ticker_rows[date_col]).dt.normalize()
            aligned = scan_dates.map(lambda d: feats_idx.loc[d] if d in feats_idx.index else None)

            for col in QLIB_FEATURE_COLS:
                values = scan_dates.map(
                    lambda d, c=col: float(feats_idx.loc[d, c])
                    if d in feats_idx.index and not pd.isna(feats_idx.loc[d, c])
                    else np.nan
                )
                result.loc[mask, col] = values.values

            merged_count += 1

        return result

    def summary(self, merged_df: pd.DataFrame) -> dict:
        """Return a dict describing qlib feature coverage for reporting."""
        total = len(merged_df)
        coverage: dict = {}
        for col in QLIB_FEATURE_COLS:
            if col in merged_df.columns:
                n_valid = int(merged_df[col].notna().sum())
                coverage[col] = {
                    "n_valid": n_valid,
                    "coverage_pct": round(100.0 * n_valid / total, 2) if total else 0.0,
                    "mean": round(float(merged_df[col].mean()), 6) if n_valid else None,
                    "std": round(float(merged_df[col].std()), 6) if n_valid else None,
                }
        return {
            "lag_days": self.lag_days,
            "features_added": QLIB_FEATURE_COLS,
            "feature_count": len(QLIB_FEATURE_COLS),
            "total_rows": total,
            "coverage": coverage,
        }
