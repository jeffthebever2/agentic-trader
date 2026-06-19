"""Per-(user,ticker) order lock — the double-order guard. The order endpoints
reject with 429 when the lock is already held, so two concurrent approvals for
the same ticker can't both fire a real order. Real money — only tighten."""
import asyncio

import web.api.fidelity as f


def test_same_key_returns_same_lock():
    # Same (user, ticker) → one shared lock so the two requests serialize.
    a = f._get_order_lock("user@x.com:NVDA")
    b = f._get_order_lock("user@x.com:NVDA")
    assert a is b


def test_distinct_keys_are_independent():
    # Different ticker or different user → independent locks (no false 429).
    base = f._get_order_lock("user@x.com:NVDA")
    assert base is not f._get_order_lock("user@x.com:AMD")
    assert base is not f._get_order_lock("other@x.com:NVDA")


def test_held_lock_reports_locked():
    """While the lock is held, .locked() is True — that's exactly the condition
    the order endpoints use to reject a duplicate concurrent order with 429."""
    async def _run():
        lock = f._get_order_lock("idem@x.com:TSLA")
        assert lock.locked() is False
        async with lock:
            assert lock.locked() is True   # endpoint would 429 here
        assert lock.locked() is False      # released → next order allowed

    asyncio.run(_run())
