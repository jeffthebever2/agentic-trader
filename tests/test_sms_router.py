"""Tests for the two-way SMS command dispatcher."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def registered_user(tmp_path, monkeypatch):
    from web import users as user_store
    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    user_store.get_or_create_user("alice@example.com")  # first user = admin
    user_store.set_phone("alice@example.com", "+15551234567", verified=True)
    return user_store


def test_unknown_number(registered_user):
    from web.sms_router import dispatch
    r = dispatch("+19999999999", "STATUS")
    assert r["matched"] is None
    assert "not registered" in r["reply"].lower()


def test_help(registered_user):
    from web.sms_router import dispatch
    r = dispatch("+15551234567", "HELP")
    assert r["matched"] == "help"
    assert "STATUS" in r["reply"]


def test_whoami(registered_user):
    from web.sms_router import dispatch
    r = dispatch("+15551234567", "whoami")
    assert "alice@example.com" in r["reply"]
    assert "role: admin" in r["reply"]


def test_unknown_command(registered_user):
    from web.sms_router import dispatch
    r = dispatch("+15551234567", "asdfgh")
    assert r["matched"] is None
    assert "Unknown command" in r["reply"]


def test_stop_then_start(registered_user):
    from web.sms_router import dispatch
    r1 = dispatch("+15551234567", "STOP")
    assert "no longer" in r1["reply"]
    assert registered_user._load()["alice@example.com"]["sms_opted_out"] is True
    r2 = dispatch("+15551234567", "start")
    assert "re-enabled" in r2["reply"]
    assert registered_user._load()["alice@example.com"]["sms_opted_out"] is False


def test_role_admin_only(tmp_path, monkeypatch):
    from web import users as user_store
    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    user_store.get_or_create_user("admin@example.com")           # admin
    user_store.set_phone("admin@example.com", "+15550000001", verified=True)
    user_store.get_or_create_user("bob@example.com")             # user
    user_store.set_phone("bob@example.com", "+15550000002", verified=True)

    from web.sms_router import dispatch
    # Non-admin trying ROLE
    r = dispatch("+15550000002", "ROLE admin@example.com user")
    assert "Admin only" in r["reply"]
    # Admin promoting bob
    r2 = dispatch("+15550000001", "ROLE bob@example.com admin")
    assert "now admin" in r2["reply"]
    assert user_store._load()["bob@example.com"]["role"] == "admin"


def test_phone_match_ignores_formatting(registered_user):
    from web.sms_router import dispatch
    # Stored: +15551234567 ; inbound: "(555) 123-4567"
    r = dispatch("(555) 123-4567", "HELP")
    assert r["matched"] == "help"
