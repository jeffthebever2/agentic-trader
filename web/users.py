"""
Local user registry.

JSON-backed mapping of Cloudflare Access email -> user record.
Source of truth for app-level role (admin / user).
First login bootstraps an admin if env CF_ACCESS_BOOTSTRAP_ADMIN matches.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
_STORE = ROOT / "tmp" / "users.json"
_LOCK = threading.Lock()
HIL_DISCLOSURE_VERSION = "1.0"
HIL_DISCLOSURE_EFFECTIVE_DATE = "2026-05-20"
_LEGAL_FIELDS = (
    "terms_accepted_at",
    "privacy_accepted_at",
    "risk_acknowledged_at",
)
_HIL_LEGAL_FIELDS = (
    "hil_disclosure_accepted_at",
    "hil_disclosure_version",
    "hil_disclosure_effective_date",
)


def _remote_store():
    """Return the configured remote backend, preferring Cloudflare D1."""
    try:
        from web import d1_store
        if d1_store.enabled():
            return d1_store
    except Exception:
        pass
    try:
        from web import supabase_store
        return supabase_store if supabase_store.enabled() else None
    except Exception:
        return None


def _load() -> dict[str, dict[str, Any]]:
    remote_store = _remote_store()
    if remote_store is not None:
        try:
            remote = remote_store.fetch_all()
            # Legal acks now persist in Supabase columns. Defensive: if a row
            # predates the columns, treat missing acks as not-accepted and
            # force re-onboarding so the legal gate is honored.
            for rec in remote.values():
                for field in _LEGAL_FIELDS:
                    rec[field] = int(rec.get(field) or 0)
                rec["hil_disclosure_accepted_at"] = int(rec.get("hil_disclosure_accepted_at") or 0)
                rec["hil_disclosure_version"] = rec.get("hil_disclosure_version") or ""
                rec["hil_disclosure_effective_date"] = rec.get("hil_disclosure_effective_date") or ""
                if not all(rec.get(field) for field in _LEGAL_FIELDS):
                    rec["onboarding_completed"] = False
            return remote
        except Exception:
            # Fall back to local file on any transport error.
            pass
    return _load_local()


def _load_local() -> dict[str, dict[str, Any]]:
    if not _STORE.exists():
        return {}
    try:
        return json.loads(_STORE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    _save_local(data)
    remote_store = _remote_store()
    if remote_store is not None:
        try:
            remote_store.upsert_many(list(data.values()))
            return
        except Exception:
            # Fall through to local persistence so data isn't lost.
            pass


def _save_local(data: dict[str, dict[str, Any]]) -> None:
    parent = _STORE.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Restrict the tmp/ directory so other local users can't browse it.
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    raw = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    # Write atomically at 0o600 so the TOTP secret and passkey material are
    # never world-readable, even briefly. Using os.open avoids the race between
    # write_text() and a subsequent chmod() call.
    fd, tmp_name = tempfile.mkstemp(prefix=".users-", suffix=".json.tmp", dir=str(parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        tmp.unlink(missing_ok=True)
        raise
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)
    try:
        os.chmod(_STORE, 0o600)
    except OSError:
        pass


def get_or_create_user(email: str) -> dict[str, Any]:
    """Return the user record for `email`, creating it on first sight.

    First user ever created becomes admin automatically (bootstrap).
    Subsequent users default to role 'user'. An env override
    CF_ACCESS_BOOTSTRAP_ADMIN=email1,email2 forces those emails to admin
    on first creation.
    """
    email = email.strip().lower()
    if not email:
        raise ValueError("email required")

    with _LOCK:
        data = _load()
        if email in data:
            return data[email]

        bootstrap = {
            e.strip().lower()
            for e in os.getenv("CF_ACCESS_BOOTSTRAP_ADMIN", "").split(",")
            if e.strip()
        }
        is_first = len(data) == 0
        role = "admin" if (is_first or email in bootstrap) else "user"

        record = {
            "email": email,
            "name": "",                # display name; from IdP claim or set by user
            "role": role,
            "created_first": is_first,
            "onboarding_completed": False,
            "terms_accepted_at": 0,
            "privacy_accepted_at": 0,
            "risk_acknowledged_at": 0,
            "hil_disclosure_accepted_at": 0,
            "hil_disclosure_version": "",
            "hil_disclosure_effective_date": "",
            "phone_number": "",       # E.164 SMS destination, blank until user opts in
            "sms_verified": False,    # set after a successful test send
            "last_login_notice_at": 0, # throttles helpful security emails
            # ── Step-up 2FA before placing real trades ──
            "step_up_method": "none",  # "none" | "totp" | "passkey"
            "totp_secret": "",         # base32 secret, set during enrollment
            "totp_enabled": False,     # True after first valid code confirms enroll
            "passkeys": [],            # list of {id, public_key, sign_count, name, created}
        }
        data[email] = record
        _save(data)
        return record


# Per-user Human-In-the-Loop trading preferences. Defaults = "balanced".
DEFAULT_HIL_PREFS: dict[str, Any] = {
    "enabled": False,             # require approval before real-money trades
    "risk_profile": "balanced",   # conservative | balanced | aggressive | custom
    "min_risk_reward": 1.5,       # minimum reward:risk to consider a setup
    "position_max_pct": 25.0,     # max % of account per position
    "position_min_pct": 10.0,     # min % of account per position
    "max_positions": 5,           # max concurrent open positions
    "daily_loss_limit_pct": 2.0,  # halt new entries past this daily drawdown
    "approval_timeout_min": 15,   # minutes to wait for SMS approval
    "auto_reject_on_timeout": True,  # True = reject on timeout, False = auto-approve
    "notify_channel": "sms",      # sms | email | none
}

# Recommended presets surfaced in the UI. Keep in sync with the frontend cards.
HIL_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {"min_risk_reward": 2.0, "position_max_pct": 10.0, "position_min_pct": 5.0,
                     "max_positions": 3, "daily_loss_limit_pct": 1.0},
    "balanced":     {"min_risk_reward": 1.5, "position_max_pct": 25.0, "position_min_pct": 10.0,
                     "max_positions": 5, "daily_loss_limit_pct": 2.0},
    "aggressive":   {"min_risk_reward": 1.2, "position_max_pct": 40.0, "position_min_pct": 15.0,
                     "max_positions": 8, "daily_loss_limit_pct": 3.5},
}


def get_hil_prefs(rec: dict[str, Any]) -> dict[str, Any]:
    """Return a user's HIL prefs with defaults filled for older records."""
    stored = (rec or {}).get("hil_prefs") or {}
    return {**DEFAULT_HIL_PREFS, **stored}


def set_hil_prefs(email: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Validate-merge HIL prefs onto a user record. Unknown keys ignored."""
    email = email.strip().lower()
    clean = {k: prefs[k] for k in DEFAULT_HIL_PREFS if k in prefs}
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        merged = {**DEFAULT_HIL_PREFS, **(data[email].get("hil_prefs") or {}), **clean}
        data[email]["hil_prefs"] = merged
        _save(data)
        return data[email]


def update_user(email: str, **fields: Any) -> dict[str, Any]:
    """Merge arbitrary fields into a user record. Internal helper for 2FA."""
    email = email.strip().lower()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        data[email].update(fields)
        _save(data)
        return data[email]


def get_user(email: str) -> dict[str, Any] | None:
    with _LOCK:
        return _load().get(email.strip().lower())


def prettify_email_name(email: str) -> str:
    """Fallback display name from an email local-part.

    jeffthebever200@x -> "Jeffthebever200"; john.doe@x -> "John Doe".
    """
    local = (email or "").split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) if parts else email


def display_name(rec: dict[str, Any]) -> str:
    """Best display name: explicit name, else IdP name, else prettified email."""
    return (rec.get("name") or "").strip() or prettify_email_name(rec.get("email", ""))


def set_name(email: str, name: str) -> dict[str, Any]:
    email = email.strip().lower()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        data[email]["name"] = (name or "").strip()[:80]
        _save(data)
        return data[email]


def set_phone(email: str, phone: str, verified: bool = False) -> dict[str, Any]:
    email = email.strip().lower()
    phone = phone.strip()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        data[email]["phone_number"] = phone
        data[email]["sms_verified"] = bool(verified)
        _save(data)
        return data[email]


def set_sms_opt_out(email: str, opted_out: bool) -> dict[str, Any]:
    email = email.strip().lower()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        data[email]["sms_opted_out"] = bool(opted_out)
        _save(data)
        return data[email]


def _phone_key(phone: str) -> str:
    """Reduce a phone to comparable form: digits-only, US country code stripped.

    +15551234567 -> 5551234567
    (555) 123-4567 -> 5551234567
    +442079460958 -> 442079460958 (no 1-prefix to strip)
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def find_by_phone(phone: str) -> dict[str, Any] | None:
    """Look up a user record by phone, tolerant of country-code differences."""
    target = _phone_key(phone)
    if not target:
        return None
    with _LOCK:
        for rec in _load().values():
            if _phone_key(rec.get("phone_number") or "") == target:
                return rec
    return None


def complete_onboarding(
    email: str,
    *,
    terms_accepted: bool = False,
    privacy_accepted: bool = False,
    risk_acknowledged: bool = False,
) -> dict[str, Any]:
    email = email.strip().lower()
    if not (terms_accepted and privacy_accepted and risk_acknowledged):
        raise ValueError("terms, privacy, and risk acknowledgement are required")
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        now = int(time.time())
        data[email]["terms_accepted_at"] = data[email].get("terms_accepted_at") or now
        data[email]["privacy_accepted_at"] = data[email].get("privacy_accepted_at") or now
        data[email]["risk_acknowledged_at"] = data[email].get("risk_acknowledged_at") or now
        data[email]["onboarding_completed"] = True
        _save(data)
        return data[email]


def accept_hil_disclosure(email: str) -> dict[str, Any]:
    email = email.strip().lower()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        now = int(time.time())
        data[email]["hil_disclosure_accepted_at"] = now
        data[email]["hil_disclosure_version"] = HIL_DISCLOSURE_VERSION
        data[email]["hil_disclosure_effective_date"] = HIL_DISCLOSURE_EFFECTIVE_DATE
        _save(data)
        return data[email]


def set_role(email: str, role: str) -> dict[str, Any]:
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    email = email.strip().lower()
    with _LOCK:
        data = _load()
        if email not in data:
            raise KeyError(f"user {email!r} not found")
        data[email]["role"] = role
        _save(data)
        return data[email]


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        return sorted(_load().values(), key=lambda r: r["email"])


def delete_user(email: str) -> None:
    email = email.strip().lower()
    with _LOCK:
        remote_store = _remote_store()
        if remote_store is not None:
            try:
                remote_store.delete(email)
                return
            except Exception:
                pass
        data = _load()
        if email in data:
            del data[email]
            _save(data)
