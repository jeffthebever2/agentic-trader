"""Cloudflare D1 REST backend for users and per-user JSON blobs.

Enabled when CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, and
CLOUDFLARE_D1_DATABASE_ID are set. Supabase remains the fallback backend.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

USERS_TABLE = "agentic_users"
_TIMEOUT = 10.0
_USER_COLUMNS = {
    "email", "name", "role", "created_first", "onboarding_completed",
    "phone_number", "sms_verified", "sms_opted_out", "last_login_notice_at",
    "step_up_method", "totp_secret", "totp_enabled", "passkeys",
    "terms_accepted_at", "privacy_accepted_at", "risk_acknowledged_at",
    "hil_disclosure_accepted_at", "hil_disclosure_version",
    "hil_disclosure_effective_date",
}
_JSON_COLUMNS = {"passkeys"}
_BOOL_COLUMNS = {"created_first", "onboarding_completed", "sms_verified", "sms_opted_out", "totp_enabled"}
_INT_COLUMNS = {
    "last_login_notice_at", "terms_accepted_at", "privacy_accepted_at",
    "risk_acknowledged_at", "hil_disclosure_accepted_at",
}


def _account_id() -> str:
    return os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()


def _database_id() -> str:
    return os.getenv("CLOUDFLARE_D1_DATABASE_ID", "").strip()


def _token() -> str:
    return os.getenv("CLOUDFLARE_API_TOKEN", "").strip()


def enabled() -> bool:
    return bool(_account_id() and _database_id() and _token())


def _endpoint() -> str:
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{_account_id()}/d1/database/{_database_id()}/query"
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(_endpoint(), headers=_headers(), json={"sql": sql, "params": params or []})
        r.raise_for_status()
        payload = r.json()
    if not payload.get("success", False):
        raise RuntimeError(str(payload.get("errors") or "D1 query failed"))
    result = payload.get("result") or []
    first = result[0] if isinstance(result, list) and result else result
    if isinstance(first, dict) and not first.get("success", True):
        raise RuntimeError(str(first.get("error") or first.get("results") or "D1 query failed"))
    rows = first.get("results") if isinstance(first, dict) else []
    return rows or []


def _encode_value(key: str, value: Any) -> Any:
    if key in _JSON_COLUMNS:
        return json.dumps(value or [])
    if key in _BOOL_COLUMNS:
        return 1 if bool(value) else 0
    if key in _INT_COLUMNS:
        return int(value or 0)
    return value


def _decode_user(row: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    for key, value in row.items():
        if key in _JSON_COLUMNS:
            try:
                rec[key] = json.loads(value or "[]")
            except Exception:
                rec[key] = []
        elif key in _BOOL_COLUMNS:
            rec[key] = bool(value)
        elif key in _INT_COLUMNS:
            rec[key] = int(value or 0)
        else:
            rec[key] = value
    return rec


def _clean(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _encode_value(k, v) for k, v in record.items() if k in _USER_COLUMNS}


def ensure_schema() -> None:
    _query(
        f"""
        CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
            email TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user',
            created_first INTEGER NOT NULL DEFAULT 0,
            onboarding_completed INTEGER NOT NULL DEFAULT 0,
            phone_number TEXT DEFAULT '',
            sms_verified INTEGER NOT NULL DEFAULT 0,
            sms_opted_out INTEGER NOT NULL DEFAULT 0,
            last_login_notice_at INTEGER NOT NULL DEFAULT 0,
            step_up_method TEXT NOT NULL DEFAULT 'none',
            totp_secret TEXT DEFAULT '',
            totp_enabled INTEGER NOT NULL DEFAULT 0,
            passkeys TEXT NOT NULL DEFAULT '[]',
            terms_accepted_at INTEGER NOT NULL DEFAULT 0,
            privacy_accepted_at INTEGER NOT NULL DEFAULT 0,
            risk_acknowledged_at INTEGER NOT NULL DEFAULT 0,
            hil_disclosure_accepted_at INTEGER NOT NULL DEFAULT 0,
            hil_disclosure_version TEXT DEFAULT '',
            hil_disclosure_effective_date TEXT DEFAULT ''
        )
        """
    )
    for col, ddl in (
        ("hil_disclosure_accepted_at", "INTEGER NOT NULL DEFAULT 0"),
        ("hil_disclosure_version", "TEXT DEFAULT ''"),
        ("hil_disclosure_effective_date", "TEXT DEFAULT ''"),
    ):
        try:
            _query(f"ALTER TABLE {USERS_TABLE} ADD COLUMN {col} {ddl}")
        except Exception:
            pass
    _query(
        """
        CREATE TABLE IF NOT EXISTS agentic_blobs (
            table_name TEXT NOT NULL,
            email TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (table_name, email)
        )
        """
    )


def fetch_all() -> dict[str, dict[str, Any]]:
    ensure_schema()
    rows = _query(f"SELECT * FROM {USERS_TABLE} ORDER BY email")
    return {row["email"]: _decode_user(row) for row in rows}


def upsert_many(records: list[dict[str, Any]]) -> None:
    if not records:
        return
    ensure_schema()
    columns = sorted(_USER_COLUMNS)
    placeholders = ",".join(["?"] * len(columns))
    updates = ",".join([f"{col}=excluded.{col}" for col in columns if col != "email"])
    sql = (
        f"INSERT INTO {USERS_TABLE} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(email) DO UPDATE SET {updates}"
    )
    for record in records:
        cleaned = _clean(record)
        params = [cleaned.get(col) for col in columns]
        _query(sql, params)


def delete(email: str) -> None:
    ensure_schema()
    _query(f"DELETE FROM {USERS_TABLE} WHERE email = ?", [email])


def blob_get(table: str, email: str) -> Optional[dict[str, Any]]:
    ensure_schema()
    rows = _query(
        "SELECT data FROM agentic_blobs WHERE table_name = ? AND email = ? LIMIT 1",
        [table, email],
    )
    if not rows:
        return None
    try:
        return json.loads(rows[0].get("data") or "{}")
    except Exception:
        return None


def blob_put(table: str, email: str, data: dict[str, Any]) -> None:
    ensure_schema()
    _query(
        """
        INSERT INTO agentic_blobs (table_name, email, data, updated_at)
        VALUES (?, ?, ?, unixepoch())
        ON CONFLICT(table_name, email) DO UPDATE SET
            data = excluded.data,
            updated_at = excluded.updated_at
        """,
        [table, email, json.dumps(data, default=str)],
    )
