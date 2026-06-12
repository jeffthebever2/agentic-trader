"""MLflow-compatible Experiment Tracker — MS-2.

Tries to import mlflow. If unavailable (CI, minimal environments), falls back
to a flat JSONL log at experiment_log.jsonl in the current directory.

Usage:
    from tradingagents.ml.experiment_tracker import ExperimentTracker

    tracker = ExperimentTracker(tracking_uri="mlruns/", experiment_name="retrain_weekly")
    tracker.start_run("cycle_47")
    tracker.log_param("n_estimators", 400)
    tracker.log_metric("wf_roc", 0.5134)
    tracker.log_artifact("ml_models/latest/training_report.json")
    tracker.end_run()
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Optional


class ExperimentTracker:
    """Log params, metrics, and artifacts to MLflow or a local JSONL fallback.

    Parameters
    ----------
    tracking_uri : str or None
        MLflow tracking URI (e.g., "mlruns/" for local file store).
        If None or mlflow not installed, uses JSONL fallback.
    experiment_name : str
        MLflow experiment name. Ignored for JSONL fallback.
    fallback_path : str or Path or None
        Path for JSONL fallback log. Defaults to "experiment_log.jsonl" in cwd.
    """

    def __init__(
        self,
        tracking_uri: Optional[str] = "mlruns/",
        experiment_name: str = "agentic_trader",
        fallback_path: Optional[str] = None,
    ):
        self._tracking_uri = tracking_uri
        self._experiment_name = experiment_name
        self._fallback_path = Path(fallback_path or "experiment_log.jsonl")
        self._use_mlflow = False
        self._run_id: Optional[str] = None
        self._run_name: Optional[str] = None
        self._pending: dict = {}

        # Try to import and configure mlflow
        if tracking_uri is not None:
            try:
                import mlflow  # type: ignore
                mlflow.set_tracking_uri(tracking_uri)
                mlflow.set_experiment(experiment_name)
                self._mlflow = mlflow
                self._use_mlflow = True
            except ImportError:
                self._use_mlflow = False
            except Exception:
                self._use_mlflow = False

    def start_run(self, run_name: str = "") -> "ExperimentTracker":
        """Start a new experiment run."""
        self._run_name = run_name or f"run_{int(time.time())}"
        self._pending = {
            "run_name": self._run_name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "params": {},
            "metrics": {},
            "artifacts": [],
        }
        if self._use_mlflow:
            try:
                active = self._mlflow.start_run(run_name=self._run_name)
                self._run_id = active.info.run_id
            except Exception:
                self._use_mlflow = False
        return self

    def log_param(self, key: str, value: Any) -> None:
        """Log a hyperparameter."""
        self._pending.setdefault("params", {})[key] = value
        if self._use_mlflow and self._run_id:
            try:
                self._mlflow.log_param(key, value)
            except Exception:
                pass

    def log_metric(self, key: str, value: float, step: Optional[int] = None) -> None:
        """Log a scalar metric."""
        self._pending.setdefault("metrics", {})[key] = value
        if self._use_mlflow and self._run_id:
            try:
                self._mlflow.log_metric(key, value, step=step)
            except Exception:
                pass

    def log_metrics(self, metrics: dict, step: Optional[int] = None) -> None:
        """Log multiple metrics at once."""
        for k, v in metrics.items():
            self.log_metric(k, v, step=step)

    def log_artifact(self, path: str) -> None:
        """Log a file artifact."""
        self._pending.setdefault("artifacts", []).append(str(path))
        if self._use_mlflow and self._run_id:
            try:
                if os.path.exists(path):
                    self._mlflow.log_artifact(path)
            except Exception:
                pass

    def end_run(self) -> None:
        """End the current run and flush to JSONL fallback if needed."""
        if self._use_mlflow:
            try:
                self._mlflow.end_run()
            except Exception:
                pass

        # Always write to JSONL (belt-and-suspenders audit trail)
        if self._pending:
            self._pending["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if self._run_id:
                self._pending["mlflow_run_id"] = self._run_id
            try:
                self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
                with self._fallback_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(self._pending, default=str) + "\n")
            except Exception:
                pass
        self._pending = {}
        self._run_id = None

    def __enter__(self) -> "ExperimentTracker":
        return self

    def __exit__(self, *args) -> None:
        self.end_run()

    @property
    def backend(self) -> str:
        return "mlflow" if self._use_mlflow else "jsonl"
