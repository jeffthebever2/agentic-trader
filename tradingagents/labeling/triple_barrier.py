"""Triple-Barrier Labeling — FE-1.

Replaces fixed-horizon return labels with path-aware labels:
  1 = target hit first (trade won at stop/target boundaries)
  0 = stop hit first OR time expired (depending on timeout_handling)

Aligns ML training objective with actual live trade behavior where the exit
is determined by whichever of {stop, target, timeout} occurs first.

Key design decision on timeout rows (HUMAN REVIEW required before production use):
  - 43.9% of timeout trades are profitable in live data.
  - "zero"        : label timeouts as 0 (conservative; underestimates WR)
  - "drop"        : exclude timeout rows from training (reduces N but is cleanest)
  - "pass_through": label timeouts by fixed-horizon return (h{hold}_return > 0)

Usage:
    from tradingagents.labeling.triple_barrier import compute_triple_barrier_labels

    labels = compute_triple_barrier_labels(
        df,
        outcome_col="outcome",
        timeout_handling="zero",
    )
"""
import pandas as pd


def compute_triple_barrier_labels(
    df: pd.DataFrame,
    outcome_col: str = "outcome",
    timeout_handling: str = "zero",
    passthrough_return_col: str | None = None,
    passthrough_threshold: float = 0.005,
) -> pd.Series:
    """Compute triple-barrier labels from an outcome column.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with an `outcome_col` containing values like:
        'TARGET_HIT', 'STOP_HIT', 'TIMED_OUT' (or substrings thereof).
        backtest.py:measure_outcome produces 'target', 'stop', 'timeout'.
    outcome_col : str
        Column containing first-hit outcome string. Case-insensitive.
    timeout_handling : str
        How to label TIMED_OUT rows:
        - "zero"         : label = 0
        - "drop"         : label = NaN (caller must dropna)
        - "pass_through" : label from fixed-horizon return (requires passthrough_return_col)
    passthrough_return_col : str or None
        Column with forward return for "pass_through" mode. Required when
        timeout_handling="pass_through".
    passthrough_threshold : float
        Return threshold for pass_through labeling. Default 0.005 (0.5%).

    Returns
    -------
    pd.Series
        Integer series (int or NaN for "drop" timeouts):
        - 1 = target hit first
        - 0 = stop hit / timeout (depending on timeout_handling)
        - NaN = dropped (timeout with "drop" handling)
    """
    if outcome_col not in df.columns:
        raise ValueError(
            f"Column '{outcome_col}' not found. Available columns: {list(df.columns)[:20]}"
        )

    outcome_upper = df[outcome_col].astype(str).str.upper()

    target_mask = outcome_upper.str.contains("TARGET", na=False)
    stop_mask = outcome_upper.str.contains("STOP", na=False)
    timeout_mask = (
        outcome_upper.str.contains("TIMED_OUT|TIMEOUT|TIME_OUT", na=False)
        | (~target_mask & ~stop_mask)
    )

    labels = pd.Series(index=df.index, dtype="float64")
    labels[target_mask] = 1.0
    labels[stop_mask] = 0.0

    if timeout_handling == "zero":
        labels[timeout_mask] = 0.0
    elif timeout_handling == "drop":
        labels[timeout_mask] = float("nan")
    elif timeout_handling == "pass_through":
        if passthrough_return_col is None:
            raise ValueError(
                "pass_through requires passthrough_return_col to be set."
            )
        if passthrough_return_col not in df.columns:
            raise ValueError(
                f"passthrough_return_col '{passthrough_return_col}' not found."
            )
        ret_col = pd.to_numeric(df[passthrough_return_col], errors="coerce")
        labels[timeout_mask] = (ret_col[timeout_mask] > passthrough_threshold).astype(float)
    else:
        raise ValueError(
            f"Unknown timeout_handling: {timeout_handling!r}. "
            "Valid values: 'zero', 'drop', 'pass_through'."
        )

    return labels.astype("float64")


def label_distribution(labels: pd.Series) -> dict:
    """Return distribution stats for a triple-barrier label series."""
    n_total = len(labels)
    n_valid = labels.notna().sum()
    n_target = int((labels == 1).sum())
    n_stop_or_timeout = int((labels == 0).sum())
    n_dropped = int(labels.isna().sum())

    return {
        "n_total": n_total,
        "n_valid": int(n_valid),
        "n_dropped": n_dropped,
        "target_pct": round(n_target / max(n_valid, 1), 4),
        "stop_timeout_pct": round(n_stop_or_timeout / max(n_valid, 1), 4),
        "dropped_pct": round(n_dropped / max(n_total, 1), 4),
        "win_rate": round(n_target / max(n_valid, 1), 4),
    }
