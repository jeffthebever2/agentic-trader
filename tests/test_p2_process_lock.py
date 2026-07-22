"""P2 audit fix (2026-07-05): cross-process locking for the web tier.

flock conflicts apply between distinct open file descriptions even inside one
process, so the cross-process semantics are testable without forking.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.portfolio.process_lock import (  # noqa: E402
    CrossProcessAsyncLock,
    SingleInstanceError,
    acquire_single_instance,
    release_single_instance,
)


def _flock_held_elsewhere(path: Path) -> bool:
    """True if an exclusive flock on ``path`` cannot be taken right now."""
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


# ── acquire_single_instance ───────────────────────────────────────────────────

def test_single_instance_acquire_and_conflict(tmp_path):
    lock = tmp_path / "web.lock"
    fd = acquire_single_instance(lock, retries=1)
    try:
        assert lock.read_text().strip() == str(os.getpid())
        with pytest.raises(SingleInstanceError):
            acquire_single_instance(lock, retries=2, delay=0.01)
    finally:
        release_single_instance(fd)
    # released → re-acquirable
    fd2 = acquire_single_instance(lock, retries=1)
    release_single_instance(fd2)


def test_single_instance_error_names_holder_pid(tmp_path):
    lock = tmp_path / "web.lock"
    fd = acquire_single_instance(lock, retries=1)
    try:
        with pytest.raises(SingleInstanceError, match=str(os.getpid())):
            acquire_single_instance(lock, retries=1, delay=0.01)
    finally:
        release_single_instance(fd)


# ── CrossProcessAsyncLock ─────────────────────────────────────────────────────

def test_cross_process_lock_holds_flock_inside_section(tmp_path):
    lock_file = tmp_path / "state.lock"
    lock = CrossProcessAsyncLock(lock_file)

    async def run():
        assert not lock.locked()
        async with lock:
            assert lock.locked()
            # another process could not enter here
            assert _flock_held_elsewhere(lock_file)
        assert not lock.locked()
        assert not _flock_held_elsewhere(lock_file)

    asyncio.run(run())


def test_cross_process_lock_serializes_tasks(tmp_path):
    lock = CrossProcessAsyncLock(tmp_path / "state.lock")
    order: list[str] = []

    async def worker(name: str):
        async with lock:
            order.append(f"{name}:in")
            await asyncio.sleep(0.02)
            order.append(f"{name}:out")

    async def run():
        await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(run())
    # critical sections must not interleave
    assert order in (["a:in", "a:out", "b:in", "b:out"],
                     ["b:in", "b:out", "a:in", "a:out"])


def test_cross_process_lock_times_out_fail_closed(tmp_path):
    """Foreign process holding the flock → acquire raises (no silent RMW)."""
    lock_file = tmp_path / "state.lock"
    foreign_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    fcntl.flock(foreign_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        lock = CrossProcessAsyncLock(lock_file, timeout=0.2, poll=0.02)

        async def run():
            async with lock:
                pass

        with pytest.raises(TimeoutError):
            asyncio.run(run())
        # the failed acquire must not leave the in-process lock held
        assert not lock.locked()
    finally:
        fcntl.flock(foreign_fd, fcntl.LOCK_UN)
        os.close(foreign_fd)


def test_paper_state_lock_is_cross_process():
    """The live import site actually got the upgrade."""
    import web.api.thematic_auto as ta
    assert isinstance(ta._paper_state_lock, CrossProcessAsyncLock)
    assert ta._paper_state_lock._lock_path.parent == ta.PAPER_STATE_FILE.parent
