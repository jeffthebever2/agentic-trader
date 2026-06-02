import sys
import asyncio
import os
import subprocess
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn


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
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
