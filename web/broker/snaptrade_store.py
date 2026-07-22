"""Per-user SnapTrade credential store + connection lifecycle.

SnapTrade identifies each end-user by a `(userId, userSecret)` pair minted at
registration. The `userSecret` is a bearer credential, so it is stored **encrypted
at rest** (Fernet, 0600) via `web.secure_store` — never in plaintext, never in
frontend state.

`userId` is derived deterministically from the app user's email (a salted hash),
so it is stable and non-reversible. All SnapTrade↔Fidelity use here is DATA-ONLY;
this module never places orders.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from web import secure_store

ROOT = Path(__file__).parent.parent.parent
_STORE_DIR = ROOT / "tmp" / "snaptrade"
_PURPOSE = "snaptrade_user"

# SnapTrade userId namespace for this app.
_USER_PREFIX = "agentictrader"


class SnapTradeConfigError(RuntimeError):
    pass


def keys_configured() -> bool:
    return bool(os.getenv("SNAPTRADE_CLIENT_ID", "").strip()
                and os.getenv("SNAPTRADE_CONSUMER_KEY", "").strip())


# Cached credential-validity probe so we don't hammer SnapTrade on every request.
_CRED_CACHE: dict[str, Any] = {"key": None, "ok": False, "reason": "unchecked", "at": 0.0}
_CRED_TTL_SECONDS = 300


def verify_credentials(force: bool = False) -> tuple[bool, str]:
    """Read-only probe that the configured keys authenticate against SnapTrade.

    Uses `list_snap_trade_users` (no writes, no user creation, no billing). Result
    is cached per key for 5 min. Returns (ok, reason). Never raises — an auth
    failure / unreachable API degrades to (False, reason) so the app treats
    SnapTrade as simply unavailable and keeps running on the local path."""
    import time as _t
    if not keys_configured():
        return (False, "keys not set")
    cid = os.getenv("SNAPTRADE_CLIENT_ID", "").strip()
    now = _t.monotonic()
    if (not force and _CRED_CACHE["key"] == cid
            and (now - _CRED_CACHE["at"]) < _CRED_TTL_SECONDS):
        return (_CRED_CACHE["ok"], _CRED_CACHE["reason"])
    ok, reason = False, "unknown"
    try:
        client().authentication.list_snap_trade_users()
        ok, reason = True, "ok"
    except Exception as e:
        detail = getattr(e, "body", None) or getattr(e, "reason", None) or repr(e)
        status = getattr(e, "status", None)
        reason = f"auth failed ({status}): {str(detail)[:120]}" if status else f"unreachable: {str(detail)[:120]}"
        # Common paste footgun: the clientId got pasted twice (even-length exact
        # duplicate). SnapTrade returns "Invalid clientId" — surface the hint.
        if len(cid) % 2 == 0 and cid[: len(cid) // 2] == cid[len(cid) // 2:]:
            reason += " — HINT: SNAPTRADE_CLIENT_ID looks pasted twice (duplicated); paste it once."
    _CRED_CACHE.update({"key": cid, "ok": ok, "reason": reason, "at": now})
    return (ok, reason)


def effective_available() -> bool:
    """True only if SnapTrade is enabled, keyed, AND the keys actually authenticate.
    This is the gate real read flows should use — a bad/absent key ⇒ False ⇒ the
    app uses the local Fidelity path instead of erroring."""
    from web.broker.snaptrade_data import is_enabled
    if not (is_enabled() and keys_configured()):
        return False
    return verify_credentials()[0]


def client():
    """Build a SnapTrade SDK client from env keys. Raises if keys/SDK missing."""
    cid = os.getenv("SNAPTRADE_CLIENT_ID", "").strip()
    ckey = os.getenv("SNAPTRADE_CONSUMER_KEY", "").strip()
    if not (cid and ckey):
        raise SnapTradeConfigError("SNAPTRADE_CLIENT_ID / SNAPTRADE_CONSUMER_KEY not set in .env")
    from snaptrade_client import SnapTrade  # lazy — module loads without the SDK
    return SnapTrade(consumer_key=ckey, client_id=cid)


def snaptrade_user_id(email: str) -> str:
    """Deterministic, non-reversible SnapTrade userId for an app email."""
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:20]
    return f"{_USER_PREFIX}_{digest}"


def _cred_path(email: str) -> Path:
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:16]
    return _STORE_DIR / f"user_{digest}.json"


def is_linked(email: str) -> bool:
    return _cred_path(email).exists()


def get_credentials(email: str) -> tuple[str, str] | None:
    """Return (userId, userSecret) if linked, else None."""
    path = _cred_path(email)
    if not path.exists():
        return None
    try:
        data = secure_store.read_encrypted_json(path, _PURPOSE)
        uid, secret = data.get("user_id"), data.get("user_secret")
        if uid and secret:
            return uid, secret
    except Exception:
        return None
    return None


def _body(resp: Any) -> Any:
    return getattr(resp, "body", resp)


def link_user(email: str) -> dict:
    """Register the app user with SnapTrade (idempotent) and persist the encrypted
    userSecret. Returns {user_id, linked}. If already linked, returns the stored id
    without re-registering."""
    existing = get_credentials(email)
    if existing:
        return {"user_id": existing[0], "linked": True, "already": True}

    uid = snaptrade_user_id(email)
    resp = client().authentication.register_snap_trade_user(body={"userId": uid})
    body = _body(resp) or {}
    user_secret = body.get("userSecret") or body.get("user_secret")
    if not user_secret:
        raise SnapTradeConfigError(f"SnapTrade register returned no userSecret: {body!r}")

    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    secure_store.write_encrypted_json(
        _cred_path(email), {"user_id": uid, "user_secret": user_secret}, _PURPOSE
    )
    return {"user_id": uid, "linked": True, "already": False}


def connect_url(email: str, *, broker: str | None = None, connection_type: str | None = None,
                custom_redirect: str | None = None) -> str:
    """Mint a SnapTrade connection-portal URL.

    broker=None → the portal shows SnapTrade's FULL list of supported brokerages
    (the user picks). A specific broker slug pre-selects it. connection_type
    defaults by capability: trade-capable brokers (Webull) → 'trade'; everything
    else (incl. generic + Fidelity) → 'read' (data-only, no trade scope requested).
    Registers the user if needed."""
    from tradingagents.brokers.capability import can_place_orders
    link_user(email)
    uid, secret = get_credentials(email)  # type: ignore[misc]
    if connection_type is None:
        connection_type = "trade" if (broker and can_place_orders(broker.lower(), "snaptrade")) else "read"
    kwargs: dict[str, Any] = {
        "user_id": uid,
        "user_secret": secret,
        "connection_type": connection_type,
    }
    if broker:
        kwargs["broker"] = broker.strip().upper()   # omit → portal shows all brokers
    if custom_redirect:
        kwargs["custom_redirect"] = custom_redirect
    resp = client().authentication.login_snap_trade_user(**kwargs)
    body = _body(resp) or {}
    url = body.get("redirectURI") or body.get("redirect_uri")
    if not url:
        raise SnapTradeConfigError(f"SnapTrade login returned no redirect URI: {body!r}")
    return url


def list_connections(email: str) -> list[dict]:
    creds = get_credentials(email)
    if not creds:
        return []
    uid, secret = creds
    resp = client().connections.list_brokerage_authorizations(user_id=uid, user_secret=secret)
    return list(_body(resp) or [])


def unlink_user(email: str) -> dict:
    """Delete the SnapTrade user (removes all their connections) and the local
    encrypted credential file."""
    creds = get_credentials(email)
    deleted_remote = False
    if creds:
        try:
            client().authentication.delete_snap_trade_user(user_id=creds[0])
            deleted_remote = True
        except Exception:
            deleted_remote = False
    try:
        _cred_path(email).unlink(missing_ok=True)
    except Exception:
        pass
    return {"unlinked": True, "deleted_remote": deleted_remote}
