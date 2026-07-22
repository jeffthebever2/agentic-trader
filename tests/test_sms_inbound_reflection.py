"""The Sendblue inbound webhook must not become an SMS reflector. An
unauthenticated POST with a forged from_number for an UNREGISTERED number must
not cause the server to text that number back (cost + spam amplification). Only
registered senders (dispatch sets result['user']) get a reply."""
import asyncio

import scripts.sms_alerts as sms_alerts
import web.sms_router as sms_router
import web.api.paper as paper


_SECRET = "test-inbound-secret"


class _FakeReq:
    def __init__(self, payload, key=_SECRET):
        self._p = payload
        self.query_params = {"key": key} if key is not None else {}
        self.headers = {}

    async def json(self):
        return self._p

    async def form(self):
        return {}


def _run(payload, dispatch_result, monkeypatch, key=_SECRET):
    monkeypatch.setenv("SENDBLUE_INBOUND_SECRET", _SECRET)
    sent = []
    monkeypatch.setattr(sms_alerts, "send_sms", lambda to, msg, *a, **k: sent.append((to, msg)))
    monkeypatch.setattr(sms_router, "dispatch", lambda num, txt: dispatch_result)
    out = asyncio.run(paper.sendblue_inbound(_FakeReq(payload, key=key)))
    return out, sent


def test_webhook_rejects_missing_key(monkeypatch):
    """M2: no/incorrect inbound key → rejected, and no SMS is dispatched."""
    monkeypatch.setenv("SENDBLUE_INBOUND_SECRET", _SECRET)
    sent = []
    monkeypatch.setattr(sms_alerts, "send_sms", lambda to, msg, *a, **k: sent.append((to, msg)))
    monkeypatch.setattr(sms_router, "dispatch", lambda num, txt: {"reply": "x", "user": "wt@x.com"})
    out = asyncio.run(paper.sendblue_inbound(_FakeReq({"from_number": "+16145078688", "content": "STATUS"}, key="wrong")))
    assert out["success"] is False and "unauthorized" in out["error"]
    assert sent == []


def test_webhook_fails_closed_when_secret_unset(monkeypatch):
    """M2: when SENDBLUE_INBOUND_SECRET is unset the endpoint fails CLOSED (does not
    accept any caller as it did before)."""
    monkeypatch.delenv("SENDBLUE_INBOUND_SECRET", raising=False)
    sent = []
    monkeypatch.setattr(sms_alerts, "send_sms", lambda to, msg, *a, **k: sent.append((to, msg)))
    monkeypatch.setattr(sms_router, "dispatch", lambda num, txt: {"reply": "x", "user": "wt@x.com"})
    out = asyncio.run(paper.sendblue_inbound(_FakeReq({"from_number": "+16145078688", "content": "STATUS"}, key=None)))
    assert out["success"] is False
    assert sent == []


def test_unregistered_sender_gets_no_reply(monkeypatch):
    out, sent = _run(
        {"from_number": "+15550001111", "content": "STATUS"},
        {"reply": "Number not registered.", "matched": None, "user": None},
        monkeypatch,
    )
    assert out["success"] is True
    assert sent == []  # no SMS reflected to the forged/unknown number


def test_registered_sender_gets_reply(monkeypatch):
    out, sent = _run(
        {"from_number": "+16145078688", "content": "STATUS"},
        {"reply": "Equity $123.", "matched": "status", "user": "wt@x.com"},
        monkeypatch,
    )
    assert out["success"] is True
    assert len(sent) == 1 and sent[0][0] == "+16145078688"
