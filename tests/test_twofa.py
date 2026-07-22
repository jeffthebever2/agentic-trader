"""Tests for step-up 2FA (TOTP + step-up token)."""
import sys
from pathlib import Path

import pytest
import pyotp

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from web import users as user_store
    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    # Force step-up secret deterministic + isolated
    monkeypatch.setenv("STEP_UP_SECRET", "test-secret-key")
    user_store.get_or_create_user("trader@example.com")
    return user_store


def test_totp_enroll_then_activate(store):
    from web import twofa
    enroll = twofa.totp_enroll("trader@example.com")
    assert enroll["secret"]
    assert enroll["otpauth_uri"].startswith("otpauth://totp/")
    # Not enabled until a code confirms
    assert twofa.step_up_status("trader@example.com")["totp_enabled"] is False
    code = pyotp.TOTP(enroll["secret"]).now()
    assert twofa.totp_activate("trader@example.com", code) is True
    st = twofa.step_up_status("trader@example.com")
    assert st["totp_enabled"] is True
    assert st["method"] == "totp"


def test_totp_wrong_code_rejected(store):
    from web import twofa
    twofa.totp_enroll("trader@example.com")
    assert twofa.totp_activate("trader@example.com", "000000") is False


def test_step_up_token_roundtrip(store):
    from web import twofa
    tok = twofa.issue_step_up_token("trader@example.com")
    assert twofa.verify_step_up_token(tok, "trader@example.com") is True


def test_step_up_token_is_email_bound(store):
    from web import twofa
    tok = twofa.issue_step_up_token("trader@example.com")
    assert twofa.verify_step_up_token(tok, "attacker@example.com") is False


def test_step_up_token_tamper_detected(store):
    from web import twofa
    tok = twofa.issue_step_up_token("trader@example.com")
    assert twofa.verify_step_up_token(tok + "x", "trader@example.com") is False
    body, _, sig = tok.partition(".")
    assert twofa.verify_step_up_token(body + ".deadbeef", "trader@example.com") is False


def test_step_up_token_expiry(store, monkeypatch):
    from web import twofa
    monkeypatch.setattr(twofa, "_STEP_UP_TTL", -1)  # already expired
    tok = twofa.issue_step_up_token("trader@example.com")
    assert twofa.verify_step_up_token(tok, "trader@example.com") is False


def test_totp_disable_clears_method(store):
    from web import twofa
    enroll = twofa.totp_enroll("trader@example.com")
    twofa.totp_activate("trader@example.com", pyotp.TOTP(enroll["secret"]).now())
    twofa.totp_disable("trader@example.com")
    st = twofa.step_up_status("trader@example.com")
    assert st["totp_enabled"] is False
    assert st["method"] == "none"


def test_email_code_locks_out_after_max_attempts():
    """H5: a wrong email code is invalidated after _EMAIL_CODE_MAX_ATTEMPTS guesses,
    so the 6-digit code cannot be brute-forced within its TTL."""
    import time as _t
    from web import twofa as tf
    email = "victim@example.com"
    tf._PENDING_EMAIL[email.lower()] = {
        "digest": tf._code_digest(email, "123456"),
        "exp": int(_t.time()) + 600,
        "sent_at": int(_t.time()),
        "purpose": "step_up",
    }
    # Wrong guesses up to the cap
    for _ in range(tf._EMAIL_CODE_MAX_ATTEMPTS):
        assert tf.verify_email_code(email, "000000") is False
    # Code is now invalidated — even the CORRECT code no longer works
    assert tf.verify_email_code(email, "123456") is False
    assert email.lower() not in tf._PENDING_EMAIL


def test_email_code_correct_before_lockout():
    import time as _t
    from web import twofa as tf
    email = "ok@example.com"
    tf._PENDING_EMAIL[email.lower()] = {
        "digest": tf._code_digest(email, "654321"),
        "exp": int(_t.time()) + 600,
        "sent_at": int(_t.time()),
        "purpose": "step_up",
    }
    assert tf.verify_email_code(email, "111111") is False   # one wrong
    assert tf.verify_email_code(email, "654321") is True     # correct still works
