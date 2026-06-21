"""Tests for thematic → Fidelity live-routing wiring.

Covers the two riskiest new behaviors:
  1. The dedicated thematic paper book is isolated from the 15-portfolio
     competition state.json and seeds with a realistic starting balance.
  2. The live-Fidelity approve leg enforces the exact same step-up 2FA gate as
     every other real-order endpoint (tokenless request is rejected).
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from web.api import thematic_auto as ta
from web.api import thematic_portfolio as tp


# ── 1. Dedicated paper book ─────────────────────────────────────────────────────
def test_paper_book_is_isolated_from_competition():
    """Thematic paper state must NOT be the papertrader competition file."""
    assert ta.PAPER_STATE_FILE == tp.PAPER_STATE_FILE, "both modules must agree on the path"
    p = str(tp.PAPER_STATE_FILE)
    assert "thematic_paper" in p
    assert "paper_trading_today/unified_brain" not in p


def test_ensure_seeds_realistic_balance_then_idempotent(tmp_path, monkeypatch):
    state_file = tmp_path / "thematic_paper" / "state.json"
    monkeypatch.setattr(tp, "PAPER_STATE_FILE", state_file)
    monkeypatch.setattr(tp, "THEMATIC_PAPER_START_CASH", 100000.0)

    tp._ensure_thematic_paper_state()
    assert state_file.exists()
    seed = json.loads(state_file.read_text())
    assert seed["cash"] == 100000.0
    assert seed["settled_cash"] == 100000.0
    assert seed["starting_cash"] == 100000.0
    assert seed["positions"] == {}

    # Idempotent: a second call must NOT clobber an in-progress book.
    seed["cash"] = 4242.0
    state_file.write_text(json.dumps(seed))
    tp._ensure_thematic_paper_state()
    assert json.loads(state_file.read_text())["cash"] == 4242.0


# ── 2. Step-up gate on the live approve leg ─────────────────────────────────────
def _arrange_step_up(monkeypatch, *, token_valid: bool):
    """Wire enforce_step_up's collaborators so only the token check decides."""
    from web import auth, twofa
    import web.api.admin as admin

    monkeypatch.setattr(auth, "_is_localhost", lambda _req: False)  # no dev bypass
    monkeypatch.setattr(admin, "_read_flags", lambda: {"real_broker_trading": True})
    monkeypatch.setattr(
        twofa, "step_up_status",
        lambda _email: {"method": "totp", "totp_enabled": True, "passkeys": [], "email_enabled": False},
    )
    monkeypatch.setattr(twofa, "verify_step_up_token", lambda tok, _email: token_valid)


def test_live_leg_rejects_missing_step_up_token(monkeypatch):
    from web.auth import enforce_step_up
    _arrange_step_up(monkeypatch, token_valid=False)
    req = SimpleNamespace(headers={})  # no X-Step-Up-Token
    user = {"email": "admin@x", "hil_disclosure_accepted_at": 1}

    with pytest.raises(HTTPException) as ei:
        asyncio.run(enforce_step_up(req, user))
    assert ei.value.status_code == 401
    assert ei.value.headers.get("X-Step-Up-Required") == "totp"


def test_live_leg_passes_with_fresh_token(monkeypatch):
    from web.auth import enforce_step_up
    _arrange_step_up(monkeypatch, token_valid=True)
    req = SimpleNamespace(headers={"x-step-up-token": "fresh"})
    user = {"email": "admin@x", "hil_disclosure_accepted_at": 1}

    assert asyncio.run(enforce_step_up(req, user)) is user


def test_live_leg_blocks_without_hil_disclosure(monkeypatch):
    """No HIL disclosure on file → 428 before any token check."""
    from web.auth import enforce_step_up
    _arrange_step_up(monkeypatch, token_valid=True)
    req = SimpleNamespace(headers={"x-step-up-token": "fresh"})
    user = {"email": "admin@x"}  # disclosure missing

    with pytest.raises(HTTPException) as ei:
        asyncio.run(enforce_step_up(req, user))
    assert ei.value.status_code == 428
