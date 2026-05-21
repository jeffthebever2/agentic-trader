"""Onboarding flag persistence tests."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_users(tmp_path, monkeypatch):
    from web import users as user_store
    monkeypatch.setattr(user_store, "_STORE", tmp_path / "users.json")
    yield user_store


def test_new_user_onboarding_false(tmp_users):
    rec = tmp_users.get_or_create_user("alice@example.com")
    assert rec["onboarding_completed"] is False
    assert rec["terms_accepted_at"] == 0
    assert rec["privacy_accepted_at"] == 0
    assert rec["risk_acknowledged_at"] == 0


def test_complete_onboarding_persists(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    rec = tmp_users.complete_onboarding(
        "alice@example.com",
        terms_accepted=True,
        privacy_accepted=True,
        risk_acknowledged=True,
    )
    assert rec["onboarding_completed"] is True
    assert rec["terms_accepted_at"] > 0
    assert rec["privacy_accepted_at"] > 0
    assert rec["risk_acknowledged_at"] > 0
    # Re-fetch confirms persistence
    again = tmp_users.get_or_create_user("alice@example.com")
    assert again["onboarding_completed"] is True


def test_complete_onboarding_requires_legal_ack(tmp_users):
    tmp_users.get_or_create_user("alice@example.com")
    with pytest.raises(ValueError):
        tmp_users.complete_onboarding(
            "alice@example.com",
            terms_accepted=True,
            privacy_accepted=False,
            risk_acknowledged=True,
        )


def test_complete_onboarding_unknown_user(tmp_users):
    with pytest.raises(KeyError):
        tmp_users.complete_onboarding(
            "ghost@example.com",
            terms_accepted=True,
            privacy_accepted=True,
            risk_acknowledged=True,
        )
