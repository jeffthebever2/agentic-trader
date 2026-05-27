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
from fastapi.responses import Response
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
from web.api.auth_routes import router as auth_router
from web.api.twofa_routes import router as twofa_router
from web.api.live_verification import router as live_verification_router
from web.api.cloudflare_ai import router as cloudflare_ai_router
from web.api.admin import router as admin_router
from web.auth import get_optional_user

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

_autostart_log = logging.getLogger("paper.autostart")
_AUTOSTART_CFG = ROOT / "tmp" / "paper_autostart.json"
_TZ_ET = ZoneInfo("America/New_York")


async def _paper_autostart_loop():
    """Ensure the paper runner is running.

    Behavior:
      - Reads `tmp/paper_autostart.json` (`enabled: true` to participate).
      - During the configured market window (premarket warmup ... 16:00 ET,
        weekdays), launches the runner if it is not already up.
      - Crash-restart: if the process exits during the window, the loop
        relaunches it on the next tick.
      - Env `PAPER_AUTOSTART_IGNORE_WINDOW=true` removes the weekday +
        market-hours gate so the runner is brought up as soon as the
        server starts and kept up 24/7.
      - First tick fires after 5s so a restarted server is back online
        quickly; subsequent ticks every 30s.
    """
    from web.api.paper import (
        DEFAULT_AUTOSTART_CONFIG,
        _process_status,
        start_paper_runner,
        PaperStartRequest,
    )
    SYSTEM_ADMIN = {"email": "system@autostart", "role": "admin"}
    ignore_window = os.getenv("PAPER_AUTOSTART_IGNORE_WINDOW", "false").lower() == "true"
    first_tick = True

    while True:
        await asyncio.sleep(5 if first_tick else 30)
        first_tick = False
        try:
            cfg = DEFAULT_AUTOSTART_CONFIG.copy()
            if _AUTOSTART_CFG.exists():
                cfg.update(json.loads(_AUTOSTART_CFG.read_text(encoding="utf-8-sig")))
            if not cfg.get("enabled"):
                continue

            if not ignore_window:
                now = dt.datetime.now(_TZ_ET)
                today = now.date()
                if today.weekday() >= 5:
                    continue
                market_open = dt.datetime.combine(today, dt.time(9, 30), tzinfo=_TZ_ET)
                market_close = dt.datetime.combine(today, dt.time(16, 0), tzinfo=_TZ_ET)
                warmup_mins = int(cfg.get("premarket_warmup_minutes", 30))
                start_window = market_open - dt.timedelta(minutes=warmup_mins)
                if not (start_window <= now < market_close):
                    continue

            proc = _process_status()
            if proc["running"]:
                # Already running (or supervisor sees an external PID); nothing to do.
                continue

            valid_fields = PaperStartRequest.model_fields.keys()
            req_kwargs = {k: v for k, v in cfg.items() if k in valid_fields}
            req = PaperStartRequest(**req_kwargs)
            await start_paper_runner(req, admin=SYSTEM_ADMIN)
            _autostart_log.info(
                "Auto-started paper trading (ignore_window=%s)", ignore_window
            )
        except Exception as exc:
            _autostart_log.warning("Auto-start loop error: %s", exc)


app = FastAPI(title="Agentic Trader Web UI", version="1.0.0")

app.add_middleware(GZipMiddleware, minimum_size=1000)

# The SPA is served by this same FastAPI app, so it is same-origin and needs no
# CORS grant. A wildcard ("*") would let any external site call the API, so we
# restrict to an explicit allow-list. Override in prod via ALLOWED_ORIGINS
# (comma-separated). Defaults cover local development only.
import os as _os
_default_origins = "http://localhost:8001,http://127.0.0.1:8001"
_allowed_origins = [o.strip() for o in _os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

import os
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class AuthContextMiddleware(BaseHTTPMiddleware):
    """Attach the verified Cloudflare Access user to request.state.

    Per-route enforcement is done by FastAPI deps (`get_current_user`,
    `require_admin`). This middleware exists only so handlers that don't
    declare the dep can still read `request.state.user` when present.
    Open paths (signed webhooks, magic-token approvals, health) bypass
    the lookup entirely.
    """

    OPEN_PATHS = {
        "/api/paper/sms/inbound",  # signed by SENDBLUE_INBOUND_SECRET
        "/api/approve",            # signed magic token in query string
        "/health",
    }

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.OPEN_PATHS:
            return await call_next(request)
        # Verifies the JWT signature if a token is present; returns None
        # otherwise. Routes that require auth must declare
        # Depends(get_current_user) which will raise 401 there.
        request.state.user = get_optional_user(request)
        return await call_next(request)

app.add_middleware(AuthContextMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth HTTP security headers (clickjacking, MIME-sniff, XSS).

    The SPA is a single inline HTML/JS bundle, so the CSP must permit
    'unsafe-inline'/'unsafe-eval' for scripts to function; even so, the policy
    still meaningfully restricts script/connect/frame *origins*, forbids object
    embeds and <base> hijacking, and blocks framing — narrowing the blast radius
    of any injected markup that slips past server-side escaping/DOMPurify.
    """

    # Origins the app legitimately loads from (fonts, DOMPurify CDN, TradingView).
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdnjs.cloudflare.com https://s3.tradingview.com https://www.tradingview.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https://s3.tradingview.com https://www.tradingview.com; "
        "frame-src 'self' https://s3.tradingview.com https://www.tradingview.com; "
        "worker-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self._CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router, prefix="/api")
app.include_router(twofa_router, prefix="/api")
app.include_router(live_verification_router, prefix="/api")
app.include_router(cloudflare_ai_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
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
            "version": "1.0.0",
            "system": system_info,
            "metrics": metrics,
        }
    except Exception as e:
        import logging
        logging.exception("Health check failed")
        return {
            "status": "unhealthy",
            "error": "An internal error occurred",
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
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
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
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
