"""Admin console support endpoints."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
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
