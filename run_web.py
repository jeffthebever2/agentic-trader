import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    loop = "uvloop" if sys.platform != "win32" else "asyncio"
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        workers=1,
        loop=loop,
        http="httptools",
        access_log=False,
    )
