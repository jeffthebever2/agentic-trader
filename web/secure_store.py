"""Encrypted local storage helpers for sensitive broker session blobs."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).parent.parent
_KEY_PATH = ROOT / "tmp" / "broker_session.key"
_TMP_DIR = ROOT / "tmp"
_SCHEME = "fernet"
_VERSION = 1
_MIN_PASSPHRASE_LEN = 32


class SecureStoreError(RuntimeError):
    """Raised when encrypted broker session storage cannot be used safely."""


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def _ensure_parent_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_private(path: Path, data: bytes) -> None:
    _ensure_parent_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    _chmod_private(path)


def _normalize_key(raw: str) -> bytes:
    value = raw.strip().encode("utf-8")
    try:
        Fernet(value)
        return value
    except Exception:
        if len(raw.strip()) < _MIN_PASSPHRASE_LEN:
            raise SecureStoreError(
                "BROKER_SESSION_KEY must be a Fernet key or at least "
                f"{_MIN_PASSPHRASE_LEN} characters."
            )
        digest = hashlib.sha256(value).digest()
        return base64.urlsafe_b64encode(digest)


def _load_key() -> bytes:
    env_key = os.getenv("BROKER_SESSION_KEY") or os.getenv("TRADINGAGENTS_BROKER_SESSION_KEY")
    if env_key:
        return _normalize_key(env_key)
    if _KEY_PATH.exists():
        _chmod_private(_KEY_PATH)
        return _KEY_PATH.read_text(encoding="utf-8").strip().encode("utf-8")
    _ensure_private_dir(_KEY_PATH.parent)
    key = Fernet.generate_key()
    fd, tmp_name = tempfile.mkstemp(prefix=".broker-session-key-", dir=str(_KEY_PATH.parent))
    tmp = Path(tmp_name)
    os.close(fd)
    _write_private(tmp, key + b"\n")
    tmp.replace(_KEY_PATH)
    _chmod_private(_KEY_PATH)
    return key


def broker_session_key_configured() -> bool:
    return bool(os.getenv("BROKER_SESSION_KEY") or os.getenv("TRADINGAGENTS_BROKER_SESSION_KEY") or _KEY_PATH.exists())


def broker_session_key_status() -> dict[str, Any]:
    env_key = os.getenv("BROKER_SESSION_KEY") or os.getenv("TRADINGAGENTS_BROKER_SESSION_KEY")
    if env_key:
        return {"source": "env", "configured": True, "private_file": None}
    if not _KEY_PATH.exists():
        return {"source": "missing", "configured": False, "private_file": None}
    try:
        mode = _KEY_PATH.stat().st_mode & 0o777
        return {"source": "local_file", "configured": True, "private_file": mode == 0o600}
    except Exception:
        return {"source": "local_file", "configured": True, "private_file": None}


def _fernet() -> Fernet:
    return Fernet(_load_key())


def _envelope(ciphertext: bytes, purpose: str) -> dict[str, Any]:
    return {
        "encrypted": True,
        "version": _VERSION,
        "scheme": _SCHEME,
        "purpose": purpose,
        "ciphertext": ciphertext.decode("ascii"),
    }


def is_encrypted_blob(data: bytes) -> bool:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        return False
    return bool(isinstance(obj, dict) and obj.get("encrypted") is True and obj.get("scheme") == _SCHEME)


def is_encrypted_path(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return is_encrypted_blob(path.read_bytes())
    except Exception:
        return False


def encrypt_bytes(data: bytes, purpose: str) -> bytes:
    token = _fernet().encrypt(data)
    return (json.dumps(_envelope(token, purpose), sort_keys=True) + "\n").encode("utf-8")


def decrypt_bytes(data: bytes, purpose: str) -> bytes:
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        return data
    if not (isinstance(obj, dict) and obj.get("encrypted") is True):
        return data
    if obj.get("scheme") != _SCHEME:
        raise ValueError(f"Unsupported encrypted blob scheme: {obj.get('scheme')}")
    if obj.get("purpose") != purpose:
        raise ValueError("Encrypted broker session purpose mismatch")
    try:
        return _fernet().decrypt(str(obj.get("ciphertext", "")).encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("Could not decrypt broker session. Check BROKER_SESSION_KEY.") from exc


def write_encrypted(path: Path, data: bytes, purpose: str) -> None:
    _ensure_parent_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    _write_private(tmp, encrypt_bytes(data, purpose))
    tmp.replace(path)
    _chmod_private(path)


def read_encrypted_or_plain(path: Path, purpose: str) -> bytes | None:
    if not path.exists():
        return None
    return decrypt_bytes(path.read_bytes(), purpose)


def write_encrypted_json(path: Path, data: dict[str, Any], purpose: str) -> None:
    raw = json.dumps(data, indent=2, sort_keys=True, default=str).encode("utf-8")
    write_encrypted(path, raw, purpose)


def read_encrypted_json(path: Path, purpose: str) -> dict[str, Any]:
    raw = read_encrypted_or_plain(path, purpose)
    if raw is None:
        return {}
    return json.loads(raw.decode("utf-8"))


def encrypted_temp_file(path: Path, purpose: str, suffix: str = ".json") -> str | None:
    raw = read_encrypted_or_plain(path, purpose)
    if raw is None:
        return None
    _ensure_private_dir(_TMP_DIR)
    fd, tmp_name = tempfile.mkstemp(prefix="broker-session-", suffix=suffix, dir=str(_TMP_DIR))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        _chmod_private(Path(tmp_name))
        return tmp_name
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            Path(tmp_name).unlink()
        except Exception:
            pass
        raise
