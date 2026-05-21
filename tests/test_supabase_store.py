"""Unit tests for the Supabase store backend (no network)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from web import supabase_store
    assert supabase_store.enabled() is False


def test_enabled_with_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    from web import supabase_store
    # conftest autouse patches enabled()->False to keep other tests off the
    # real DB; verify the underlying predicate instead.
    assert bool(supabase_store._url() and supabase_store._key()) is True


def test_clean_drops_unknown_columns():
    from web import supabase_store
    rec = {
        "email": "a@b.com", "name": "A", "role": "admin",
        "created_at": "2026", "updated_at": "2026",  # DB-managed, must drop
        "bogus": 1,                                    # unknown, must drop
        "passkeys": [{"id": "x"}],
    }
    cleaned = supabase_store._clean(rec)
    assert "created_at" not in cleaned
    assert "updated_at" not in cleaned
    assert "bogus" not in cleaned
    assert cleaned["email"] == "a@b.com"
    assert cleaned["passkeys"] == [{"id": "x"}]


def test_users_falls_back_to_json_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from web import users
    monkeypatch.setattr(users, "_STORE", tmp_path / "users.json")
    rec = users.get_or_create_user("local@x.com")
    assert rec["email"] == "local@x.com"
    assert (tmp_path / "users.json").exists()
