"""Tests for the local user registry + auth deps."""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_users(tmp_path, monkeypatch):
    """Point the user store at a clean temp file for each test."""
    from web import users as user_store
    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    yield user_store


def test_first_user_becomes_admin(tmp_users):
    rec = tmp_users.get_or_create_user("alice@example.com")
    assert rec["role"] == "admin"
    assert rec["email"] == "alice@example.com"


def test_second_user_is_plain_user(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    rec = tmp_users.get_or_create_user("bob@example.com")
    assert rec["role"] == "user"


def test_bootstrap_admin_env_overrides(tmp_users, monkeypatch):
    monkeypatch.setenv("CF_ACCESS_BOOTSTRAP_ADMIN", "bob@example.com")
    tmp_users.get_or_create_user("alice@example.com")
    rec = tmp_users.get_or_create_user("bob@example.com")
    assert rec["role"] == "admin"


def test_email_normalized(tmp_users):
    a = tmp_users.get_or_create_user("Alice@Example.com")
    b = tmp_users.get_or_create_user("alice@example.com")
    assert a["email"] == b["email"] == "alice@example.com"


def test_set_role(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    tmp_users.get_or_create_user("bob@example.com")
    rec = tmp_users.set_role("bob@example.com", "admin")
    assert rec["role"] == "admin"


def test_set_role_unknown_user(tmp_users):
    with pytest.raises(KeyError):
        tmp_users.set_role("ghost@example.com", "admin")


def test_set_role_invalid(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    with pytest.raises(ValueError):
        tmp_users.set_role("alice@example.com", "superuser")


def test_delete_user(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    tmp_users.delete_user("alice@example.com")
    assert tmp_users.list_users() == []


def test_dev_localhost_fallback(monkeypatch, tmp_users):
    """get_optional_user returns dev@local on localhost when not required."""
    monkeypatch.setenv("CF_ACCESS_REQUIRED", "false")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)

    from web import auth as auth_mod
    importlib.reload(auth_mod)
    # patch user store reference inside reloaded auth
    monkeypatch.setattr(auth_mod, "get_or_create_user", tmp_users.get_or_create_user)

    class FakeRequest:
        headers = {"host": "localhost:8001"}
        cookies = {}
    u = auth_mod.get_optional_user(FakeRequest())
    assert u and u["email"] == "dev@local"
    assert u["role"] == "admin"  # first user auto-admin


def test_no_token_returns_none(monkeypatch, tmp_users):
    monkeypatch.setenv("CF_ACCESS_REQUIRED", "true")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
    from web import auth as auth_mod
    importlib.reload(auth_mod)
    monkeypatch.setattr(auth_mod, "get_or_create_user", tmp_users.get_or_create_user)

    class FakeRequest:
        headers = {"host": "app.example.com"}
        cookies = {}
    assert auth_mod.get_optional_user(FakeRequest()) is None
