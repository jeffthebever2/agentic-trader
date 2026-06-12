"""Training report schema validation — LOG-2.

Validates that a training_report.json dict has all required top-level keys
and sub-keys that downstream scripts (snapshot_baseline, paper_backtest_drift,
retrain_weekly) depend on. Raises SchemaError on violation.

Usage:
    from tradingagents.ml.training_report_schema import validate_training_report
    validate_training_report(report)   # raises SchemaError if invalid
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class SchemaError(ValueError):
    """Raised when training_report.json fails schema validation."""


# Required top-level keys
_REQUIRED_TOP = frozenset({"settings", "label_distribution", "models", "leakage_check"})

# Required keys inside settings
_REQUIRED_SETTINGS = frozenset({
    "source", "hold", "rows_used", "train_rows", "test_rows",
    "feature_count", "calibrated",
})

# Required keys in label_distribution.train / label_distribution.test
_REQUIRED_LABEL_DIST_SPLIT = frozenset({"n", "win_rate"})


def validate_training_report(report: Dict[str, Any], strict: bool = False) -> List[str]:
    """Validate the structure of a training report dict.

    Parameters
    ----------
    report : dict
        Parsed training_report.json content.
    strict : bool
        If True, raises SchemaError on first violation.
        If False, returns a list of warning strings (empty = valid).

    Returns
    -------
    List[str]
        Validation warnings. Empty if report passes all checks.

    Raises
    ------
    SchemaError
        When strict=True and any required key is missing or has wrong type.
    """
    warnings: List[str] = []

    def _warn(msg: str) -> None:
        warnings.append(msg)
        if strict:
            raise SchemaError(msg)

    if not isinstance(report, dict):
        _warn(f"training_report must be a dict, got {type(report).__name__}")
        return warnings

    # Top-level keys
    for key in _REQUIRED_TOP:
        if key not in report:
            _warn(f"Missing required top-level key: '{key}'")

    # settings sub-keys
    settings = report.get("settings")
    if isinstance(settings, dict):
        for key in _REQUIRED_SETTINGS:
            if key not in settings:
                _warn(f"Missing required settings key: 'settings.{key}'")
        # Type checks
        for int_key in ("rows_used", "train_rows", "test_rows", "feature_count"):
            val = settings.get(int_key)
            if val is not None and not isinstance(val, (int, float)):
                _warn(f"settings.{int_key} should be numeric, got {type(val).__name__}")
        if settings.get("feature_count", 1) <= 0:
            _warn("settings.feature_count must be > 0")
        if settings.get("rows_used", 1) <= 0:
            _warn("settings.rows_used must be > 0")
    elif settings is not None:
        _warn(f"report['settings'] must be a dict, got {type(settings).__name__}")

    # label_distribution sub-keys
    ld = report.get("label_distribution")
    if isinstance(ld, dict):
        for split in ("train", "test"):
            split_data = ld.get(split)
            if isinstance(split_data, dict):
                for key in _REQUIRED_LABEL_DIST_SPLIT:
                    if key not in split_data:
                        _warn(f"Missing label_distribution.{split}.{key}")
                wr = split_data.get("win_rate")
                if wr is not None and not (0.0 <= float(wr) <= 1.0):
                    _warn(f"label_distribution.{split}.win_rate={wr} out of [0,1]")
    elif ld is not None:
        _warn(f"report['label_distribution'] must be a dict, got {type(ld).__name__}")

    # leakage_check must exist (don't require specific sub-keys — may vary)
    lc = report.get("leakage_check")
    if lc is not None and not isinstance(lc, dict):
        _warn(f"report['leakage_check'] must be a dict, got {type(lc).__name__}")

    # walk_forward — optional but if present, check roc_auc is a float
    wf = report.get("walk_forward")
    if isinstance(wf, dict) and "roc_auc" in wf:
        roc = wf["roc_auc"]
        if roc is not None and not isinstance(roc, (int, float)):
            _warn(f"walk_forward.roc_auc should be numeric or null, got {type(roc).__name__}")

    return warnings
