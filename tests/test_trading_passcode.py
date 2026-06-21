"""Trading-passcode step-up method: hashing, verification, lockout, status.

The passcode is convenience re-verification for real-money trades, so the
security properties matter: never stored in plain text, salted so equal
passcodes hash differently per user, and online guessing is throttled.
"""
import secrets

import pytest

import web.twofa as t


def _rec(passcode: str) -> dict:
    salt = secrets.token_bytes(16)
    return {"passcode_salt": salt.hex(), "passcode_hash": t._derive_passcode(passcode, salt)}


def test_derive_is_deterministic_and_salted():
    s1, s2 = secrets.token_bytes(16), secrets.token_bytes(16)
    assert t._derive_passcode("hunter2", s1) == t._derive_passcode("hunter2", s1)
    assert t._derive_passcode("hunter2", s1) != t._derive_passcode("hunter2", s2)  # salt matters
    assert t._derive_passcode("hunter2", s1) != t._derive_passcode("hunter3", s1)  # code matters


def test_hash_is_not_plaintext():
    rec = _rec("opensesame")
    assert "opensesame" not in rec["passcode_hash"]
    assert len(bytes.fromhex(rec["passcode_hash"])) == 32  # sha256 digest


def test_verify_roundtrip(monkeypatch):
    rec = _rec("opensesame")
    monkeypatch.setattr(t.user_store, "get_user", lambda e: rec)
    t._PASSCODE_FAILS.pop("u@x.com", None)
    assert t.verify_passcode("u@x.com", "opensesame") is True
    assert t.verify_passcode("u@x.com", "wrong") is False
    assert t.verify_passcode("u@x.com", "") is False


def test_verify_no_passcode_set(monkeypatch):
    monkeypatch.setattr(t.user_store, "get_user", lambda e: {"passcode_salt": "", "passcode_hash": ""})
    assert t.verify_passcode("none@x.com", "whatever") is False


def test_success_clears_failures(monkeypatch):
    rec = _rec("rightcode")
    monkeypatch.setattr(t.user_store, "get_user", lambda e: rec)
    email = "clear@x.com"
    t._PASSCODE_FAILS.pop(email, None)
    t.verify_passcode(email, "nope")
    t.verify_passcode(email, "nope")
    assert t._PASSCODE_FAILS.get(email, {}).get("count") == 2
    assert t.verify_passcode(email, "rightcode") is True
    assert email not in t._PASSCODE_FAILS  # reset on success


def test_lockout_triggers_after_max_fails(monkeypatch):
    rec = _rec("rightcode")
    monkeypatch.setattr(t.user_store, "get_user", lambda e: rec)
    email = "lock@x.com"
    t._PASSCODE_FAILS.pop(email, None)
    assert t.passcode_lockout_remaining(email) == 0
    for _ in range(t._PASSCODE_MAX_FAILS):
        assert t.verify_passcode(email, "nope") is False
    remaining = t.passcode_lockout_remaining(email)
    assert 0 < remaining <= t._PASSCODE_LOCK_BASE
    t._PASSCODE_FAILS.pop(email, None)


def test_lockout_escalates_and_is_capped(monkeypatch):
    email = "esc@x.com"
    t._PASSCODE_FAILS.pop(email, None)
    # Drive many failures; the lockout must grow but never exceed the cap.
    for _ in range(t._PASSCODE_MAX_FAILS + 12):
        t._record_passcode_fail(email)
    assert t.passcode_lockout_remaining(email) <= t._PASSCODE_LOCK_CAP
    t._PASSCODE_FAILS.pop(email, None)


def test_status_reports_passcode(monkeypatch):
    monkeypatch.setattr(
        t.user_store, "get_user",
        lambda e: {"step_up_method": "passcode", "passcode_enabled": True},
    )
    st = t.step_up_status("a@b.com")
    assert st["method"] == "passcode"
    assert st["passcode_enabled"] is True


def test_min_length_enforced(monkeypatch):
    monkeypatch.setattr(t.user_store, "update_user", lambda *a, **k: None)
    with pytest.raises(ValueError):
        t.set_passcode("a@b.com", "12345")  # < 6 chars
    # 6+ chars is accepted (update_user stubbed)
    t.set_passcode("a@b.com", "123456")
