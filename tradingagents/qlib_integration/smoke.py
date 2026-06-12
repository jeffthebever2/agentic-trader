"""Smoke test for the qlib integration layer."""
from __future__ import annotations

import datetime as dt
from typing import Dict, Any


def smoke_test() -> Dict[str, Any]:
    """Verify qlib is installed and the integration layer is functional.

    Returns dict with keys:
      - qlib_installed: bool
      - qlib_version: str or None
      - adapter_ok: bool
      - engine_ok: bool
      - errors: list of str

    Raises nothing — caller should inspect the returned dict.
    """
    result: Dict[str, Any] = {
        "qlib_installed": False,
        "qlib_version": None,
        "adapter_ok": False,
        "engine_ok": False,
        "errors": [],
    }

    # 1. Check qlib import
    try:
        import qlib
        result["qlib_installed"] = True
        result["qlib_version"] = getattr(qlib, "__version__", "unknown")
    except ImportError as exc:
        result["errors"].append(f"qlib import failed: {exc}")
        return result

    # 2. Check adapter
    try:
        import pandas as pd
        from tradingagents.qlib_integration.adapter import QlibDataAdapter

        adapter = QlibDataAdapter()
        # Build synthetic OHLCV DataFrame
        idx = pd.date_range("2024-01-01", periods=60, freq="B")
        import numpy as np
        rng = np.random.default_rng(42)
        prices = 100 + rng.standard_normal(60).cumsum()
        raw = pd.DataFrame({
            "Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
            "Close": prices, "Volume": rng.integers(1_000_000, 5_000_000, 60).astype(float),
        }, index=idx)
        norm = adapter.normalize_ohlcv(raw)
        assert "close" in norm.columns and len(norm) == 60
        features = adapter.extract_alpha_features(norm, ["TEST"])
        assert "TEST" in features
        result["adapter_ok"] = True
    except Exception as exc:
        result["errors"].append(f"adapter check failed: {exc}")

    # 3. Check engine (without network)
    try:
        from tradingagents.qlib_integration.engine import QlibResearchEngine
        engine = QlibResearchEngine()
        assert engine._qlib_available  # qlib is installed
        result["engine_ok"] = True
    except Exception as exc:
        result["errors"].append(f"engine check failed: {exc}")

    return result


if __name__ == "__main__":
    import json
    print(json.dumps(smoke_test(), indent=2))
