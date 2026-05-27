from pathlib import Path

import pytest

from web import secure_store


def test_encrypted_json_roundtrip_uses_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BROKER_SESSION_KEY", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_BROKER_SESSION_KEY", raising=False)
    monkeypatch.setattr(secure_store, "_KEY_PATH", tmp_path / "broker_session.key")

    path = tmp_path / "broker.json"
    secure_store.write_encrypted_json(path, {"token": "secret", "account_id": "abc"}, "broker-test")

    raw = path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert secure_store.is_encrypted_path(path)
    assert secure_store.read_encrypted_json(path, "broker-test") == {
        "account_id": "abc",
        "token": "secret",
    }


def test_encrypted_json_rejects_wrong_purpose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BROKER_SESSION_KEY", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_BROKER_SESSION_KEY", raising=False)
    monkeypatch.setattr(secure_store, "_KEY_PATH", tmp_path / "broker_session.key")

    path = tmp_path / "broker.json"
    secure_store.write_encrypted_json(path, {"token": "secret"}, "broker-test")

    with pytest.raises(ValueError, match="purpose mismatch"):
        secure_store.read_encrypted_json(path, "other-purpose")


def test_short_env_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BROKER_SESSION_KEY", "too-short")
    monkeypatch.setattr(secure_store, "_KEY_PATH", tmp_path / "broker_session.key")

    with pytest.raises(secure_store.SecureStoreError, match="at least 32 characters"):
        secure_store.write_encrypted_json(tmp_path / "broker.json", {"token": "secret"}, "broker-test")


def test_generated_key_and_temp_file_are_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BROKER_SESSION_KEY", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_BROKER_SESSION_KEY", raising=False)
    monkeypatch.setattr(secure_store, "_KEY_PATH", tmp_path / "broker_session.key")
    monkeypatch.setattr(secure_store, "_TMP_DIR", tmp_path / "secure-tmp")

    path = tmp_path / "broker.json"
    secure_store.write_encrypted_json(path, {"token": "secret"}, "broker-test")
    temp_name = secure_store.encrypted_temp_file(path, "broker-test")

    assert ((tmp_path / "broker_session.key").stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "secure-tmp").stat().st_mode & 0o777) == 0o700
    assert (Path(temp_name).stat().st_mode & 0o777) == 0o600
    Path(temp_name).unlink()
