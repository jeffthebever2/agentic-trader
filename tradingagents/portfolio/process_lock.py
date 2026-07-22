"""Cross-process locking primitives for the trading web tier.

Every in-process guard in the web server (``_paper_state_lock``, the per-ticker
``_ORDER_LOCKS``, alert cooldowns) is an ``asyncio.Lock`` — correct only while
exactly ONE web process exists. A multi-worker deployment (``gunicorn -w N``,
``uvicorn --workers N``) silently voids all of them: duplicate live broker
orders, clobbered paper state. Port binding does NOT protect against this —
forked workers share the listening socket.

Two primitives:

- ``acquire_single_instance(path)`` — exclusive ``flock`` held for the process
  lifetime. The web server takes it at startup and refuses to run as a second
  concurrent instance. flock is released by the kernel on process death, so a
  crash can never leave a stale lock behind.

- ``CrossProcessAsyncLock`` — drop-in replacement for an ``asyncio.Lock`` used
  with ``async with``, adding an ``flock`` on a sidecar file so the critical
  section is exclusive across PROCESSES as well as tasks. The flock acquire
  runs in a worker thread so the event loop never blocks.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import time
from pathlib import Path


class SingleInstanceError(RuntimeError):
    """Another process already holds the single-instance lock."""


def acquire_single_instance(
    lock_path: str | Path,
    *,
    retries: int = 10,
    delay: float = 0.5,
) -> int:
    """Take an exclusive flock on ``lock_path`` for the life of this process.

    Returns the open fd (keep a reference; closing it releases the lock).
    Retries briefly to ride out a restart overlap (old process exiting while
    the new one starts), then raises ``SingleInstanceError``.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    for attempt in range(max(1, retries)):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except OSError:
            if attempt + 1 >= max(1, retries):
                break
            time.sleep(delay)
    holder = ""
    try:
        with open(lock_path, "r") as f:
            holder = f.read().strip()
    except OSError:
        pass
    os.close(fd)
    raise SingleInstanceError(
        f"another process (pid {holder or 'unknown'}) holds {lock_path} — "
        "the trading web server must run as a single process (its order/state "
        "locks are in-process asyncio locks)"
    )


def flock_is_held(lock_path: str | Path) -> bool:
    """True if some process currently holds an exclusive flock on ``lock_path``.

    Used for service arbitration: the webserver's internal exit-guard loop skips
    its cycle while the standalone exit-guard runner holds its lifetime lock, so
    running both never double-raises proposals. Missing file = not held.
    """
    lock_path = Path(lock_path)
    if not lock_path.exists():
        return False
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def release_single_instance(fd: int) -> None:
    """Release a lock taken by acquire_single_instance (normally never needed —
    process exit releases it)."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class CrossProcessAsyncLock:
    """asyncio.Lock + file flock: exclusive across tasks AND processes.

    Same usage as the asyncio.Lock it replaces::

        _paper_state_lock = CrossProcessAsyncLock(STATE_FILE.parent / "state.lock")
        async with _paper_state_lock:
            ...read-modify-write...

    The inner asyncio.Lock serializes tasks in this process; the flock (taken in
    a worker thread, polled non-blocking) excludes other processes. Acquire
    raises ``TimeoutError`` after ``timeout`` seconds rather than deadlocking —
    callers' read-modify-write is then skipped entirely (fail closed, no write).
    """

    def __init__(self, lock_path: str | Path, *, timeout: float = 10.0,
                 poll: float = 0.05):
        self._lock_path = Path(lock_path)
        self._timeout = timeout
        self._poll = poll
        self._alock = asyncio.Lock()
        self._fd: int | None = None

    def _acquire_flock(self) -> int:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError(
                        f"could not flock {self._lock_path} within "
                        f"{self._timeout}s — another process is holding it"
                    )
                time.sleep(self._poll)

    async def __aenter__(self) -> "CrossProcessAsyncLock":
        await self._alock.acquire()
        try:
            self._fd = await asyncio.to_thread(self._acquire_flock)
        except BaseException:
            self._alock.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        finally:
            self._alock.release()

    def locked(self) -> bool:
        return self._alock.locked()
