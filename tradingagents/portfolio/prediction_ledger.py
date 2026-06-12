"""
Prediction ledger — records ML signals at decision time, BEFORE outcomes.

Each ledger entry is written when a BUY decision is made (or seriously
considered).  After the trade closes, the grader joins these entries with
actual outcomes to measure calibration and alpha.

The ledger is an append-only JSONL file so entries are never overwritten and
audit trail is preserved even if the trade log is later pruned.

Usage::

    ledger = PredictionLedger("paper_accounts/algorithm/prediction_ledger.jsonl")
    ledger.log(
        ticker="AAPL",
        decision="BUY",
        ml_probability=0.68,
        expected_return=0.04,
        large_loss_probability=0.12,
        alpha_tier="A",
        alpha_score=0.81,
        breakout_score=0.74,
        regime="bull",
        model_version="cycle46",
    )
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PredictionEntry:
    """One ML signal snapshot captured at decision time."""

    entry_id: str
    ticker: str
    logged_at: str               # ISO-8601, UTC

    # ── Decision ─────────────────────────────────────────────────────────────
    decision: str                # "BUY" | "SKIP" (if logged for skipped candidates)
    skip_reason: str             # empty string if BUY

    # ── ML outputs at decision time ──────────────────────────────────────────
    ml_probability: Optional[float]
    expected_return: Optional[float]
    large_loss_probability: Optional[float]
    target_before_stop_probability: Optional[float]
    timeout_probability: Optional[float]

    # ── Trade plan ──────────────────────────────────────────────────────────
    entry_price: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    atr: Optional[float]

    # ── Context ─────────────────────────────────────────────────────────────
    alpha_tier: str
    alpha_score: Optional[float]
    breakout_score: Optional[float]
    regime: str
    model_version: str

    # ── Extra metadata (strategy, scan date, etc.) ───────────────────────────
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # flatten extra into top-level for convenience
        d.update(d.pop("extra", {}))
        return d


class PredictionLedger:
    """Append-only JSONL ledger of ML predictions captured before outcomes.

    Parameters
    ----------
    path : str or Path
        Path to the JSONL ledger file.  Created on first write.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── Write ────────────────────────────────────────────────────────────────

    def log(
        self,
        ticker: str,
        decision: str = "BUY",
        *,
        ml_probability: float | None = None,
        expected_return: float | None = None,
        large_loss_probability: float | None = None,
        target_before_stop_probability: float | None = None,
        timeout_probability: float | None = None,
        entry_price: float | None = None,
        stop: float | None = None,
        target: float | None = None,
        atr: float | None = None,
        alpha_tier: str = "",
        alpha_score: float | None = None,
        breakout_score: float | None = None,
        regime: str = "unknown",
        model_version: str = "",
        skip_reason: str = "",
        now: dt.datetime | None = None,
        **extra: Any,
    ) -> PredictionEntry:
        """Append one prediction entry to the ledger and return it."""
        if now is None:
            now = dt.datetime.utcnow()
        entry = PredictionEntry(
            entry_id=str(uuid.uuid4()),
            ticker=ticker,
            logged_at=now.isoformat() + "Z",
            decision=decision,
            skip_reason=skip_reason,
            ml_probability=ml_probability,
            expected_return=expected_return,
            large_loss_probability=large_loss_probability,
            target_before_stop_probability=target_before_stop_probability,
            timeout_probability=timeout_probability,
            entry_price=entry_price,
            stop=stop,
            target=target,
            atr=atr,
            alpha_tier=alpha_tier,
            alpha_score=alpha_score,
            breakout_score=breakout_score,
            regime=regime,
            model_version=model_version,
            extra=extra,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict()) + "\n")
        return entry

    # ── Read ─────────────────────────────────────────────────────────────────

    def read_all(self) -> List[Dict[str, Any]]:
        """Return all entries as list of dicts (oldest first)."""
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def read_ticker(self, ticker: str) -> List[Dict[str, Any]]:
        """Return all entries for a single ticker."""
        return [e for e in self.read_all() if e.get("ticker") == ticker]

    def read_buys(self) -> List[Dict[str, Any]]:
        """Return only BUY-decision entries."""
        return [e for e in self.read_all() if e.get("decision") == "BUY"]
