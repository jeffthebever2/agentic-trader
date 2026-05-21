import sys
import asyncio
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
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
