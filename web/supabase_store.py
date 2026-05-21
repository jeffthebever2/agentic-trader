"""
Supabase (PostgREST) backend for the user registry.

Active only when SUPABASE_URL and SUPABASE_SERVICE_KEY are set; otherwise
web/users.py falls back to the local tmp/users.json file.

We use the service-role key so the app (already authenticated by
Cloudflare Access) bypasses RLS. The agentic_users table has RLS enabled
with no permissive policy, so anon/publishable keys cannot read it.

Table: public.agentic_users  (email primary key)
"""
from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

import httpx

TABLE = "agentic_users"
_TIMEOUT = 10.0

# Columns that exist in the table; anything else in a record is dropped
# before upsert so stray keys don't 400 the request.
_COLUMNS = {
    "email", "name", "role", "created_first", "onboarding_completed",
    "phone_number", "sms_verified", "sms_opted_out", "last_login_notice_at",
    "step_up_method", "totp_secret", "totp_enabled", "passkeys",
    "terms_accepted_at", "privacy_accepted_at", "risk_acknowledged_at",
}


def _url() -> str:
    return os.getenv("SUPABASE_URL", "").strip().rstrip("/")


def _key() -> str:
    return os.getenv("SUPABASE_SERVICE_KEY", "").strip()


def enabled() -> bool:
    return bool(_url() and _key())


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "apikey": _key(),
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _rest(path: str) -> str:
    return f"{_url()}/rest/v1/{path}"


def _clean(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k in _COLUMNS}


def fetch_all() -> dict[str, dict[str, Any]]:
    """Return all users as {email: record}."""
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.get(_rest(f"{TABLE}?select=*"), headers=_headers())
        r.raise_for_status()
        rows = r.json()
    return {row["email"]: row for row in rows}


def upsert_many(records: list[dict[str, Any]]) -> None:
    """Upsert (insert or merge on email PK) a batch of user records."""
    if not records:
        return
    payload = [_clean(r) for r in records]
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(
            _rest(TABLE),
            headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            json=payload,
        )
        r.raise_for_status()


def delete(email: str) -> None:
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.delete(
            _rest(f"{TABLE}?email=eq.{quote(email, safe='')}"),
            headers=_headers({"Prefer": "return=minimal"}),
        )
        # 404 / empty delete is fine.
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()


# ── Generic per-user JSON blob (portfolios, prefs, ...) ────────────
def blob_get(table: str, email: str) -> Optional[dict[str, Any]]:
    """Return the `data` jsonb for a per-user blob row, or None."""
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.get(
            _rest(f"{table}?email=eq.{quote(email, safe='')}&select=data"),
            headers=_headers(),
        )
        r.raise_for_status()
        rows = r.json()
    return rows[0]["data"] if rows else None


def blob_put(table: str, email: str, data: dict[str, Any]) -> None:
    """Upsert a per-user blob row keyed by email."""
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(
            _rest(table),
            headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            json=[{"email": email, "data": data}],
        )
        r.raise_for_status()
