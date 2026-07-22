"""Tests for the copy-trade store + reconcile orchestration (broker mocked)."""
import asyncio

import pytest

from web import copytrade as ct


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Point the state file at a temp dir so tests never touch real state."""
    monkeypatch.setattr(ct, "STATE_DIR", tmp_path / "copytrade")
    monkeypatch.setattr(ct, "STATE_FILE", tmp_path / "copytrade" / "state.json")
    ct._reconcile_locks.clear()
    yield


EMAIL = "trader@example.com"


def test_config_defaults_and_set():
    cfg = ct.get_config(EMAIL)
    assert cfg["enabled"] is False
    assert cfg["mode"] == "hil"
    cfg = ct.set_config(EMAIL, {"enabled": True, "mode": "auto",
                                "follow_portfolio_id": "breakout_phoenix"})
    assert cfg["enabled"] is True
    assert cfg["mode"] == "auto"
    assert cfg["follow_portfolio_id"] == "breakout_phoenix"


def test_set_config_rejects_bad_values():
    cfg = ct.set_config(EMAIL, {"mode": "garbage", "max_weight": 0.99})
    assert cfg["mode"] == "hil"           # garbage ignored → default
    assert cfg["max_weight"] == 0.10      # clamped to compliance cap


def test_pending_add_and_resolve():
    ct._add_pending(EMAIL, [
        {"action": "buy", "ticker": "NVDA", "target_pct": 8.0, "reason": "x"},
        {"action": "sell", "ticker": "TSLA", "target_pct": 0.0, "reason": "y"},
    ])
    pend = ct.list_pending(EMAIL)
    assert {p["ticker"] for p in pend} == {"NVDA", "TSLA"}
    pid = pend[0]["id"]
    resolved = ct.resolve_pending(EMAIL, pid, "skipped")
    assert resolved["status"] == "skipped"
    assert len(ct.list_pending(EMAIL)) == 1


def test_pending_dedup():
    a = [{"action": "buy", "ticker": "NVDA", "target_pct": 8.0, "reason": "x"}]
    ct._add_pending(EMAIL, a)
    ct._add_pending(EMAIL, a)  # same action again — should not duplicate
    assert len(ct.list_pending(EMAIL)) == 1


def test_reconcile_hil_enqueues(monkeypatch):
    ct.set_config(EMAIL, {"enabled": True, "mode": "hil",
                          "follow_portfolio_id": "p1"})
    monkeypatch.setattr(ct, "_portfolio_snapshot",
                        lambda pid: ([{"ticker": "NVDA", "shares": 10, "current_price": 8}], 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    sms = {}
    monkeypatch.setattr(ct, "_sms", lambda email, msg: sms.update({"msg": msg}))

    res = asyncio.run(ct.reconcile(EMAIL))
    assert res["mode"] == "hil"
    assert res["queued"] == 1
    pend = ct.list_pending(EMAIL)
    assert pend[0]["ticker"] == "NVDA"
    assert "NVDA" in sms["msg"]


def test_reconcile_auto_blocked_without_killswitch(monkeypatch):
    ct.set_config(EMAIL, {"enabled": True, "mode": "auto",
                          "follow_portfolio_id": "p1"})
    monkeypatch.setattr(ct, "_portfolio_snapshot",
                        lambda pid: ([{"ticker": "NVDA", "shares": 10, "current_price": 8}], 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    monkeypatch.setattr(ct, "_sms", lambda email, msg: None)
    monkeypatch.setenv("COPYTRADE_AUTONOMOUS", "false")  # kill-switch OFF

    res = asyncio.run(ct.reconcile(EMAIL))
    # Auto requested but kill-switch off → falls back to HIL queue, no execution.
    assert res["mode"] == "hil"
    assert res["autonomous"] is False


def test_reconcile_auto_executes_with_killswitch(monkeypatch):
    ct.set_config(EMAIL, {"enabled": True, "mode": "auto",
                          "follow_portfolio_id": "p1"})
    monkeypatch.setattr(ct, "_portfolio_snapshot",
                        lambda pid: ([{"ticker": "NVDA", "shares": 10, "current_price": 8}], 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    monkeypatch.setattr(ct, "_sms", lambda email, msg: None)
    monkeypatch.setattr(ct, "_resolve_account", lambda email, cfg: "12345678")
    monkeypatch.setenv("COPYTRADE_AUTONOMOUS", "true")

    executed = []

    async def fake_exec(email, action, account):
        executed.append(action)
        return {"ticker": action["ticker"], "action": action["action"], "result": {"ok": True}}

    monkeypatch.setattr(ct, "_execute_action", fake_exec)

    res = asyncio.run(ct.reconcile(EMAIL))
    assert res["mode"] == "auto"
    assert res["autonomous"] is True
    assert len(res["fills"]) == 1
    assert executed[0]["ticker"] == "NVDA"


def test_reconcile_disabled_skips():
    ct.set_config(EMAIL, {"enabled": False, "follow_portfolio_id": "p1"})
    res = asyncio.run(ct.reconcile(EMAIL))
    assert res["skipped"] == "disabled"


def test_reconcile_no_portfolio_skips():
    ct.set_config(EMAIL, {"enabled": True})
    res = asyncio.run(ct.reconcile(EMAIL))
    assert "no portfolio" in res["skipped"]


def test_buy_throttle_caps_per_sync(monkeypatch):
    ct.set_config(EMAIL, {"enabled": True, "mode": "hil",
                          "follow_portfolio_id": "p1", "max_new_buys_per_sync": 2})
    # 4 equal-weight names, book 1000 → each 20% raw, clamp 10%
    positions = [{"ticker": t, "shares": 25, "current_price": 8} for t in ("AAA", "BBB", "CCC", "DDD")]
    monkeypatch.setattr(ct, "_portfolio_snapshot", lambda pid: (positions, 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    monkeypatch.setattr(ct, "_sms", lambda email, msg: None)

    res = asyncio.run(ct.reconcile(EMAIL))
    assert res["queued"] == 2  # throttled to 2 buys


def test_force_sync_never_auto_executes(monkeypatch):
    """H6: HTTP /copytrade/sync (execute_allowed=False) must NEVER place a real
    order, even in auto mode with the kill-switch on — it enqueues instead."""
    ct.set_config(EMAIL, {"enabled": True, "mode": "auto",
                          "follow_portfolio_id": "p1"})
    monkeypatch.setattr(ct, "_portfolio_snapshot",
                        lambda pid: ([{"ticker": "NVDA", "shares": 10, "current_price": 8}], 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    monkeypatch.setattr(ct, "_sms", lambda email, msg: None)
    monkeypatch.setenv("COPYTRADE_AUTONOMOUS", "true")

    executed = []

    async def fake_exec(email, action, account):
        executed.append(action)
        return {"ticker": action["ticker"], "action": action["action"]}

    monkeypatch.setattr(ct, "_execute_action", fake_exec)

    res = asyncio.run(ct.reconcile(EMAIL, force_execute=True, execute_allowed=False))
    assert executed == []                      # no real order placed
    assert res["autonomous"] is False
    assert res["mode"] == "hil"
    assert ct.list_pending(EMAIL)              # queued for approval instead


def test_background_loop_still_autoexecutes(monkeypatch):
    """The env-gated background path (execute_allowed=True, the default) still
    auto-executes — H6 fix only closes the HTTP path, not the loop."""
    ct.set_config(EMAIL, {"enabled": True, "mode": "auto",
                          "follow_portfolio_id": "p1"})
    monkeypatch.setattr(ct, "_portfolio_snapshot",
                        lambda pid: ([{"ticker": "NVDA", "shares": 10, "current_price": 8}], 1000.0))
    monkeypatch.setattr(ct, "_external_holdings", lambda email: set())
    monkeypatch.setattr(ct, "_sms", lambda email, msg: None)
    monkeypatch.setattr(ct, "_resolve_account", lambda email, cfg: "12345678")
    monkeypatch.setenv("COPYTRADE_AUTONOMOUS", "true")

    executed = []

    async def fake_exec(email, action, account):
        executed.append(action)
        return {"ticker": action["ticker"], "action": action["action"]}

    monkeypatch.setattr(ct, "_execute_action", fake_exec)

    res = asyncio.run(ct.reconcile(EMAIL))     # default execute_allowed=True
    assert res["autonomous"] is True
    assert len(executed) == 1
