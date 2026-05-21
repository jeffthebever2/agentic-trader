"""
Step-up 2FA before placing real-money trades.

Two enrollable methods, chosen per-user in Settings:
  - TOTP  : 6-digit code from Microsoft/Google Authenticator (pyotp)
  - Passkey: WebAuthn (Face ID / fingerprint / hardware key)

A successful challenge mints a short-lived, HMAC-signed *step-up token*.
Trade endpoints require a valid token in the `X-Step-Up-Token` header.
The token is stateless: base64(email|exp).hmac_sig, TTL 5 minutes.

Env:
  STEP_UP_SECRET   HMAC key for step-up tokens. Auto-generated + persisted
                   to tmp/.step_up_secret if unset.
  WEBAUTHN_RP_ID   Relying-party ID (domain). Defaults to app host.
  WEBAUTHN_ORIGIN  Expected origin, e.g. https://app.agentictrader.org
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import pyotp

from web import users as user_store

ROOT = Path(__file__).parent.parent
_SECRET_FILE = ROOT / "tmp" / ".step_up_secret"
_TOTP_QR_LOGO = ROOT / "web" / "static" / "agentic-trader-icon.png"
_STEP_UP_TTL = 300  # seconds
_EMAIL_CODE_TTL = 600  # seconds
_EMAIL_CODE_RESEND_SECONDS = 60

TOTP_ISSUER = "Agentic Trader"


# ── Step-up token (stateless, HMAC-signed) ─────────────────────────
def _secret() -> bytes:
    env = os.getenv("STEP_UP_SECRET", "").strip()
    if env:
        return env.encode()
    # Persist a generated secret so tokens survive restarts.
    try:
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_text().strip().encode()
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        gen = secrets.token_urlsafe(48)
        _SECRET_FILE.write_text(gen)
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
        return gen.encode()
    except Exception:
        # Last-resort ephemeral secret (tokens won't survive restart).
        return b"ephemeral-" + secrets.token_bytes(32)


def issue_step_up_token(email: str) -> str:
    exp = int(time.time()) + _STEP_UP_TTL
    payload = f"{email.lower()}|{exp}".encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_step_up_token(token: str, email: str) -> bool:
    if not token or "." not in token:
        return False
    body, _, sig = token.partition(".")
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        pad = "=" * (-len(body) % 4)
        decoded = base64.urlsafe_b64decode(body + pad).decode()
        tok_email, exp_str = decoded.split("|", 1)
        exp = int(exp_str)
    except Exception:
        return False
    if tok_email != email.lower():
        return False
    if time.time() > exp:
        return False
    return True


# ── TOTP ───────────────────────────────────────────────────────────
def _totp_qr_png(uri: str) -> bytes:
    """Return a local PNG QR for authenticator enrollment."""
    try:
        import qrcode
        from PIL import Image
    except Exception:
        return b""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0e1118", back_color="#f6f7f9").convert("RGBA")

    if _TOTP_QR_LOGO.exists():
        try:
            logo = Image.open(_TOTP_QR_LOGO).convert("RGBA")
            logo_size = max(64, img.size[0] // 5)
            logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
            pad = max(10, logo_size // 8)
            badge = Image.new("RGBA", (logo.width + pad * 2, logo.height + pad * 2), "#f6f7f9")
            badge.alpha_composite(logo, (pad, pad))
            img.alpha_composite(
                badge,
                ((img.width - badge.width) // 2, (img.height - badge.height) // 2),
            )
        except Exception:
            pass

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _totp_qr_data_url(uri: str) -> str:
    """Return a local PNG QR data URL for authenticator enrollment."""
    png = _totp_qr_png(uri)
    if not png:
        return ""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def totp_enroll(email: str) -> dict[str, str]:
    """Generate (or reuse pending) a TOTP secret and return enroll data."""
    user = user_store.get_user(email)
    if not user:
        raise KeyError(email)
    secret = user.get("totp_secret") or pyotp.random_base32()
    # Store but keep disabled until first code confirms.
    user_store.update_user(email, totp_secret=secret, totp_enabled=False)
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "issuer": TOTP_ISSUER,
        "qr_data_url": _totp_qr_data_url(uri),
    }


def totp_verify(email: str, code: str) -> bool:
    user = user_store.get_user(email)
    if not user:
        return False
    secret = user.get("totp_secret")
    if not secret:
        return False
    # valid_window=1 tolerates ±30s clock drift.
    return pyotp.TOTP(secret).verify((code or "").strip(), valid_window=1)


def totp_qr_png_for_user(email: str) -> bytes:
    user = user_store.get_user(email)
    if not user:
        raise KeyError(email)
    secret = user.get("totp_secret")
    if not secret:
        return b""
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)
    return _totp_qr_png(uri)


def totp_activate(email: str, code: str) -> bool:
    """Confirm enrollment: a valid code flips totp_enabled on."""
    if not totp_verify(email, code):
        return False
    user_store.update_user(email, totp_enabled=True, step_up_method="totp")
    return True


def totp_disable(email: str) -> None:
    user = user_store.get_user(email)
    method = user.get("step_up_method") if user else "none"
    fields: dict[str, Any] = {"totp_secret": "", "totp_enabled": False}
    if method == "totp":
        fields["step_up_method"] = "none"
    user_store.update_user(email, **fields)


# ── WebAuthn / passkeys ────────────────────────────────────────────
def _rp_id(request_host: str = "") -> str:
    env = os.getenv("WEBAUTHN_RP_ID", "").strip()
    if env:
        return env
    host = (request_host or "app.agentictrader.org").split(":")[0]
    return host


def _origin(request_origin: str = "") -> str:
    env = os.getenv("WEBAUTHN_ORIGIN", "").strip()
    if env:
        return env
    if request_origin:
        return request_origin
    return f"https://{_rp_id()}"


def passkey_register_options(email: str, host: str = "") -> dict[str, Any]:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    user = user_store.get_user(email)
    existing = user.get("passkeys", []) if user else []
    opts = generate_registration_options(
        rp_id=_rp_id(host),
        rp_name=TOTP_ISSUER,
        user_name=email,
        user_id=email.encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    # Stash the challenge for verification (per-user, single-use).
    _PENDING_REG[email.lower()] = opts.challenge
    return json.loads(options_to_json(opts))


def passkey_register_verify(email: str, credential: dict, host: str = "", origin: str = "") -> bool:
    from webauthn import verify_registration_response
    from webauthn.helpers.structs import RegistrationCredential

    challenge = _PENDING_REG.pop(email.lower(), None)
    if challenge is None:
        return False
    verification = verify_registration_response(
        credential=RegistrationCredential.parse_raw(json.dumps(credential)),
        expected_challenge=challenge,
        expected_rp_id=_rp_id(host),
        expected_origin=_origin(origin),
    )
    user = user_store.get_user(email) or {}
    passkeys = list(user.get("passkeys", []))
    passkeys.append({
        "id": base64.urlsafe_b64encode(verification.credential_id).decode().rstrip("="),
        "public_key": base64.urlsafe_b64encode(verification.credential_public_key).decode().rstrip("="),
        "sign_count": verification.sign_count,
        "name": credential.get("_name") or "Passkey",
        "created": int(time.time()),
    })
    user_store.update_user(email, passkeys=passkeys, step_up_method="passkey")
    return True


def passkey_auth_options(email: str, host: str = "") -> dict[str, Any]:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
    )

    user = user_store.get_user(email) or {}
    allow = []
    for pk in user.get("passkeys", []):
        pad = "=" * (-len(pk["id"]) % 4)
        allow.append(PublicKeyCredentialDescriptor(
            id=base64.urlsafe_b64decode(pk["id"] + pad)
        ))
    opts = generate_authentication_options(
        rp_id=_rp_id(host),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _PENDING_AUTH[email.lower()] = opts.challenge
    return json.loads(options_to_json(opts))


def passkey_auth_verify(email: str, credential: dict, host: str = "", origin: str = "") -> bool:
    from webauthn import verify_authentication_response
    from webauthn.helpers.structs import AuthenticationCredential

    challenge = _PENDING_AUTH.pop(email.lower(), None)
    if challenge is None:
        return False
    raw_id = credential.get("rawId") or credential.get("id")
    pad = "=" * (-len(raw_id) % 4)
    try:
        target_id = base64.urlsafe_b64decode(raw_id + pad)
    except Exception:
        target_id = None

    user = user_store.get_user(email) or {}
    passkeys = list(user.get("passkeys", []))
    for idx, pk in enumerate(passkeys):
        pad2 = "=" * (-len(pk["id"]) % 4)
        stored_id = base64.urlsafe_b64decode(pk["id"] + pad2)
        if target_id is not None and stored_id != target_id:
            continue
        pub_pad = "=" * (-len(pk["public_key"]) % 4)
        try:
            verification = verify_authentication_response(
                credential=AuthenticationCredential.parse_raw(json.dumps(credential)),
                expected_challenge=challenge,
                expected_rp_id=_rp_id(host),
                expected_origin=_origin(origin),
                credential_public_key=base64.urlsafe_b64decode(pk["public_key"] + pub_pad),
                credential_current_sign_count=pk.get("sign_count", 0),
            )
        except Exception:
            return False
        # Update sign count to mitigate cloned-authenticator replay.
        passkeys[idx]["sign_count"] = verification.new_sign_count
        user_store.update_user(email, passkeys=passkeys)
        return True
    return False


def remove_passkey(email: str, passkey_id: str) -> None:
    user = user_store.get_user(email) or {}
    passkeys = [p for p in user.get("passkeys", []) if p["id"] != passkey_id]
    fields: dict[str, Any] = {"passkeys": passkeys}
    if not passkeys and (user.get("step_up_method") == "passkey"):
        fields["step_up_method"] = "none"
    user_store.update_user(email, **fields)


# ── Email one-time codes ───────────────────────────────────────────
def email_otp_available() -> bool:
    try:
        from scripts.email_sender import smtp_configured

        return smtp_configured()
    except Exception:
        return False


def _code_digest(email: str, code: str) -> str:
    msg = f"{email.lower()}|{code.strip()}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def send_email_code(email: str, purpose: str = "trade") -> dict[str, Any]:
    """Send a short-lived email OTP for step-up authorization."""
    if not email_otp_available():
        return {"success": False, "error": "Email sending is not configured"}

    now = int(time.time())
    pending = _PENDING_EMAIL.get(email.lower())
    if pending and now - int(pending.get("sent_at", 0)) < _EMAIL_CODE_RESEND_SECONDS:
        wait = _EMAIL_CODE_RESEND_SECONDS - (now - int(pending.get("sent_at", 0)))
        return {"success": False, "error": f"Wait {wait}s before requesting another code"}

    code = f"{secrets.randbelow(1_000_000):06d}"
    exp = now + _EMAIL_CODE_TTL
    _PENDING_EMAIL[email.lower()] = {
        "digest": _code_digest(email, code),
        "exp": exp,
        "sent_at": now,
        "purpose": purpose,
    }

    from scripts.email_sender import send_email

    subject = "Agentic Trader one-time code"
    body = (
        "Your Agentic Trader one-time code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not request it, "
        "ignore this email and review your account access."
    )
    result = send_email(email, subject, body)
    if not result.get("success"):
        _PENDING_EMAIL.pop(email.lower(), None)
    return {
        "success": bool(result.get("success")),
        "expires_in": _EMAIL_CODE_TTL,
        "resend_after": _EMAIL_CODE_RESEND_SECONDS,
        "error": result.get("error"),
    }


def verify_email_code(email: str, code: str) -> bool:
    pending = _PENDING_EMAIL.get(email.lower())
    if not pending:
        return False
    if int(time.time()) > int(pending.get("exp", 0)):
        _PENDING_EMAIL.pop(email.lower(), None)
        return False
    expected = pending.get("digest", "")
    actual = _code_digest(email, code)
    if not hmac.compare_digest(expected, actual):
        return False
    _PENDING_EMAIL.pop(email.lower(), None)
    return True


def set_email_method(email: str) -> None:
    if not email_otp_available():
        raise RuntimeError("Email sending is not configured")
    user_store.update_user(email, step_up_method="email")


# In-memory single-use challenge stores (per process; fine for one server).
_PENDING_REG: dict[str, bytes] = {}
_PENDING_AUTH: dict[str, bytes] = {}
_PENDING_EMAIL: dict[str, dict[str, Any]] = {}


def step_up_status(email: str) -> dict[str, Any]:
    user = user_store.get_user(email) or {}
    return {
        "method": user.get("step_up_method", "none"),
        "totp_enabled": bool(user.get("totp_enabled", False)),
        "email_enabled": email_otp_available(),
        "passkeys": [
            {"id": p["id"], "name": p.get("name", "Passkey"), "created": p.get("created")}
            for p in user.get("passkeys", [])
        ],
    }
