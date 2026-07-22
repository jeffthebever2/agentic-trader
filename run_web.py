import sys
import asyncio
import os
import subprocess
import time
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

ROOT = Path(__file__).parent


def _preflight_checks() -> None:
    """Warn about missing config before uvicorn starts. Never hard-exits — operator may be in dev."""
    warnings: list[str] = []

    # Check model availability
    model_path = ROOT / "ml_models" / "latest" / "model_bundle.joblib"
    fallback_path = ROOT / "ml_models" / "stock_universe" / "model_bundle.joblib"
    if not model_path.exists() and not fallback_path.exists():
        warnings.append("No ML model found at ml_models/latest/ or ml_models/stock_universe/ — run ./start.sh train first")

    # Check tmp/ is writable
    tmp_dir = ROOT / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        test_file = tmp_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
    except OSError as e:
        warnings.append(f"tmp/ directory is not writable: {e}")

    # Warn if auth is enabled but domain is unset
    if os.getenv("CF_ACCESS_REQUIRED", "false").lower() == "true":
        if not os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip():
            warnings.append("CF_ACCESS_REQUIRED=true but CF_ACCESS_TEAM_DOMAIN is not set — auth will fail for all requests")
        if not os.getenv("CF_ACCESS_AUD", "").strip():
            warnings.append("CF_ACCESS_REQUIRED=true but CF_ACCESS_AUD is not set — JWT validation will fail")

    # Warn if STEP_UP_SECRET is default
    if os.getenv("STEP_UP_SECRET", "change-me-to-a-random-secret") == "change-me-to-a-random-secret":
        warnings.append("STEP_UP_SECRET is using the default value — set a random secret in .env")

    for w in warnings:
        print(f"[preflight] WARNING: {w}", flush=True)


def _kill_port(port: int) -> None:
    """Kill any process already bound to `port` so we can bind cleanly."""
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if not out:
            return
        pids = [int(p) for p in out.splitlines() if p.strip().isdigit() and int(p) != os.getpid()]
        for pid in pids:
            try:
                os.kill(pid, 15)  # SIGTERM
            except ProcessLookupError:
                pass
        if pids:
            time.sleep(1.5)  # give processes time to exit
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


if __name__ == "__main__":
    _preflight_checks()
    _kill_port(int(os.getenv("WEB_PORT", "8001")))
    loop = "uvloop" if sys.platform != "win32" else "asyncio"
    uvicorn.run(
        "web.app:app",
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=8001,
        reload=False,
        workers=1,
        loop=loop,
        http="httptools",
        access_log=False,
        # Honor X-Forwarded-Proto/For from cloudflared so request.url.scheme
        # is "https" behind the tunnel and WebSocket upgrades work over wss://.
        # SECURITY (H5): trust forwarding headers ONLY from the loopback peer
        # (cloudflared connects to the origin on 127.0.0.1). "*" trusted X-Forwarded-For
        # from any client, letting an attacker spoof their source IP to defeat the
        # rate limiter and feed the localhost trust check. Override via
        # FORWARDED_ALLOW_IPS only for a non-loopback proxy topology.
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,::1"),
    )
