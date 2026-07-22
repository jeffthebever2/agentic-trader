#!/usr/bin/env python3
"""Standalone exit-guard runner — watches the live broker book independently of
the web server.

P0-1 from the 2026-07-05 audit: the ONLY process checking stops/targets on real
Fidelity holdings was ``_exit_guard_loop`` inside the webserver. Webserver down
(decommission, crash, deploy window) = live book unwatched. This runner is a
separate service so the watch survives webserver outages; on the server it runs
as its own systemd unit (see deploy/systemd/).

Propose-only, like the in-server loop: ``run_exit_guard`` raises priority HIL
EXIT proposals (+ SMS notify); it never places or exits a live order. Execution
still requires a human + step-up 2FA through the compliance-gated endpoints.

Duplicate-guard: holds ``tmp/exit_guard.lock`` (flock) for its lifetime. The
webserver's internal ``_exit_guard_loop`` checks that lock each cycle and skips
while it is held, so running both services never double-raises or double-texts.
The same lock prevents two copies of this runner.

Env (same knobs as the in-server loop):
  HOLDINGS_BRAIN_ENABLED   master gate (default false — runner idles, logs)
  EXIT_GUARD_INTERVAL_MIN  cycle interval in minutes during market hours (15)

Usage:  python3 scripts/run_exit_guard.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# web.app import loads .env and defines the shared helpers; it builds the
# FastAPI app object but starts no server and fires no startup hooks.
from web.app import _brain_market_open, _fidelity_sessioned_emails  # noqa: E402
from tradingagents.config import env_bool  # noqa: E402
from tradingagents.portfolio.process_lock import (  # noqa: E402
    SingleInstanceError,
    acquire_single_instance,
)

EXIT_GUARD_LOCK = ROOT / "tmp" / "exit_guard.lock"
HEARTBEAT_FILE = ROOT / "tmp" / "exit_guard_heartbeat.json"

log = logging.getLogger("exit_guard_runner")


def _write_heartbeat(status: str, detail: dict | None = None) -> None:
    """Ops visibility: last cycle time + outcome. Best-effort."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                   "pid": os.getpid(), "status": status, **(detail or {})}
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, HEARTBEAT_FILE)
    except Exception:
        pass


async def _cycle() -> None:
    from web.api.holdings_brain import run_exit_guard
    n_breaches = 0
    emails = _fidelity_sessioned_emails()
    for email in emails:
        try:
            breaches = await run_exit_guard(email, broker="fidelity")
            if breaches:
                n_breaches += len(breaches)
                log.warning("EXIT-GUARD %s: %d breach(es) → proposals raised: %s",
                            email[:16], len(breaches),
                            ", ".join(f"{b['ticker']}:{b['reason']}" for b in breaches))
        except Exception as e:
            log.warning("exit guard failed for %s: %s", email[:16], e)
    _write_heartbeat("ok", {"emails": len(emails), "breaches": n_breaches})


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    try:
        guard_fd = acquire_single_instance(EXIT_GUARD_LOCK)  # noqa: F841 — held for life
    except SingleInstanceError as e:
        log.critical("refusing to start: %s", e)
        raise SystemExit(1)

    log.info("standalone exit guard up (pid %d) — gate HOLDINGS_BRAIN_ENABLED=%s",
             os.getpid(), os.getenv("HOLDINGS_BRAIN_ENABLED", "false"))
    while True:
        try:
            if not env_bool("HOLDINGS_BRAIN_ENABLED", False):
                _write_heartbeat("disabled")
            # Regular session, matching the in-server loop. Calendar-aware now:
            # the old weekday+clock gate ran on market HOLIDAYS and evaluated
            # stops against quotes that had not moved since the previous close.
            # Not widened to extended hours — ratchet_stops persists stop levels,
            # and a thin after-hours print would ratchet a trail to a price that
            # never really traded (plus it would blow the FMP daily quota, which
            # would block exits entirely).
            elif not _brain_market_open():
                _write_heartbeat("market_closed")
            else:
                await _cycle()
        except Exception as e:
            log.warning("cycle error: %s", e)
            _write_heartbeat("error", {"error": str(e)[:200]})
        interval = max(2, int(float(os.getenv("EXIT_GUARD_INTERVAL_MIN", "15")))) * 60
        await asyncio.sleep(interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
