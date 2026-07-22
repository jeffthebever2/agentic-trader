"""P0-1 audit fix (2026-07-05): standalone exit-guard runner + service arbitration.

The runner itself is an infinite loop over network calls — these tests cover the
pieces that make double-running impossible and ops-visible: the flock arbitration
primitive, the heartbeat file, and the runner module's wiring.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.portfolio.process_lock import flock_is_held  # noqa: E402


def test_flock_is_held_missing_file(tmp_path):
    assert flock_is_held(tmp_path / "nope.lock") is False


def test_flock_is_held_reflects_holder(tmp_path):
    lock = tmp_path / "exit_guard.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR)
    try:
        assert flock_is_held(lock) is False
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert flock_is_held(lock) is True
        fcntl.flock(fd, fcntl.LOCK_UN)
        assert flock_is_held(lock) is False
    finally:
        os.close(fd)


def test_runner_module_wiring(monkeypatch, tmp_path):
    """Runner imports, points at the shared lock path, and writes a heartbeat."""
    import scripts.run_exit_guard as reg

    # lock path must be the one the webserver's _exit_guard_loop checks
    assert reg.EXIT_GUARD_LOCK.name == "exit_guard.lock"
    assert reg.EXIT_GUARD_LOCK.parent.name == "tmp"

    monkeypatch.setattr(reg, "HEARTBEAT_FILE", tmp_path / "hb.json")
    reg._write_heartbeat("market_closed", {"emails": 0})
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["status"] == "market_closed"
    assert hb["pid"] == os.getpid()
    assert hb["ts"]


def test_webserver_loop_checks_the_same_lock():
    """web/app.py must arbitrate on tmp/exit_guard.lock (source-level check —
    the loop itself is an infinite coroutine)."""
    src = (ROOT / "web" / "app.py").read_text()
    assert 'flock_is_held(ROOT / "tmp" / "exit_guard.lock")' in src
