"""Fidelity portfolio integration via Playwright browser automation.

Auth flow (WebSocket /ws/fidelity-auth):
  client → {"username", "password"}
  server → {"step":"logging_in"}
  server → {"step":"need_totp", "prompt":"Enter verification code"}  (if 2FA triggered)
  client → {"totp":"123456"}
  server → {"step":"authenticated"}  or  {"step":"error", "message":"..."}

Once authenticated, REST endpoints use the stored browser session.
"""
import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from web.auth import require_admin, require_step_up
from web.secure_store import encrypted_temp_file, is_encrypted_path, write_encrypted
from pydantic import BaseModel, Field

router = APIRouter()

# Persistent browser state so re-login not required each server restart.
# Fidelity sessions are keyed by the authenticated Access email. Never share a
# browser context between users because the context contains live broker cookies.
_LEGACY_STORAGE_STATE = ROOT / ".fidelity_session.json"
_PW_CONTEXTS: dict[str, object] = {}
_PW_INSTANCES: dict[str, object] = {}
_PW_BROWSERS: dict[str, object] = {}
_FIDELITY_STORAGE_PURPOSE = "fidelity-playwright-storage-state"

LOGIN_URL = "https://digital.fidelity.com/ftgw/digital/login/full-page"
PORTFOLIO_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/positions"
SUMMARY_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"

# ── Playwright helpers ─────────────────────────────────────────

def _user_key(email: str) -> str:
    return (email or "").strip().lower()


def _fidelity_state_path(email: str) -> Path:
    digest = hashlib.sha256(_user_key(email).encode()).hexdigest()[:16]
    return ROOT / f".fidelity_session_{digest}.json"


def _session_owner_hash(email: str) -> str:
    return hashlib.sha256(_user_key(email).encode()).hexdigest()[:12]


async def _reset_browser_state(email: str):
    key = _user_key(email)
    context = _PW_CONTEXTS.pop(key, None)
    browser = _PW_BROWSERS.pop(key, None)
    instance = _PW_INSTANCES.pop(key, None)
    try:
        if context:
            await context.close()
    except Exception:
        pass
    try:
        if browser:
            await browser.close()
    except Exception:
        pass
    try:
        if instance:
            await instance.__aexit__(None, None, None)
    except Exception:
        pass


async def _ensure_browser(email: str):
    key = _user_key(email)
    if not key:
        raise RuntimeError("Authenticated user email is required for Fidelity session isolation")
    context = _PW_CONTEXTS.get(key)
    browser = _PW_BROWSERS.get(key)
    if context is not None:
        # Check browser is still alive before returning cached context
        try:
            if browser and browser.is_connected():
                return context
        except Exception:
            pass
        # Browser dead — reset and fall through to create a new one
        await _reset_browser_state(key)
    from playwright.async_api import async_playwright
    instance = await async_playwright().__aenter__()
    _PW_INSTANCES[key] = instance
    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--ignore-certificate-errors",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    hidden_args = launch_args + [
        "--window-position=-32000,-32000",  # off-screen, not visible to user
        "--window-size=1280,900",
    ]
    # Try system Edge/Chrome headless first (less detectable than bundled Chromium)
    try:
        browser = await instance.chromium.launch(
            channel="msedge", headless=True, args=launch_args
        )
    except Exception:
        try:
            browser = await instance.chromium.launch(
                channel="chrome", headless=True, args=launch_args
            )
        except Exception:
            # Bundled Chromium — Fidelity blocks headless, run headed but off-screen
            browser = await instance.chromium.launch(
                headless=False, args=hidden_args
            )
    _PW_BROWSERS[key] = browser
    storage_path = _fidelity_state_path(key)
    storage_tmp = encrypted_temp_file(storage_path, _FIDELITY_STORAGE_PURPOSE) if storage_path.exists() else None
    context_kwargs = {
        "viewport": {"width": 1280, "height": 900},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "java_script_enabled": True,
        "accept_downloads": False,
    }
    if storage_tmp:
        context_kwargs["storage_state"] = storage_tmp
    try:
        context = await browser.new_context(**context_kwargs)
    finally:
        if storage_tmp:
            try:
                Path(storage_tmp).unlink()
            except Exception:
                pass
    if storage_path.exists() and not is_encrypted_path(storage_path):
        await _save_context_storage(context, storage_path)
    # Suppress automation flags
    await context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    _PW_CONTEXTS[key] = context
    return context


async def _save_storage(email: str):
    key = _user_key(email)
    context = _PW_CONTEXTS.get(key)
    if context:
        path = _fidelity_state_path(key)
        await _save_context_storage(context, path)


async def _save_context_storage(context, path: Path):
    fd, tmp_name = tempfile.mkstemp(prefix="fidelity-storage-", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        await context.storage_state(path=str(tmp_path))
        write_encrypted(path, tmp_path.read_bytes(), _FIDELITY_STORAGE_PURPOSE)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


async def _is_logged_in(email: str) -> bool:
    key = _user_key(email)
    if _PW_CONTEXTS.get(key) is None:
        if not _fidelity_state_path(key).exists():
            return False
        try:
            await _ensure_browser(key)
        except Exception:
            return False
    try:
        ctx = await _ensure_browser(key)
        page = await ctx.new_page()
        await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(2)
        url = page.url
        await page.close()
        return "login" not in url.lower() and "digital.fidelity" in url
    except Exception:
        return False


async def _close_session(email: str):
    key = _user_key(email)
    await _reset_browser_state(key)
    path = _fidelity_state_path(key)
    if path.exists():
        path.unlink()


# ── Auth WebSocket ─────────────────────────────────────────────

async def _try_fill(page, selectors: list, value: str, timeout: int = 3000) -> bool:
    """Try multiple selectors, fill the first visible one."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.fill(value)
            return True
        except Exception:
            continue
    return False


async def _try_click(page, selectors: list, timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def _detect_page_state(page) -> str:
    """Classify what Fidelity page we're on after a login step."""
    await asyncio.sleep(2)
    url = page.url.lower()
    html = (await page.content()).lower()

    if "portfolio" in url or "accounts" in url:
        return "authenticated"
    if any(k in url for k in ("twofactor", "mfa", "verify", "otp", "2fa")):
        return "need_totp"
    if any(k in html for k in ("verification code", "one-time", "authenticator", "security code", "enter the code")):
        return "need_totp"
    if "login" in url or "username" in html or "sign in" in html:
        return "login_page"
    if any(k in html for k in ("incorrect", "invalid", "failed", "error")):
        return "login_error"
    # Landed somewhere else — could be home/dashboard
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname or ""
    if (hostname == "fidelity.com" or hostname.endswith(".fidelity.com")) and "login" not in url:
        return "authenticated"
    return "unknown"


@router.websocket("/ws/fidelity-auth")
async def ws_fidelity_auth(websocket: WebSocket):
    """Drive Fidelity login. Pauses for TOTP when required."""
    await websocket.accept()
    # ── Admin auth gate (Cloudflare Access JWT verified) ──
    from web.auth import ws_require_admin
    _ws_user = await ws_require_admin(websocket)
    if _ws_user is None:
        return
    user_email = _ws_user["email"]

    async def send(data: dict):
        try:
            await websocket.send_json(data)
        except Exception:
            pass

    try:
        creds = await websocket.receive_json()
    except Exception as e:
        await send({"step": "error", "message": str(e)})
        await websocket.close()
        return

    username = creds.get("username", "").strip()
    password = creds.get("password", "")
    if not username or not password:
        await send({"step": "error", "message": "username and password required"})
        await websocket.close()
        return

    await send({"step": "logging_in", "message": "Starting browser…"})

    try:
        await _reset_browser_state(user_email)
        ctx = await _ensure_browser(user_email)
        page = await ctx.new_page()

        await send({"step": "logging_in", "message": "Navigating to Fidelity login…"})
        await page.goto(LOGIN_URL, wait_until="commit", timeout=60_000)
        await asyncio.sleep(4)

        # --- Username ---
        await send({"step": "logging_in", "message": "Entering username…"})
        filled_user = await _try_fill(page, [
            "#dom-username-input",
            "input[name='userId']",
            "input[id*='username' i]",
            "input[placeholder*='username' i]",
            "input[type='text']",
        ], username, timeout=8000)

        if not filled_user:
            await send({"step": "error", "message": "Could not find username field. Fidelity may have changed their login page."})
            await page.close()
            return

        # Click Next / Continue if needed (some flows split username + password)
        next_clicked = await _try_click(page, [
            "button[data-testid='nextBtn']",
            "button[id*='next' i]",
            "#dom-username-go-button",
            "button[type='submit']",
        ], timeout=3000)
        if next_clicked:
            await asyncio.sleep(1.5)

        # --- Password ---
        await send({"step": "logging_in", "message": "Entering password…"})
        filled_pw = await _try_fill(page, [
            "#dom-pswd-input",
            "input[name='password']",
            "input[type='password']",
            "input[id*='password' i]",
        ], password, timeout=8000)

        if not filled_pw:
            await send({"step": "error", "message": "Could not find password field."})
            await page.close()
            return

        await _try_click(page, [
            "#dom-login-button",
            "button[data-testid='loginBtn']",
            "button[type='submit']",
            "#fs-login-button",
        ], timeout=5000)

        await asyncio.sleep(3)
        state = await _detect_page_state(page)

        # --- TOTP / 2FA ---
        if state == "need_totp":
            await send({
                "step": "need_totp",
                "message": "Two-factor authentication required.",
                "prompt": "Enter the 6-digit code from your authenticator app or SMS",
            })

            # Wait for TOTP code from client (up to 3 minutes)
            try:
                totp_msg = await asyncio.wait_for(websocket.receive_json(), timeout=180)
            except asyncio.TimeoutError:
                await send({"step": "error", "message": "Timed out waiting for verification code (3 min limit)"})
                await page.close()
                return

            code = str(totp_msg.get("totp", "")).strip()
            if not code:
                await send({"step": "error", "message": "No verification code provided"})
                await page.close()
                return

            await send({"step": "logging_in", "message": "Submitting verification code…"})

            filled_otp = await _try_fill(page, [
                "input[name='OTP']",
                "input[id*='otp' i]",
                "input[id*='totp' i]",
                "input[placeholder*='code' i]",
                "input[type='number']",
                "input[maxlength='6']",
                "input[maxlength='8']",
                "input[type='text']",
            ], code, timeout=8000)

            if not filled_otp:
                await send({"step": "error", "message": "Could not find verification code input field."})
                await page.close()
                return

            # Click submit / continue
            await _try_click(page, [
                "button[type='submit']",
                "button[data-testid='submitBtn']",
                "button[id*='continue' i]",
                "button[id*='submit' i]",
                "button[id*='verify' i]",
            ], timeout=5000)

            await asyncio.sleep(3)
            state = await _detect_page_state(page)

        # --- Handle "remember device" prompt ---
        if state not in ("authenticated",):
            remember_clicked = await _try_click(page, [
                "button[data-testid='rememberDeviceBtn']",
                "button[id*='remember' i]",
                "button[id*='trust' i]",
            ], timeout=2000)
            if remember_clicked:
                await asyncio.sleep(2)
                state = await _detect_page_state(page)

        if state == "login_error":
            html_snippet = await page.locator("body").inner_text()
            err_lines = [l.strip() for l in html_snippet.splitlines() if any(k in l.lower() for k in ("incorrect","invalid","failed","error","wrong"))]
            await send({"step": "error", "message": "Login failed: " + (err_lines[0] if err_lines else "incorrect credentials")})
            await page.close()
            return

        if state != "authenticated":
            # Try navigating to portfolio directly
            await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
            state = await _detect_page_state(page)

        if state != "authenticated":
            await send({"step": "error", "message": f"Login did not complete. Current URL: {page.url}"})
            await page.close()
            return

        await _save_storage(user_email)
        await page.close()

        await send({"step": "authenticated", "message": "Connected to Fidelity successfully"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        await send({"step": "error", "message": str(e), "traceback": traceback.format_exc()})


# ── REST endpoints (require active session) ────────────────────

@router.get("/fidelity/status")
async def fidelity_status(user: dict = Depends(require_admin)):
    email = user["email"]
    path = _fidelity_state_path(email)
    connected = await _is_logged_in(email)
    return {
        "connected": connected,
        "session_file": path.exists(),
        "session_encrypted": is_encrypted_path(path),
        "session_scope": "per_user",
        "session_owner_hash": _session_owner_hash(email),
        "legacy_session_file": _LEGACY_STORAGE_STATE.exists(),
    }


@router.post("/fidelity/logout")
async def fidelity_logout(admin: dict = Depends(require_admin)):
    await _close_session(admin["email"])
    return {"success": True}


async def _nav(page, url: str, sleep: float = 5.0):
    """Navigate then wait for JS to settle."""
    try:
        await page.goto(url, wait_until="commit", timeout=60_000)
    except Exception:
        pass
    await asyncio.sleep(sleep)


@router.get("/fidelity/debug-html")
async def fidelity_debug_html(admin: dict = Depends(require_admin)):
    """Return page URL + first 8000 chars of body text for scraping diagnosis."""
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        html = await page.content()
        body_text = await page.evaluate("() => document.body?.innerText?.slice(0,8000) || ''")
        return {"url": page.url, "html_snippet": html[:6000], "body_text": body_text}
    finally:
        await page.close()


@router.get("/fidelity/debug-grid")
async def fidelity_debug_grid(admin: dict = Depends(require_admin)):
    """Dump AG-Grid col-id structure to find exact field names."""
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        grid_data = await page.evaluate("""
        () => {
            const rows = [];
            document.querySelectorAll('.ag-row[row-index]').forEach(row => {
                const ri = row.getAttribute('row-index');
                const cells = {};
                row.querySelectorAll('[col-id]').forEach(cell => {
                    cells[cell.getAttribute('col-id')] = cell.innerText.trim().slice(0, 60);
                });
                if (Object.keys(cells).length) rows.push({row_index: ri, cells});
            });
            // Also check pinned left container
            const pinLeft = [];
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const cells = {};
                row.querySelectorAll('[col-id]').forEach(cell => {
                    cells[cell.getAttribute('col-id')] = cell.innerText.trim().slice(0, 60);
                });
                if (Object.keys(cells).length) pinLeft.push({row_index: row.getAttribute('row-index'), cells});
            });
            return {rows, pinLeft, totalRows: rows.length};
        }
        """)
        return grid_data
    finally:
        await page.close()


@router.get("/fidelity/positions")
async def fidelity_positions(admin: dict = Depends(require_admin)):
    from fastapi import HTTPException
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)

        if "login" in page.url.lower():
            raise HTTPException(status_code=401, detail="Not authenticated with Fidelity")

        try:
            await page.wait_for_selector(
                '.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000
            )
        except Exception:
            pass
        await asyncio.sleep(2)

        result = await page.evaluate("""
        () => {
            const SKIP = ['Cash', 'Pending', 'Account:', 'Grand', 'HELD'];

            // Build sym map: row-index → symbol text (pinned-left, col-id="sym")
            const symMap = {};
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const cell = row.querySelector('[col-id="sym"]');
                if (cell) symMap[row.getAttribute('row-index')] = cell.innerText.trim();
            });

            // Build data map: row-index → {col-id: text} (center container)
            const dataMap = {};
            document.querySelectorAll('.ag-center-cols-container .ag-row[row-index]').forEach(row => {
                const cells = {};
                row.querySelectorAll('[col-id]').forEach(cell => {
                    cells[cell.getAttribute('col-id')] = cell.innerText.trim();
                });
                if (Object.keys(cells).length >= 2) dataMap[row.getAttribute('row-index')] = cells;
            });

            const positions = [];
            const grandTotals = {};

            Object.entries(symMap).forEach(([ri, symText]) => {
                const lines = symText.split('\\n').map(l => l.trim()).filter(Boolean);
                const ticker = lines[0] || '';

                // Capture grand total
                if (ticker.startsWith('Grand total')) {
                    const d = dataMap[ri] || {};
                    grandTotals.total_value = (d.curVal || '').split('\\n')[0];
                    const todLines = (d.todGLStk || '').split('\\n').filter(l => l && !/Not Priced/.test(l));
                    grandTotals.daily_change = todLines[0] || '';
                    grandTotals.daily_change_pct = todLines[1] || '';
                    return;
                }

                // Skip non-position rows
                if (!ticker || ticker.length > 10 || !/^[A-Z]/.test(ticker) ||
                    SKIP.some(s => ticker.startsWith(s))) return;

                const desc = lines.find((l, i) => i > 0 && l.length > 2 && !/^Not Priced|^\\$|^[+-]/.test(l)) || '';
                const d = dataMap[ri] || {};

                const lstLines = (d.lstPrStk || '').split('\\n');
                const todLines = (d.todGLStk || '').split('\\n').filter(l => l && !/Not Priced/.test(l));
                const totLines = (d.totGLStk || '').split('\\n');
                const cstLines = (d.cstBasStk || '').split('\\n');

                positions.push({
                    symbol:          ticker,
                    description:     desc,
                    last_price:      lstLines[0] || '',
                    today_gain_loss: todLines[0] || '',
                    today_gain_pct:  todLines[1] || '',
                    total_gain_loss: totLines[0] || '',
                    total_gain_pct:  totLines[1] || '',
                    market_value:    (d.curVal || '').split('\\n')[0],
                    pct_of_account:  d.actPer || '',
                    qty:             d.qty || '',
                    cost_basis:      cstLines[0] || '',
                    cost_per_share:  cstLines[1] || '',
                });
            });

            return { positions, grandTotals };
        }
        """)

        await _save_storage(admin["email"])
        positions = result.get("positions", [])
        grand = result.get("grandTotals", {})
        return {"positions": positions, "grand_totals": grand, "url": page.url, "count": len(positions)}
    finally:
        await page.close()


@router.get("/fidelity/summary")
async def fidelity_summary(admin: dict = Depends(require_admin)):
    """Pull summary from positions page (grand total row) — no separate navigation."""
    from fastapi import HTTPException
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)

        if "login" in page.url.lower():
            raise HTTPException(status_code=401, detail="Not authenticated with Fidelity")

        try:
            await page.wait_for_selector(
                '.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000
            )
        except Exception:
            pass
        await asyncio.sleep(2)

        summary = await page.evaluate("""
        () => {
            let total_value = null, daily_change = null, daily_change_pct = null;
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const symText = row.querySelector('[col-id="sym"]')?.innerText?.trim() || '';
                if (!symText.startsWith('Grand total')) return;
                const ri = row.getAttribute('row-index');
                const centerRow = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${ri}"]`);
                if (!centerRow) return;
                total_value = (centerRow.querySelector('[col-id="curVal"]')?.innerText?.trim() || '').split('\\n')[0] || null;
                const todLines = (centerRow.querySelector('[col-id="todGLStk"]')?.innerText?.trim() || '')
                    .split('\\n').filter(l => l && !/Not Priced/.test(l));
                daily_change = todLines[0] || null;
                daily_change_pct = todLines[1] || null;
            });
            return { total_value, daily_change, daily_change_pct };
        }
        """)

        await _save_storage(admin["email"])
        return {"summary": summary, "url": page.url}
    finally:
        await page.close()


@router.get("/fidelity/screenshot")
async def fidelity_screenshot(admin: dict = Depends(require_admin)):
    """Return base64 screenshot of current Fidelity page (debug)."""
    import base64
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
        img = await page.screenshot(type="png")
        encoded = base64.b64encode(img).decode()
        return {"image_b64": encoded, "url": page.url}
    finally:
        await page.close()

class FidelityTradeRequest(BaseModel):
    symbol: str
    action: str = Field(..., description="Buy, Sell")
    quantity: float
    order_type: str = Field("Limit", description="Market, Limit")
    limit_price: Optional[float] = None
    time_in_force: str = Field("Day")
    account: Optional[str] = None
    execute: bool = False

@router.post("/fidelity/trade")
async def fidelity_trade(body: FidelityTradeRequest, admin: dict = Depends(require_step_up)):
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED
    # ── Compliance hard block ──────────────────────────────────────────────────
    # Two independent gates must both pass before any real order is placed:
    #   1. LIVE_TRADING_HARD_BLOCKED (source-code constant) — absolute kill switch
    #   2. LIVE_TRADING_ENABLED (.env toggle, default off) — operational switch
    if LIVE_TRADING_HARD_BLOCKED:
        raise HTTPException(
            status_code=403,
            detail=(
                "Live trading is hard-blocked in compliance.py. "
                "Set LIVE_TRADING_HARD_BLOCKED = False in source to enable, "
                "then also set LIVE_TRADING_ENABLED=true in .env."
            ),
        )
    decision = validate_live_order(body.model_dump())
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    if not live_trading_enabled():
        raise HTTPException(
            status_code=403,
            detail="Live trading is disabled. Set LIVE_TRADING_ENABLED=true in .env to enable.",
        )
    # ── End compliance check ───────────────────────────────────────────────────
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        # Navigate to Trade Entry
        await _nav(page, "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry", sleep=5)

        if "login" in page.url.lower():
            raise HTTPException(status_code=401, detail="Not authenticated with Fidelity")

        # Select Account (if provided)
        if body.account:
            try:
                await page.locator('#dest-acct-dropdown').click()
                await asyncio.sleep(1)
                await page.locator(f'.dropdown-menu li:has-text("{body.account}")').first.click()
                await asyncio.sleep(1)
            except Exception:
                pass

        # Symbol — confirmed ID from live page inspection
        try:
            sym = page.locator('#eq-ticket-dest-symbol')
            await sym.wait_for(state="visible", timeout=10000)
            await sym.click()
            await sym.fill(body.symbol)
            await asyncio.sleep(1.5)
            # Accept first autocomplete suggestion
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(2)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to enter symbol: {e}")

        # Action — click dropdown button then find option by text or keyboard
        try:
            await page.locator('#dest-dropdownlist-button-action').click()
            await asyncio.sleep(1)
            # Try visible list items / options first
            clicked = False
            for sel in [
                f'[role="option"]:has-text("{body.action}")',
                f'[role="listitem"]:has-text("{body.action}")',
                f'li:has-text("{body.action}")',
                f'a:has-text("{body.action}")',
                f'span:has-text("{body.action}")',
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                # Keyboard fallback: Buy=first option, Sell=second
                presses = 1 if body.action.lower() == "buy" else 2
                for _ in range(presses):
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
            await asyncio.sleep(0.8)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to select action '{body.action}': {e}")

        # Quantity — label overlays the input, use JS to set + trigger Angular events
        try:
            qty_val = str(int(body.quantity))
            await page.evaluate(f"""
            () => {{
                const el = document.getElementById('eqt-shared-quantity');
                if (!el) throw new Error('qty input not found');
                el.focus();
                const nativeInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                nativeInput.set.call(el, '{qty_val}');
                el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
            }}
            """)
            await asyncio.sleep(0.8)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to enter quantity: {e}")

        # Order Type — dropdown button
        try:
            await page.locator('#dest-dropdownlist-button-ordertype').click()
            await asyncio.sleep(1)
            clicked = False
            for sel in [
                f'[role="option"]:has-text("{body.order_type}")',
                f'[role="listitem"]:has-text("{body.order_type}")',
                f'li:has-text("{body.order_type}")',
                f'a:has-text("{body.order_type}")',
                f'span:has-text("{body.order_type}")',
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                # Keyboard fallback: Limit=1st, Market=2nd (typical Fidelity order)
                presses = 1 if body.order_type.lower() == "limit" else 2
                for _ in range(presses):
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
            await asyncio.sleep(0.8)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to select order type '{body.order_type}': {e}")

        # Limit Price (input appears after selecting Limit order type)
        if body.order_type.lower() == "limit" and body.limit_price is not None:
            try:
                price_input = page.locator('input[id*="price" i], input[name*="price" i], input[class*="price" i]').first
                await price_input.wait_for(state="visible", timeout=5000)
                await price_input.click(click_count=3)
                await price_input.fill(str(body.limit_price))
                await asyncio.sleep(0.8)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to enter limit price: {e}")

        # Preview Order — only full-width primary button on page
        try:
            preview_btn = page.locator('button.pvd-button--primary.pvd-button--full-width, button:has-text("Preview order"), button:has-text("Preview Order")').first
            await preview_btn.wait_for(state="visible", timeout=8000)
            await preview_btn.click()
            await asyncio.sleep(5)
        except Exception as e:
            err_text = ""
            try:
                err_text = await page.locator('.pvd-inline-alert, .message-error, .alert-error').first.inner_text()
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=f"Failed to click Preview: {err_text or str(e)}")

        # Check for preview warnings/errors on page
        preview_text = await page.evaluate("() => document.body.innerText")

        # Place Order
        order_status = "previewed"
        if body.execute:
            try:
                place_btn = page.locator('button:has-text("Place Order")').first
                await place_btn.click()
                await asyncio.sleep(5)
                order_status = "executed"
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to place order: {e}")

        await _save_storage(admin["email"])
        return {"success": True, "status": order_status, "preview_text_snippet": preview_text[:1000]}

    finally:
        await page.close()


@router.get("/fidelity/debug-trade")
async def fidelity_debug_trade(admin: dict = Depends(require_admin)):
    """Navigate to trade entry page and dump all input/button/select elements for selector diagnosis."""
    page = None
    current_url = "n/a"
    try:
        ctx = await _ensure_browser(admin["email"])
        page = await ctx.new_page()
        await _nav(page, "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry", sleep=7)
        current_url = page.url
        if "login" in current_url.lower():
            return {"error": "Not authenticated — log in via Fidelity panel first", "url": current_url, "elements": [], "body_snippet": ""}
        # Safe JS — all values coerced to strings, className handled for SVG elements
        elements = await page.evaluate("""
        () => {
            const safe = v => { try { return String(v || '').slice(0, 80); } catch(e) { return ''; } };
            const out = [];
            document.querySelectorAll('input, select, button, [role="combobox"], [role="textbox"]').forEach(el => {
                try {
                    out.push({
                        tag: safe(el.tagName),
                        id: safe(el.id),
                        name: safe(el.name),
                        type: safe(el.type),
                        placeholder: safe(el.placeholder),
                        ariaLabel: safe(el.getAttribute('aria-label')),
                        ariaPlaceholder: safe(el.getAttribute('aria-placeholder')),
                        role: safe(el.getAttribute('role')),
                        className: safe(typeof el.className === 'string' ? el.className : el.className?.baseVal),
                        dataTestId: safe(el.getAttribute('data-testid')),
                        innerText: safe(el.innerText),
                        visible: el.offsetParent !== null,
                    });
                } catch(e2) {}
            });
            return out;
        }
        """)
        body_text = await page.evaluate("() => { try { return document.body.innerText.slice(0, 3000); } catch(e) { return ''; } }")
        return {"url": current_url, "elements": elements, "body_snippet": body_text}
    except Exception as e:
        import logging; logging.exception("Fidelity snapshot failed"); return {"error": "An internal error occurred", "url": current_url, "elements": [], "body_snippet": ""}
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
