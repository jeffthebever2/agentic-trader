"""Broker-level account protection — the Roth/IRA no-trade kill switch and
account-number validation in web/api/fidelity.py. This guard blocks ANY order
routed to a protected account from ANY code path; it is the last line before a
real retirement account could be traded. Only ever tighten these.
"""
import pytest
from fastapi import HTTPException

import web.api.fidelity as f


# ── _validate_account_number ────────────────────────────────────────────────
def test_validate_account_number_accepts_valid():
    assert f._validate_account_number("123456789") == "123456789"
    assert f._validate_account_number("123-456-789") == "123456789"  # dashes stripped
    assert f._validate_account_number(" 12345678 ") == "12345678"
    assert f._validate_account_number(None) is None


def test_validate_account_number_rejects_invalid():
    for bad in ("abc123", "12345", "1" * 16, "12.34", ""):
        with pytest.raises(ValueError):
            f._validate_account_number(bad)


# ── _assert_account_tradeable ───────────────────────────────────────────────
def test_no_protected_env_allows_any(monkeypatch):
    monkeypatch.delenv("FIDELITY_PROTECTED_ACCOUNTS", raising=False)
    # No protected list configured → never raises.
    f._assert_account_tradeable("999999999")
    f._assert_account_tradeable(None)


def test_protected_account_blocked(monkeypatch):
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    with pytest.raises(HTTPException) as ei:
        f._assert_account_tradeable("262502469")
    assert ei.value.status_code == 403
    # normalized forms must also be blocked
    with pytest.raises(HTTPException):
        f._assert_account_tradeable("262-502-469")
    with pytest.raises(HTTPException):
        f._assert_account_tradeable(" 262502469 ")


def test_non_protected_account_allowed(monkeypatch):
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    f._assert_account_tradeable("111111111")  # different account → ok


def test_default_account_refused_in_strict_mode(monkeypatch):
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    monkeypatch.setenv("FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", "true")
    # Empty/default target with protected accounts present + strict mode → refuse.
    with pytest.raises(HTTPException) as ei:
        f._assert_account_tradeable(None)
    assert ei.value.status_code == 403


def test_default_account_refused_by_default(monkeypatch):
    # Strict mode now DEFAULTS ON when a protected list exists (2026-07-05 audit:
    # account-select fallthrough could land orders on the broker default account).
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    monkeypatch.delenv("FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", raising=False)
    with pytest.raises(HTTPException) as ei:
        f._assert_account_tradeable(None)
    assert ei.value.status_code == 403


def test_default_account_allowed_when_strict_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    monkeypatch.setenv("FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", "false")
    f._assert_account_tradeable(None)  # explicit opt-out only
