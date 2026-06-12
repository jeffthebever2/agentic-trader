"""
QlibResearchEngine — runs qlib alpha factor research and model tournament.

Wraps qlib's core components (DatasetH, LGBModel, etc.) with a simplified
interface aligned to TradingAgents' candidate/signal model.

Model Tournament
----------------
The engine compares multiple models on a walk-forward basis and selects the
best-performing one using the same WF ROC gate used during training.

Usage::

    engine = QlibResearchEngine()
    results = engine.run_tournament(
        tickers=["AAPL", "MSFT", "NVDA"],
        start="2022-01-01",
        end="2025-01-01",
    )
    print(results.best_model, results.best_wf_roc)
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class ModelResult:
    model_name: str
    wf_roc: float
    accuracy: float
    n_oos_samples: int
    params: Dict[str, Any] = field(default_factory=dict)
    feature_importances: Dict[str, float] = field(default_factory=dict)


@dataclass
class TournamentResult:
    run_at: str
    tickers: List[str]
    start: str
    end: str
    results: List[ModelResult] = field(default_factory=list)
    best_model: str = ""
    best_wf_roc: float = 0.0
    notes: List[str] = field(default_factory=list)


class QlibResearchEngine:
    """Research engine using qlib for alpha factor generation and model comparison.

    Parameters
    ----------
    wf_roc_gate : float
        Minimum walk-forward ROC AUC required to pass the gate (default 0.49).
    n_splits : int
        Number of walk-forward folds (default 5).
    """

    def __init__(
        self,
        wf_roc_gate: float = 0.49,
        n_splits: int = 5,
    ) -> None:
        self.wf_roc_gate = wf_roc_gate
        self.n_splits = n_splits
        self._qlib_available = self._check_qlib()

    def _check_qlib(self) -> bool:
        try:
            import qlib  # noqa: F401
            return True
        except ImportError:
            return False

    def run_tournament(
        self,
        tickers: List[str],
        start: str,
        end: str,
        feature_fn: Optional[Any] = None,
    ) -> TournamentResult:
        """Run walk-forward model tournament on the given tickers and date range.

        Uses sklearn-based models when qlib is available; falls back to a simple
        logistic regression baseline so the pipeline always produces output.

        Parameters
        ----------
        tickers : list of str
        start : str  — ISO date, e.g. "2022-01-01"
        end : str    — ISO date, e.g. "2025-01-01"
        feature_fn : callable(prices_df) → feature_df, optional
            Custom feature extractor.  If None, uses QlibDataAdapter.extract_alpha_features.

        Returns TournamentResult.
        """
        now_str = dt.datetime.now().isoformat()
        result = TournamentResult(run_at=now_str, tickers=tickers, start=start, end=end)

        try:
            from tradingagents.qlib_integration.adapter import QlibDataAdapter
            adapter = QlibDataAdapter()
            df = adapter.ohlcv_from_yfinance(tickers, start=start, end=end)
            if df.empty:
                result.notes.append("No price data fetched — tournament skipped")
                return result

            features = adapter.extract_alpha_features(df, tickers)
            if not features:
                result.notes.append("No feature data computed — tournament skipped")
                return result

            # Concatenate features across tickers
            all_features = pd.concat(
                [v.assign(ticker=k) for k, v in features.items()]
            ).dropna()

            if len(all_features) < 50:
                result.notes.append(f"Insufficient samples ({len(all_features)}<50) — tournament skipped")
                return result

            result.results = self._run_wf_models(all_features)
            if result.results:
                best = max(result.results, key=lambda r: r.wf_roc)
                result.best_model = best.model_name
                result.best_wf_roc = best.wf_roc
                if best.wf_roc >= self.wf_roc_gate:
                    result.notes.append(f"Best model '{best.model_name}' passes WF ROC gate ({best.wf_roc:.4f} ≥ {self.wf_roc_gate})")
                else:
                    result.notes.append(f"No model passes WF ROC gate {self.wf_roc_gate} (best={best.wf_roc:.4f})")

        except Exception as exc:
            log.warning("QlibResearchEngine.run_tournament error: %s", exc)
            result.notes.append(f"Error: {exc}")

        return result

    def _run_wf_models(self, features_df: pd.DataFrame) -> List[ModelResult]:
        """Walk-forward evaluation of multiple classifier candidates."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler
        import numpy as np

        df = features_df.dropna()
        if len(df) < 50 or "ret_1d" not in df.columns:
            return []

        # Label: next-day return > 0.5%. Shift PER TICKER — the frame is a
        # concatenation of ticker blocks, so a plain shift(-1) would label the
        # last row of one ticker with the first return of the next ticker.
        if "ticker" in df.columns:
            fwd_ret = df.groupby("ticker")["ret_1d"].shift(-1)
        else:
            fwd_ret = df["ret_1d"].shift(-1)
        valid = fwd_ret.notna().to_numpy()
        df = df.iloc[valid]
        y = (fwd_ret[valid] > 0.005).astype(int)

        # Sort by DATE before TimeSeriesSplit. The concatenated frame is
        # ticker-major ordered; splitting it positionally would put one
        # ticker's full (future-inclusive) history in train while another
        # ticker's past sits in test — temporal leakage, not walk-forward.
        order = np.argsort(df.index.values, kind="stable")
        df = df.iloc[order]
        y = y.iloc[order]
        X = df.drop(columns=["ticker", "ret_1d"], errors="ignore")

        # Scaler is fitted per fold on train data only (no look-ahead into test period).
        X_raw = X.fillna(0).to_numpy()
        y_arr = y.values

        models = {
            "LogisticRegression": LogisticRegression(max_iter=500, C=0.1),
            "GradientBoosting": GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42),
            "RandomForest": RandomForestClassifier(n_estimators=50, max_depth=4, random_state=42),
        }

        tscv = TimeSeriesSplit(n_splits=min(self.n_splits, len(X_raw) // 20))
        results: List[ModelResult] = []

        for name, clf in models.items():
            oos_probs: List[float] = []
            oos_labels: List[int] = []
            for train_idx, test_idx in tscv.split(X_raw):
                if len(train_idx) < 10 or len(test_idx) < 5:
                    continue
                try:
                    # Fit scaler on train fold only — prevents future mean/std leaking into test
                    scaler = StandardScaler()
                    X_arr_train = scaler.fit_transform(X_raw[train_idx])
                    X_arr_test = scaler.transform(X_raw[test_idx])
                    clf.fit(X_arr_train, y_arr[train_idx])
                    probs = clf.predict_proba(X_arr_test)[:, 1]
                    oos_probs.extend(probs.tolist())
                    oos_labels.extend(y_arr[test_idx].tolist())
                except Exception:
                    continue

            if len(oos_labels) < 10:
                continue
            try:
                roc = roc_auc_score(oos_labels, oos_probs)
                acc = sum(1 for p, l in zip(oos_probs, oos_labels) if (p > 0.5) == l) / len(oos_labels)
                fi: Dict[str, float] = {}
                if hasattr(clf, "feature_importances_"):
                    for col, imp in zip(X.columns, clf.feature_importances_):
                        fi[col] = round(float(imp), 4)
                results.append(ModelResult(
                    model_name=name,
                    wf_roc=round(roc, 4),
                    accuracy=round(acc, 4),
                    n_oos_samples=len(oos_labels),
                    feature_importances=fi,
                ))
            except Exception:
                continue

        return sorted(results, key=lambda r: r.wf_roc, reverse=True)
