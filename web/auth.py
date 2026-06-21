"""
Cloudflare Access JWT verification + FastAPI auth dependencies.

The named tunnel terminates at Cloudflare's edge. Cloudflare Access
injects two artifacts on authenticated requests reaching the origin:
  - Header  Cf-Access-Jwt-Assertion: <signed JWT>
  - Cookie  CF_Authorization=<signed JWT>
  - Header  Cf-Access-Authenticated-User-Email: <verified email>

We verify the JWT signature against the team JWKS so the origin does
NOT need to blindly trust those headers. Spoofing is impossible without
Cloudflare's signing key.

Env:
  CF_ACCESS_TEAM_DOMAIN  e.g. jeffthebever.cloudflareaccess.com
  CF_ACCESS_AUD          AUD tag from the Access application
  CF_ACCESS_REQUIRED     "true" to enforce; "false" (default) enables
                         localhost dev fallback that injects dev@local
                         as admin when CF_ACCESS_TEAM_DOMAIN is unset.
  CF_ACCESS_LOCAL_DEV    "true" allows localhost-only dev auth even when
                         CF_ACCESS_REQUIRED=true for tunnel/public traffic.
  CF_ACCESS_LOCAL_DEV_EMAIL email to use for that localhost-only dev user.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status

from web.users import get_or_create_user

log = logging.getLogger("auth")

# JWT verification deps are imported lazily so the module is importable
# even when PyJWT is missing (e.g. during static analysis or initial
# install). Verification calls will raise if PyJWT is not installed.
try:
    import jwt  # PyJWT
    from jwt import PyJWKClient
    _PYJWT_AVAILABLE = True
except Exception:  # pragma: no cover - optional import
    jwt = None  # type: ignore
    PyJWKClient = None  # type: ignore
    _PYJWT_AVAILABLE = False


_JWKS_CLIENT: Optional[Any] = None
_JWKS_CLIENT_TEAM: Optional[str] = None
_LAST_FAIL_LOG_AT = 0.0


def _team_domain() -> str:
    return os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/")


def _audience() -> str:
    return os.getenv("CF_ACCESS_AUD", "").strip()


def _required() -> bool:
    return os.getenv("CF_ACCESS_REQUIRED", "false").lower() == "true"


def _local_dev_enabled() -> bool:
    return os.getenv("CF_ACCESS_LOCAL_DEV", "false").lower() == "true"


def _local_dev_email() -> str:
    email = os.getenv("CF_ACCESS_LOCAL_DEV_EMAIL", "").strip().lower()
    if email:
        return email
    _bootstrap_raw = os.getenv("CF_ACCESS_BOOTSTRAP_ADMIN", "").strip()
    bootstrap = [
        e.strip().lower()
        for e in _bootstrap_raw.split(",")
        if e.strip()
    ] if _bootstrap_raw else []
    return bootstrap[0] if bootstrap else "dev@local"


def _is_localhost(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _jwks_client() -> Any:
    """Cached PyJWKClient for the current team domain."""
    global _JWKS_CLIENT, _JWKS_CLIENT_TEAM
    if not _PYJWT_AVAILABLE:
        raise RuntimeError(
            "PyJWT is not installed. Run: pip install PyJWT cryptography"
        )
    team = _team_domain()
    if not team:
        raise RuntimeError("CF_ACCESS_TEAM_DOMAIN is not set")
    if _JWKS_CLIENT is None or _JWKS_CLIENT_TEAM != team:
        certs_url = f"https://{team}/cdn-cgi/access/certs"
        # PyJWKClient handles its own HTTP + caching.
        _JWKS_CLIENT = PyJWKClient(certs_url, cache_keys=True, lifespan=300)
        _JWKS_CLIENT_TEAM = team
    return _JWKS_CLIENT


def verify_cf_jwt(token: str) -> dict[str, Any]:
    """Verify a Cloudflare Access JWT. Returns decoded claims on success."""
    if not _PYJWT_AVAILABLE:
        raise RuntimeError("PyJWT not installed")
    team = _team_domain()
    aud = _audience()
    if not team or not aud:
        raise RuntimeError("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD must be set")

    signing_key = _jwks_client().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=aud,
        issuer=f"https://{team}",
        options={"require": ["exp", "iat", "iss", "aud"]},
    )


def _extract_token(request: Request) -> Optional[str]:
    """Pull the Access JWT from the standard header or cookie."""
    hdr = request.headers.get("Cf-Access-Jwt-Assertion")
    if hdr:
        return hdr.strip()
    cookie = request.cookies.get("CF_Authorization")
    if cookie:
        return cookie.strip()
    return None


def _check_manager_key(request: Request) -> Optional[dict[str, Any]]:
    """Check X-Manager-Key header against MANAGER_API_KEY env var.

    Returns an admin user dict if the key matches, None otherwise.
    The key is compared with hmac.compare_digest to prevent timing attacks.
    Only active when MANAGER_API_KEY is set in .env.
    """
    import hmac
    configured = os.getenv("MANAGER_API_KEY", "").strip()
    if not configured:
        return None
    presented = request.headers.get("X-Manager-Key", "").strip()
    if not presented:
        return None
    if not hmac.compare_digest(presented, configured):
        log.warning("X-Manager-Key presented but did not match")
        return None
    # Key matched — synthesise an admin identity from env
    email = os.getenv("CF_ACCESS_LOCAL_DEV_EMAIL", "").strip().lower()
    if not email:
        admins = [e.strip().lower() for e in os.getenv("CF_ACCESS_BOOTSTRAP_ADMIN", "").split(",") if e.strip()]
        email = admins[0] if admins else "manager@local"
    return get_or_create_user(email)


async def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency: returns the authenticated user record.

    Order of precedence:
      1. Valid X-Manager-Key header (MANAGER_API_KEY in .env) -> admin user.
      2. Localhost + local dev auth enabled                    -> dev fallback.
      3. Valid Cloudflare Access JWT                           -> verified user.
      4. Anything else                                         -> 401.

    The user record (dict with email, role) is stashed on
    request.state.user for downstream handlers.
    """
    global _LAST_FAIL_LOG_AT

    manager_user = _check_manager_key(request)
    if manager_user is not None:
        request.state.user = manager_user
        return manager_user

    if _is_localhost(request) and (not _required() or _local_dev_enabled()):
        user = _apply_view_as(request, get_or_create_user(_local_dev_email()))
        request.state.user = user
        return user

    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Cloudflare Access token",
        )

    try:
        claims = verify_cf_jwt(token)
    except Exception as exc:
        now = time.time()
        if now - _LAST_FAIL_LOG_AT > 5:
            log.warning("CF Access JWT verification failed: %s", exc)
            _LAST_FAIL_LOG_AT = now
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Cloudflare Access token",
        )

    email = (claims.get("email") or claims.get("identity_nonce") or "").lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing email claim",
        )

    user = get_or_create_user(email)
    # Backfill display name from the IdP `name` claim on first sight.
    idp_name = (claims.get("name") or "").strip()
    if idp_name and not (user.get("name") or "").strip():
        from web.users import set_name
        user = set_name(email, idp_name)
    user = _apply_view_as(request, user)
    request.state.user = user
    return user


def _apply_view_as(request: Request, actual_user: dict[str, Any]) -> dict[str, Any]:
    """Let admins preview the app as another existing user.

    This is intentionally request-scoped: no server-side session is mutated, and
    the header is ignored unless the real authenticated user is an admin.
    """
    target_email = (request.headers.get("x-agentic-view-as") or "").strip().lower()
    if not target_email:
        return actual_user
    if actual_user.get("role") != "admin":
        return actual_user
    from web.users import get_user

    target = get_user(target_email)
    if not target:
        return actual_user
    viewed = dict(target)
    viewed["viewed_by_admin"] = True
    viewed["actual_admin_email"] = actual_user.get("email", "")
    viewed["actual_admin_role"] = actual_user.get("role", "")
    return viewed


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """FastAPI dependency: 403 unless user.role == 'admin'."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


async def enforce_step_up(
    request: Request,
    user: dict[str, Any],
) -> dict[str, Any]:
    """Validate the step-up 2FA gate for an already-authenticated admin `user`.

    This is the reusable core of `require_step_up`. Non-dependency call sites —
    e.g. the thematic approve endpoint, which is frictionless paper-only but must
    require 2FA on its live-Fidelity leg — call this directly so the exact same
    gate (admin feature flag, HIL disclosure, localhost-dev bypass, fresh
    `X-Step-Up-Token`) is enforced without duplicating or weakening it.
    """
    try:
        from web.api.admin import _read_flags

        flags = _read_flags()
    except Exception:
        flags = {}
    if not flags.get("real_broker_trading", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Real broker trading is disabled by the admin feature flag.",
        )
    if not user.get("hil_disclosure_accepted_at"):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="HIL trading disclosure must be accepted before real broker trading.",
        )
    # Local dev convenience: skip step-up when localhost dev auth is relaxed.
    if _is_localhost(request) and (not _required() or _local_dev_enabled()):
        return user

    from web import twofa
    st = twofa.step_up_status(user["email"])
    method = st["method"]
    ready = (
        (method == "totp" and st["totp_enabled"])
        or (method == "passkey" and bool(st["passkeys"]))
        or (method == "email" and st.get("email_enabled"))
        or (method == "passcode" and st.get("passcode_enabled"))
    )
    if method == "none" or not ready:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Step-up 2FA required before trading. Enroll a trading passcode, TOTP, email codes, or a passkey in Settings.",
        )
    token = request.headers.get("x-step-up-token", "")
    if not twofa.verify_step_up_token(token, user["email"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Step-up verification required",
            headers={"X-Step-Up-Required": st["method"]},
        )
    return user


async def require_step_up(
    request: Request,
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """FastAPI dependency: enforce a valid step-up 2FA token on trade actions.

    The user must (a) be admin and (b) present a fresh `X-Step-Up-Token`
    minted by /auth/2fa/step-up/* — UNLESS they have no step-up method
    enrolled, in which case a 428 tells the client to set one up first.
    Bypassed only on localhost dev when CF_ACCESS_REQUIRED!=true.
    """
    return await enforce_step_up(request, user)


def get_optional_user(request: Request) -> Optional[dict[str, Any]]:
    """Non-raising variant for middleware-style lookups."""
    try:
        # We can't await here; replicate the sync portion only.
        if _is_localhost(request) and not _required():
            return get_or_create_user("dev@local")
        token = _extract_token(request)
        if not token:
            return None
        claims = verify_cf_jwt(token)
        email = (claims.get("email") or "").lower()
        if not email:
            return None
        return get_or_create_user(email)
    except Exception:
        return None


# ── WebSocket auth helpers ─────────────────────────────────────────
# FastAPI deps on @websocket endpoints receive `WebSocket` not `Request`.
# We need handshake-time inspection of the same Access cookie/header and
# the ability to close the socket with 1008 (policy violation) on
# auth/role failure.

def _ws_extract_token(ws) -> Optional[str]:
    """Pull the Access JWT from a WebSocket handshake (header or cookie)."""
    hdr = ws.headers.get("cf-access-jwt-assertion") or ws.headers.get(
        "Cf-Access-Jwt-Assertion"
    )
    if hdr:
        return hdr.strip()
    cookie_header = ws.headers.get("cookie") or ""
    for chunk in cookie_header.split(";"):
        if "=" in chunk:
            k, v = chunk.strip().split("=", 1)
            if k == "CF_Authorization":
                return v.strip()
    return None


def _ws_is_localhost(ws) -> bool:
    host = (ws.headers.get("host") or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "::1")


async def ws_require_admin(websocket) -> Optional[dict[str, Any]]:
    """Authenticate a WebSocket handshake; close with 1008 on failure.

    Returns the verified user record on success; returns None and has
    already called `websocket.close()` when access is denied — caller
    should `return` immediately after a None.
    """
    user: Optional[dict[str, Any]] = None
    try:
        if _ws_is_localhost(websocket) and not _required():
            user = get_or_create_user("dev@local")
        else:
            token = _ws_extract_token(websocket)
            if token:
                claims = verify_cf_jwt(token)
                email = (claims.get("email") or "").lower()
                if email:
                    user = get_or_create_user(email)
    except Exception as exc:
        log.warning("WS auth verification error: %s", exc)
        user = None

    if user is None:
        await websocket.close(code=1008, reason="Authentication required")
        return None
    if user.get("role") != "admin":
        await websocket.close(code=1008, reason="Admin role required")
        return None
    return user
