import sys
import asyncio
import time
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

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from web.api.analysis import router as analysis_router
from web.api.portfolio import router as portfolio_router
from web.api.thematic_portfolio import router as thematic_router
from web.api.thematic_auto import router as thematic_auto_router
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
from web.api.system import router as system_router
from web.auth import get_optional_user

import datetime as dt
import json
import logging
import os as _os_app
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
            # PAPER_AUTOSTART_IGNORE_WINDOW=true implies enabled (why ignore the window if not running?)
            if not cfg.get("enabled") and not ignore_window:
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


app = FastAPI(
    title="Agentic Trader API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── CORS ──────────────────────────────────────────────────────────────────────
# The SPA is served by this same FastAPI app, so it is same-origin and needs no
# CORS grant. A wildcard ("*") would let any external site call the API, so we
# restrict to an explicit allow-list. Override in prod via ALLOWED_ORIGINS
# (comma-separated). Defaults cover local development only.
_default_origins = "http://localhost:8001,http://127.0.0.1:8001,tauri://localhost,https://tauri.localhost"
_allowed_origins = [o.strip() for o in _os_app.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Step-Up-Token", "X-Agentic-View-As", "X-Manager-Key"],
)

# ── Rate limiting via SlowAPI ──────────────────────────────────────────────────
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    _limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    from slowapi.middleware import SlowAPIMiddleware
    app.add_middleware(SlowAPIMiddleware)
    _RATE_LIMITING_ENABLED = True
except ImportError:
    _RATE_LIMITING_ENABLED = False
    _limiter = None

import os
from starlette.middleware.base import BaseHTTPMiddleware

# ── Global exception handlers ─────────────────────────────────────────────────
_api_log = logging.getLogger("api")


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    _api_log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Internal server error", "code": 500},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"ok": False, "error": "Validation failed", "code": 422, "detail": exc.errors()},
    )


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
        "/health/deep",
        "/api/health",             # same endpoints under /api prefix for React client
        "/api/health/deep",
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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Structured access logging. Logs method, path (no query params — avoids
    leaking API keys passed as ?key=...), status code, and response time.
    WebSocket upgrade requests are skipped to avoid logging noise.
    """

    _log = logging.getLogger("api.access")
    # Paths too chatty to log at INFO
    _QUIET = {"/health", "/health/deep", "/api/health", "/api/health/deep"}

    async def dispatch(self, request: Request, call_next):
        # Skip WebSocket upgrades
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        t0 = time.monotonic()
        response = await call_next(request)
        ms = round((time.monotonic() - t0) * 1000)
        path = request.url.path
        level = logging.DEBUG if path in self._QUIET else logging.INFO
        self._log.log(level, "%s %s %d %dms", request.method, path, response.status_code, ms)
        return response


app.add_middleware(RequestLoggingMiddleware)


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
app.include_router(thematic_router, prefix="/api")
app.include_router(thematic_auto_router, prefix="/api")
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
app.include_router(system_router, prefix="/api")

@app.get("/health")
@app.get("/api/health")
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

@app.get("/health/deep")
@app.get("/api/health/deep")
async def deep_health_check():
    """Unauthenticated deep health check for autofix monitor and uptime watchers.
    Returns per-subsystem status so monitors can distinguish what is actually broken.
    """
    import os, time
    now = dt.datetime.now(dt.timezone.utc)
    checks: dict = {}

    # ── ML model ────────────────────────────────────────────────────────────
    ml_ok = False
    ml_detail: dict = {}
    try:
        mp = ROOT / "ml_models" / "latest" / "model_bundle.joblib"
        if not mp.exists():
            mp = ROOT / "ml_models" / "stock_universe" / "model_bundle.joblib"
        if mp.exists():
            age_h = (now.timestamp() - mp.stat().st_mtime) / 3600
            ml_ok = True
            ml_detail = {"path": str(mp), "age_hours": round(age_h, 1)}
        else:
            ml_detail = {"error": "model_bundle.joblib not found"}
    except Exception as e:
        ml_detail = {"error": str(e)}
    checks["ml_model"] = {"ok": ml_ok, **ml_detail}

    # ── Paper trader state freshness ─────────────────────────────────────────
    paper_ok = False
    paper_detail: dict = {}
    try:
        data_dir = ROOT / "tmp" / "paper_trading_today"
        state_files = list(data_dir.rglob("state.json")) if data_dir.exists() else []
        if state_files:
            newest = max(state_files, key=lambda p: p.stat().st_mtime)
            age_m = (now.timestamp() - newest.stat().st_mtime) / 60
            paper_ok = age_m < 120  # stale if >2h
            paper_detail = {"newest_state": str(newest), "age_minutes": round(age_m, 1)}
        else:
            paper_detail = {"error": "no state.json found under data/paper"}
    except Exception as e:
        paper_detail = {"error": str(e)}
    checks["paper_trader"] = {"ok": paper_ok, **paper_detail}

    # ── Cloudflare tunnel ────────────────────────────────────────────────────
    tunnel_ok = False
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "cloudflared tunnel run"],
            capture_output=True, timeout=3
        )
        tunnel_ok = result.returncode == 0
        checks["cloudflare_tunnel"] = {"ok": tunnel_ok, "pids": result.stdout.decode().strip().split() if tunnel_ok else []}
    except Exception as e:
        checks["cloudflare_tunnel"] = {"ok": False, "error": str(e)}

    # ── Disk space ──────────────────────────────────────────────────────────
    disk_ok = False
    try:
        stat = os.statvfs(str(ROOT))
        free_gb = (stat.f_bavail * stat.f_frsize) / 1e9
        disk_ok = free_gb > 5.0
        checks["disk"] = {"ok": disk_ok, "free_gb": round(free_gb, 2)}
    except Exception as e:
        checks["disk"] = {"ok": False, "error": str(e)}

    # ── Autofix monitor ─────────────────────────────────────────────────────
    autofix_ok = False
    try:
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "autofix_monitor.py"],
            capture_output=True, timeout=3
        )
        autofix_ok = result.returncode == 0
        checks["autofix_monitor"] = {"ok": autofix_ok}
    except Exception as e:
        checks["autofix_monitor"] = {"ok": False, "error": str(e)}

    # ── Webserver self-check ─────────────────────────────────────────────────
    checks["webserver"] = {"ok": True, "note": "responding (this endpoint)"}

    # paper_trader state freshness is operational status, not core system health.
    # Exclude it from overall so a paused paper runner doesn't show DEGRADED.
    core_checks = {k: v for k, v in checks.items() if k != "paper_trader"}
    overall = all(v.get("ok", False) for v in core_checks.values())
    return {
        "status": "healthy" if overall else "degraded",
        "timestamp": now.isoformat(),
        "checks": checks,
    }


async def _thematic_scan_loop():
    """Auto-trigger thematic scan every 4 hours if THEMATIC_AUTO_SCAN=true in env."""
    import json as _json
    from web.api.thematic_auto import _run_scan, STATUS_FILE as _SCAN_STATUS
    _INTERVAL = 4 * 3600  # 4 hours
    await asyncio.sleep(60)  # initial delay — let server warm up
    while True:
        try:
            if os.getenv("THEMATIC_AUTO_SCAN", "false").lower() == "true":
                status = {}
                if _SCAN_STATUS.exists():
                    try:
                        status = _json.loads(_SCAN_STATUS.read_text())
                    except Exception:
                        pass
                if status.get("status") != "running":
                    asyncio.create_task(_run_scan())
        except Exception as e:
            logging.getLogger("thematic_scan_loop").warning("Loop error: %s", e)
        await asyncio.sleep(_INTERVAL)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_paper_autostart_loop())
    asyncio.create_task(_thematic_scan_loop())


_react_dist = Path(__file__).parent / "static" / "dist"

_SPA_NO_CACHE = {
    "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Surrogate-Control": "no-store",
    "CDN-Cache-Control": "no-store",
}


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app", status_code=302)


# Mounts registered before route handlers so exact asset paths take priority.
# /app/assets → hashed Vite bundles (js, css, fonts)
app.mount("/app/assets", StaticFiles(directory=str(_react_dist / "assets")), name="react-assets")


def _react_file_response(filename: str) -> Response:
    path = _react_dist / filename
    if not path.exists():
        return Response(status_code=404)
    ext = path.suffix.lower()
    media = {"svg": "image/svg+xml", "png": "image/png", "ico": "image/x-icon"}.get(ext[1:], "application/octet-stream")
    return Response(content=path.read_bytes(), media_type=media,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/app/favicon.svg")
async def _favicon(_r: Request): return _react_file_response("favicon.svg")

@app.get("/app/icons.svg")
async def _icons(_r: Request): return _react_file_response("icons.svg")

@app.get("/app/agentic-trader-icon.png")
async def _logo(_r: Request): return _react_file_response("agentic-trader-icon.png")


def _spa_response() -> Response:
    return Response(
        content=(_react_dist / "index.html").read_bytes(),
        media_type="text/html",
        headers={
            "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/app")
@app.get("/app/{full_path:path}")
async def react_app(full_path: str = ""):
    return _spa_response()
