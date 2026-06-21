"""Thematic trade-request SMS = ONE batched digest per scan, cooldown-filtered.

Was: one text per qualifying ticker (+ a separate count text) → "a bunch of texts
for the same stock". Now: a single digest covering all fresh names, deduped by the
per-ticker cooldown.
"""
import asyncio

import pytest

import web.api.thematic_auto as ta


@pytest.fixture(autouse=True)
def _wire(monkeypatch, tmp_path):
    # isolated cooldown file + a known phone + not quiet hours
    from web import alert_cooldown
    monkeypatch.setattr(alert_cooldown, "_FILE", tmp_path / "ac.json")
    monkeypatch.setattr(ta, "_in_sms_quiet_hours", lambda: False)
    monkeypatch.setenv("PAPER_SMS_NUMBER", "+15550001111")

    sent = []
    import scripts.sms_alerts as sa
    monkeypatch.setattr(sa, "send_sms", lambda to, msg, *a, **k: sent.append({"to": to, "msg": msg}) or {"success": True})

    from web import users as us
    monkeypatch.setattr(us, "get_user", lambda e: {"email": e, "phone_number": "+15550001111"})
    monkeypatch.setattr(us, "get_thematic_hil", lambda rec: {"sms_notify": True})
    # no chart fetch in tests
    async def _no_chart(*a, **k):
        return None
    monkeypatch.setattr(ta, "_generate_signal_chart", _no_chart)
    return sent


def _sig(t, tp=25, sp=10, crowd=""):
    return {"ticker": t, "target_pct": tp, "stop_pct": sp, "crowd_view": crowd}


def test_multi_signal_is_one_digest(_wire):
    items = [(_sig("TSM"), 98.0), (_sig("MRVL"), 83.0), (_sig("AMD"), 73.0)]
    n = asyncio.run(ta._notify_thematic_trade_request("u@x.com", items))
    assert n == 3
    assert len(_wire) == 1                      # ONE text, not three
    msg = _wire[0]["msg"]
    assert "3 trade requests" in msg
    assert "TSM" in msg and "MRVL" in msg and "AMD" in msg
    assert "/app/hil?tab=approvals" in msg


def test_single_signal_clean_format(_wire):
    n = asyncio.run(ta._notify_thematic_trade_request("u@x.com", [(_sig("TSM", crowd="loading up"), 98.0)]))
    assert n == 1 and len(_wire) == 1
    msg = _wire[0]["msg"]
    assert "trade request" in msg and "TSM" in msg and "98/100" in msg
    assert "Target +25%" in msg and "Stop -10%" in msg


def test_cooldown_dedupes_same_stock_next_scan(_wire):
    items = [(_sig("TSM"), 98.0)]
    asyncio.run(ta._notify_thematic_trade_request("u@x.com", items))
    asyncio.run(ta._notify_thematic_trade_request("u@x.com", items))  # immediate re-scan
    assert len(_wire) == 1                       # second send suppressed by cooldown


def test_score_jump_repages(_wire, monkeypatch):
    monkeypatch.setenv("ALERT_RESCORE_DELTA", "8")
    asyncio.run(ta._notify_thematic_trade_request("u@x.com", [(_sig("TSM"), 80.0)]))
    asyncio.run(ta._notify_thematic_trade_request("u@x.com", [(_sig("TSM"), 92.0)]))  # +12 >= 8
    assert len(_wire) == 2


def test_partial_cooldown_only_sends_new(_wire):
    asyncio.run(ta._notify_thematic_trade_request("u@x.com", [(_sig("TSM"), 98.0)]))
    assert len(_wire) == 1
    # next scan: TSM still on cooldown, MRVL is new → digest with only MRVL
    n = asyncio.run(ta._notify_thematic_trade_request("u@x.com", [(_sig("TSM"), 98.0), (_sig("MRVL"), 83.0)]))
    assert n == 1
    assert "MRVL" in _wire[1]["msg"] and "TSM" not in _wire[1]["msg"]
