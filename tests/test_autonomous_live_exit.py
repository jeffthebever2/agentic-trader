"""Autonomous live exits are allowed only through a separate explicit path:
default-off, short-lived arm record, and priority exit-guard proposals only."""
import inspect
import time

import web.api.holdings_brain as hb


def test_autonomous_live_exit_default_off(monkeypatch):
    monkeypatch.delenv("THEMATIC_LIVE_EXIT_AUTONOMOUS", raising=False)
    assert hb._live_exit_auto_enabled() is False


def test_live_exit_arm_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "TMP", tmp_path)
    path = hb._live_exit_arm_path("me@example.com")
    hb._atomic_write(path, {"account": "123456789", "expires_at": time.time() - 1})
    assert hb._load_live_exit_arm("me@example.com") == {}


def test_auto_live_exit_eligibility_requires_priority_exit_guard():
    eligible = {
        "status": "pending",
        "priority": True,
        "action": {"kind": "EXIT", "source": "exit_guard", "risk_flags": ["below_managed_stop"]},
    }
    assert hb._proposal_is_auto_live_exit_eligible(eligible) is True
    assert hb._proposal_is_auto_live_exit_eligible({**eligible, "priority": False}) is False
    assert hb._proposal_is_auto_live_exit_eligible({
        **eligible,
        "action": {"kind": "EXIT", "source": "llm", "risk_flags": ["below_managed_stop"]},
    }) is False


def test_propose_only_functions_stay_clean():
    for name in ("run_brain_cycle", "run_exit_guard"):
        src = inspect.getsource(getattr(hb, name))
        assert "run_autonomous_live_exit_executor" not in src
        assert "_fidelity_thematic_exit_inner(" not in src
