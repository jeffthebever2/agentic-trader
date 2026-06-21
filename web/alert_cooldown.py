"""Per-ticker alert cooldown — stop the same recommendation re-paging the user.

Both the thematic scan and the holdings-brain rebuild their proposal sets every
cycle, so without memory the *same* idea re-sends an SMS each scan. This module
records when (scope, ticker) was last alerted and suppresses a repeat within
``ALERT_COOLDOWN_HOURS`` UNLESS the action kind changed or the score moved by at
least ``ALERT_RESCORE_DELTA`` — i.e. only materially-different news re-pages.

File-backed so it survives restarts. Single web process → an unlocked
read-modify-write is sufficient (same scope as the other tmp/*.json state).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "tmp" / "alert_cooldown.json"
_PRUNE_AFTER_SEC = 7 * 86400  # forget entries older than a week (bounds file size)


def _cooldown_hours() -> float:
    try:
        return max(0.0, float(os.getenv("ALERT_COOLDOWN_HOURS", "12")))
    except (TypeError, ValueError):
        return 12.0


def _rescore_delta() -> float:
    try:
        return max(0.0, float(os.getenv("ALERT_RESCORE_DELTA", "8")))
    except (TypeError, ValueError):
        return 8.0


def _key(scope: str, ticker: str) -> str:
    return f"{scope}:{ticker}".strip().upper()


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_FILE.parent, prefix=".tmp_ac_")
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _FILE)
    except Exception:
        pass


def should_alert(scope: str, ticker: str, *, score: float = 0.0, kind: str = "") -> bool:
    """True if we may page about (scope, ticker) now.

    Returns True when there is no prior alert, the cooldown has elapsed, the
    action ``kind`` changed, or ``score`` moved by >= ALERT_RESCORE_DELTA.
    Does NOT record the alert — call :func:`record_alert` only after a send
    actually succeeds, so a failed SMS doesn't start the cooldown.
    """
    cd = _cooldown_hours() * 3600.0
    if cd <= 0:
        return True
    rec = _load().get(_key(scope, ticker))
    if not rec:
        return True
    if (time.time() - float(rec.get("ts", 0) or 0)) >= cd:
        return True
    if kind and kind != rec.get("kind", ""):
        return True
    if abs(float(score or 0) - float(rec.get("score", 0) or 0)) >= _rescore_delta():
        return True
    return False


def record_alert(scope: str, ticker: str, *, score: float = 0.0, kind: str = "") -> None:
    """Stamp (scope, ticker) as alerted now. Prunes entries older than a week."""
    d = _load()
    d[_key(scope, ticker)] = {"ts": time.time(), "score": float(score or 0), "kind": kind}
    cutoff = time.time() - _PRUNE_AFTER_SEC
    d = {k: v for k, v in d.items() if float(v.get("ts", 0) or 0) >= cutoff}
    _save(d)
