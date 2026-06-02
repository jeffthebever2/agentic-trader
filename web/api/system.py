"""System-level endpoints: metrics, services, and retrain control.

All endpoints require at minimum user auth.  Destructive/action endpoints
(retrain trigger) require admin.  No secrets are ever returned.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.auth import get_current_user, require_admin

ROOT = Path(__file__).parent.parent.parent
TMP = ROOT / "tmp"

router = APIRouter()

_START_TIME = dt.datetime.now(dt.timezone.utc)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ok(data: Any) -> dict:
    return {"ok": True, "timestamp": _now_iso(), "data": data}


# ── Metrics ───────────────────────────────────────────────────────────────────

@router.get("/system/metrics")
async def system_metrics(_user: dict = Depends(get_current_user)):
    """CPU, RAM, disk, process uptime.  No secrets exposed."""
    now = dt.datetime.now(dt.timezone.utc)
    uptime_s = round((now - _START_TIME).total_seconds())
    data: dict[str, Any] = {"uptime_seconds": uptime_s}

    # psutil (installed; fallback gracefully if missing)
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(ROOT))
        proc = psutil.Process(os.getpid())
        data["cpu_percent"] = round(cpu, 1)
        data["memory"] = {
            "total_mb": round(mem.total / 1024**2),
            "used_mb": round(mem.used / 1024**2),
            "available_mb": round(mem.available / 1024**2),
            "percent": round(mem.percent, 1),
        }
        data["disk"] = {
            "total_gb": round(disk.total / 1e9, 1),
            "used_gb": round(disk.used / 1e9, 1),
            "free_gb": round(disk.free / 1e9, 1),
            "percent": round(disk.percent, 1),
        }
        data["process"] = {
            "pid": proc.pid,
            "threads": proc.num_threads(),
            "memory_mb": round(proc.memory_info().rss / 1024**2, 1),
            "cpu_percent": round(proc.cpu_percent(interval=None), 1),
        }
    except ImportError:
        # Fallback: os.statvfs for disk only
        try:
            st = os.statvfs(str(ROOT))
            free_gb = round((st.f_bavail * st.f_frsize) / 1e9, 1)
            total_gb = round((st.f_blocks * st.f_frsize) / 1e9, 1)
            data["disk"] = {
                "free_gb": free_gb,
                "total_gb": total_gb,
                "percent": round((1 - st.f_bavail / st.f_blocks) * 100, 1) if st.f_blocks else 0,
            }
        except Exception:
            data["disk"] = {"error": "unavailable"}
        data["note"] = "psutil not installed — CPU/RAM metrics unavailable"
    except Exception as exc:
        data["error"] = str(exc)

    return _ok(data)


# ── Services ──────────────────────────────────────────────────────────────────

def _pgrep(pattern: str) -> list[int]:
    try:
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=3)
        return [int(x) for x in res.stdout.decode().split() if x.strip().isdigit()]
    except Exception:
        return []


def _is_running(pids: list[int]) -> bool:
    return bool(pids)


@router.get("/system/services")
async def system_services(_user: dict = Depends(get_current_user)):
    """Status of key background services. No credentials returned."""
    data: dict[str, Any] = {}

    # Paper runner
    from web.api.paper import _process_status
    try:
        ps = _process_status()
        data["paper_runner"] = {
            "running": ps.get("running", False),
            "pid": ps.get("pid"),
            "started_at": ps.get("started_at"),
        }
    except Exception:
        data["paper_runner"] = {"running": False, "error": "status unavailable"}

    # Cloudflare tunnel
    cf_pids = _pgrep("cloudflared tunnel run")
    data["cloudflare_tunnel"] = {"running": _is_running(cf_pids), "pids": cf_pids}

    # Autofix monitor
    af_pids = _pgrep("autofix_monitor.py")
    data["autofix_monitor"] = {"running": _is_running(af_pids), "pids": af_pids}

    # Retrain process
    rt_pids = _pgrep("retrain_weekly.py")
    data["retrain"] = {"running": _is_running(rt_pids), "pids": rt_pids}

    # ML model freshness
    ml_ok = False
    ml_info: dict[str, Any] = {}
    try:
        mp = ROOT / "ml_models" / "latest" / "model_bundle.joblib"
        if not mp.exists():
            mp = ROOT / "ml_models" / "stock_universe" / "model_bundle.joblib"
        if mp.exists():
            age_h = (dt.datetime.now(dt.timezone.utc).timestamp() - mp.stat().st_mtime) / 3600
            report_path = mp.parent / "training_report.json"
            wf_roc = None
            if report_path.exists():
                import json
                try:
                    rpt = json.loads(report_path.read_text())
                    wf_roc = rpt.get("walk_forward", {}).get("roc_auc")
                except Exception:
                    pass
            ml_ok = True
            ml_info = {"ok": True, "age_hours": round(age_h, 1), "wf_roc": wf_roc}
        else:
            ml_info = {"ok": False, "error": "model bundle not found"}
    except Exception as e:
        ml_info = {"ok": False, "error": str(e)}
    data["ml_model"] = ml_info

    # Disk
    try:
        st = os.statvfs(str(ROOT))
        free_gb = round((st.f_bavail * st.f_frsize) / 1e9, 1)
        data["disk"] = {"ok": free_gb > 5.0, "free_gb": free_gb}
    except Exception:
        data["disk"] = {"ok": None, "error": "unavailable"}

    return _ok(data)


# ── Retrain ───────────────────────────────────────────────────────────────────

class RetrainRequest(BaseModel):
    tickers: str = "all_tickers.txt"


@router.post("/ml/retrain")
async def trigger_retrain(body: RetrainRequest, admin: dict = Depends(require_admin)):
    """Trigger a background retrain.  Admin only.

    Does NOT wait for completion — returns immediately with PID and log path.
    The retrain runs as a subprocess; monitor via `GET /system/services`.
    """
    # Validate tickers param — only allow known filenames, no path traversal
    ticker_name = Path(body.tickers).name
    if ticker_name != body.tickers or not ticker_name.endswith(".txt"):
        raise HTTPException(status_code=422, detail="tickers must be a .txt filename without path separators")
    ticker_path = ROOT / ticker_name
    if not ticker_path.exists():
        raise HTTPException(status_code=404, detail=f"Ticker file not found: {ticker_name}")

    # Check if already running
    running_pids = _pgrep("retrain_weekly.py")
    if running_pids:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": f"Retrain already running (pids: {running_pids})", "pids": running_pids},
        )

    log_path = TMP / "retrain_triggered.log"
    TMP.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "retrain_weekly.py"), "--tickers", ticker_name],
        cwd=str(ROOT),
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    from web.api.admin import record_audit_event
    record_audit_event(
        "ml_retrain_triggered", admin["email"],
        detail=f"Manual retrain started with tickers={ticker_name}",
        meta={"pid": proc.pid, "log": str(log_path)},
    )

    return {"ok": True, "message": "Retrain started", "pid": proc.pid, "log": str(log_path), "tickers": ticker_name}
