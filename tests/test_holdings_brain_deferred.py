"""Deferred-trades persistence (per-cycle budget surfaced to the HIL Approvals tab).

The brain cycle holds back trades over the per-cycle budget; they must persist so
the UI can show them, and must NOT be wiped when a proposal is approved/skipped
(those call _save_proposals without an explicit deferred list).
"""
import web.api.holdings_brain as wb


def test_deferred_persists_and_survives_resave(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, "TMP", tmp_path)
    email = "deferred@test"
    deferred = [{"ticker": "ASTS", "kind": "EXIT", "conviction": 3,
                 "holding": {"ticker": "ASTS", "pct_of_account": 17.2},
                 "reason": "trade budget 2/cycle reached — deferred to a later cycle"}]
    wb._save_proposals(email, [{"id": "n1", "ticker": "NVDA",
                                "action": {"kind": "ADOPT"}, "status": "pending"}],
                       deferred=deferred)
    assert wb._load_deferred(email) == deferred

    # Approve/skip path resaves proposals WITHOUT a deferred arg → must preserve it.
    wb._save_proposals(email, [])
    assert wb._load_deferred(email) == deferred


def test_deferred_cleared_by_explicit_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, "TMP", tmp_path)
    email = "deferred2@test"
    wb._save_proposals(email, [], deferred=[{"ticker": "X", "kind": "TRIM", "reason": "r"}])
    assert len(wb._load_deferred(email)) == 1
    wb._save_proposals(email, [], deferred=[])   # new cycle with nothing deferred
    assert wb._load_deferred(email) == []


# ── Seamless trade-request SMS (Sendblue) ───────────────────────────────────────
import asyncio


def test_brain_sms_message_and_link(monkeypatch):
    sent = {}
    import scripts.sms_alerts as sa
    monkeypatch.setattr(sa, "send_sms", lambda to, msg, provider=None: sent.update({"to": to, "msg": msg}) or {"success": True})
    monkeypatch.setenv("PAPER_SMS_NUMBER", "+15550001111")
    monkeypatch.setenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org")
    monkeypatch.setenv("HOLDINGS_BRAIN_SMS", "true")
    props = [{"ticker": "ASTS", "action": {"kind": "EXIT"}},
             {"ticker": "NVDA", "action": {"kind": "TRIM"}}]
    asyncio.run(wb._notify_brain_pending("anyone@test", props))
    assert sent["to"] == "+15550001111"
    assert "2 holdings to manage" in sent["msg"]
    assert "DROP ASTS" in sent["msg"] and "TRIM NVDA" in sent["msg"]
    assert "/app/hil?tab=approvals" in sent["msg"]


def test_brain_sms_disabled_flag(monkeypatch):
    sent = {}
    import scripts.sms_alerts as sa
    monkeypatch.setattr(sa, "send_sms", lambda *a, **k: sent.update({"x": 1}))
    monkeypatch.setenv("HOLDINGS_BRAIN_SMS", "false")
    asyncio.run(wb._notify_brain_pending("x@test", [{"ticker": "T", "action": {"kind": "EXIT"}}]))
    assert not sent
