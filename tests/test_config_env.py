"""Characterization tests for the config/env boundary.

The env_bool tests explicitly PIN equivalence to the legacy copy-pasted idiom,
so the 21-site migration is provably behavior-preserving.
"""
import os

import pytest

from tradingagents.config import env_bool, env_int, env_float, env_str, env_list


def _legacy_permissive(name: str, default: str) -> bool:
    """The exact idiom being replaced across 21 call sites."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@pytest.mark.parametrize("value", [
    None, "true", "True", "TRUE", " true ", "false", "False", "0", "1",
    "yes", "no", "on", "off", "", "garbage",
])
@pytest.mark.parametrize("legacy_default", ["true", "false"])
def test_env_bool_matches_legacy_idiom(monkeypatch, value, legacy_default):
    """env_bool(name, default) == the legacy permissive idiom, for every input."""
    name = "TA_TEST_FLAG"
    monkeypatch.delenv(name, raising=False)
    if value is not None:
        monkeypatch.setenv(name, value)
    default_bool = legacy_default == "true"
    assert env_bool(name, default_bool) == _legacy_permissive(name, legacy_default)


def test_env_bool_unset_returns_default(monkeypatch):
    monkeypatch.delenv("TA_MISSING", raising=False)
    assert env_bool("TA_MISSING", True) is True
    assert env_bool("TA_MISSING", False) is False


def test_env_bool_recognized_truthy(monkeypatch):
    for v in ("1", "true", "yes", "on", "TRUE", " On "):
        monkeypatch.setenv("TA_B", v)
        assert env_bool("TA_B") is True


def test_env_bool_falsey(monkeypatch):
    for v in ("0", "false", "no", "off", "", "banana"):
        monkeypatch.setenv("TA_B", v)
        assert env_bool("TA_B") is False


def test_env_int(monkeypatch):
    monkeypatch.setenv("TA_I", "42")
    assert env_int("TA_I", 7) == 42
    monkeypatch.setenv("TA_I", "42.9")   # legacy int(float(...)) behavior
    assert env_int("TA_I", 7) == 42
    monkeypatch.setenv("TA_I", "garbage")
    assert env_int("TA_I", 7) == 7
    monkeypatch.delenv("TA_I", raising=False)
    assert env_int("TA_I", 7) == 7


def test_env_float(monkeypatch):
    monkeypatch.setenv("TA_F", "3.14")
    assert env_float("TA_F", 1.0) == 3.14
    monkeypatch.setenv("TA_F", "x")
    assert env_float("TA_F", 1.0) == 1.0
    monkeypatch.delenv("TA_F", raising=False)
    assert env_float("TA_F", 1.0) == 1.0


def test_env_str(monkeypatch):
    monkeypatch.setenv("TA_S", "  hello  ")
    assert env_str("TA_S") == "hello"
    monkeypatch.delenv("TA_S", raising=False)
    assert env_str("TA_S", "fallback") == "fallback"


def test_env_list(monkeypatch):
    monkeypatch.setenv("TA_L", "a, b ,,c")
    assert env_list("TA_L") == ["a", "b", "c"]
    monkeypatch.delenv("TA_L", raising=False)
    assert env_list("TA_L") == []
    assert env_list("TA_L", ["x"]) == ["x"]
