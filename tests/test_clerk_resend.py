"""Tests for the Resend email transport + Clerk step-up 2FA verification."""
import io
import json

import pytest


# ── Resend email transport ─────────────────────────────────────────────────────

def test_resend_configured(monkeypatch):
    import scripts.email_sender as es
    monkeypatch.setattr(es, "load_env_defaults", lambda: None)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert es.resend_configured() is False
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("RESEND_FROM", "bot@x.com")
    assert es.resend_configured() is True


def test_use_resend_provider_selection(monkeypatch):
    import scripts.email_sender as es
    monkeypatch.setattr(es, "load_env_defaults", lambda: None)
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("RESEND_FROM", "bot@x.com")
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    assert es._use_resend() is False               # explicit smtp wins
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    assert es._use_resend() is True
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    assert es._use_resend() is True                # auto: resend configured


def test_send_email_via_resend(monkeypatch):
    import scripts.email_sender as es
    import urllib.request
    monkeypatch.setattr(es, "load_env_defaults", lambda: None)
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("RESEND_FROM", "bot@x.com")
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")

    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"id": "email_123"}).encode()

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["ua"] = req.headers.get("User-agent")
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = es.send_email("wt@x.com", "Subj", "Body")
    assert out["success"] and out["provider"] == "resend" and out["id"] == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_x"
    assert "AgenticTrader" in (captured["ua"] or "")   # non-default UA (Cloudflare 1010 fix)
    assert captured["body"]["to"] == ["wt@x.com"]


# ── Clerk step-up verification ──────────────────────────────────────────────────

def test_clerk_configured(monkeypatch):
    from web import clerk
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    assert clerk.clerk_configured() is False
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_x")
    monkeypatch.setenv("CLERK_ISSUER", "https://x.clerk.accounts.dev")
    assert clerk.clerk_configured() is True


def test_verify_step_up_not_configured(monkeypatch):
    from web import clerk
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    ok, reason = clerk.verify_step_up("tok", "wt@x.com")
    assert not ok and "not configured" in reason


def test_verify_step_up_email_match(monkeypatch):
    from web import clerk
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_x")
    monkeypatch.setenv("CLERK_ISSUER", "https://x.clerk.accounts.dev")
    monkeypatch.setattr(clerk, "verify_session_token",
                        lambda t: {"sub": "u1", "email": "WT@x.com"})
    ok, reason = clerk.verify_step_up("tok", "wt@x.com")
    assert ok and reason == "ok"


def test_verify_step_up_email_mismatch(monkeypatch):
    from web import clerk
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_x")
    monkeypatch.setenv("CLERK_ISSUER", "https://x.clerk.accounts.dev")
    monkeypatch.setattr(clerk, "verify_session_token",
                        lambda t: {"sub": "u1", "email": "someone-else@x.com"})
    ok, reason = clerk.verify_step_up("tok", "wt@x.com")
    assert not ok and "does not match" in reason


def test_verify_step_up_bad_token(monkeypatch):
    from web import clerk
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_x")
    monkeypatch.setenv("CLERK_ISSUER", "https://x.clerk.accounts.dev")
    def _boom(t): raise ValueError("bad signature")
    monkeypatch.setattr(clerk, "verify_session_token", _boom)
    ok, reason = clerk.verify_step_up("tok", "wt@x.com")
    assert not ok and "invalid" in reason


def test_step_up_status_reports_clerk(monkeypatch):
    from web import twofa
    monkeypatch.setattr(twofa, "_clerk_configured", lambda: True)
    monkeypatch.setattr(twofa.user_store, "get_user", lambda e: {"step_up_method": "clerk"})
    st = twofa.step_up_status("wt@x.com")
    assert st["clerk_enabled"] is True and st["method"] == "clerk"
