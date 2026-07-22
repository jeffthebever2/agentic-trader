import sys
import asyncio
import datetime as _dt
import time
import os
from pathlib import Path
from tradingagents.config import env_bool

# Playwright needs ProactorEventLoop on Windows to spawn subprocesses
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)
load_dotenv(ROOT / ".env.enterprise", override=False)

from fastapi import FastAPI, Request, Depends
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
from web.api.paper_portfolios import router as paper_portfolios_router
from web.api.copytrade import router as copytrade_router
from web.api.broker_routes import router as broker_router
from web.api.ml import router as ml_router
from web.api.rl import router as rl_router
from web.api.webull_portfolio import router as webull_router
from web.api.fidelity import router as fidelity_router
from web.api.performance import router as performance_router
from web.api.holdings_brain import router as holdings_brain_router
from web.api.scanner import router as scanner_router
from web.api.market import router as market_router
from web.api.auth_routes import router as auth_router
from web.api.twofa_routes import router as twofa_router
from web.api.live_verification import router as live_verification_router
from web.api.cloudflare_ai import router as cloudflare_ai_router
from web.api.admin import router as admin_router
from web.api.system import router as system_router
from web.api.portfolios import router as portfolios_router
from web.auth import get_optional_user, require_admin as _require_admin

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
    ignore_window = env_bool("PAPER_AUTOSTART_IGNORE_WINDOW", False)
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
app.include_router(paper_portfolios_router, prefix="/api")
app.include_router(copytrade_router, prefix="/api")
app.include_router(broker_router, prefix="/api")
app.include_router(ml_router, prefix="/api")
app.include_router(rl_router, prefix="/api")
app.include_router(webull_router, prefix="/api")
app.include_router(fidelity_router, prefix="/api")
app.include_router(performance_router, prefix="/api")
app.include_router(holdings_brain_router, prefix="/api")
app.include_router(scanner_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(portfolios_router)  # routes self-declare /api/portfolios/...

@app.get("/portfolios", include_in_schema=False)
async def portfolios_page():
    """Portfolio competition dashboard."""
    from fastapi.responses import FileResponse
    _page = Path(__file__).parent / "static" / "portfolios.html"
    if _page.exists():
        return FileResponse(str(_page), media_type="text/html")
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<h1>portfolios.html not found</h1>", status_code=404)


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

@app.get("/health/loops")
@app.get("/api/health/loops")
async def health_loops(admin: dict = Depends(_require_admin)):
    """Background-loop supervision probe (D4). Reports each loop's liveness so a
    monitor can alert on a dead task — previously invisible. Admin-gated (loop
    names are internal)."""
    loops = {}
    for name, task in _LOOP_TASKS.items():
        exc = None
        if task.done() and not task.cancelled():
            try:
                e = task.exception()
                exc = repr(e) if e else None
            except Exception:
                exc = None
        # task.done() only reports the SUPERVISOR, which never returns — so it is
        # True for a healthy loop and for one crash-looping every tick alike.
        # _LOOP_HEALTH is what distinguishes them.
        h = _LOOP_HEALTH.get(name, {})
        consecutive = int(h.get("consecutive_failures", 0) or 0)
        # crash_looping is derived from RECENCY, not a latched counter.
        # `consecutive_failures` is only written when a loop exits, so it never
        # returns to 0 on recovery — reporting it directly meant a loop that
        # crashed twice at boot and then ran healthily for a week still showed
        # not-ok forever, and an alert that can never go green gets muted.
        # It also missed the opposite case: a loop crashing every 6 minutes
        # resets the counter to 1 each time (ran_for >= 300) and looked healthy.
        # Recency catches both: still failing recently ⇒ still crash-looping.
        _since_fail = None
        if h.get("last_failed_at"):
            try:
                _since_fail = (_dt.datetime.now()
                               - _dt.datetime.fromisoformat(h["last_failed_at"])).total_seconds()
            except (TypeError, ValueError):
                _since_fail = None
        recently_failed = _since_fail is not None and _since_fail <= _LOOP_CRASH_WINDOW_SECONDS
        loops[name] = {
            "alive": not task.done(),
            "cancelled": task.cancelled(),
            "exception": exc,
            "restarts": int(h.get("restarts", 0) or 0),
            "consecutive_failures": consecutive,
            "seconds_since_last_failure": (None if _since_fail is None
                                           else round(_since_fail, 1)),
            "last_ran_seconds": h.get("last_ran_seconds"),
            "last_error": h.get("last_error"),
            "last_started_at": h.get("last_started_at"),
            "last_failed_at": h.get("last_failed_at"),
            # Failing repeatedly AND recently. Recovers on its own once the loop
            # stays up past the window.
            "crash_looping": bool(recently_failed and consecutive >= 2),
            # A loop that keeps dying on a slow cadence never accumulates
            # consecutive failures, so surface it separately.
            "restarting_recently": bool(recently_failed),
        }
    all_alive = all(v["alive"] for v in loops.values()) if loops else False
    none_looping = not any(v["crash_looping"] for v in loops.values())
    return {"ok": all_alive and none_looping,
            "count": len(loops),
            "crash_looping": [n for n, v in loops.items() if v["crash_looping"]],
            "loops": loops}


@app.get("/health/preflight")
@app.get("/api/health/preflight")
async def health_preflight(admin: dict = Depends(_require_admin)):
    """Configuration-safety probe.

    Individual flags are all validated; their dangerous COMBINATIONS were not,
    and every dangerous combination booted cleanly and reported healthy. A
    CRITICAL finding here means live execution is latched OFF until the config
    is corrected — see tradingagents/preflight.py."""
    from tradingagents.compliance import (
        LIVE_TRADING_HARD_BLOCKED, preflight_block_reason,
    )
    from tradingagents.preflight import run_preflight
    result = run_preflight(os.environ, hard_blocked=LIVE_TRADING_HARD_BLOCKED)
    payload = result.as_dict()
    payload["live_execution_latched_off"] = bool(preflight_block_reason())
    payload["latch_reason"] = preflight_block_reason()
    return payload


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
            # Basename only — never disclose the absolute filesystem layout to an
            # unauthenticated caller (M6). Operational health fields below are fine.
            ml_detail = {"path": mp.name, "age_hours": round(age_h, 1)}
            # Run full ModelHealthChecker for ROC, drift, calibration, age_days
            try:
                from tradingagents.portfolio.production_safety import ModelHealthChecker
                import joblib as _jl
                _bundle = _jl.load(str(mp))
                _hc = ModelHealthChecker()
                _report_path = mp.parent / "training_report.json"
                _drift_path = mp.parent / "drift_log.json"
                # validation_summary_path expects {roc_auc, brier_score} at top level —
                # training_report.json has these nested under walk_forward/models; pass None
                # to avoid silent None reads. ROC is surfaced separately via /api/portfolios/model-health.
                _hc_result = _hc.check(
                    bundle=_bundle,
                    validation_summary_path=None,
                    drift_log_path=_drift_path if _drift_path.exists() else None,
                )
                # Supplement with WF ROC from training_report.json (correct location)
                _wf_roc = None
                if _report_path.exists():
                    try:
                        import json as _json
                        _tr = _json.loads(_report_path.read_text())
                        _wf_roc = (_tr.get("walk_forward") or {}).get("roc_auc")
                    except Exception:
                        pass
                ml_detail.update({
                    "age_days": _hc_result.get("age_days"),
                    "wf_roc": _wf_roc,
                    "n_features": _hc_result.get("n_features"),
                    "halt_reasons": _hc_result.get("halt_reasons", []),
                    "warn_reasons": _hc_result.get("warn_reasons", []),
                })
                ml_ok = not _hc_result.get("halt_reasons")
            except Exception as _hc_err:
                ml_ok = True  # file exists, checker failed non-fatally
                ml_detail["health_check_error"] = str(_hc_err)
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
            # Staleness only matters while the market is open — overnight and
            # on weekends the runner correctly writes nothing, so a fixed 2h
            # threshold would flag "down" every night.
            now_et = dt.datetime.now(_TZ_ET)
            market_open_now = (
                now_et.weekday() < 5
                and dt.time(9, 30) <= now_et.time() <= dt.time(16, 0)
            )
            paper_ok = (age_m < 120) or not market_open_now
            paper_detail = {
                "newest_state": str(newest),
                "age_minutes": round(age_m, 1),
                "market_open": market_open_now,
            }
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


async def _fidelity_keepalive_loop():
    """Every 10 min, navigate to Fidelity summary for each live session to prevent cookie expiry.

    10 min beats Fidelity's ~15-20 min idle timeout so a session that's only
    touched by background keepalive (no user activity) stays warm.
    """
    _log = logging.getLogger("fidelity_keepalive")
    await asyncio.sleep(30)  # let server warm up
    _INTERVAL = 10 * 60  # 10 minutes
    while True:
        try:
            import hashlib as _hl
            from pathlib import Path as _Path
            from web.api.fidelity import (
                _ensure_browser, _save_storage, _set_session_cache, SUMMARY_URL,
                _auto_relogin, _is_authenticated_url, _ORDER_IN_FLIGHT,
                _LOGIN_IN_FLIGHT, _is_manual_login_required, _user_key,
                _revalidate_positions, _revalidate_accounts,
            )
            root = _Path(__file__).parent.parent

            # Build digest → email map from user registry
            digest_to_email: dict[str, str] = {}
            try:
                users_file = root / "tmp" / "users.json"
                if users_file.exists():
                    import json as _j
                    for email in _j.loads(users_file.read_text()):
                        e = email.strip().lower()
                        if e:
                            d = _hl.sha256(e.encode()).hexdigest()[:16]
                            digest_to_email[d] = e
            except Exception:
                pass

            for sf in root.glob(".fidelity_session_*.json"):
                # filename: .fidelity_session_<16hex>.json
                parts = sf.stem.rsplit("_", 1)
                digest = parts[-1] if len(parts) == 2 else ""
                email = digest_to_email.get(digest, digest)  # use email if known
                if _user_key(email) in _ORDER_IN_FLIGHT or _user_key(email) in _LOGIN_IN_FLIGHT:
                    continue  # don't touch the browser during an order or interactive login
                if _is_manual_login_required(email):
                    continue  # device untrusted → don't open a browser / hit Fidelity (rate-limit)
                try:
                    ctx = await _ensure_browser(email)
                    page = await ctx.new_page()
                    try:
                        await page.goto(SUMMARY_URL, wait_until="domcontentloaded", timeout=25_000)
                        await asyncio.sleep(3)
                        url = page.url.lower()
                        connected = _is_authenticated_url(url)
                        if connected:
                            await _save_storage(email)
                            _set_session_cache(email, True)
                            _log.debug("Session kept alive: %s", email[:20])
                            # Warm the holdings/balances snapshots so the Broker page
                            # loads instantly from cache (no 20-40s scrape on click).
                            try:
                                await _revalidate_positions(email)
                                await _revalidate_accounts(email)
                                # Seed today's portfolio-performance snapshot from the
                                # just-warmed cache (dedups to one per trading day;
                                # read-only). Opt out with PERF_SNAPSHOT_ENABLED=false.
                                import os as _po
                                if env_bool("PERF_SNAPSHOT_ENABLED", True):
                                    from web.api.performance import capture_snapshot as _cap
                                    await _cap(email, from_cache=True)
                                # Did the orders we SUBMITTED actually fill? State is
                                # committed on broker acceptance, but these are DAY
                                # limit orders — one that never trades expires at
                                # 16:00 silently, leaving a position the broker has
                                # never heard of. The snapshots were just refreshed
                                # above, so holdings are as fresh as they get.
                                # Outside market hours the session is over, so an
                                # unfilled order can no longer fill → treat as expired.
                                try:
                                    from web.api.fidelity import verify_pending_fills
                                    _verdicts = verify_pending_fills(
                                        email, session_expired=not _brain_market_open()
                                    )
                                    _phantom = [v for v in _verdicts if v.get("status") == "unfilled"]
                                    if _phantom:
                                        _log.error(
                                            "PHANTOM POSITIONS for %s — order(s) never filled but "
                                            "state was written on acceptance: %s. Internal state "
                                            "believes these are held; the broker does not.",
                                            email[:20],
                                            ", ".join(f"{v['ticker']} x{v['intended_shares']:g}"
                                                      for v in _phantom),
                                        )
                                    for _v in _verdicts:
                                        if _v.get("status") == "partial":
                                            _log.warning(
                                                "PARTIAL FILL %s: %g of %g filled — sizing, stops "
                                                "and the concentration cap are computed against "
                                                "the intended quantity.",
                                                _v["ticker"], _v["filled_shares"],
                                                _v["intended_shares"])
                                except Exception as _fe:
                                    _log.debug("fill verification %s: %s", email[:20], _fe)
                            except Exception as _ce:
                                _log.debug("keepalive cache warm %s: %s", email[:20], _ce)
                        else:
                            _set_session_cache(email, False)
                            _log.info("Session expired (keepalive check): %s — attempting silent re-login", email[:20])
                            # Stored creds + trusted device → reconnect with no user action.
                            try:
                                if await _auto_relogin(email):
                                    _log.info("Keepalive silent re-login succeeded: %s", email[:20])
                            except Exception as _re:
                                _log.warning("Keepalive re-login error %s: %s", email[:20], _re)
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass
                except Exception as e:
                    _log.warning("Keepalive error for %s: %s", email[:20], e)
        except Exception as e:
            logging.getLogger("fidelity_keepalive").warning("Keepalive loop error: %s", e)
        await asyncio.sleep(_INTERVAL)


async def _thematic_scan_loop():
    """Auto-trigger thematic scan every 4 hours if THEMATIC_AUTO_SCAN=true in env."""
    import json as _json
    from web.api.thematic_auto import _run_scan, _scan_status_stale, STATUS_FILE as _SCAN_STATUS
    _INTERVAL = 4 * 3600  # 4 hours
    await asyncio.sleep(60)  # initial delay — let server warm up
    while True:
        try:
            if env_bool("THEMATIC_AUTO_SCAN", False):
                status = {}
                if _SCAN_STATUS.exists():
                    try:
                        status = _json.loads(_SCAN_STATUS.read_text())
                    except Exception:
                        pass
                # A "running" status left behind by a killed process would
                # otherwise block this loop forever; trigger_scan already treats
                # stale-running as not-running — mirror that here.
                if status.get("status") != "running" or _scan_status_stale(status):
                    asyncio.create_task(_run_scan())
        except Exception as e:
            logging.getLogger("thematic_scan_loop").warning("Loop error: %s", e)
        await asyncio.sleep(_INTERVAL)


async def _fd_janitor_loop():
    """Free leaked yfinance tz-cache sqlite handles every 5 minutes.

    yfinance opens a peewee sqlite connection per downloader thread and dead
    threads' connections linger until garbage collection. Under launchd's
    default 256-fd limit the server eventually fails every endpoint with
    OSError Errno 24 (observed 2026-06-10: 123 leaked tkr-tz.db handles).
    """
    while True:
        await asyncio.sleep(300)
        try:
            from yfinance.cache import _TzDBManager
            _TzDBManager.close_db()
        except Exception:
            pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass


def _brain_market_open() -> bool:
    """True during the US regular session — for anything that EXECUTES.

    Now calendar-aware. The old weekday+clock test returned True on market
    holidays, so the exit guard evaluated stops against quotes that had not
    updated since the previous close: a stale-price decision on real positions,
    proposing a fill that could not happen. It also ignored early closes."""
    from tradingagents.market_calendar import is_regular_session
    return is_regular_session()


def _brain_risk_window() -> bool:
    """True 04:00-20:00 ET on a trading day.

    INTENTIONALLY UNUSED. Do not wire this into a loop without reading this note.

    The motivation is real: gating stop DETECTION on the regular session means a
    Friday 15:59 breach goes unlooked-at until Monday 09:30 (~65.5h, longer over
    a holiday weekend), which is exactly when an earnings miss or a weekend
    headline does its damage. It was wired into the exit guard and the paper exit
    loop, and had to be reverted — "it only proposes, so widening is free" is
    false here:

      1. ``run_exit_guard`` calls ``ratchet_stops``, which MUTATES and PERSISTS
         ``trail_high``/``stop``. On a thin after-hours print an earnings spike
         ratchets the trail to a level that never really traded; the stock opens
         below it and the guard proposes liquidating a real winner.
      2. ``_trusted_quotes`` fans out one FMP call per holding with a 2s cache.
         04:00-20:00 at 15-min cadence is ~448 calls/day against a 250/day limit.
         Exhausting the only trusted provider makes PreTradeGate reject every
         order INCLUDING EXITS — positions could not be closed.
      3. ``_check_thematic_exits`` prices from yfinance DAILY bars, so out of
         hours it re-reads a close already evaluated at 15:59.

    Prerequisites before using it: batch/cache the quote fan-out, suppress the
    stop ratchet outside the regular session, and move the paper exit loop to an
    intraday price source. EXECUTION must stay on _brain_market_open() regardless
    — extended-hours liquidity is thin.
    """
    from tradingagents.market_calendar import is_extended_session
    return is_extended_session()


def _fidelity_sessioned_emails() -> list[str]:
    """Emails with a live Fidelity session file (reuses the keepalive digest map)."""
    import hashlib as _hl
    import json as _j
    root = Path(__file__).parent.parent
    digest_to_email: dict[str, str] = {}
    try:
        users_file = root / "tmp" / "users.json"
        if users_file.exists():
            for email in _j.loads(users_file.read_text()):
                e = str(email).strip().lower()
                if e:
                    digest_to_email[_hl.sha256(e.encode()).hexdigest()[:16]] = e
    except Exception:
        pass
    emails: list[str] = []
    for sf in root.glob(".fidelity_session_*.json"):
        parts = sf.stem.rsplit("_", 1)
        digest = parts[-1] if len(parts) == 2 else ""
        emails.append(digest_to_email.get(digest, digest))
    return emails


async def _holdings_brain_loop():
    """Full AI assessment of real holdings → HIL proposals. Gated HOLDINGS_BRAIN_ENABLED.

    Never places an order. Runs in market hours on the slower cadence
    (HOLDINGS_BRAIN_INTERVAL_MIN, default 240 min)."""
    _log = logging.getLogger("holdings_brain_loop")
    await asyncio.sleep(90)  # let server + sessions warm up
    while True:
        try:
            if env_bool("HOLDINGS_BRAIN_ENABLED", False) and _brain_market_open():
                from web.api.holdings_brain import run_brain_cycle
                for email in _fidelity_sessioned_emails():
                    try:
                        summary = await run_brain_cycle(email, broker="fidelity", use_ai=True)
                        _log.info("brain cycle %s: %s", email[:16], summary)
                    except Exception as e:
                        _log.warning("brain cycle failed for %s: %s", email[:16], e)
        except Exception as e:
            _log.warning("loop error: %s", e)
        interval = max(15, int(float(os.getenv("HOLDINGS_BRAIN_INTERVAL_MIN", "240")))) * 60
        await asyncio.sleep(interval)


async def _exit_guard_loop():
    """Fast stop/target guard on managed real holdings → priority EXIT proposals.

    Gated HOLDINGS_BRAIN_ENABLED. Never auto-fires (human approves with step-up
    2FA). Runs every EXIT_GUARD_INTERVAL_MIN (default 15) during market hours."""
    _log = logging.getLogger("exit_guard_loop")
    await asyncio.sleep(120)
    _deferred_logged = False
    while True:
        try:
            # REGULAR SESSION, deliberately. Widening this to the extended risk
            # window looked free ("it only proposes") and was not:
            #   1. run_exit_guard calls ratchet_stops, which MUTATES and
            #      PERSISTS trail_high/stop. On a thin after-hours print an
            #      earnings spike ratchets the trail to a peak that never really
            #      traded; the stock opens below it next morning and the guard
            #      proposes a full liquidation of a real winner.
            #   2. _trusted_quotes fans out one FMP call per holding with a 2s
            #      cache. 04:00-20:00 at 15min = 64 cycles/day; with 7 holdings
            #      that is ~448 calls against a 250/day limit. Exhausting the
            #      only trusted provider makes PreTradeGate reject every order
            #      INCLUDING EXITS — the exact failure preflight calls CRITICAL.
            # The calendar-awareness below is the real fix (no more evaluating
            # stops against stale quotes on holidays). The overnight gap remains
            # a known limit, documented in the runbook.
            if env_bool("HOLDINGS_BRAIN_ENABLED", False) and _brain_market_open():
                # The standalone runner (scripts/run_exit_guard.py) holds
                # tmp/exit_guard.lock for its lifetime — when it's up, it owns
                # the live-book watch and this loop stands down (no duplicate
                # proposals/SMS). If the runner dies, its flock vanishes and
                # this loop resumes on the next cycle: belt and suspenders.
                from tradingagents.portfolio.process_lock import flock_is_held
                if flock_is_held(ROOT / "tmp" / "exit_guard.lock"):
                    if not _deferred_logged:
                        _log.info("standalone exit-guard runner active — in-server loop standing down")
                        _deferred_logged = True
                    await asyncio.sleep(60)
                    continue
                _deferred_logged = False
                from web.api.holdings_brain import run_exit_guard
                for email in _fidelity_sessioned_emails():
                    try:
                        breaches = await run_exit_guard(email, broker="fidelity")
                        if breaches:
                            _log.warning("EXIT-GUARD %s: %d breach(es) → proposals raised: %s",
                                         email[:16], len(breaches),
                                         ", ".join(f"{b['ticker']}:{b['reason']}" for b in breaches))
                    except Exception as e:
                        _log.warning("exit guard failed for %s: %s", email[:16], e)
        except Exception as e:
            _log.warning("loop error: %s", e)
        interval = max(2, int(float(os.getenv("EXIT_GUARD_INTERVAL_MIN", "15")))) * 60
        await asyncio.sleep(interval)


async def _thematic_exit_loop():
    """Fast stop/target/trailing enforcement on the THEMATIC PAPER book, decoupled
    from the 4-hour scan loop (audit #1: stops were otherwise checked <=6x/day, and
    not at all if the scan froze).

    PAPER-ONLY: _check_thematic_exits(execute=True) writes the paper book and exit
    log — it does NOT place or exit any live broker order. The live Fidelity book
    is mirrored into paper for tracking, so the exit signals it computes surface as
    HIL exit proposals; live execution still requires human + step-up 2FA. Gated by
    THEMATIC_EXIT_LOOP (default off); interval THEMATIC_EXIT_INTERVAL_MIN (default 15)."""
    _log = logging.getLogger("thematic_exit_loop")
    await asyncio.sleep(150)  # let the server warm up
    while True:
        try:
            # REGULAR SESSION. _check_thematic_exits prices from yfinance DAILY
            # bars, so out of hours it just re-reads a close already evaluated at
            # 15:59 — zero new information for 2.5x the yfinance load, against a
            # documented sqlite-fd leak (_fd_janitor_loop exists to mop it up).
            if env_bool("THEMATIC_EXIT_LOOP", False) and _brain_market_open():
                from web.api.thematic_auto import _check_thematic_exits
                exits = await _check_thematic_exits(execute=True)  # paper-only
                if exits:
                    _log.info("thematic exits (paper) %d: %s", len(exits),
                              ", ".join(f"{e.get('ticker')}:{e.get('reason')}" for e in exits))
        except Exception as e:
            _log.warning("loop error: %s", e)
        interval = max(2, int(float(os.getenv("THEMATIC_EXIT_INTERVAL_MIN", "15")))) * 60
        await asyncio.sleep(interval)


async def _autonomous_live_exit_loop():
    """Optional armed live-exit executor for managed Fidelity holdings.

    Default OFF. Requires THEME/THEMATIC_LIVE_EXIT_AUTONOMOUS=true plus a recent
    step-up arm record created through /thematic/brain/live-exits/arm. The normal
    exit guard remains propose-only; this loop only executes existing priority
    stop/crash EXIT proposals through the Fidelity compliance path.
    """
    _log = logging.getLogger("autonomous_live_exit_loop")
    await asyncio.sleep(180)
    while True:
        try:
            if env_bool("THEMATIC_LIVE_EXIT_AUTONOMOUS", False) and _brain_market_open():
                # _fidelity_sessioned_emails lives in THIS module (defined above),
                # not in web.api.fidelity. The bad import raised ImportError every
                # cycle and was swallowed by the outer `except Exception`, so the
                # armed autonomous exit executor never ran once — a silent false
                # safety net on the real book.
                from web.api.holdings_brain import run_autonomous_live_exit_executor
                for email in _fidelity_sessioned_emails():
                    try:
                        executed = await run_autonomous_live_exit_executor(email, broker="fidelity")
                        if executed:
                            _log.warning("AUTO-LIVE-EXIT %s: %d order(s): %s",
                                         email[:16], len(executed),
                                         ", ".join(e.get("ticker", "?") for e in executed))
                    except Exception as e:
                        _log.warning("auto live exit failed for %s: %s", email[:16], e)
        except Exception as e:
            _log.warning("loop error: %s", e)
        interval = max(2, int(float(os.getenv("THEMATIC_LIVE_EXIT_INTERVAL_MIN", "5")))) * 60
        await asyncio.sleep(interval)


_background_tasks: list[asyncio.Task] = []
# name → task, for supervision + the /health/loops probe.
_LOOP_TASKS: dict[str, asyncio.Task] = {}
# name → health record. The supervisor wrapper never returns, so task.done() is
# always False and says nothing about the loop inside it; this is the real
# liveness signal. See _spawn_supervised_loop.
_LOOP_HEALTH: dict[str, dict] = {}
#: How recently a loop must have failed to still count as crash-looping. Wider
#: than the longest loop interval (copytrade 10min, thematic exit 15min) so a
#: loop dying once per cycle stays visible, and narrow enough that a recovered
#: loop clears on its own rather than latching the probe red forever.
_LOOP_CRASH_WINDOW_SECONDS = 45 * 60


def _spawn_supervised_loop(loop_factory, name: str) -> asyncio.Task:
    """Start a background loop under a supervisor that restarts it if it ever
    exits or raises (D4).

    The loops are `while True` bodies with their own inner try/except, so they
    should never return — if one does (or an exception escapes the inner guard,
    e.g. a malformed-env parse), the task previously died SILENTLY and was never
    restarted, making a dead trade/exit executor indistinguishable from a healthy
    idle one. This wrapper logs CRITICAL and relaunches with a fixed backoff.
    """
    _sup_log = logging.getLogger("loop_supervisor")

    async def _supervised():
        # Exponential backoff. A loop that returns or crashes IMMEDIATELY (e.g. a
        # bad import, or an early `return` when a feature flag is off) otherwise
        # relaunches every 10s for the life of the process — a hot restart loop
        # that spams CRITICAL and burns CPU while looking like an active alert.
        # Backing off keeps a genuinely transient failure fast to recover while a
        # permanent one settles into a quiet, still-visible heartbeat.
        #
        # The supervisor NEVER returns, so `task.done()` is permanently False and
        # cannot indicate loop health. _LOOP_HEALTH carries the real signal —
        # without it, a loop crashing on every tick reports {"ok": true}.
        delay, max_delay = 10, 600
        health = _LOOP_HEALTH.setdefault(
            name, {"restarts": 0, "consecutive_failures": 0,
                   "last_error": None, "last_started_at": None,
                   "last_failed_at": None})
        while True:
            loop_time = asyncio.get_running_loop().time()
            started = loop_time
            health["last_started_at"] = _dt.datetime.now().isoformat(timespec="seconds")
            try:
                await loop_factory()
                health["last_error"] = "exited without raising"
                _sup_log.critical("Background loop %r exited unexpectedly", name)
            except asyncio.CancelledError:
                raise  # honor shutdown
            except Exception as exc:
                health["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                _sup_log.critical("Background loop %r crashed: %s", name, exc)
            ran_for = asyncio.get_running_loop().time() - started
            health["restarts"] += 1
            health["last_failed_at"] = _dt.datetime.now().isoformat(timespec="seconds")
            health["last_ran_seconds"] = round(ran_for, 1)
            # Ran for a meaningful stretch ⇒ treat the next failure as fresh.
            if ran_for >= 300:
                delay = 10
                health["consecutive_failures"] = 1
            else:
                health["consecutive_failures"] += 1
            _sup_log.critical("Background loop %r restarting in %ds (restart #%d, "
                              "%d consecutive fast failure(s))",
                              name, delay, health["restarts"],
                              health["consecutive_failures"])
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    task = asyncio.create_task(_supervised(), name=name)
    _LOOP_TASKS[name] = task
    _background_tasks.append(task)
    return task


async def _performance_snapshot_loop():
    """Once per trading day (after market close) capture a Fidelity account snapshot
    for each connected user, so portfolio-performance history accrues automatically.
    Env-gated PERF_SNAPSHOT_ENABLED (default off). Manual capture is always available
    via POST /api/performance/sync."""
    import datetime as _dt2, hashlib as _hl, json as _json
    from pathlib import Path as _Path
    _log = logging.getLogger("performance")
    await asyncio.sleep(45)  # let the server + sessions warm up
    from web.api.performance import capture_snapshot, _load_snapshots
    while True:
        # Gate INSIDE the loop. Returning early when disabled made the supervisor
        # treat a clean exit as a crash and relaunch every 10s forever — a hot
        # restart loop spamming CRITICAL logs for the entire life of the process.
        # Checking here also matches every other loop: the flag is read fresh each
        # tick, so an operator can enable it without a restart.
        if not env_bool("PERF_SNAPSHOT_ENABLED", False):
            await asyncio.sleep(300)
            continue
        try:
            now = _dt2.datetime.now()
            # capture after 16:05 local on weekdays (US market closed) — one per day
            after_close = now.weekday() < 5 and (now.hour > 16 or (now.hour == 16 and now.minute >= 5))
            if after_close:
                root = _Path(__file__).parent.parent
                today = _dt2.date.today().isoformat()
                digest_to_email: dict[str, str] = {}
                users_file = root / "tmp" / "users.json"
                if users_file.exists():
                    for em in _json.loads(users_file.read_text()):
                        e = (em or "").strip().lower()
                        if e:
                            digest_to_email[_hl.sha256(e.encode()).hexdigest()[:16]] = e
                for sf in root.glob(".fidelity_session_*.json"):
                    digest = sf.stem.rsplit("_", 1)[-1]
                    email = digest_to_email.get(digest)
                    if not email:
                        continue
                    snaps = _load_snapshots(email)
                    if any(s.date == today and s.ok for s in snaps):
                        continue  # already captured today
                    res = await capture_snapshot(email)
                    _log.info("daily perf snapshot %s: %s", email[:20], res)
        except Exception as e:
            logging.getLogger("performance").warning("perf snapshot loop error: %s", e)
        await asyncio.sleep(3600)  # re-check hourly


async def _copytrade_loop():
    """Follow a chosen paper portfolio into real Fidelity — HIL or autonomous.

    Env-gated COPYTRADE_ENABLED (default off). Runs only in market hours on
    COPYTRADE_INTERVAL_MIN cadence (default 10 min). Per-user mode decides whether
    each reconcile enqueues HIL approvals or auto-executes (auto also requires the
    COPYTRADE_AUTONOMOUS kill-switch). Never places an order outside the compliance
    gates in the Fidelity execution layer."""
    _log = logging.getLogger("copytrade_loop")
    await asyncio.sleep(75)  # let server + sessions warm up
    while True:
        try:
            if env_bool("COPYTRADE_ENABLED", False) and _brain_market_open():
                from web.copytrade import run_copytrade_cycle
                summary = await run_copytrade_cycle()
                if summary:
                    _log.info("copytrade cycle: %s", summary)
        except Exception as e:
            _log.warning("copytrade loop error: %s", e)
        interval = max(2, int(float(os.getenv("COPYTRADE_INTERVAL_MIN", "10")))) * 60
        await asyncio.sleep(interval)


# Held (never released) for the process lifetime — see acquire_single_instance.
_instance_lock_fd: int | None = None


@app.on_event("startup")
async def _startup():
    # All order/state guards in this tier (_ORDER_LOCKS, _paper_state_lock,
    # alert cooldowns) assume ONE web process. A second worker (gunicorn -w N,
    # uvicorn --workers N) voids them → duplicate live orders. Fail loud here
    # rather than trade unguarded. flock dies with the process — no stale lock.
    global _instance_lock_fd
    if (env_bool("WEB_SINGLE_INSTANCE_LOCK", True)
            and _instance_lock_fd is None):
        # _instance_lock_fd guard: flock conflicts across fds even within one
        # process, so a re-fired startup (TestClient, reload) must reuse the
        # lock it already holds instead of fighting itself.
        from tradingagents.portfolio.process_lock import (
            SingleInstanceError, acquire_single_instance)
        try:
            _instance_lock_fd = acquire_single_instance(ROOT / "tmp" / "webserver.lock")
        except SingleInstanceError:
            logging.getLogger("startup").critical(
                "REFUSING TO START: another web server process is running. "
                "This server must be single-worker — its live-order and "
                "paper-state locks are in-process. Stop the other instance or "
                "set WEB_SINGLE_INSTANCE_LOCK=false (dangerous) to override.")
            raise
    elif not env_bool("WEB_SINGLE_INSTANCE_LOCK", True):
        logging.getLogger("startup").warning(
            "WEB_SINGLE_INSTANCE_LOCK=false — multi-instance protection OFF")
    # ── Configuration preflight ───────────────────────────────────────────────
    # Runs BEFORE any loop starts. Individual flags are validated everywhere;
    # their dangerous combinations were not, and each of them booted cleanly and
    # reported healthy — most importantly "live trading armed with no stop
    # watcher", which means real positions get ZERO stop checks per day.
    # A CRITICAL finding latches live execution OFF (fail closed) rather than
    # merely logging; paper trading, scanning and proposals continue so the
    # operator can see and fix the problem.
    try:
        from tradingagents.compliance import (
            LIVE_TRADING_HARD_BLOCKED, block_live_trading_for_preflight,
        )
        from tradingagents.preflight import format_findings, run_preflight
        _pf = run_preflight(os.environ, hard_blocked=LIVE_TRADING_HARD_BLOCKED)
        _pf_log = logging.getLogger("preflight")
        if _pf.critical:
            _pf_log.critical("%s", format_findings(_pf))
            _codes = ", ".join(f.code for f in _pf.critical)
            block_live_trading_for_preflight(_codes)
            _pf_log.critical(
                "LIVE EXECUTION LATCHED OFF by preflight (%s). Paper trading and "
                "proposals continue. Fix the configuration and restart; check "
                "/health/preflight for detail.", _codes)
        elif _pf.warnings:
            _pf_log.warning("%s", format_findings(_pf))
        else:
            _pf_log.info("preflight: all checks passed")
    except Exception as _pfe:  # never prevent boot on a preflight bug
        logging.getLogger("preflight").error("preflight failed to run: %s", _pfe)

    _spawn_supervised_loop(_paper_autostart_loop, "paper_autostart")
    _spawn_supervised_loop(_performance_snapshot_loop, "performance_snapshot")
    _spawn_supervised_loop(_thematic_scan_loop, "thematic_scan")
    _spawn_supervised_loop(_fidelity_keepalive_loop, "fidelity_keepalive")
    _spawn_supervised_loop(_fd_janitor_loop, "fd_janitor")
    _spawn_supervised_loop(_holdings_brain_loop, "holdings_brain")
    _spawn_supervised_loop(_exit_guard_loop, "exit_guard")
    _spawn_supervised_loop(_thematic_exit_loop, "thematic_exit")
    _spawn_supervised_loop(_autonomous_live_exit_loop, "autonomous_live_exit")
    _spawn_supervised_loop(_copytrade_loop, "copytrade")

    # SnapTrade is an OPTIONAL data overlay — the app runs fully on the local
    # Fidelity path without it. If it's enabled but the keys don't authenticate
    # (e.g. only trial/placeholder keys, no production access yet), log clearly and
    # keep running on local data rather than failing.
    try:
        from web.broker.snaptrade_data import is_enabled as _st_enabled
        from web.broker import snaptrade_store as _st_store
        _slog = logging.getLogger("snaptrade")
        if _st_enabled():
            if not _st_store.keys_configured():
                _slog.warning("SNAPTRADE_ENABLED=true but keys not set — using local Fidelity data.")
            else:
                _ok, _reason = _st_store.verify_credentials()
                if _ok:
                    _slog.info("SnapTrade credentials valid — Fidelity data overlay active (data only).")
                else:
                    _slog.warning("SnapTrade enabled but credentials invalid (%s) — using local Fidelity data. "
                                  "Production keys require a public app page + company info at snaptrade.com.", _reason)
        else:
            _slog.info("SnapTrade disabled — using local Fidelity data + execution.")
    except Exception as _e:
        logging.getLogger("snaptrade").debug("snaptrade startup check skipped: %s", _e)


@app.on_event("shutdown")
async def _shutdown():
    # Drain in-flight broker orders BEFORE cancelling anything. A restart (deploy,
    # `systemctl restart`, launchd kickstart) that lands between "Place Order" and
    # the confirmation read would otherwise kill the task with the order already
    # live at the broker and no state written — a real position nothing tracks,
    # with no stop. Bounded so shutdown can never hang: past the deadline we log
    # loudly and proceed, and the pending-fill ledger + holdings reconciliation
    # will surface the order on next boot.
    _sd_log = logging.getLogger("shutdown")
    try:
        from web.api.fidelity import _ORDER_IN_FLIGHT
        deadline = asyncio.get_running_loop().time() + 45.0
        waited = False
        while _ORDER_IN_FLIGHT and asyncio.get_running_loop().time() < deadline:
            if not waited:
                _sd_log.warning(
                    "Shutdown held: %d broker order(s) in flight (%s) — draining "
                    "before cancelling background tasks.",
                    len(_ORDER_IN_FLIGHT), ", ".join(sorted(_ORDER_IN_FLIGHT)))
                waited = True
            await asyncio.sleep(0.5)
        if _ORDER_IN_FLIGHT:
            _sd_log.critical(
                "SHUTTING DOWN WITH %d ORDER(S) STILL IN FLIGHT (%s). These may be "
                "live at the broker with no local record — verify manually and "
                "check /health/preflight and the pending-fill ledger on restart.",
                len(_ORDER_IN_FLIGHT), ", ".join(sorted(_ORDER_IN_FLIGHT)))
        elif waited:
            _sd_log.info("In-flight orders drained cleanly.")
    except Exception as _sde:
        _sd_log.warning("order drain check failed: %s", _sde)

    for t in _background_tasks:
        if not t.done():
            t.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)


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

# /charts → generated trade-request chart PNGs (public, so Sendblue MMS can fetch
# the media_url). check_dir=False so the mount survives a fresh checkout before
# the first chart is written.
_charts_dir = Path(__file__).parent / "static" / "charts"
_charts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=str(_charts_dir), check_dir=False), name="trade-charts")


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
