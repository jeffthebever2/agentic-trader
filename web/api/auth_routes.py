"""User identity + role management endpoints."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from web.auth import get_current_user, require_admin
from web import users as user_store
from web.api.admin import _read_flags, record_audit_event

router = APIRouter()


def _send_email_safe(to: str, subject: str, message: str) -> None:
    try:
        from scripts.email_sender import send_email

        send_email(to, subject, message)
    except Exception:
        pass


def _maybe_send_login_notice(user: dict[str, Any], request: Request, background: BackgroundTasks) -> None:
    email = (user.get("email") or "").strip().lower()
    if not email or email.endswith("@local"):
        return
    now = int(time.time())
    last = int(user.get("last_login_notice_at") or 0)
    if now - last < 86400:
        return
    try:
        user_store.update_user(email, last_login_notice_at=now)
    except Exception:
        return
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "Agentic Trader"
    message = (
        "Agentic Trader noticed a dashboard sign-in for this account.\n\n"
        f"Account: {email}\n"
        f"Site: {host}\n\n"
        "If this was you, no action is needed. If this was not you, remove the user in "
        "Settings, review Cloudflare Access policies, and rotate any exposed API or broker credentials."
    )
    background.add_task(_send_email_safe, email, "Agentic Trader sign-in notice", message)


@router.get("/auth/me")
async def me(
    request: Request,
    background: BackgroundTasks,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the verified current user (email + role + onboarding flag).

    Frontend calls this on load to decide which controls to render and
    whether to show the first-time onboarding modal.
    """
    _maybe_send_login_notice(user, request, background)
    return {
        "email": user["email"],
        "name": user_store.display_name(user),
        "role": user["role"],
        "is_admin": user["role"] == "admin",
        "onboarding_completed": bool(user.get("onboarding_completed", False)),
        "legal_accepted": bool(
            user.get("terms_accepted_at")
            and user.get("privacy_accepted_at")
            and user.get("risk_acknowledged_at")
        ),
        "terms_accepted_at": int(user.get("terms_accepted_at") or 0),
        "privacy_accepted_at": int(user.get("privacy_accepted_at") or 0),
        "risk_acknowledged_at": int(user.get("risk_acknowledged_at") or 0),
        "hil_disclosure_accepted": bool(user.get("hil_disclosure_accepted_at")),
        "hil_disclosure_accepted_at": int(user.get("hil_disclosure_accepted_at") or 0),
        "hil_disclosure_version": user.get("hil_disclosure_version", ""),
        "hil_disclosure_effective_date": user.get("hil_disclosure_effective_date", ""),
        "current_hil_disclosure_version": user_store.HIL_DISCLOSURE_VERSION,
        "current_hil_disclosure_effective_date": user_store.HIL_DISCLOSURE_EFFECTIVE_DATE,
        "created_first": bool(user.get("created_first", False)),
        "phone_number": user.get("phone_number", ""),
        "sms_verified": bool(user.get("sms_verified", False)),
        "sms_opted_out": bool(user.get("sms_opted_out", False)),
        "sms_service": user.get("sms_service", ""),
        "viewed_by_admin": bool(user.get("viewed_by_admin", False)),
        "hil_prefs": user_store.get_hil_prefs(user),
    }


@router.get("/auth/features")
async def features(user: dict[str, Any] = Depends(get_current_user)):
    """Return user-visible feature availability without exposing admin controls."""
    flags = _read_flags()
    return {
        "real_broker_trading": bool(flags.get("real_broker_trading", False)),
        "sms_trade_approvals": bool(flags.get("sms_trade_approvals", True)),
        "email_one_time_codes": bool(flags.get("email_one_time_codes", True)),
        "onboarding_required": bool(flags.get("onboarding_required", True)),
        "cloudflare_ai_primary": bool(flags.get("cloudflare_ai_primary", True)),
    }


class HilPrefsBody(BaseModel):
    enabled: bool | None = None
    risk_profile: str | None = Field(None, pattern="^(conservative|balanced|aggressive|custom)$")
    min_risk_reward: float | None = Field(None, ge=0.5, le=10.0)
    position_max_pct: float | None = Field(None, ge=1.0, le=100.0)
    position_min_pct: float | None = Field(None, ge=0.5, le=100.0)
    max_positions: int | None = Field(None, ge=1, le=50)
    daily_loss_limit_pct: float | None = Field(None, ge=0.1, le=50.0)
    approval_timeout_min: int | None = Field(None, ge=1, le=120)
    auto_reject_on_timeout: bool | None = None
    notify_channel: str | None = Field(None, pattern="^(sms|email|none)$")


@router.post("/auth/me/hil-prefs")
async def set_my_hil_prefs(
    body: HilPrefsBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Save the current user's Human-In-the-Loop trading preferences."""
    prefs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not prefs:
        raise HTTPException(status_code=400, detail="No preferences supplied")
    rec = user_store.set_hil_prefs(user["email"], prefs)
    record_audit_event(
        "hil_prefs_updated", user["email"], target=user["email"],
        detail=f"Updated HIL preferences: {', '.join(sorted(prefs))}",
    )
    return {"hil_prefs": user_store.get_hil_prefs(rec)}


class HilDisclosureBody(BaseModel):
    accepted: bool
    version: str = Field(..., min_length=1, max_length=20)


@router.post("/auth/me/hil-disclosure")
async def accept_my_hil_disclosure(
    body: HilDisclosureBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Record the required HIL trading disclosure acknowledgment."""
    if not body.accepted:
        raise HTTPException(status_code=400, detail="HIL disclosure acceptance is required")
    if body.version != user_store.HIL_DISCLOSURE_VERSION:
        raise HTTPException(status_code=409, detail="HIL disclosure version mismatch. Refresh and try again.")
    rec = user_store.accept_hil_disclosure(user["email"])
    record_audit_event(
        "hil_disclosure_accepted",
        user["email"],
        target=user["email"],
        detail=f"Accepted HIL disclosure version {user_store.HIL_DISCLOSURE_VERSION}.",
        meta={"version": user_store.HIL_DISCLOSURE_VERSION},
    )
    return {
        "hil_disclosure_accepted": True,
        "hil_disclosure_accepted_at": int(rec.get("hil_disclosure_accepted_at") or 0),
        "hil_disclosure_version": rec.get("hil_disclosure_version", ""),
        "hil_disclosure_effective_date": rec.get("hil_disclosure_effective_date", ""),
    }


class NameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.post("/auth/me/name")
async def set_my_name(body: NameBody, user: dict[str, Any] = Depends(get_current_user)):
    rec = user_store.set_name(user["email"], body.name)
    return {"name": user_store.display_name(rec)}


class OnboardingCompleteBody(BaseModel):
    terms_accepted: bool
    privacy_accepted: bool
    risk_acknowledged: bool


@router.post("/auth/me/complete-onboarding")
async def complete_onboarding(
    body: OnboardingCompleteBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Mark current user's onboarding modal as dismissed."""
    try:
        rec = user_store.complete_onboarding(
            user["email"],
            terms_accepted=body.terms_accepted,
            privacy_accepted=body.privacy_accepted,
            risk_acknowledged=body.risk_acknowledged,
        )
        record_audit_event(
            "onboarding_completed",
            user["email"],
            target=user["email"],
            detail="User completed onboarding and accepted legal acknowledgments.",
        )
        return rec
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class PhoneBody(BaseModel):
    phone: str = Field(..., min_length=4, max_length=24)
    send_test: bool = True


def _normalize_phone(raw: str) -> str:
    """E.164-ish normalization: keep leading +, strip everything else.

    If the result has no country code and looks like a US 10-digit number,
    prepend +1. Not a full phonenumbers-grade validator — Sendblue rejects
    invalid numbers itself.
    """
    s = raw.strip()
    plus = s.startswith("+")
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    if not plus and len(digits) == 10:
        return "+1" + digits
    if not plus and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ("+" if plus else "") + digits


class OptOutBody(BaseModel):
    opt_out: bool


@router.post("/auth/me/opt-out")
async def set_my_opt_out(
    body: OptOutBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Pause or resume SMS alerts for the current user (same as STOP/START
    SMS commands but settable from the dashboard).
    """
    rec = user_store.set_sms_opt_out(user["email"], body.opt_out)
    return {"email": rec["email"], "sms_opted_out": rec.get("sms_opted_out", False)}


@router.post("/auth/me/phone")
async def set_my_phone(
    body: PhoneBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Save the current user's SMS number and optionally send a test message.

    `verified` is set True only when Sendblue accepts the test send. If
    `send_test=False` (or Sendblue isn't configured), the number is still
    stored but `sms_verified` stays False.
    """
    phone = _normalize_phone(body.phone)
    if not phone or len(phone) < 8:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    test_result = None
    verified = False
    if body.send_test:
        try:
            # Import lazily; CLI script lives outside the web package.
            import sys as _sys
            from pathlib import Path as _Path
            root = _Path(__file__).parent.parent.parent
            if str(root) not in _sys.path:
                _sys.path.insert(0, str(root))
            from scripts.sms_alerts import send_sendblue
            test_result = send_sendblue(
                phone,
                f"Agentic Trader: SMS alerts enabled for {user['email']}. Reply STOP to opt out.",
            )
            verified = bool(test_result and test_result.get("success"))
        except Exception as exc:
            import logging; logging.exception("Test send failed"); test_result = {"success": False, "error": "An internal error occurred"}

    saved = user_store.set_phone(user["email"], phone, verified=verified)
    return {
        "phone_number": saved["phone_number"],
        "sms_verified": saved["sms_verified"],
        "test_send": test_result,
    }


def _load_sms_alerts():
    """Lazy import of the CLI SMS helper (lives outside the web package)."""
    import sys as _sys
    from pathlib import Path as _Path
    root = _Path(__file__).parent.parent.parent
    if str(root) not in _sys.path:
        _sys.path.insert(0, str(root))
    import scripts.sms_alerts as _m
    return _m


@router.post("/auth/me/phone/verify")
async def verify_my_phone(
    body: PhoneBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Verify a contact number via Sendblue's evaluate-service (no message sent).

    Confirms the number is reachable and reports iMessage vs SMS capability.
    Stores the number + detected service and marks it verified when reachable.
    """
    phone = _normalize_phone(body.phone)
    if not phone or len(phone) < 8:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    try:
        res = _load_sms_alerts().evaluate_sendblue(phone)
    except Exception as exc:
        import logging; logging.exception("Verify failed"); res = {"success": False, "error": "An internal error occurred"}
    reachable = bool(res.get("success"))
    service = res.get("service")
    saved = user_store.set_phone(user["email"], phone, verified=reachable)
    if service:
        user_store.update_user(user["email"], sms_service=service)
    return {
        "phone_number": saved["phone_number"],
        "sms_verified": saved["sms_verified"],
        "service": service,
        "reachable": reachable,
        "error": None if reachable else res.get("error"),
    }


@router.get("/auth/users")
async def list_users(admin: dict[str, Any] = Depends(require_admin)):
    return {"users": user_store.list_users()}


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


@router.put("/auth/users/{email}/role")
async def set_role(
    email: str,
    body: RoleUpdate,
    background: BackgroundTasks,
    admin: dict[str, Any] = Depends(require_admin),
):
    try:
        rec = user_store.set_role(email, body.role)
        record_audit_event(
            "role_changed",
            admin["email"],
            target=rec.get("email", email),
            detail=f"Role set to {body.role}.",
            meta={"role": body.role},
        )
        if rec.get("email") and not str(rec["email"]).endswith("@local"):
            background.add_task(
                _send_email_safe,
                rec["email"],
                "Agentic Trader role updated",
                (
                    "Your Agentic Trader dashboard role was updated.\n\n"
                    f"Account: {rec['email']}\n"
                    f"New role: {rec['role']}\n"
                    f"Changed by: {admin['email']}\n\n"
                    "If you did not expect this, contact support@agentictrader.org."
                ),
            )
        return rec
    except KeyError:
        raise HTTPException(status_code=404, detail=f"user {email} not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/auth/users/{email}")
async def delete_user(
    email: str,
    admin: dict[str, Any] = Depends(require_admin),
):
    if email.lower() == admin["email"].lower():
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    user_store.delete_user(email)
    record_audit_event(
        "user_deleted",
        admin["email"],
        target=email.lower(),
        detail="User record deleted.",
    )
    return {"deleted": email}
