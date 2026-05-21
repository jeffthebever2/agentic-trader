import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeRequest:
    def __init__(self, email: str):
        self.headers = {"x-agentic-view-as": email}


def test_admin_can_view_as_existing_user(tmp_path, monkeypatch):
    from web import users as user_store
    from web.auth import _apply_view_as

    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    admin = user_store.get_or_create_user("admin@example.com")
    target = user_store.get_or_create_user("target@example.com")

    viewed = _apply_view_as(FakeRequest(target["email"]), admin)

    assert viewed["email"] == "target@example.com"
    assert viewed["viewed_by_admin"] is True
    assert viewed["actual_admin_email"] == "admin@example.com"


def test_non_admin_view_as_header_is_ignored(tmp_path, monkeypatch):
    from web import users as user_store
    from web.auth import _apply_view_as

    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    admin = user_store.get_or_create_user("admin@example.com")
    regular = user_store.get_or_create_user("regular@example.com")
    target = user_store.get_or_create_user("target@example.com")
    user_store.set_role(admin["email"], "admin")
    user_store.set_role(regular["email"], "user")

    viewed = _apply_view_as(FakeRequest(target["email"]), regular)

    assert viewed["email"] == "regular@example.com"
    assert not viewed.get("viewed_by_admin")
