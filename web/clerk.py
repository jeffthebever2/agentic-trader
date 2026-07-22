"""Clerk step-up 2FA verification.

Clerk is a SECOND factor here, not the primary login (that stays Cloudflare
Access). On a money action the frontend re-authenticates the user with Clerk
(Google), obtains a short-lived Clerk session JWT, and posts it to the step-up
route. This module verifies that token and binds it to the app user:

  1. Verify the JWT signature against Clerk's JWKS (RS256), plus issuer + expiry.
  2. Resolve the Clerk user's *verified primary email* via the Clerk Backend API.
  3. The step-up passes only if that email == the app user's (CF Access) email.

Because both the CF Access login and the Clerk factor are Google identities, a
matching email proves the same human re-authenticated. Nothing here weakens the
existing gates — it is an additional method alongside TOTP/passkey/email/passcode.

Config (env; dormant until set):
  CLERK_SECRET_KEY      Clerk Backend API key (sk_...). Server-only.
  CLERK_ISSUER          e.g. https://your-app.clerk.accounts.dev  (JWT `iss`)
  CLERK_JWKS_URL        optional; defaults to `${CLERK_ISSUER}/.well-known/jwks.json`
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from typing import Any


def _ssl_ctx() -> ssl.SSLContext:
    """A verifying SSL context using certifi's CA bundle — some Python builds
    (e.g. python.org 3.14 on macOS) ship without system CA certs, which would
    otherwise break the HTTPS calls to Clerk's JWKS + Backend API."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

try:
    import jwt
    from jwt import PyJWKClient
    _PYJWT = True
except Exception:  # pragma: no cover
    _PYJWT = False

_JWKS_CLIENT: Any = None
_JWKS_URL_CACHED: str | None = None


def clerk_configured() -> bool:
    return bool(os.getenv("CLERK_SECRET_KEY", "").strip()
                and os.getenv("CLERK_ISSUER", "").strip())


def _issuer() -> str:
    return os.getenv("CLERK_ISSUER", "").strip().rstrip("/")


def _jwks_url() -> str:
    url = os.getenv("CLERK_JWKS_URL", "").strip()
    if url:
        return url
    iss = _issuer()
    if not iss:
        raise RuntimeError("CLERK_ISSUER (or CLERK_JWKS_URL) must be set")
    return f"{iss}/.well-known/jwks.json"


def _jwks() -> Any:
    global _JWKS_CLIENT, _JWKS_URL_CACHED
    if not _PYJWT:
        raise RuntimeError("PyJWT not installed")
    url = _jwks_url()
    if _JWKS_CLIENT is None or _JWKS_URL_CACHED != url:
        try:
            _JWKS_CLIENT = PyJWKClient(url, ssl_context=_ssl_ctx())
        except TypeError:
            # Older PyJWT without ssl_context support.
            _JWKS_CLIENT = PyJWKClient(url)
        _JWKS_URL_CACHED = url
    return _JWKS_CLIENT


def verify_session_token(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT signature + issuer + expiry. Returns claims."""
    if not _PYJWT:
        raise RuntimeError("PyJWT not installed")
    iss = _issuer()
    if not iss:
        raise RuntimeError("CLERK_ISSUER must be set")
    signing_key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        issuer=iss,
        options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
    )


def get_user_email(user_id: str) -> str | None:
    """Fetch the Clerk user's verified primary email via the Backend API."""
    secret = os.getenv("CLERK_SECRET_KEY", "").strip()
    if not (secret and user_id):
        return None
    req = urllib.request.Request(
        f"https://api.clerk.com/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return None
    primary_id = data.get("primary_email_address_id")
    for addr in data.get("email_addresses", []) or []:
        if addr.get("id") == primary_id:
            # Only accept a verified email.
            if (addr.get("verification") or {}).get("status") == "verified":
                return str(addr.get("email_address") or "").strip().lower()
    return None


def verify_step_up(token: str, expected_email: str) -> tuple[bool, str]:
    """Verify a Clerk token AND that it belongs to `expected_email`. Never raises —
    returns (ok, reason) so the route can 401 cleanly."""
    if not clerk_configured():
        return (False, "clerk not configured")
    if not token:
        return (False, "no token")
    try:
        claims = verify_session_token(token)
    except Exception as e:
        return (False, f"token invalid: {str(e)[:120]}")
    sub = claims.get("sub")
    # Prefer an email claim if the JWT template includes one; else Backend API.
    email = (claims.get("email") or "").strip().lower() or (get_user_email(sub) or "")
    if not email:
        return (False, "could not resolve Clerk email")
    if email != (expected_email or "").strip().lower():
        return (False, "Clerk identity does not match the signed-in user")
    return (True, "ok")
