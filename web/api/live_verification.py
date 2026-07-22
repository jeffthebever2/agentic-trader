"""Lightweight live-readiness checks for the dashboard.

These checks avoid broker navigation and never expose credentials. They are
meant to show whether isolation, notifications, and the paper supervisor are
wired correctly for the signed-in user.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from scripts.email_sender import smtp_configured
from tradingagents.config import env_bool
from web.auth import get_current_user
from web.api.fidelity import _fidelity_state_path, _session_owner_hash
from web.api.paper import AUTOSTART_CONFIG_PATH, DEFAULT_AUTOSTART_CONFIG, _process_status
from web.api.webull_portfolio import _wb_owner_hash, _wb_state_path
from web.secure_store import broker_session_key_status, is_encrypted_path

router = APIRouter()


def _check(check_id: str, label: str, status: str, detail: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "meta": meta or {},
    }


def _read_autostart_config() -> dict[str, Any]:
    cfg = DEFAULT_AUTOSTART_CONFIG.copy()
    if AUTOSTART_CONFIG_PATH.exists():
        try:
            import json

            saved = json.loads(AUTOSTART_CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except Exception:
            pass
    return cfg


def _exists_private(path: Path) -> tuple[bool, bool | None]:
    if not path.exists():
        return False, None
    try:
        mode = path.stat().st_mode & 0o777
        return True, mode == 0o600
    except Exception:
        return True, None


@router.get("/live/verification")
async def live_verification(user: dict = Depends(get_current_user)):
    email = user["email"]
    checks: list[dict[str, Any]] = []

    legal_ok = all(
        int(user.get(field) or 0) > 0
        for field in ("terms_accepted_at", "privacy_accepted_at", "risk_acknowledged_at")
    )
    checks.append(_check(
        "access_identity",
        "Cloudflare Access identity",
        "pass",
        f"Signed in as {email}.",
        {"role": user.get("role", "user")},
    ))
    checks.append(_check(
        "legal_gate",
        "Onboarding legal gate",
        "pass" if legal_ok else "warn",
        "Terms, privacy, and risk acknowledgments are recorded." if legal_ok else "User will be held at onboarding until all legal acknowledgments are signed.",
    ))
    hil_ok = bool(user.get("hil_disclosure_accepted_at"))
    checks.append(_check(
        "hil_disclosure",
        "HIL trading disclosure",
        "pass" if hil_ok else "warn",
        (
            f"HIL disclosure version {user.get('hil_disclosure_version') or 'unknown'} is accepted."
            if hil_ok
            else "Real broker trading is blocked until the HIL disclosure is accepted."
        ),
        {
            "accepted_at": int(user.get("hil_disclosure_accepted_at") or 0),
            "version": user.get("hil_disclosure_version", ""),
        },
    ))

    key_status = broker_session_key_status()
    checks.append(_check(
        "broker_session_key",
        "Broker session encryption key",
        "pass" if key_status.get("configured") and key_status.get("private_file") is not False else "warn",
        (
            "Broker session encryption key is supplied by the environment."
            if key_status.get("source") == "env"
            else "Broker session encryption key is stored locally with private permissions."
            if key_status.get("source") == "local_file" and key_status.get("private_file") is True
            else "Set BROKER_SESSION_KEY for strongest protection, or ensure tmp/broker_session.key is chmod 600."
        ),
        key_status,
    ))

    fidelity_path = _fidelity_state_path(email)
    fidelity_exists, fidelity_private = _exists_private(fidelity_path)
    fidelity_encrypted = is_encrypted_path(fidelity_path)
    checks.append(_check(
        "fidelity_isolation",
        "Fidelity encrypted per-user session",
        "pass" if not fidelity_exists or (fidelity_private and fidelity_encrypted) else "warn",
        (
            "Session state is scoped to this Access email and encrypted at rest."
            if fidelity_exists and fidelity_private and fidelity_encrypted
            else "No Fidelity session file exists yet."
            if not fidelity_exists
            else "Session state is scoped per user but should be encrypted and chmod 600."
        ),
        {
            "session_scope": "per_user",
            "session_file": fidelity_exists,
            "encrypted": fidelity_encrypted,
            "private_file": fidelity_private,
            "owner_hash": _session_owner_hash(email),
        },
    ))

    webull_path = _wb_state_path(email)
    webull_exists, webull_private = _exists_private(webull_path)
    webull_encrypted = is_encrypted_path(webull_path)
    checks.append(_check(
        "webull_isolation",
        "Webull encrypted per-user session",
        "pass" if not webull_exists or (webull_private and webull_encrypted) else "warn",
        (
            "Session state is scoped to this Access email and encrypted at rest."
            if webull_exists and webull_private and webull_encrypted
            else "No Webull session file exists yet."
            if not webull_exists
            else "Session state is scoped per user but should be encrypted and chmod 600."
        ),
        {
            "session_scope": "per_user",
            "session_file": webull_exists,
            "encrypted": webull_encrypted,
            "private_file": webull_private,
            "owner_hash": _wb_owner_hash(email),
        },
    ))

    proc = _process_status()
    autostart = _read_autostart_config()
    checks.append(_check(
        "paper_supervisor",
        "Shared paper runner supervision",
        "pass" if proc.get("running") else ("warn" if autostart.get("enabled") else "fail"),
        "Paper runner is currently running." if proc.get("running") else (
            "Autostart is enabled and should relaunch during its configured window." if autostart.get("enabled") else "Autostart is disabled, so the shared paper runner will not relaunch by itself."
        ),
        {
            "running": bool(proc.get("running")),
            "pid": proc.get("pid"),
            "autostart_enabled": bool(autostart.get("enabled")),
            "ignore_market_window": env_bool("PAPER_AUTOSTART_IGNORE_WINDOW", False),
        },
    ))

    checks.append(_check(
        "email_codes",
        "Email one-time codes",
        "pass" if smtp_configured() else "fail",
        "SMTP is configured for login notices and trade step-up codes." if smtp_configured() else "SMTP is missing required settings.",
    ))

    cf_ai_ok = bool(
        os.getenv("CLOUDFLARE_API_TOKEN")
        and (os.getenv("CLOUDFLARE_ACCOUNT_ID") or os.getenv("CLOUDFLARE_AI_GATEWAY_URL"))
    )
    checks.append(_check(
        "cloudflare_workers_ai",
        "Cloudflare Workers AI",
        "pass" if cf_ai_ok else "warn",
        "Workers AI is configured as the main LLM provider." if cf_ai_ok else "Set Cloudflare account ID and API token to enable Workers AI.",
        {
            "provider": os.getenv("LLM_PROVIDER", "cloudflare") or "cloudflare",
            "quick_model": os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", ""),
            "deep_model": os.getenv("CLOUDFLARE_DEFAULT_DEEP_MODEL", ""),
        },
    ))

    d1_ok = bool(
        os.getenv("CLOUDFLARE_ACCOUNT_ID")
        and os.getenv("CLOUDFLARE_API_TOKEN")
        and os.getenv("CLOUDFLARE_D1_DATABASE_ID")
    )
    checks.append(_check(
        "cloudflare_d1",
        "Cloudflare D1 storage",
        "pass" if d1_ok else "warn",
        "D1 is configured and will be preferred over Supabase for users and portfolio blobs." if d1_ok else "D1 is not configured; Supabase/local storage remains active.",
    ))

    sms_ok = bool(os.getenv("SENDBLUE_API_KEY_ID") and os.getenv("SENDBLUE_API_SECRET"))
    checks.append(_check(
        "sms_alerts",
        "SMS alert provider",
        "pass" if sms_ok else "warn",
        "Sendblue credentials are configured." if sms_ok else "Sendblue credentials are not fully configured.",
    ))

    step_method = user.get("step_up_method") or "none"
    checks.append(_check(
        "trade_step_up",
        "Trade step-up method",
        "pass" if step_method != "none" else "warn",
        f"Real broker actions require {step_method} verification." if step_method != "none" else "No trade step-up method is selected.",
        {"method": step_method},
    ))

    order = {"fail": 0, "warn": 1, "pass": 2}
    overall = min((c["status"] for c in checks), key=lambda s: order.get(s, 0))
    return {
        "overall": overall,
        "email": email,
        "checks": checks,
    }
