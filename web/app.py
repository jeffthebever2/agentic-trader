import sys
import asyncio
from pathlib import Path

# Playwright needs ProactorEventLoop on Windows to spawn subprocesses
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)
load_dotenv(ROOT / ".env.enterprise", override=False)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from web.api.analysis import router as analysis_router
from web.api.portfolio import router as portfolio_router
from web.api.backtest import router as backtest_router
from web.api.history import router as history_router
from web.api.settings import router as settings_router
from web.api.logs import router as logs_router
from web.api.paper import router as paper_router
from web.api.ml import router as ml_router
from web.api.rl import router as rl_router
from web.api.webull_portfolio import router as webull_router
from web.api.fidelity import router as fidelity_router
from web.api.scanner import router as scanner_router
from web.api.market import router as market_router

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

_autostart_log = logging.getLogger("paper.autostart")
_AUTOSTART_CFG = ROOT / "tmp" / "paper_autostart.json"
_TZ_ET = ZoneInfo("America/New_York")


async def _paper_autostart_loop():
    """Fire paper trading during market hours if auto-start is enabled."""
    from web.api.paper import (
        DEFAULT_AUTOSTART_CONFIG,
        _process_status,
        start_paper_runner,
        PaperStartRequest,
    )
    fired_on: dt.date | None = None

    while True:
        await asyncio.sleep(30)
        try:
            cfg = DEFAULT_AUTOSTART_CONFIG.copy()
            if _AUTOSTART_CFG.exists():
                cfg.update(json.loads(_AUTOSTART_CFG.read_text(encoding="utf-8-sig")))
            if not cfg.get("enabled"):
                continue

            now = dt.datetime.now(_TZ_ET)
            today = now.date()
            if today.weekday() >= 5 or fired_on == today:
                continue

            market_open = dt.datetime.combine(today, dt.time(9, 30), tzinfo=_TZ_ET)
            market_close = dt.datetime.combine(today, dt.time(16, 0), tzinfo=_TZ_ET)
            warmup_mins = int(cfg.get("premarket_warmup_minutes", 30))
            start_window = market_open - dt.timedelta(minutes=warmup_mins)
            if not (start_window <= now < market_close):
                continue

            proc = _process_status()
            if proc["running"]:
                fired_on = today
                continue

            valid_fields = PaperStartRequest.model_fields.keys()
            req_kwargs = {k: v for k, v in cfg.items() if k in valid_fields}
            req = PaperStartRequest(**req_kwargs)
            await start_paper_runner(req)
            fired_on = today
            _autostart_log.info("Auto-started paper trading for %s", today)
        except Exception as exc:
            _autostart_log.warning("Auto-start loop error: %s", exc)


app = FastAPI(title="TradingAgents Web UI", version="0.2.4")

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
import base64
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Block Dashboard from temporary public tunnels. Do not block every
        # Cloudflare-proxied request: production custom domains also include
        # Cloudflare headers such as cf-ray.
        x_forward = request.headers.get("x-forwarded-host", "")
        is_tunnel = (
            x_forward.endswith("trycloudflare.com")
            or x_forward.endswith("pinggy-free.link")
            or x_forward.endswith("loca.lt")
        )
        if is_tunnel:
            path = request.url.path
            allowed_paths = ["/api/approve", "/api/paper/hil/resolve", "/api/paper/sms/inbound"]
            if path not in allowed_paths:
                return Response(
                    content="Dashboard access is blocked from the public tunnel for security. Only direct trade approvals are allowed.",
                    status_code=403
                )

        # Sendblue inbound webhook can't send Basic Auth; it is secured by the
        # SENDBLUE_INBOUND_SECRET shared key checked inside the route handler.
        if request.url.path == "/api/paper/sms/inbound":
            return await call_next(request)

        # 2. Enforce HTTP Basic Auth
        password = os.getenv("DASHBOARD_PASS")
        if password:
            auth_header = request.headers.get("Authorization")
            authenticated = False
            if auth_header and auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    u, p = decoded.split(":", 1)
                    if u == os.getenv("DASHBOARD_USER", "admin") and p == password:
                        authenticated = True
                except Exception:
                    pass
            if not authenticated:
                return Response(
                    content="Unauthorized. Please set DASHBOARD_USER and DASHBOARD_PASS in .env",
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic realm=\"TradingAgents Dashboard\""},
                )

        return await call_next(request)

app.add_middleware(BasicAuthMiddleware)

app.include_router(analysis_router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(history_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(ml_router, prefix="/api")
app.include_router(rl_router, prefix="/api")
app.include_router(webull_router, prefix="/api")
app.include_router(fidelity_router, prefix="/api")
app.include_router(scanner_router, prefix="/api")
app.include_router(market_router, prefix="/api")

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    try:
        from tradingagents.metrics import get_metrics
        import os

        metrics = get_metrics().get_summary()

        system_info: dict = {}
        try:
            import psutil
            process = psutil.Process(os.getpid())
            system_info = {
                "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
                "cpu_percent": process.cpu_percent(),
            }
        except ImportError:
            pass

        return {
            "status": "healthy",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "version": "0.2.4",
            "system": system_info,
            "metrics": metrics,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

@app.on_event("startup")
async def _startup():
    asyncio.create_task(_paper_autostart_loop())


static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


_INDEX_CACHE: bytes | None = None
_INDEX_MTIME: float = 0.0

@app.get("/")
async def root():
    global _INDEX_CACHE, _INDEX_MTIME
    path = static_dir / "index.html"
    mtime = path.stat().st_mtime
    if _INDEX_CACHE is None or mtime != _INDEX_MTIME:
        _INDEX_CACHE = path.read_bytes()
        _INDEX_MTIME = mtime
    return Response(
        content=_INDEX_CACHE,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    global _INDEX_CACHE, _INDEX_MTIME
    path = static_dir / "index.html"
    mtime = path.stat().st_mtime
    if _INDEX_CACHE is None or mtime != _INDEX_MTIME:
        _INDEX_CACHE = path.read_bytes()
        _INDEX_MTIME = mtime
    return Response(
        content=_INDEX_CACHE,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )
