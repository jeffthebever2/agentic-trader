import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_d1_enabled_requires_account_token_and_database(monkeypatch):
    from web import d1_store

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_D1_DATABASE_ID", raising=False)
    assert d1_store.enabled() is False

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setenv("CLOUDFLARE_D1_DATABASE_ID", "db")
    assert d1_store.enabled() is True


def test_d1_user_encoding_round_trip():
    from web import d1_store

    rec = {
        "email": "a@example.com",
        "role": "admin",
        "created_first": True,
        "totp_enabled": False,
        "passkeys": [{"id": "key-1"}],
        "terms_accepted_at": "123",
    }

    cleaned = d1_store._clean(rec)
    decoded = d1_store._decode_user(cleaned)

    assert cleaned["created_first"] == 1
    assert decoded["created_first"] is True
    assert decoded["totp_enabled"] is False
    assert decoded["passkeys"] == [{"id": "key-1"}]
    assert decoded["terms_accepted_at"] == 123
