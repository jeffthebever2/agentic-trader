"""2FA enrollment + step-up challenge endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from web.auth import get_current_user
from web import twofa

router = APIRouter()


@router.get("/auth/2fa/status")
async def status(user: dict[str, Any] = Depends(get_current_user)):
    return twofa.step_up_status(user["email"])


# ── TOTP enrollment ────────────────────────────────────────────────
@router.post("/auth/2fa/totp/enroll")
async def totp_enroll(user: dict[str, Any] = Depends(get_current_user)):
    """Begin TOTP enrollment: returns secret + otpauth URI for QR display."""
    return twofa.totp_enroll(user["email"])


@router.get("/auth/2fa/totp/qr")
async def totp_qr(user: dict[str, Any] = Depends(get_current_user)):
    """Return the current pending TOTP enrollment QR code as a PNG."""
    png = twofa.totp_qr_png_for_user(user["email"])
    if not png:
        raise HTTPException(status_code=404, detail="No pending TOTP secret")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


class CodeBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=10)


@router.post("/auth/2fa/totp/activate")
async def totp_activate(body: CodeBody, user: dict[str, Any] = Depends(get_current_user)):
    if not twofa.totp_activate(user["email"], body.code):
        raise HTTPException(status_code=400, detail="Invalid code")
    return {"ok": True, "method": "totp"}


@router.post("/auth/2fa/totp/disable")
async def totp_disable(user: dict[str, Any] = Depends(get_current_user)):
    twofa.totp_disable(user["email"])
    return {"ok": True}


# ── Passkey enrollment ─────────────────────────────────────────────
def _host(request: Request) -> str:
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]


def _origin(request: Request) -> str:
    return request.headers.get("origin") or ""


@router.post("/auth/2fa/passkey/register/begin")
async def passkey_register_begin(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    return twofa.passkey_register_options(user["email"], host=_host(request))


@router.post("/auth/2fa/passkey/register/complete")
async def passkey_register_complete(
    credential: dict,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
):
    ok = twofa.passkey_register_verify(
        user["email"], credential, host=_host(request), origin=_origin(request)
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Passkey registration failed")
    return {"ok": True, "method": "passkey"}


@router.delete("/auth/2fa/passkey/{passkey_id}")
async def passkey_remove(passkey_id: str, user: dict[str, Any] = Depends(get_current_user)):
    twofa.remove_passkey(user["email"], passkey_id)
    return {"ok": True}


class MethodBody(BaseModel):
    method: str = Field(..., pattern="^(none|totp|passkey|email)$")


@router.post("/auth/2fa/method")
async def set_method(body: MethodBody, user: dict[str, Any] = Depends(get_current_user)):
    from web import users as user_store
    st = twofa.step_up_status(user["email"])
    if body.method == "totp" and not st["totp_enabled"]:
        raise HTTPException(status_code=400, detail="TOTP not enrolled")
    if body.method == "passkey" and not st["passkeys"]:
        raise HTTPException(status_code=400, detail="No passkey registered")
    if body.method == "email" and not st["email_enabled"]:
        raise HTTPException(status_code=400, detail="Email sending is not configured")
    user_store.update_user(user["email"], step_up_method=body.method)
    return {"ok": True, "method": body.method}


# ── Step-up challenge (run right before a trade) ───────────────────
@router.post("/auth/2fa/step-up/totp")
async def step_up_totp(body: CodeBody, user: dict[str, Any] = Depends(get_current_user)):
    if not twofa.totp_verify(user["email"], body.code):
        raise HTTPException(status_code=401, detail="Invalid code")
    return {"step_up_token": twofa.issue_step_up_token(user["email"])}


@router.post("/auth/2fa/step-up/email/send")
async def step_up_email_send(user: dict[str, Any] = Depends(get_current_user)):
    result = twofa.send_email_code(user["email"], purpose="trade")
    if not result.get("success"):
        raise HTTPException(status_code=429, detail="Email code failed to send")
    return {
        "ok": True,
        "sent_to": user["email"],
        "expires_in": result.get("expires_in", 600),
        "resend_after": result.get("resend_after", 60),
    }


@router.post("/auth/2fa/step-up/email")
async def step_up_email(body: CodeBody, user: dict[str, Any] = Depends(get_current_user)):
    if not twofa.verify_email_code(user["email"], body.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    return {"step_up_token": twofa.issue_step_up_token(user["email"])}


@router.post("/auth/2fa/step-up/passkey/begin")
async def step_up_passkey_begin(request: Request, user: dict[str, Any] = Depends(get_current_user)):
    st = twofa.step_up_status(user["email"])
    if not st["passkeys"]:
        raise HTTPException(status_code=400, detail="No passkey registered")
    return twofa.passkey_auth_options(user["email"], host=_host(request))


@router.post("/auth/2fa/step-up/passkey/complete")
async def step_up_passkey_complete(
    credential: dict,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
):
    ok = twofa.passkey_auth_verify(
        user["email"], credential, host=_host(request), origin=_origin(request)
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Passkey verification failed")
    return {"step_up_token": twofa.issue_step_up_token(user["email"])}
