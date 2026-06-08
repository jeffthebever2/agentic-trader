#!/usr/bin/env python3
"""One-command, resumable training orchestrator for TradingAgents.

This script intentionally optimizes for not wasting long training runs:
  - every stage has its own log file
  - state is checkpointed after every stage
  - long outputs train into staging directories first
  - existing deployed artifacts are backed up before promotion
  - failed validation blocks promotion instead of overwriting good models

Common use:
    python3 scripts/train_everything.py
    python3 scripts/train_everything.py --dry-run
    python3 scripts/train_everything.py --resume tmp/train_everything/<run_id>/state.json
    python3 scripts/train_everything.py --profile full --include-rl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ROOT / "tmp" / "train_everything"


@dataclass
class Stage:
    name: str
    command: list[str] = field(default_factory=list)
    status: str = "pending"
    log_path: str = ""
    started_at: str = ""
    ended_at: str = ""
    exit_code: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


def _tee_run(cmd: list[str], log_path: Path, cwd: Path = ROOT) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{_now()}] CMD: {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        rc = proc.wait()
        log.write(f"\n[{_now()}] EXIT: {rc}\n")
        return rc


def _promote_dir(staging: Path, target: Path, backups_root: Path, dry_run: bool) -> Path | None:
    if dry_run:
        print(f"[dry-run] promote {_rel(staging)} -> {_rel(target)}")
        return None
    if not staging.exists():
        raise FileNotFoundError(f"staging directory not found: {staging}")
    backup = None
    if target.exists():
        backups_root.mkdir(parents=True, exist_ok=True)
        backup = backups_root / f"{target.name}.backup_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if backup.exists():
            shutil.rmtree(backup)
        shutil.move(str(target), str(backup))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(target))
    return backup


def _validate_ml_artifacts(model_dir: Path, min_wf_roc: float = 0.49) -> dict[str, Any]:
    bundle = model_dir / "model_bundle.joblib"
    report_path = model_dir / "training_report.json"
    if not bundle.exists():
        raise RuntimeError(f"model bundle missing: {bundle}")
    if not report_path.exists():
        raise RuntimeError(f"training report missing: {report_path}")
    report = _load_json(report_path)

    try:
        from tradingagents.ml.training_report_schema import validate_training_report
        schema_warnings = validate_training_report(report, strict=False)
    except Exception as exc:
        schema_warnings = [f"schema validation skipped: {exc}"]

    leakage = report.get("leakage_check", {})
    if leakage.get("leaky_features"):
        raise RuntimeError(f"leakage check failed: {leakage}")
    if leakage.get("status") not in (None, "clean"):
        raise RuntimeError(f"leakage status not clean: {leakage}")

    wf = report.get("walk_forward", {})
    wf_roc = wf.get("roc_auc") if isinstance(wf, dict) else None
    if wf_roc is not None and float(wf_roc) < min_wf_roc:
        raise RuntimeError(f"walk-forward ROC {wf_roc} < {min_wf_roc}")

    return {
        "bundle": str(bundle),
        "report": str(report_path),
        "rows_used": report.get("settings", {}).get("rows_used"),
        "wf_roc": wf_roc,
        "high_conf_win_rate": wf.get("high_conf_win_rate") if isinstance(wf, dict) else None,
        "deflated_sharpe": report.get("deflated_sharpe"),
        "cpcv": report.get("cpcv"),
        "noise_feature_test": report.get("noise_feature_test"),
        "schema_warnings": schema_warnings,
    }


def _validate_hmm_artifacts(model_dir: Path) -> dict[str, Any]:
    bundle = model_dir / "hmm_regime.joblib"
    report_path = model_dir / "hmm_regime_report.json"
    if not bundle.exists():
        raise RuntimeError(f"HMM bundle missing: {bundle}")
    if not report_path.exists():
        raise RuntimeError(f"HMM report missing: {report_path}")
    report = _load_json(report_path)
    if int(report.get("n_training_bars", 0)) < 250:
        raise RuntimeError(f"HMM trained on too few bars: {report.get('n_training_bars')}")
    return {"bundle": str(bundle), "report": str(report_path), "n_training_bars": report.get("n_training_bars")}


def _validate_rl_artifacts(checkpoint_dir: Path) -> dict[str, Any]:
    required = ["actor.pt", "critic.pt", "meta.json"]
    missing = [name for name in required if not (checkpoint_dir / name).exists()]
    if missing:
        raise RuntimeError(f"RL checkpoint missing files: {missing}")
    meta = _load_json(checkpoint_dir / "meta.json")
    return {"checkpoint_dir": str(checkpoint_dir), "tickers": meta.get("tickers"), "obs_dim": meta.get("obs_dim")}


class TrainingRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.python = sys.executable
        if args.resume:
            self.state_path = Path(args.resume).expanduser()
            self.state = _load_json(self.state_path)
            self.run_dir = self.state_path.parent
            self.run_id = self.state.get("run_id", self.run_dir.name)
        else:
            self.run_id = args.run_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_dir = RUNS_ROOT / self.run_id
            self.state_path = self.run_dir / "state.json"
            self.state = {
                "run_id": self.run_id,
                "created_at": _now(),
                "profile": args.profile,
                "root": str(ROOT),
                "stages": {},
                "promotions": [],
            }
        self.logs_dir = self.run_dir / "logs"
        self.staging_dir = self.run_dir / "staging"
        self.backups_dir = self.run_dir / "backups"

    def save(self) -> None:
        _write_json(self.state_path, self.state)

    def stage(self, name: str, cmd: list[str] | None = None, detail: dict[str, Any] | None = None) -> Stage:
        stages = self.state.setdefault("stages", {})
        existing = stages.get(name, {})
        stage = Stage(**{**asdict(Stage(name=name)), **existing})
        if cmd is not None:
            stage.command = cmd
        if detail:
            stage.detail.update(detail)
        if not stage.log_path:
            stage.log_path = str(self.logs_dir / f"{name}.log")
        stages[name] = asdict(stage)
        self.save()
        return stage

    def should_skip(self, name: str) -> bool:
        if not self.args.resume:
            return False
        status = self.state.get("stages", {}).get(name, {}).get("status")
        return status == "succeeded" and not self.args.force_stage

    def run_command_stage(self, name: str, cmd: list[str], *, optional: bool = False) -> None:
        if self.should_skip(name):
            print(f"[resume] skipping succeeded stage: {name}")
            return
        stage = self.stage(name, cmd)
        stage.status = "dry_run" if self.args.dry_run else "running"
        stage.started_at = _now()
        self.state["stages"][name] = asdict(stage)
        self.save()

        print(f"\n=== {name} ===")
        print(" ".join(cmd))
        if self.args.dry_run:
            stage.status = "succeeded"
            stage.exit_code = 0
            stage.ended_at = _now()
            self.state["stages"][name] = asdict(stage)
            self.save()
            return

        rc = _tee_run(cmd, Path(stage.log_path))
        stage.exit_code = rc
        stage.ended_at = _now()
        stage.status = "succeeded" if rc == 0 else "failed"
        self.state["stages"][name] = asdict(stage)
        self.save()
        if rc != 0 and not optional:
            raise SystemExit(f"stage {name!r} failed with exit code {rc}; resume with --resume {self.state_path}")

    def run_callable_stage(self, name: str, fn, *, optional: bool = False) -> None:
        if self.should_skip(name):
            print(f"[resume] skipping succeeded stage: {name}")
            return
        stage = self.stage(name)
        stage.status = "dry_run" if self.args.dry_run else "running"
        stage.started_at = _now()
        self.state["stages"][name] = asdict(stage)
        self.save()
        print(f"\n=== {name} ===")
        try:
            if self.args.dry_run:
                stage.detail["dry_run"] = True
            else:
                result = fn()
                if result is not None:
                    stage.detail.update(result)
            stage.status = "succeeded"
            stage.exit_code = 0
        except Exception as exc:
            stage.status = "failed"
            stage.exit_code = 1
            stage.detail["error"] = str(exc)
            self.state["stages"][name] = asdict(stage)
            self.save()
            if not optional:
                raise
        finally:
            stage.ended_at = _now()
            self.state["stages"][name] = asdict(stage)
            self.save()

    def preflight(self) -> None:
        missing = []
        for rel in [
            "backtest.py",
            "scripts/retrain_weekly.py",
            "scripts/train_ml_models.py",
            "scripts/train_ml_from_stock_data.py",
            "scripts/train_hmm_regime.py",
            "scripts/model_readiness_report.py",
            "all_tickers.txt",
        ]:
            if not (ROOT / rel).exists():
                missing.append(rel)
        if missing:
            raise RuntimeError(f"missing required files: {missing}")
        return {
            "python": self.python,
            "run_dir": str(self.run_dir),
            "state_path": str(self.state_path),
        }

    def command_retrain_weekly(self) -> list[str]:
        staging_output = self.staging_dir / "latest"
        cmd = [
            self.python, "scripts/retrain_weekly.py",
            "--months", str(self.args.months),
            "--output-dir", str(staging_output),
            "--hold", str(self.args.production_hold),
            "--min-roc", str(self.args.min_roc),
            "--max-brier", str(self.args.max_brier),
            "--account-commission", str(self.args.account_commission),
            "--account-slippage-bps", str(self.args.account_slippage_bps),
            "--compute-dsr",
            "--dsr-n-trials", str(self.args.dsr_n_trials),
        ]
        if self.args.cpcv:
            cmd.extend(["--cpcv", "--cpcv-splits", str(self.args.cpcv_splits), "--cpcv-test-splits", str(self.args.cpcv_test_splits)])
        if self.args.noise_feature_test:
            cmd.append("--noise-feature-test")
        if self.args.production_resume_csv:
            cmd.extend(["--resume-csv", self.args.production_resume_csv])
        if self.args.skip_holdout:
            cmd.append("--skip-holdout")
        if getattr(self.args, "include_qlib_features", False):
            cmd.append("--include-qlib-features")
        return cmd

    def command_stock_universe(self) -> tuple[list[str], Path]:
        target = ROOT / "ml_models" / "stock_universe"
        staging = self.staging_dir / "stock_universe"
        end_date = self.args.stock_end
        if not end_date:
            end_date = (dt.date.today() - dt.timedelta(days=self.args.stock_hold + 15)).isoformat()
        cmd = [
            self.python, "scripts/train_ml_from_stock_data.py",
            "--tickers", self.args.tickers,
            "--start", self.args.stock_start,
            "--end", end_date,
            "--output-dir", str(staging),
            "--hold", str(self.args.stock_hold),
            "--target-mult", str(self.args.target_mult),
            "--stop-mult", str(self.args.stop_mult),
            "--label-mode", self.args.label_mode,
            "--label-slippage-bps", str(self.args.label_slippage_bps),
            "--compute-dsr",
            "--dsr-n-trials", str(self.args.dsr_n_trials),
            "--models", "xgb", "rf",
        ]
        if self.args.cpcv:
            cmd.extend(["--cpcv", "--cpcv-splits", str(self.args.cpcv_splits), "--cpcv-test-splits", str(self.args.cpcv_test_splits)])
        if self.args.noise_feature_test:
            cmd.append("--noise-feature-test")
        if self.args.rebuild_stock_dataset:
            cmd.append("--rebuild-dataset")
        else:
            cmd.append("--resume-dataset")
        if self.args.max_tickers:
            cmd.extend(["--max-tickers", str(self.args.max_tickers)])
        return cmd, target

    def command_hmm(self) -> tuple[list[str], Path]:
        target = ROOT / "ml_models" / "hmm_regime"
        staging = self.staging_dir / "hmm_regime"
        return [
            self.python, "scripts/train_hmm_regime.py",
            "--ticker", self.args.hmm_ticker,
            "--start", self.args.hmm_start,
            "--end", self.args.hmm_end or dt.date.today().isoformat(),
            "--output-dir", str(staging),
        ], target

    def command_rl(self) -> tuple[list[str], Path]:
        target = ROOT / "rl_models" / "td3_checkpoint"
        staging = self.staging_dir / "td3_checkpoint"
        cmd = [
            self.python, "scripts/train_rl_agent.py",
            "--start", self.args.rl_start,
            "--end", self.args.rl_end,
            "--test-start", self.args.rl_test_start,
            "--test-end", self.args.rl_test_end,
            "--iterations", str(self.args.rl_iterations),
            "--checkpoint-dir", str(staging),
            "--log-dir", str(self.run_dir / "rl_runs"),
            "--device", self.args.rl_device,
        ]
        if self.args.rl_tickers_file:
            cmd.extend(["--tickers-file", self.args.rl_tickers_file])
        else:
            cmd.extend(["--tickers", *self.args.rl_tickers])
        if self.args.rl_resume_checkpoint:
            cmd.extend(["--checkpoint", self.args.rl_resume_checkpoint])
        return cmd, target

    def run(self) -> None:
        self.run_callable_stage("preflight", self.preflight)

        if not self.args.skip_tests:
            self.run_command_stage("pre_training_tests", [
                self.python, "-m", "pytest",
                "tests/test_ml_training.py",
                "tests/test_label_leakage_guard.py",
                "tests/test_training_report_schema.py",
                "tests/test_model_readiness_report.py",
                "tests/test_qlib_integration.py",
                "tests/test_hmm_regime.py",
                "-q",
            ])

        if not self.args.skip_production:
            self.run_command_stage("production_retrain_latest", self.command_retrain_weekly())
            self.run_callable_stage(
                "production_validate_promote_latest",
                lambda: self._validate_and_promote_ml(self.staging_dir / "latest", ROOT / "ml_models" / "latest"),
            )
            self.run_command_stage("production_readiness", [
                self.python, "scripts/model_readiness_report.py",
                "--bundle", "ml_models/latest/model_bundle.joblib",
                "--report", "ml_models/latest/training_report.json",
                "--json",
            ])

        if not self.args.skip_stock_universe:
            stock_cmd, stock_target = self.command_stock_universe()
            self.run_command_stage("stock_universe_train_staging", stock_cmd)
            self.run_callable_stage(
                "stock_universe_validate_promote",
                lambda: self._validate_and_promote_ml(self.staging_dir / "stock_universe", stock_target),
            )

        if not self.args.skip_hmm:
            hmm_cmd, hmm_target = self.command_hmm()
            self.run_command_stage("hmm_regime_train_staging", hmm_cmd)
            self.run_callable_stage(
                "hmm_regime_validate_promote",
                lambda: self._validate_and_promote_hmm(self.staging_dir / "hmm_regime", hmm_target),
            )

        if not self.args.skip_qlib:
            self.run_command_stage("qlib_smoke", [
                self.python, "-m", "tradingagents.qlib_integration.smoke",
            ])

        if self.args.include_rl:
            rl_cmd, rl_target = self.command_rl()
            self.run_command_stage("rl_td3_train_staging", rl_cmd)
            self.run_callable_stage(
                "rl_td3_validate_promote",
                lambda: self._validate_and_promote_rl(self.staging_dir / "td3_checkpoint", rl_target),
            )

        if not self.args.skip_final_tests:
            self.run_command_stage("final_relevant_tests", [
                self.python, "-m", "pytest",
                "tests/test_ml_training.py",
                "tests/test_label_leakage_guard.py",
                "tests/test_training_report_schema.py",
                "tests/test_model_readiness_report.py",
                "tests/test_qlib_integration.py",
                "tests/test_hmm_regime.py",
                "tests/test_deflated_sharpe.py",
                "tests/test_factor_ic.py",
                "-q",
            ])

        self.run_command_stage("daily_audit", [
            self.python, "scripts/daily_audit.py", "--json",
        ], optional=True)
        self.state["completed_at"] = _now()
        self.save()
        print(f"\nTraining orchestration complete. State: {self.state_path}")

    def _validate_and_promote_ml(self, staging: Path, target: Path) -> dict[str, Any]:
        validation = _validate_ml_artifacts(staging, min_wf_roc=self.args.min_roc)
        backup = _promote_dir(staging, target, self.backups_dir, self.args.dry_run)
        promotion = {"kind": "ml", "target": str(target), "backup": str(backup) if backup else None, "validation": validation}
        self.state.setdefault("promotions", []).append(promotion)
        self.save()
        return promotion

    def _validate_and_promote_hmm(self, staging: Path, target: Path) -> dict[str, Any]:
        validation = _validate_hmm_artifacts(staging)
        backup = _promote_dir(staging, target, self.backups_dir, self.args.dry_run)
        promotion = {"kind": "hmm", "target": str(target), "backup": str(backup) if backup else None, "validation": validation}
        self.state.setdefault("promotions", []).append(promotion)
        self.save()
        return promotion

    def _validate_and_promote_rl(self, staging: Path, target: Path) -> dict[str, Any]:
        validation = _validate_rl_artifacts(staging)
        backup = _promote_dir(staging, target, self.backups_dir, self.args.dry_run)
        promotion = {"kind": "rl", "target": str(target), "backup": str(backup) if backup else None, "validation": validation}
        self.state.setdefault("promotions", []).append(promotion)
        self.save()
        return promotion


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train/retrain all TradingAgents models in one resumable command.")
    p.add_argument("--profile", choices=["quick", "safe", "full"], default="safe",
                   help="quick is for smoke-sized tests; safe is production default; full enables all long validations.")
    p.add_argument("--run-id", default="")
    p.add_argument("--resume", default="", help="Resume from tmp/train_everything/<run_id>/state.json")
    p.add_argument("--force-stage", action="store_true", help="With --resume, rerun stages even if marked succeeded.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--tickers", default="all_tickers.txt")
    p.add_argument("--max-tickers", type=int, default=None, help="Limit stock-universe tickers for smoke testing.")

    p.add_argument("--skip-tests", action="store_true")
    p.add_argument("--skip-final-tests", action="store_true")
    p.add_argument("--skip-production", action="store_true")
    p.add_argument("--skip-stock-universe", action="store_true")
    p.add_argument("--skip-hmm", action="store_true")
    p.add_argument("--skip-qlib", action="store_true")
    p.add_argument("--include-qlib-features", action="store_true",
                   help="Pass --include-qlib-features to retrain_weekly.py to train with qlib alpha factors.")
    p.add_argument("--include-rl", action="store_true", help="Include long TD3/RL training.")

    p.add_argument("--months", type=int, default=84)
    p.add_argument("--production-hold", type=int, default=10)
    p.add_argument("--production-resume-csv", default="")
    p.add_argument("--min-roc", type=float, default=0.49)
    p.add_argument("--max-brier", type=float, default=0.25)
    p.add_argument("--account-commission", type=float, default=1.0)
    p.add_argument("--account-slippage-bps", type=float, default=5.0)
    p.add_argument("--skip-holdout", action="store_true")

    p.add_argument("--stock-start", default="2019-01-01")
    p.add_argument("--stock-end", default="", help="Default: today - hold - 15 days, so labels have forward data.")
    p.add_argument("--stock-hold", type=int, default=3)
    p.add_argument("--target-mult", type=float, default=1.2)
    p.add_argument("--stop-mult", type=float, default=1.0)
    p.add_argument("--label-mode", choices=["fixed_horizon", "triple_barrier"], default="triple_barrier")
    p.add_argument("--label-slippage-bps", type=float, default=10.0)
    p.add_argument("--rebuild-stock-dataset", action="store_true")

    p.add_argument("--cpcv", action="store_true", default=True)
    p.add_argument("--no-cpcv", dest="cpcv", action="store_false")
    p.add_argument("--cpcv-splits", type=int, default=5)
    p.add_argument("--cpcv-test-splits", type=int, default=2)
    p.add_argument("--dsr-n-trials", type=int, default=50)
    p.add_argument("--noise-feature-test", action="store_true", default=True)
    p.add_argument("--no-noise-feature-test", dest="noise_feature_test", action="store_false")

    p.add_argument("--hmm-ticker", default="SPY")
    p.add_argument("--hmm-start", default="2015-01-01")
    p.add_argument("--hmm-end", default="")

    p.add_argument("--rl-tickers", nargs="+", default=["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"])
    p.add_argument("--rl-tickers-file", default="")
    p.add_argument("--rl-start", default="2018-01-01")
    p.add_argument("--rl-end", default="2023-12-31")
    p.add_argument("--rl-test-start", default="2024-01-01")
    p.add_argument("--rl-test-end", default="2024-12-31")
    p.add_argument("--rl-iterations", type=int, default=500_000)
    p.add_argument("--rl-device", default="cpu")
    p.add_argument("--rl-resume-checkpoint", default="")
    args = p.parse_args()

    if args.profile == "quick":
        args.max_tickers = args.max_tickers or 25
        args.months = min(args.months, 12)
        args.rl_iterations = min(args.rl_iterations, 2_000)
        args.skip_holdout = True
    elif args.profile == "full":
        args.include_rl = True if not any(a == "--include-rl" for a in sys.argv) else args.include_rl
    return args


def main() -> None:
    run = TrainingRun(parse_args())
    run.save()
    try:
        run.run()
    except KeyboardInterrupt:
        print(f"\nInterrupted. Resume with: python3 scripts/train_everything.py --resume {run.state_path}")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nFAILED: {exc}")
        print(f"Resume after fixing with: python3 scripts/train_everything.py --resume {run.state_path}")
        raise


if __name__ == "__main__":
    main()
