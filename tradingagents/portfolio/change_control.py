"""
Change-control guard for risky configuration settings.

AI and CLI automation may PROPOSE changes to sensitive parameters but cannot
apply them without explicit human approval.  Every proposal and its outcome
(approved / rejected / expired) is persisted to an append-only JSONL log.

Usage (proposal flow)::

    cc = ChangeControl("paper_accounts/algorithm/change_control.jsonl")

    proposal = cc.propose(
        setting="risk_per_trade_pct",
        current_value=1.0,
        proposed_value=1.5,
        reason="volatility expanded; tighter sizing needed",
        proposed_by="daily_audit.py",
    )
    print(proposal.proposal_id)   # share this with the operator

    # Operator reviews and approves via CLI or web UI:
    cc.approve(proposal.proposal_id, approved_by="operator")

    # Before any automated code reads a risky setting it MUST call:
    cc.require_approval("risk_per_trade_pct")  # raises if unapproved pending change exists

Risky settings list (see RISKY_SETTINGS below) is intentionally conservative.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Settings that require change-control approval before modification.
RISKY_SETTINGS: frozenset[str] = frozenset({
    "risk_per_trade_pct",
    "max_positions",
    "max_heat_pct",
    "position_cap_pct",
    "ml_probability_threshold",
    "min_risk_reward",
    "stop_loss_pct",
    "target_mult",
    "max_drawdown_halt_pct",
    "pretrade_max_quote_age_seconds",
    "kill_switch",
    "live_trading_enabled",
})

# How long a proposal stays valid for approval before it expires (hours).
PROPOSAL_TTL_HOURS: int = 48


@dataclass
class ProposedChange:
    proposal_id: str
    setting: str
    current_value: Any
    proposed_value: Any
    reason: str
    proposed_by: str
    proposed_at: str        # ISO-8601
    status: str             # "pending" | "approved" | "rejected" | "expired"
    reviewed_by: str = ""
    reviewed_at: str = ""
    review_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_expired(self, now: dt.datetime | None = None) -> bool:
        if now is None:
            now = dt.datetime.utcnow()
        try:
            proposed = dt.datetime.fromisoformat(self.proposed_at.rstrip("Z"))
            return (now - proposed).total_seconds() > PROPOSAL_TTL_HOURS * 3600
        except Exception:
            return False


class UnapprovedChangeError(RuntimeError):
    """Raised when code tries to apply a risky setting that has an unapproved proposal."""


class ChangeControl:
    """Append-only log of proposed and reviewed configuration changes.

    Parameters
    ----------
    log_path : str or Path
        Path to the JSONL change-control log file.  Created on first write.
    """

    def __init__(self, log_path: str | Path) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Read ─────────────────────────────────────────────────────────────────

    def load_all(self) -> List[ProposedChange]:
        if not self.log_path.exists():
            return []
        out: List[ProposedChange] = []
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    out.append(ProposedChange(**d))
                except Exception:
                    continue
        return out

    def pending(self, now: dt.datetime | None = None) -> List[ProposedChange]:
        """Return all proposals currently in 'pending' status (not yet expired).

        `now` is injectable for deterministic tests; defaults to utcnow.
        """
        if now is None:
            now = dt.datetime.utcnow()
        # Latest entry per proposal_id wins (approved/rejected entries supersede original)
        latest: Dict[str, ProposedChange] = {}
        for p in self.load_all():
            latest[p.proposal_id] = p
        return [
            p for p in latest.values()
            if p.status == "pending" and not p.is_expired(now)
        ]

    def get(self, proposal_id: str) -> Optional[ProposedChange]:
        for p in reversed(self.load_all()):
            if p.proposal_id == proposal_id:
                return p
        return None

    # ── Write ────────────────────────────────────────────────────────────────

    def _append(self, change: ProposedChange) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(change.to_dict()) + "\n")

    def _update_status(
        self,
        proposal_id: str,
        new_status: str,
        reviewed_by: str = "",
        review_note: str = "",
        now: dt.datetime | None = None,
    ) -> ProposedChange:
        if now is None:
            now = dt.datetime.utcnow()
        change = self.get(proposal_id)
        if change is None:
            raise KeyError(f"proposal {proposal_id!r} not found")
        if change.status != "pending":
            raise ValueError(f"proposal {proposal_id!r} already {change.status!r}")
        import dataclasses
        updated = dataclasses.replace(
            change,
            status=new_status,
            reviewed_by=reviewed_by,
            reviewed_at=now.isoformat() + "Z",
            review_note=review_note,
        )
        self._append(updated)
        return updated

    # ── Public API ────────────────────────────────────────────────────────────

    def propose(
        self,
        setting: str,
        current_value: Any,
        proposed_value: Any,
        reason: str,
        proposed_by: str = "automation",
        now: dt.datetime | None = None,
    ) -> ProposedChange:
        """Log a proposed change.  Returns the ProposedChange (status=pending)."""
        if now is None:
            now = dt.datetime.utcnow()
        change = ProposedChange(
            proposal_id=str(uuid.uuid4()),
            setting=setting,
            current_value=current_value,
            proposed_value=proposed_value,
            reason=reason,
            proposed_by=proposed_by,
            proposed_at=now.isoformat() + "Z",
            status="pending",
        )
        self._append(change)
        return change

    def approve(
        self,
        proposal_id: str,
        approved_by: str,
        note: str = "",
        now: dt.datetime | None = None,
    ) -> ProposedChange:
        """Mark a pending proposal as approved."""
        return self._update_status(proposal_id, "approved", approved_by, note, now)

    def reject(
        self,
        proposal_id: str,
        rejected_by: str,
        note: str = "",
        now: dt.datetime | None = None,
    ) -> ProposedChange:
        """Mark a pending proposal as rejected."""
        return self._update_status(proposal_id, "rejected", rejected_by, note, now)

    def require_approval(
        self,
        setting: str,
        now: dt.datetime | None = None,
    ) -> None:
        """Raise UnapprovedChangeError if there is a pending (unapproved) proposal
        for *setting*.  Call this before any automated write to a risky setting."""
        if setting not in RISKY_SETTINGS:
            return
        if now is None:
            now = dt.datetime.utcnow()
        for p in self.pending(now):
            if p.setting == setting:
                raise UnapprovedChangeError(
                    f"Setting '{setting}' has a pending change-control proposal "
                    f"({p.proposal_id}) — approve or reject it before applying."
                )

    def is_risky(self, setting: str) -> bool:
        return setting in RISKY_SETTINGS
