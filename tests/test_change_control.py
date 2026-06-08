"""Tests for tradingagents.portfolio.change_control.ChangeControl"""
import datetime as dt
import json
import pytest
from tradingagents.portfolio.change_control import (
    ChangeControl,
    ProposedChange,
    UnapprovedChangeError,
    RISKY_SETTINGS,
    PROPOSAL_TTL_HOURS,
)

NOW = dt.datetime(2026, 6, 6, 14, 0, 0)


@pytest.fixture()
def cc(tmp_path):
    return ChangeControl(tmp_path / "cc.jsonl")


# ── propose ───────────────────────────────────────────────────────────────────

def test_propose_returns_pending(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    assert p.status == "pending"
    assert p.proposal_id


def test_propose_creates_file(cc):
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    assert cc.log_path.exists()


def test_propose_writes_json_line(cc):
    cc.propose("max_positions", 5, 8, "expand", proposed_by="audit", now=NOW)
    lines = cc.log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d["setting"] == "max_positions"
    assert d["status"] == "pending"


def test_propose_multiple(cc):
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "r1", now=NOW)
    cc.propose("max_heat_pct", 80, 70, "r2", now=NOW)
    assert len(cc.load_all()) == 2


# ── approve / reject ─────────────────────────────────────────────────────────

def test_approve_changes_status(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    approved = cc.approve(p.proposal_id, approved_by="operator", now=NOW)
    assert approved.status == "approved"
    assert approved.reviewed_by == "operator"


def test_reject_changes_status(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    rejected = cc.reject(p.proposal_id, rejected_by="operator", note="too risky", now=NOW)
    assert rejected.status == "rejected"
    assert rejected.review_note == "too risky"


def test_cannot_approve_already_approved(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    cc.approve(p.proposal_id, "op", now=NOW)
    with pytest.raises(ValueError, match="already"):
        cc.approve(p.proposal_id, "op2", now=NOW)


def test_cannot_reject_already_rejected(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    cc.reject(p.proposal_id, "op", now=NOW)
    with pytest.raises(ValueError):
        cc.reject(p.proposal_id, "op2", now=NOW)


def test_approve_nonexistent_raises_key_error(cc):
    with pytest.raises(KeyError):
        cc.approve("no-such-id", "op", now=NOW)


# ── require_approval ──────────────────────────────────────────────────────────

def test_require_approval_no_pending_ok(cc):
    cc.require_approval("risk_per_trade_pct", now=NOW)  # no exception


def test_require_approval_pending_raises(cc):
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    with pytest.raises(UnapprovedChangeError, match="pending"):
        cc.require_approval("risk_per_trade_pct", now=NOW)


def test_require_approval_after_approve_ok(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=NOW)
    cc.approve(p.proposal_id, "op", now=NOW)
    cc.require_approval("risk_per_trade_pct", now=NOW)  # no exception


def test_require_approval_non_risky_always_ok(cc):
    cc.require_approval("some_unknown_setting", now=NOW)


def test_require_approval_expired_proposal_ok(cc):
    past = NOW - dt.timedelta(hours=PROPOSAL_TTL_HOURS + 1)
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "test", now=past)
    cc.require_approval("risk_per_trade_pct", now=NOW)  # expired → no exception


# ── pending() ────────────────────────────────────────────────────────────────

def test_pending_excludes_approved(cc):
    p = cc.propose("risk_per_trade_pct", 1.0, 1.5, "t", now=NOW)
    cc.approve(p.proposal_id, "op", now=NOW)
    assert cc.pending() == []


def test_pending_excludes_expired(cc):
    past = NOW - dt.timedelta(hours=PROPOSAL_TTL_HOURS + 1)
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "t", now=past)
    assert cc.pending() == []


def test_pending_returns_active(cc):
    cc.propose("risk_per_trade_pct", 1.0, 1.5, "t1", now=NOW)
    cc.propose("max_heat_pct", 80, 70, "t2", now=NOW)
    assert len(cc.pending()) == 2


# ── is_risky ─────────────────────────────────────────────────────────────────

def test_risky_settings_known(cc):
    for s in ["risk_per_trade_pct", "max_positions", "kill_switch"]:
        assert cc.is_risky(s)


def test_non_risky_setting(cc):
    assert not cc.is_risky("dashboard_refresh_seconds")


# ── ProposedChange helpers ────────────────────────────────────────────────────

def test_proposed_change_to_dict_roundtrip():
    p = ProposedChange(
        proposal_id="abc", setting="kill_switch", current_value=False,
        proposed_value=True, reason="halt", proposed_by="audit",
        proposed_at="2026-06-06T14:00:00Z", status="pending",
    )
    d = p.to_dict()
    assert d["proposal_id"] == "abc"
    assert d["status"] == "pending"
