"""Monthly API budget guard — pure, persisted (thematic revamp cost-safety).

The X (and later Grok) data source runs on credits/prepaid balance, so it must
never overspend silently. This guard tracks running spend against a monthly cap,
HARD-STOPS new calls when the cap is hit, and emits a one-time "reload" alert the
web layer texts to the user. Pure/network-free + disk-persisted; the actual SMS
send (Sendblue) and endpoint cost estimate live in the caller.

Unit-agnostic: the caller records whatever it meters — normally dollars for X
pay-per-use, but post-reads also work for dry-run accounting. Two alert levels fire
once per calendar month: WARN at ``warn_pct`` of the cap, and STOP at/over the cap.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class ApiBudget:
    name: str
    base_dir: Path
    monthly_cap: float           # in the caller's unit ($ or post-reads)
    warn_pct: float = 0.8
    now_fn: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        self._path = self.base_dir / f"budget_{self.name}.json"
        self._d = self._load()
        self._roll()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            if self._path.exists():
                d = json.loads(self._path.read_text())
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._d))
            tmp.replace(self._path)
        except Exception:
            pass

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.gmtime(self.now_fn()))

    def _roll(self) -> None:
        """Reset spend + alert flags at the start of a new calendar month."""
        m = self._month()
        if self._d.get("month") != m:
            self._d = {"month": m, "spent": 0.0, "warned": False, "stopped": False}
            self._save()

    # ── accounting ───────────────────────────────────────────────────────────
    def spent(self) -> float:
        self._roll()
        return float(self._d.get("spent", 0.0))

    def remaining(self) -> float:
        return max(0.0, self.monthly_cap - self.spent())

    def allow(self) -> bool:
        """True if a new call is within budget (spend strictly under the cap)."""
        return self.spent() < self.monthly_cap

    def record(self, amount: float) -> float:
        """Add ``amount`` of spend; returns the new monthly total."""
        self._roll()
        self._d["spent"] = float(self._d.get("spent", 0.0)) + max(0.0, float(amount or 0.0))
        self._save()
        return self._d["spent"]

    def take_alert(self) -> Optional[str]:
        """Return a one-time alert message if a threshold was crossed this month,
        else None. WARN fires once at ``warn_pct``; STOP fires once at/over the cap.
        Each level's flag is latched (per month) so the caller texts the user ONCE.
        """
        self._roll()
        spent = float(self._d.get("spent", 0.0))
        cap = self.monthly_cap
        if spent >= cap and not self._d.get("stopped"):
            self._d["stopped"] = True
            self._d["warned"] = True  # imply the warn level too
            self._save()
            return (f"{self.name}: API budget EXHAUSTED ({spent:.2f}/{cap:.2f} this month) "
                    f"— calls paused. Reload to resume.")
        if spent >= cap * self.warn_pct and not self._d.get("warned"):
            self._d["warned"] = True
            self._save()
            return (f"{self.name}: API budget {spent / cap * 100:.0f}% used "
                    f"({spent:.2f}/{cap:.2f} this month). Reload soon to avoid a pause.")
        return None

    def status(self) -> dict:
        return {"name": self.name, "month": self._month(), "spent": round(self.spent(), 4),
                "cap": self.monthly_cap, "remaining": round(self.remaining(), 4),
                "allow": self.allow()}
