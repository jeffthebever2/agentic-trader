"""Admin console support endpoints."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.auth import require_admin
from web import users as user_store

ROOT = Path(__file__).parent.parent.parent
TMP = ROOT / "tmp"
AUDIT_FILE = TMP / "admin_audit.jsonl"
FLAGS_FILE = TMP / "admin_flags.json"

router = APIRouter()

DEFAULT_FLAGS: dict[str, bool] = {
    "paper_only_mode": False,
    "real_broker_trading": False,
    "sms_trade_approvals": True,
    "email_one_time_codes": True,
    "onboarding_required": True,
    "cloudflare_ai_primary": True,
}


def record_audit_event(
    event: str,
    actor: str,
    *,
    target: str = "",
    detail: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    """Best-effort local audit append. Never blocks product behavior."""
    try:
        TMP.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "event": event,
            "actor": actor,
            "target": target,
            "detail": detail,
            "meta": meta or {},
        }
        with AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass


def _read_flags() -> dict[str, bool]:
    flags = DEFAULT_FLAGS.copy()
    try:
        if FLAGS_FILE.exists():
            raw = json.loads(FLAGS_FILE.read_text(encoding="utf-8") or "{}")
            for key in DEFAULT_FLAGS:
                if key in raw:
                    flags[key] = bool(raw[key])
    except Exception:
        pass
    return flags


def _write_flags(flags: dict[str, bool]) -> dict[str, bool]:
    cleaned = DEFAULT_FLAGS.copy()
    for key in DEFAULT_FLAGS:
        if key in flags:
            cleaned[key] = bool(flags[key])
    TMP.mkdir(parents=True, exist_ok=True)
    FLAGS_FILE.write_text(json.dumps(cleaned, indent=2, sort_keys=True), encoding="utf-8")
    return cleaned


def _env_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _masked(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "set"
    return value[:4] + "..." + value[-4:]


def _run_capture(cmd: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "return_code": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as exc:
        return {"ok": False, "return_code": None, "stdout": "", "stderr": str(exc)}


def _tail(path: Path, lines: int = 80) -> list[str]:
    try:
        if not path.exists():
            return []
        return path.read_text(errors="ignore").splitlines()[-lines:]
    except Exception as exc:
        return [f"Could not read {path.name}: {exc}"]


def _port_pids(port: int = 8001) -> list[str]:
    if not shutil.which("lsof"):
        return []
    proc = _run_capture(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], timeout=3)
    return [line.strip() for line in proc.get("stdout", "").splitlines() if line.strip()]


def _screen_sessions() -> list[str]:
    if not shutil.which("screen"):
        return []
    proc = _run_capture(["screen", "-ls"], timeout=3)
    return [line.strip() for line in proc.get("stdout", "").splitlines() if "." in line]


def _cloudflared_pids() -> list[str]:
    proc = _run_capture(["pgrep", "-f", "cloudflared.*tunnel"], timeout=3)
    return [line.strip() for line in proc.get("stdout", "").splitlines() if line.strip()]


class RuntimeActionBody(BaseModel):
    port: int = 8001
    tunnel: bool = False


@router.get("/admin/audit")
async def admin_audit(admin: dict[str, Any] = Depends(require_admin)):
    rows: list[dict[str, Any]] = []
    if AUDIT_FILE.exists():
        try:
            for line in AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-250:]:
                if line.strip():
                    rows.append(json.loads(line))
        except Exception:
            rows = []

    # Derived events make a fresh install useful before the audit file fills up.
    for user in user_store.list_users():
        email = user.get("email", "")
        if user.get("terms_accepted_at"):
            rows.append({
                "ts": int(user.get("terms_accepted_at") or 0),
                "event": "legal_acceptance",
                "actor": email,
                "target": email,
                "detail": "Terms, privacy, and risk acknowledgments recorded.",
                "meta": {"role": user.get("role", "user")},
            })
        if user.get("last_login_notice_at"):
            rows.append({
                "ts": int(user.get("last_login_notice_at") or 0),
                "event": "login_notice",
                "actor": email,
                "target": email,
                "detail": "Daily sign-in notice sent.",
                "meta": {},
            })

    rows.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
    return {"events": rows[:100]}


@router.get("/admin/flags")
async def admin_flags(admin: dict[str, Any] = Depends(require_admin)):
    return {"flags": _read_flags(), "defaults": DEFAULT_FLAGS}


class FlagsBody(BaseModel):
    flags: dict[str, bool]


@router.post("/admin/flags")
async def save_admin_flags(body: FlagsBody, admin: dict[str, Any] = Depends(require_admin)):
    flags = _write_flags(body.flags)
    record_audit_event("feature_flags_updated", admin["email"], detail="Admin feature flags changed.", meta=flags)
    return {"flags": flags}


@router.get("/admin/cloudflare")
async def admin_cloudflare(admin: dict[str, Any] = Depends(require_admin)):
    tunnel_cfg = Path.home() / ".cloudflared" / "config.yml"
    tunnel_running = False
    try:
        import subprocess

        proc = subprocess.run(
            ["pgrep", "-f", "cloudflared.*tunnel"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        tunnel_running = proc.returncode == 0
    except Exception:
        tunnel_running = False

    return {
        "domain": os.getenv("PUBLIC_APP_URL", "https://app.agentictrader.org"),
        "access_team_domain": os.getenv("CF_ACCESS_TEAM_DOMAIN", ""),
        "tunnel_config_present": tunnel_cfg.exists(),
        "tunnel_running": tunnel_running,
        "workers_ai": {
            "configured": _env_set("CLOUDFLARE_API_TOKEN") and _env_set("CLOUDFLARE_ACCOUNT_ID"),
            "account_id": _masked(os.getenv("CLOUDFLARE_ACCOUNT_ID", "")),
            "quick_model": os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", ""),
            "deep_model": os.getenv("CLOUDFLARE_DEFAULT_DEEP_MODEL", ""),
        },
        "d1": {
            "configured": _env_set("CLOUDFLARE_D1_DATABASE_ID"),
            "database_id": _masked(os.getenv("CLOUDFLARE_D1_DATABASE_ID", "")),
        },
        "r2": {
            "configured": _env_set("CLOUDFLARE_R2_BUCKET") or _env_set("R2_BUCKET"),
            "bucket": os.getenv("CLOUDFLARE_R2_BUCKET") or os.getenv("R2_BUCKET", ""),
        },
    }


@router.get("/admin/runtime/status")
async def admin_runtime_status(admin: dict[str, Any] = Depends(require_admin)):
    port = int(os.getenv("PORT", "8001"))
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "root": str(ROOT),
        "python": sys.executable,
        "platform": platform.platform(),
        "port": port,
        "web_pids": _port_pids(port),
        "screen_sessions": _screen_sessions(),
        "cloudflared_pids": _cloudflared_pids(),
        "commands": {
            "git": shutil.which("git"),
            "python3": shutil.which("python3"),
            "uv": shutil.which("uv"),
            "cloudflared": shutil.which("cloudflared"),
            "screen": shutil.which("screen"),
            "lsof": shutil.which("lsof"),
        },
        "logs": {
            "web": str(TMP / "web.screen.log"),
            "cloudflared": str(TMP / "cloudflared.screen.log"),
        },
    }


@router.get("/admin/runtime/diagnostics")
async def admin_runtime_diagnostics(admin: dict[str, Any] = Depends(require_admin)):
    git_status = _run_capture(["git", "status", "--short"], timeout=5) if shutil.which("git") else {"stderr": "git not found"}
    git_branch = _run_capture(["git", "branch", "--show-current"], timeout=5) if shutil.which("git") else {"stderr": "git not found"}
    return {
        "runtime": await admin_runtime_status(admin),
        "git": {
            "branch": (git_branch.get("stdout") or "").strip(),
            "status_short": (git_status.get("stdout") or "").splitlines()[:200],
            "error": git_status.get("stderr") or git_branch.get("stderr") or "",
        },
        "env": {
            "env_file_present": (ROOT / ".env").exists(),
            "cloudflare_access": _env_set("CF_ACCESS_TEAM_DOMAIN") and _env_set("CF_ACCESS_AUD"),
            "cloudflare_ai": _env_set("CLOUDFLARE_API_TOKEN") and _env_set("CLOUDFLARE_ACCOUNT_ID"),
            "supabase": _env_set("SUPABASE_URL") and _env_set("SUPABASE_SERVICE_KEY"),
            "smtp": _env_set("SMTP_HOST") and _env_set("SMTP_USERNAME"),
            "sendblue": _env_set("SENDBLUE_API_KEY_ID") and _env_set("SENDBLUE_API_SECRET"),
        },
        "log_tail": {
            "web": _tail(TMP / "web.screen.log"),
            "cloudflared": _tail(TMP / "cloudflared.screen.log"),
        },
    }


@router.post("/admin/runtime/web/restart")
async def admin_runtime_web_restart(
    body: RuntimeActionBody,
    admin: dict[str, Any] = Depends(require_admin),
):
    record_audit_event("runtime_web_restart", admin["email"], detail=f"Requested web restart on port {body.port}.")
    tunnel_flag = [] if body.tunnel else ["--no-tunnel"]
    cmd = [
        "bash",
        "-lc",
        (
            "sleep 1; "
            f"{shutil.which('python3') or sys.executable} "
            "cli/restore_runtime.py start --restart "
            f"--port {int(body.port)} {' '.join(tunnel_flag)} "
            ">> tmp/runtime-admin-actions.log 2>&1"
        ),
    ]
    subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"success": True, "message": "Restart scheduled. Refresh in a few seconds."}


@router.post("/admin/runtime/tunnel/start")
async def admin_runtime_tunnel_start(
    body: RuntimeActionBody,
    admin: dict[str, Any] = Depends(require_admin),
):
    record_audit_event("runtime_tunnel_start", admin["email"], detail="Requested Cloudflare tunnel start.")
    cmd = [sys.executable, "cli/restore_runtime.py", "start", "--port", str(body.port)]
    proc = _run_capture(cmd, timeout=15)
    return {"success": proc["ok"], "result": proc}


@router.post("/admin/runtime/tunnel/stop")
async def admin_runtime_tunnel_stop(admin: dict[str, Any] = Depends(require_admin)):
    record_audit_event("runtime_tunnel_stop", admin["email"], detail="Requested Cloudflare tunnel stop.")
    results: list[dict[str, Any]] = []
    if shutil.which("screen"):
        results.append(_run_capture(["screen", "-S", "agentic-tunnel", "-X", "quit"], timeout=5))
    # Only stop the managed screen session; do not kill unrelated/root cloudflared processes.
    return {"success": True, "results": results, "remaining_cloudflared_pids": _cloudflared_pids()}


@router.get("/admin/export")
async def admin_export(admin: dict[str, Any] = Depends(require_admin)):
    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generated_by": admin["email"],
        "users": user_store.list_users(),
        "feature_flags": _read_flags(),
        "cloudflare": {
            "access_configured": _env_set("CF_ACCESS_TEAM_DOMAIN"),
            "workers_ai_configured": _env_set("CLOUDFLARE_API_TOKEN") and _env_set("CLOUDFLARE_ACCOUNT_ID"),
            "d1_configured": _env_set("CLOUDFLARE_D1_DATABASE_ID"),
        },
        "system": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    for name in ("paper_autostart.json", "hil_state.json"):
        path = TMP / name
        if path.exists():
            try:
                payload[name.removesuffix(".json")] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload[name.removesuffix(".json")] = {"error": "could not parse"}

    record_audit_event("admin_export", admin["email"], detail="Admin exported system snapshot.")
    return JSONResponse(
        payload,
        headers={"Content-Disposition": "attachment; filename=agentic-trader-admin-export.json"},
    )
