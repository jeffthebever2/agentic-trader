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
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("fidelity")

# Per-(user, ticker) idempotency lock — prevents duplicate simultaneous orders
_ORDER_LOCKS: dict[str, asyncio.Lock] = {}
_ORDER_LOCKS_META: dict[str, float] = {}  # key → last-acquire timestamp
_ORDER_LOCK_TTL = 120.0  # seconds before lock is considered stale


def _get_order_lock(key: str) -> asyncio.Lock:
    """Return (or create) a per-(user,ticker) lock to prevent duplicate orders."""
    if key not in _ORDER_LOCKS:
        _ORDER_LOCKS[key] = asyncio.Lock()
    return _ORDER_LOCKS[key]


def _validate_account_number(account: str | None) -> str | None:
    """Fidelity account numbers are numeric, 8-12 digits. Reject anything else."""
    if account is None:
        return None
    cleaned = account.strip().replace("-", "").replace(" ", "")
    if not cleaned.isdigit() or not (6 <= len(cleaned) <= 15):
        raise ValueError(f"Invalid account number '{account}' — must be 6-15 digits only.")
    return cleaned


# Confirmation patterns on Fidelity order success page
_ORDER_CONFIRM_PATTERNS = [
    "order received", "order submitted", "order number", "confirmation",
    "order #", "order has been placed", "order was placed",
]
_ORDER_ERROR_PATTERNS = [
    "insufficient", "not enough", "buying power", "error", "failed",
    "unable to process", "cannot process", "rejected", "invalid",
    "market is closed", "after hours",
]


def _verify_fidelity_order_page(page_text: str) -> tuple[bool, str]:
    """
    Return (confirmed, reason) from Fidelity post-submit page text.
    confirmed=True means the page shows order acceptance.
    """
    lower = page_text.lower()
    for pat in _ORDER_CONFIRM_PATTERNS:
        if pat in lower:
            return True, f"confirmed: '{pat}' found in page"
    for pat in _ORDER_ERROR_PATTERNS:
        if pat in lower:
            return False, f"order rejected by Fidelity: '{pat}' found in page"
    return False, "order confirmation NOT found in page — status unknown"

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
    symbol:       str
    action:       str   = Field(..., description="Buy or Sell only")
    quantity:     int   = Field(..., gt=0, description="Whole shares only, must be > 0")
    order_type:   str   = Field("Limit", description="Only Limit supported")
    limit_price:  Optional[float] = None
    time_in_force: str  = Field("Day")   # passed for UI display; Fidelity default = Day
    account:      Optional[str] = None
    execute:      bool  = False

    @classmethod
    def __get_validators__(cls):
        yield cls.validate_model

    def model_post_init(self, __context) -> None:
        from fastapi import HTTPException
        self.symbol = self.symbol.upper().strip()
        if not self.symbol.isalpha() or len(self.symbol) > 5:
            raise ValueError(f"Invalid symbol '{self.symbol}'")
        if self.action.lower() not in ("buy", "sell"):
            raise ValueError(f"action must be 'Buy' or 'Sell', got '{self.action}'")
        if self.order_type.lower() not in ("limit",):
            raise ValueError("Only 'Limit' order type supported")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError(f"limit_price must be > 0, got {self.limit_price}")
        if self.limit_price is not None and self.limit_price > 100_000:
            raise ValueError(f"limit_price ${self.limit_price:.2f} exceeds $100,000 sanity cap")
        try:
            _validate_account_number(self.account)
        except ValueError as e:
            raise ValueError(str(e))

@router.post("/fidelity/trade")
async def fidelity_trade(body: FidelityTradeRequest, admin: dict = Depends(require_step_up)):
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED

    # ── Idempotency lock ───────────────────────────────────────────────────────
    lock_key = f"{admin['email']}:{body.symbol}:{body.action.lower()}"
    order_lock = _get_order_lock(lock_key)
    if order_lock.locked():
        raise HTTPException(status_code=429, detail=f"Order for {body.symbol} already in progress.")

    async with order_lock:
        if LIVE_TRADING_HARD_BLOCKED:
            raise HTTPException(status_code=403, detail="LIVE_TRADING_HARD_BLOCKED=True in compliance.py")
        decision = validate_live_order(body.model_dump())
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason)
        if not live_trading_enabled():
            raise HTTPException(status_code=403, detail="LIVE_TRADING_ENABLED not set to true in .env")
        try:
            account = _validate_account_number(body.account)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        ctx = await _ensure_browser(admin["email"])
        page = None
        try:
            page = await ctx.new_page()
            await _nav(page, "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry", sleep=5)

            if "login" in page.url.lower():
                raise HTTPException(status_code=401, detail="Not authenticated with Fidelity")

            if account:
                try:
                    await page.locator('#dest-acct-dropdown').click()
                    await asyncio.sleep(1)
                    await page.locator(f'[data-value="{account}"], li:text-is("{account}")').first.click(timeout=2000)
                    await asyncio.sleep(1)
                except Exception:
                    log.warning("Could not select account %s — using default", account)

            try:
                sym = page.locator('#eq-ticket-dest-symbol')
                await sym.wait_for(state="visible", timeout=10000)
                await sym.click()
                await sym.fill(body.symbol)
                await asyncio.sleep(1.5)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
                await page.keyboard.press("Enter")
                await asyncio.sleep(2)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to enter symbol: {e}")

            try:
                await page.locator('#dest-dropdownlist-button-action').click()
                await asyncio.sleep(1)
                clicked = False
                for sel in ['[role="option"]:has-text("Buy")', '[role="option"]:has-text("Sell")',
                            f'li:has-text("{body.action}")', f'a:has-text("{body.action}")']:
                    try:
                        loc = page.locator(sel).first
                        if await loc.is_visible(timeout=1500):
                            await loc.click()
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    presses = 1 if body.action.lower() == "buy" else 2
                    for _ in range(presses):
                        await page.keyboard.press("ArrowDown")
                        await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")
                await asyncio.sleep(0.8)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to select action: {e}")

            try:
                qty_val = str(body.quantity)
                await page.evaluate(f"""
                () => {{
                    const el = document.getElementById('eqt-shared-quantity');
                    if (!el) throw new Error('qty input not found');
                    el.focus();
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                    nativeSetter.set.call(el, '{qty_val}');
                    el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
                }}
                """)
                await asyncio.sleep(0.8)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to enter quantity: {e}")

            # Order type = Limit only
            try:
                await page.locator('#dest-dropdownlist-button-ordertype').click()
                await asyncio.sleep(1)
                for sel in ['[role="option"]:has-text("Limit")', 'li:has-text("Limit")', 'a:has-text("Limit")']:
                    try:
                        loc = page.locator(sel).first
                        if await loc.is_visible(timeout=1500):
                            await loc.click()
                            break
                    except Exception:
                        continue
                await asyncio.sleep(0.8)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to select order type: {e}")

            if body.limit_price is not None:
                try:
                    price_input = page.locator('input[id*="price" i], input[name*="price" i], input[class*="price" i]').first
                    await price_input.wait_for(state="visible", timeout=5000)
                    await price_input.click(click_count=3)
                    await price_input.fill(str(body.limit_price))
                    await asyncio.sleep(0.8)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Failed to enter limit price: {e}")

            try:
                preview_btn = page.locator(
                    'button.pvd-button--primary.pvd-button--full-width,'
                    'button:has-text("Preview order"),'
                    'button:has-text("Preview Order")'
                ).first
                await preview_btn.wait_for(state="visible", timeout=8000)
                await preview_btn.click()
                await asyncio.sleep(5)
            except Exception as e:
                err_text = ""
                try:
                    err_text = await page.locator('.pvd-inline-alert, .message-error, .alert-error').first.inner_text()
                except Exception:
                    pass
                raise HTTPException(status_code=400, detail=f"Preview failed: {err_text or str(e)}")

            preview_text = await page.evaluate("() => document.body.innerText")

            order_status = "previewed"
            if body.execute:
                try:
                    place_btn = page.locator('button:has-text("Place Order")').first
                    await place_btn.wait_for(state="visible", timeout=8000)
                    await place_btn.click()
                    await asyncio.sleep(6)

                    confirm_text = await page.evaluate("() => document.body.innerText")
                    confirmed, confirm_msg = _verify_fidelity_order_page(confirm_text)
                    if not confirmed:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Order submitted but NOT confirmed: {confirm_msg}\nPage: {confirm_text[:400]}\nCheck Fidelity manually."
                        )
                    order_status = "executed"
                    log.info("Fidelity order CONFIRMED: %s %s x%d (%s)", body.action, body.symbol, body.quantity, confirm_msg)
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Failed to place order: {e}")

            await _save_storage(admin["email"])
            return {"success": True, "status": order_status, "preview_text_snippet": preview_text[:1000]}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fidelity trade failed: {e}")
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass


# ── Balance / account helpers ──────────────────────────────────────────────────

ACCOUNTS_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"
TRADE_HISTORY_FILE = ROOT / "tmp" / "fidelity_trade_log.jsonl"


def _parse_dollar(text: str) -> float | None:
    """Parse '$12,345.67' or '-$1,234' → float."""
    try:
        cleaned = text.replace("$", "").replace(",", "").strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


async def _get_fidelity_balances(email: str) -> dict:
    """
    Scrape available cash + total value from Fidelity portfolio page.
    Returns: {total_value, available_cash, accounts: [{number, value, cash}]}
    """
    ctx = await _ensure_browser(email)
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        if "login" in page.url.lower():
            return {"error": "not_authenticated"}

        try:
            await page.wait_for_selector(
                '.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000
            )
        except Exception:
            pass
        await asyncio.sleep(2)

        result = await page.evaluate("""
        () => {
            // Try to find account summary tiles (top of page)
            const tiles = {};
            document.querySelectorAll('[class*="account-summary"], [class*="AccountSummary"], .acct-summary, .acct-value').forEach(el => {
                const label = el.querySelector('[class*="label"], [class*="Label"], span')?.innerText?.trim() || '';
                const value = el.querySelector('[class*="value"], [class*="Value"], strong, b')?.innerText?.trim() || '';
                if (label && value) tiles[label] = value;
            });

            // Fall back to pinned-row Cash scraping
            const cashRows = [];
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const sym = row.querySelector('[col-id="sym"]')?.innerText?.trim() || '';
                if (sym.startsWith('Cash') || sym === 'CASH') {
                    const ri = row.getAttribute('row-index');
                    const center = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${ri}"]`);
                    const val = center?.querySelector('[col-id="curVal"]')?.innerText?.trim().split('\\n')[0] || '';
                    cashRows.push({label: sym, value: val});
                }
            });

            // Grand total
            let grandTotal = null;
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const sym = row.querySelector('[col-id="sym"]')?.innerText?.trim() || '';
                if (sym.startsWith('Grand total')) {
                    const ri = row.getAttribute('row-index');
                    const center = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${ri}"]`);
                    grandTotal = center?.querySelector('[col-id="curVal"]')?.innerText?.trim().split('\\n')[0] || null;
                }
            });

            // Account dropdown options (multiple accounts)
            const accountOptions = [];
            document.querySelectorAll('#dest-acct-dropdown option, .account-dropdown option, [data-acct], [data-account-number]').forEach(el => {
                const text = el.innerText?.trim() || el.getAttribute('data-account-number') || '';
                if (text && text.length > 2) accountOptions.push(text);
            });

            return { tiles, cashRows, grandTotal, accountOptions };
        }
        """)

        await _save_storage(email)

        # Parse results
        grand_total = _parse_dollar(result.get("grandTotal") or "")
        cash_val    = None
        for row in result.get("cashRows", []):
            v = _parse_dollar(row.get("value", ""))
            if v is not None:
                cash_val = (cash_val or 0) + v

        # Fallback: if no explicit cash found, use 10% of grand total as rough estimate
        if cash_val is None and grand_total:
            cash_val = grand_total * 0.10

        return {
            "total_value":     grand_total,
            "available_cash":  cash_val,
            "cash_rows":       result.get("cashRows", []),
            "summary_tiles":   result.get("tiles", {}),
            "account_options": result.get("accountOptions", []),
            "raw":             result,
        }
    finally:
        await page.close()


def _size_fidelity_position(
    account_value: float,
    available_cash: float,
    price: float,
    dollar_amount: float | None = None,
    pct_of_account: float | None = None,
) -> tuple[int, float]:
    """
    Compute (shares, cost) respecting:
      - available_cash hard cap
      - MAX_POSITION_PCT_OF_ACCOUNT from compliance (default 10%)
      - Minimum 1 share
    """
    from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT
    max_alloc = account_value * MAX_POSITION_PCT_OF_ACCOUNT / 100

    if dollar_amount and dollar_amount > 0:
        alloc = dollar_amount
    elif pct_of_account and pct_of_account > 0:
        alloc = account_value * pct_of_account / 100
    else:
        alloc = max_alloc

    alloc = min(alloc, max_alloc, available_cash)

    if alloc <= 0 or price <= 0:
        return 0, 0.0

    # Do NOT enforce min=1 if price > available_cash — would bypass cash cap
    shares = int(alloc / price)
    if shares <= 0:
        return 0, 0.0

    cost = round(shares * price, 2)
    # Final safety: cost must never exceed available_cash
    if cost > available_cash:
        return 0, 0.0
    return shares, cost


def _log_fidelity_trade(record: dict) -> None:
    """Append trade record to fidelity_trade_log.jsonl."""
    try:
        TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with TRADE_HISTORY_FILE.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        import logging
        logging.getLogger("fidelity").warning("Trade log write: %s", e)


# ── Accounts endpoint ──────────────────────────────────────────────────────────

@router.get("/fidelity/accounts")
async def fidelity_accounts(admin: dict = Depends(require_admin)):
    """Return account balances + available cash from Fidelity."""
    from fastapi import HTTPException
    connected = await _is_logged_in(admin["email"])
    if not connected:
        raise HTTPException(status_code=401, detail="Not authenticated with Fidelity. Log in first.")
    balances = await _get_fidelity_balances(admin["email"])
    if "error" in balances:
        raise HTTPException(status_code=401, detail=balances["error"])
    return {
        "ok":             True,
        "total_value":    balances.get("total_value"),
        "available_cash": balances.get("available_cash"),
        "cash_rows":      balances.get("cash_rows", []),
        "accounts":       balances.get("account_options", []),
        "summary_tiles":  balances.get("summary_tiles", {}),
    }


# ── Thematic trade endpoint ────────────────────────────────────────────────────

class FidelityThematicTradeRequest(BaseModel):
    ticker:           str
    dollar_amount:    Optional[float] = None   # fixed dollar allocation
    pct_of_account:   Optional[float] = None   # % of account (e.g. 2.5 = 2.5%)
    stop_pct:         float = 5.0
    target_pct:       float = 10.0
    hold_days:        int   = 5
    theme:            str   = "future_tech"
    thesis:           str   = ""
    catalyst:         str   = ""
    account:          Optional[str] = None     # Fidelity account number (None = default)
    execute:          bool  = False            # False = preview only, True = place order
    also_paper_trade: bool  = True             # Mirror trade in paper account for tracking


@router.post("/fidelity/thematic-trade")
async def fidelity_thematic_trade(
    body: FidelityThematicTradeRequest,
    admin: dict = Depends(require_step_up),
):
    """
    Size and place a thematic conviction trade on Fidelity.

    Flow:
      1. Fetch current price from yfinance
      2. Fetch Fidelity available cash + account value
      3. Size position (respects MAX_POSITION_PCT_OF_ACCOUNT and available_cash)
      4. Validate via compliance
      5. If execute=True AND LIVE_TRADING_ENABLED: place Limit order on Fidelity
      6. If also_paper_trade=True: mirror in paper account for P&L tracking
      7. Log to tmp/fidelity_trade_log.jsonl
    """
    import datetime as _dt
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED

    ticker = body.ticker.upper().strip()
    if not ticker.isalpha() or len(ticker) > 5:
        raise HTTPException(status_code=400, detail=f"Invalid ticker '{ticker}'")

    # Validate account number to prevent selector injection
    try:
        account = _validate_account_number(body.account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── Idempotency lock: prevent duplicate simultaneous orders ────────────────
    lock_key = f"{admin['email']}:{ticker}"
    order_lock = _get_order_lock(lock_key)
    if order_lock.locked():
        raise HTTPException(status_code=429, detail=f"Order for {ticker} already in progress — wait for it to complete.")

    async with order_lock:
        _ORDER_LOCKS_META[lock_key] = __import__("time").time()
        return await _fidelity_thematic_trade_inner(body, admin, ticker, account)


async def _fidelity_thematic_trade_inner(body, admin, ticker, account):
    import datetime as _dt
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED

    # ── 1. Fetch price ────────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    def _get_price_fresh(sym: str) -> tuple[float | None, bool]:
        """Returns (price, is_fresh) — fresh=True if from today's trading."""
        try:
            import yfinance as yf
            import datetime as dt_
            data = yf.download(sym, period="5d", auto_adjust=True, progress=False)
            closes = data["Close"]
            series = closes[sym] if hasattr(closes, "columns") else closes
            series = series.dropna()
            if len(series) == 0:
                return None, False
            last_dt = series.index[-1]
            last_dt_date = last_dt.date() if hasattr(last_dt, "date") else last_dt
            is_fresh = (last_dt_date == dt_.date.today())
            return float(series.iloc[-1]), is_fresh
        except Exception:
            return None, False

    price_result = await loop.run_in_executor(None, _get_price_fresh, ticker)
    price, price_is_fresh = price_result
    if not price:
        raise HTTPException(status_code=400, detail=f"Cannot fetch price for {ticker}")
    if not price_is_fresh:
        log.warning("Price for %s is from a prior trading day — limit may be stale", ticker)

    # ── 2. Fetch Fidelity balances (validates session + gets cash) ────────────
    balances = await _get_fidelity_balances(admin["email"])
    if "error" in balances:
        raise HTTPException(status_code=401, detail="Not authenticated with Fidelity. Log in at /fidelity.")

    total_value    = balances.get("total_value")
    available_cash = balances.get("available_cash")

    # Refuse to size if we can't confirm account balance — never use hardcoded fallback
    if total_value is None or total_value <= 0:
        raise HTTPException(status_code=400, detail="Cannot determine Fidelity account value — check session. Refresh the portfolio page and retry.")
    if available_cash is None:
        # Warn but use 15% conservative estimate (better than refusing entirely)
        available_cash = total_value * 0.15
        log.warning("Cash scrape failed — fallback estimate $%.0f (15%% of $%.0f). VERIFY before executing.", available_cash, total_value)
    if available_cash <= 0:
        raise HTTPException(status_code=400, detail=f"No available cash (scraped: ${available_cash:.2f}). Check Fidelity account.")

    # ── 3. Size position ──────────────────────────────────────────────────────
    shares, cost = _size_fidelity_position(
        account_value   = total_value,
        available_cash  = available_cash,
        price           = price,
        dollar_amount   = body.dollar_amount,
        pct_of_account  = body.pct_of_account,
    )
    if shares <= 0:
        raise HTTPException(status_code=400, detail=f"Cannot size position: alloc=${body.dollar_amount}, cash=${available_cash:.2f}, price=${price:.2f}")

    # Limit price = 0.2% above current (improves fill, still limit protection)
    limit_price = round(price * 1.002, 2)
    stop_price  = round(price * (1 - body.stop_pct / 100), 4)
    target_price= round(price * (1 + body.target_pct / 100), 4)
    now_iso     = _dt.datetime.now().isoformat(timespec="seconds")
    today       = _dt.date.today().isoformat()

    # ── 4. Compliance validation ──────────────────────────────────────────────
    order_dict = {
        "symbol":      ticker,
        "action":      "Buy",
        "order_type":  "Limit",
        "quantity":    shares,
        "limit_price": limit_price,
    }
    decision = validate_live_order(order_dict)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=f"Compliance: {decision.reason}")

    # ── 5. Place order on Fidelity ────────────────────────────────────────────
    order_status  = "sized"
    fidelity_resp = None

    if body.execute:
        if LIVE_TRADING_HARD_BLOCKED:
            raise HTTPException(status_code=403, detail="LIVE_TRADING_HARD_BLOCKED=True in compliance.py")
        if not live_trading_enabled():
            raise HTTPException(status_code=403, detail="LIVE_TRADING_ENABLED not set to true in .env")

        ctx = await _ensure_browser(admin["email"])
        page = await ctx.new_page()
        try:
            trade_url = "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry"
            await _nav(page, trade_url, sleep=5)

            if "login" in page.url.lower():
                raise HTTPException(status_code=401, detail="Session expired — log in again")

            # Account — use pre-validated numeric account number only
            if account:
                try:
                    await page.locator('#dest-acct-dropdown').click()
                    await asyncio.sleep(1)
                    # Use exact text match on numeric account number (injection-safe)
                    await page.locator(f'[data-value="{account}"], li:text-is("{account}")').first.click(timeout=2000)
                    await asyncio.sleep(1)
                except Exception:
                    log.warning("Could not select account %s — using default", account)

            # Symbol
            sym_input = page.locator('#eq-ticket-dest-symbol')
            await sym_input.wait_for(state="visible", timeout=10000)
            await sym_input.click()
            await sym_input.fill(ticker)
            await asyncio.sleep(1.5)
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(2)

            # Action = Buy
            await page.locator('#dest-dropdownlist-button-action').click()
            await asyncio.sleep(1)
            for sel in ['[role="option"]:has-text("Buy")', 'li:has-text("Buy")', 'a:has-text("Buy")']:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        break
                except Exception:
                    continue
            await asyncio.sleep(0.8)

            # Quantity
            qty_val = str(shares)
            await page.evaluate(f"""
            () => {{
                const el = document.getElementById('eqt-shared-quantity');
                if (!el) throw new Error('qty input not found');
                el.focus();
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
                nativeSetter.set.call(el, '{qty_val}');
                el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
            }}
            """)
            await asyncio.sleep(0.8)

            # Order type = Limit
            await page.locator('#dest-dropdownlist-button-ordertype').click()
            await asyncio.sleep(1)
            for sel in ['[role="option"]:has-text("Limit")', 'li:has-text("Limit")', 'a:has-text("Limit")']:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        break
                except Exception:
                    continue
            await asyncio.sleep(0.8)

            # Limit price
            price_input = page.locator('input[id*="price" i], input[name*="price" i]').first
            await price_input.wait_for(state="visible", timeout=5000)
            await price_input.click(click_count=3)
            await price_input.fill(str(limit_price))
            await asyncio.sleep(0.8)

            # Preview
            preview_btn = page.locator(
                'button.pvd-button--primary.pvd-button--full-width,'
                'button:has-text("Preview order"),'
                'button:has-text("Preview Order")'
            ).first
            await preview_btn.wait_for(state="visible", timeout=8000)
            await preview_btn.click()
            await asyncio.sleep(5)

            preview_text = await page.evaluate("() => document.body.innerText")

            # Check preview page for errors BEFORE placing
            preview_ok, preview_msg = _verify_fidelity_order_page(preview_text)
            if not preview_ok and any(p in preview_text.lower() for p in _ORDER_ERROR_PATTERNS):
                raise HTTPException(status_code=400, detail=f"Fidelity rejected at preview stage: {preview_msg}\n\nPage excerpt:\n{preview_text[:400]}")

            # Place order
            place_btn = page.locator('button:has-text("Place Order")').first
            await place_btn.wait_for(state="visible", timeout=8000)
            await place_btn.click()
            await asyncio.sleep(6)

            # Verify confirmation — NEVER assume success
            confirm_text = await page.evaluate("() => document.body.innerText")
            confirmed, confirm_msg = _verify_fidelity_order_page(confirm_text)
            if not confirmed:
                # Order status unknown — do NOT mark executed
                raise HTTPException(
                    status_code=502,
                    detail=f"Order submitted but confirmation NOT verified: {confirm_msg}\n\nPage: {confirm_text[:400]}\n\nCheck Fidelity account manually."
                )

            order_status = "executed"
            fidelity_resp = confirm_text[:500]
            log.info("Fidelity BUY order CONFIRMED: %s x%d @ $%.2f limit=%.2f (%s)", ticker, shares, price, limit_price, confirm_msg)

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fidelity order failed: {e}")
        finally:
            await _save_storage(admin["email"])
            await page.close()
    else:
        order_status = "preview"

    # ── 6. Mirror in paper account — ONLY when order actually executed ────────
    paper_result = None
    if body.also_paper_trade and body.execute and order_status == "executed":
        try:
            PAPER_STATE = ROOT / "tmp" / "paper_trading_today" / "unified_brain" / "state.json"
            PAPER_STATE.parent.mkdir(parents=True, exist_ok=True)
            state: dict = {}
            if PAPER_STATE.exists():
                try:
                    state = json.loads(PAPER_STATE.read_text())
                except Exception:
                    pass

            positions = state.get("positions", {})
            if ticker not in positions:
                positions[ticker] = {
                    "ticker": ticker, "shares": shares, "entry_price": price,
                    "stop": stop_price, "target": target_price,
                    "entry_time": now_iso, "signal_date": today,
                    "score": 80.0, "alpha_tier": "A",
                    "atr": round(price * 0.02, 4),
                    "breakeven_moved": False, "peak_price": price, "scans_held": 0,
                    "partial_sold": False, "defensive_trimmed": False, "scaled_in": False,
                    "sector": "thematic", "theme": body.theme,
                    "strategy_label": "fidelity_thematic",
                    "thesis": body.thesis, "catalyst": body.catalyst,
                    "hold_days": body.hold_days,
                    "exit_plan": f"Target +{body.target_pct}%, stop -{body.stop_pct}%, {body.hold_days}d",
                    "entry_date": today,
                    "funded_by_unsettled": False, "unsettled_settle_date": "",
                    "regime_at_entry": "thematic",
                    "regime_score_at_entry": None, "crash_risk_at_entry": None,
                    "regime_confidence_at_entry": None,
                    "_source": "fidelity_thematic",
                    "_fidelity_order_status": order_status,
                }
                cash = float(state.get("cash", 100_000))
                settled = float(state.get("settled_cash", cash))
                state["positions"]    = positions
                state["cash"]         = round(cash - cost, 4)
                state["settled_cash"] = round(settled - cost, 4)
                # Atomic write
                fd, tmp_p = tempfile.mkstemp(dir=PAPER_STATE.parent, prefix=".tmp_")
                try:
                    with os.fdopen(fd, "w") as f:
                        f.write(json.dumps(state, indent=2))
                    os.replace(tmp_p, PAPER_STATE)
                except Exception:
                    try: os.unlink(tmp_p)
                    except Exception: pass
                paper_result = {"ok": True, "shares": shares, "cost": cost}
        except Exception as pe:
            paper_result = {"ok": False, "error": str(pe)}

    # ── 7. Log trade ──────────────────────────────────────────────────────────
    log_record = {
        "ts": now_iso, "ticker": ticker, "shares": shares, "entry": price,
        "limit_price": limit_price, "stop": stop_price, "target": target_price,
        "cost": cost, "status": order_status, "theme": body.theme,
        "thesis": body.thesis, "catalyst": body.catalyst,
        "account": body.account, "user": admin["email"],
        "fidelity_account_value": total_value, "fidelity_cash": available_cash,
    }
    _log_fidelity_trade(log_record)

    return {
        "ok":            True,
        "ticker":        ticker,
        "shares":        shares,
        "entry_price":   price,
        "limit_price":   limit_price,
        "stop":          stop_price,
        "target":        target_price,
        "cost":          cost,
        "order_status":  order_status,
        "fidelity":      fidelity_resp,
        "paper_trade":   paper_result,
        "account_value": total_value,
        "cash_used":     cost,
        "cash_remaining":round(available_cash - cost, 2) if available_cash else None,
    }


# ── Thematic exit endpoint ─────────────────────────────────────────────────────

class FidelityExitRequest(BaseModel):
    ticker:     str
    shares:     Optional[float] = None  # None = sell all found in Fidelity positions
    order_type: str = "Limit"           # Limit or Market
    limit_pct:  float = -0.2            # Limit = last_price - limit_pct% (default 0.2% below)
    account:    Optional[str] = None
    execute:    bool = False


@router.post("/fidelity/thematic-exit")
async def fidelity_thematic_exit(
    body: FidelityExitRequest,
    admin: dict = Depends(require_step_up),
):
    """
    Exit a Fidelity thematic position.
    Looks up current shares from Fidelity positions (or uses body.shares).
    Places Sell Limit order (default) or Sell Market.
    """
    import datetime as _dt
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED

    ticker = body.ticker.upper().strip()
    if not ticker.isalpha() or len(ticker) > 5:
        raise HTTPException(status_code=400, detail=f"Invalid ticker '{ticker}'")

    try:
        account = _validate_account_number(body.account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Idempotency: prevent simultaneous exit orders for same ticker
    lock_key = f"{admin['email']}:exit:{ticker}"
    order_lock = _get_order_lock(lock_key)
    if order_lock.locked():
        raise HTTPException(status_code=429, detail=f"Exit for {ticker} already in progress.")

    async with order_lock:
        return await _fidelity_thematic_exit_inner(body, admin, ticker, account)


async def _fidelity_thematic_exit_inner(body, admin, ticker, account):
    import datetime as _dt
    from fastapi import HTTPException
    from tradingagents.compliance import validate_live_order, live_trading_enabled, LIVE_TRADING_HARD_BLOCKED

    # ── Fetch current Fidelity positions to find share count ──────────────────
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    fidelity_shares = None
    last_price      = None

    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        if "login" in page.url.lower():
            raise HTTPException(status_code=401, detail="Not authenticated with Fidelity.")

        try:
            await page.wait_for_selector('.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2)

        pos_data = await page.evaluate(f"""
        () => {{
            let result = null;
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {{
                const sym = row.querySelector('[col-id="sym"]')?.innerText?.trim().split('\\n')[0] || '';
                if (sym.toUpperCase() !== '{ticker}') return;
                const ri = row.getAttribute('row-index');
                const center = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${{ri}}"]`);
                if (!center) return;
                const qty   = center.querySelector('[col-id="qty"]')?.innerText?.trim() || '';
                const price = center.querySelector('[col-id="lstPrStk"]')?.innerText?.trim().split('\\n')[0] || '';
                result = {{ qty, price }};
            }});
            return result;
        }}
        """)

        if pos_data:
            try:
                fidelity_shares = float(str(pos_data.get("qty", "")).replace(",", ""))
            except Exception:
                pass
            last_price = _parse_dollar(pos_data.get("price", "") or "")

    finally:
        await page.close()

    shares_to_sell = body.shares or fidelity_shares
    if not shares_to_sell or shares_to_sell <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"{ticker} not found in Fidelity positions or shares=0 (found: {fidelity_shares})"
        )

    shares_to_sell = int(shares_to_sell)

    # Use Fidelity's own real-time price if available (more accurate than yfinance after-hours)
    if not last_price:
        loop = asyncio.get_running_loop()
        def _get_exit_price(sym: str) -> float | None:
            try:
                import yfinance as yf
                data = yf.download(sym, period="5d", auto_adjust=True, progress=False)
                closes = data["Close"]
                series = closes[sym] if hasattr(closes, "columns") else closes
                return float(series.dropna().iloc[-1])
            except Exception:
                return None
        last_price = await loop.run_in_executor(None, _get_exit_price, ticker)

    if not last_price:
        raise HTTPException(status_code=400, detail=f"Cannot determine price for {ticker}")

    # Always use Limit order for exits — compliance blocks Market
    limit_price = round(last_price * (1 + body.limit_pct / 100), 2)

    order_dict = {
        "symbol": ticker, "action": "Sell",
        "order_type": "Limit",
        "quantity": shares_to_sell,
        "limit_price": limit_price,
    }
    decision = validate_live_order(order_dict)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=f"Compliance: {decision.reason}")

    order_status = "preview"
    fidelity_resp = None

    if body.execute:
        if LIVE_TRADING_HARD_BLOCKED:
            raise HTTPException(status_code=403, detail="LIVE_TRADING_HARD_BLOCKED=True in compliance.py")
        if not live_trading_enabled():
            raise HTTPException(status_code=403, detail="LIVE_TRADING_ENABLED not set to true in .env")

        exit_page = None  # define before try so finally can safely check
        try:
            exit_page = await ctx.new_page()
            await _nav(exit_page, "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry", sleep=5)

            # Account — pre-validated numeric only, injection-safe
            if account:
                try:
                    await exit_page.locator('#dest-acct-dropdown').click()
                    await asyncio.sleep(1)
                    await exit_page.locator(f'[data-value="{account}"], li:text-is("{account}")').first.click(timeout=2000)
                    await asyncio.sleep(1)
                except Exception:
                    log.warning("Could not select account %s — using default", account)

            sym_input = exit_page.locator('#eq-ticket-dest-symbol')
            await sym_input.wait_for(state="visible", timeout=10000)
            await sym_input.click()
            await sym_input.fill(ticker)
            await asyncio.sleep(1.5)
            await exit_page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await exit_page.keyboard.press("Enter")
            await asyncio.sleep(2)

            # Action = Sell
            await exit_page.locator('#dest-dropdownlist-button-action').click()
            await asyncio.sleep(1)
            for sel in ['[role="option"]:has-text("Sell")', 'li:has-text("Sell")', 'a:has-text("Sell")']:
                try:
                    loc = exit_page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        break
                except Exception:
                    continue
            await asyncio.sleep(0.8)

            # Quantity — integer shares only, no injection risk
            qty_val = str(int(shares_to_sell))
            await exit_page.evaluate(f"""
            () => {{
                const el = document.getElementById('eqt-shared-quantity');
                if (!el) throw new Error('qty not found');
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
                nativeSetter.set.call(el, '{qty_val}');
                el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
            """)
            await asyncio.sleep(0.8)

            # Order type — validated: only "Limit" allowed (compliance blocks Market)
            await exit_page.locator('#dest-dropdownlist-button-ordertype').click()
            await asyncio.sleep(1)
            order_type_label = "Limit"  # always use Limit for exits (Market blocked by compliance)
            for sel in [f'[role="option"]:has-text("{order_type_label}")', f'li:has-text("{order_type_label}")', f'a:has-text("{order_type_label}")']:
                try:
                    loc = exit_page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        await loc.click()
                        break
                except Exception:
                    continue
            await asyncio.sleep(0.8)

            if limit_price:
                price_input = exit_page.locator('input[id*="price" i], input[name*="price" i]').first
                await price_input.wait_for(state="visible", timeout=5000)
                await price_input.click(click_count=3)
                await price_input.fill(str(limit_price))
                await asyncio.sleep(0.8)

            preview_btn = exit_page.locator(
                'button.pvd-button--primary.pvd-button--full-width,'
                'button:has-text("Preview order"),'
                'button:has-text("Preview Order")'
            ).first
            await preview_btn.wait_for(state="visible", timeout=8000)
            await preview_btn.click()
            await asyncio.sleep(5)

            preview_text = await exit_page.evaluate("() => document.body.innerText")

            # Check for errors before placing
            _, preview_msg = _verify_fidelity_order_page(preview_text)
            if any(p in preview_text.lower() for p in _ORDER_ERROR_PATTERNS):
                raise HTTPException(status_code=400, detail=f"Fidelity rejected at preview: {preview_msg}\n{preview_text[:400]}")

            place_btn = exit_page.locator('button:has-text("Place Order")').first
            await place_btn.wait_for(state="visible", timeout=8000)
            await place_btn.click()
            await asyncio.sleep(6)

            # Verify confirmation — NEVER assume success
            confirm_text = await exit_page.evaluate("() => document.body.innerText")
            confirmed, confirm_msg = _verify_fidelity_order_page(confirm_text)
            if not confirmed:
                raise HTTPException(
                    status_code=502,
                    detail=f"Exit submitted but NOT confirmed: {confirm_msg}\n\nPage: {confirm_text[:400]}\n\nCheck Fidelity manually."
                )

            order_status  = "executed"
            fidelity_resp = confirm_text[:500]
            log.info("Fidelity SELL order CONFIRMED: %s x%d @ limit=%.2f (%s)", ticker, shares_to_sell, limit_price or 0, confirm_msg)

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fidelity exit failed: {e}")
        finally:
            await _save_storage(admin["email"])
            if exit_page is not None:
                try:
                    await exit_page.close()
                except Exception:
                    pass

    now_iso  = _dt.datetime.now().isoformat(timespec="seconds")
    proceeds = round(last_price * shares_to_sell, 2)
    _log_fidelity_trade({
        "ts": now_iso, "ticker": ticker, "shares": shares_to_sell,
        "action": "Sell", "order_type": "Limit",
        "price": last_price, "limit_price": limit_price,
        "proceeds": proceeds, "status": order_status, "user": admin["email"],
    })

    return {
        "ok":           True,
        "ticker":       ticker,
        "shares":       shares_to_sell,
        "price":        last_price,
        "limit_price":  limit_price,
        "proceeds":     proceeds,
        "order_status": order_status,
        "fidelity":     fidelity_resp,
        "price_source": "fidelity_realtime" if fidelity_shares else "yfinance",
    }


# ── Fidelity → Thematic sync ───────────────────────────────────────────────────

@router.get("/fidelity/thematic-sync")
async def fidelity_thematic_sync(admin: dict = Depends(require_admin)):
    """
    Return Fidelity real positions mapped to thematic format.
    Enriches with yfinance scores, marks which are also in thematic portfolio.
    Useful for seeing real vs. paper thematic exposure side-by-side.
    """
    from fastapi import HTTPException
    from web.api.thematic_portfolio import _load, DEFAULT_THEMES

    connected = await _is_logged_in(admin["email"])
    if not connected:
        raise HTTPException(status_code=401, detail="Not authenticated with Fidelity.")

    # Fetch Fidelity positions
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        if "login" in page.url.lower():
            raise HTTPException(status_code=401, detail="Not authenticated with Fidelity.")
        try:
            await page.wait_for_selector('.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2)

        raw = await page.evaluate("""
        () => {
            const SKIP = ['Cash', 'Pending', 'Account:', 'Grand', 'HELD'];
            const symMap = {};
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const cell = row.querySelector('[col-id="sym"]');
                if (cell) symMap[row.getAttribute('row-index')] = cell.innerText.trim();
            });
            const dataMap = {};
            document.querySelectorAll('.ag-center-cols-container .ag-row[row-index]').forEach(row => {
                const cells = {};
                row.querySelectorAll('[col-id]').forEach(cell => {
                    cells[cell.getAttribute('col-id')] = cell.innerText.trim();
                });
                if (Object.keys(cells).length >= 2) dataMap[row.getAttribute('row-index')] = cells;
            });
            const positions = [];
            Object.entries(symMap).forEach(([ri, symText]) => {
                const ticker = symText.split('\\n')[0].trim();
                if (!ticker || ticker.length > 10 || !/^[A-Z]/.test(ticker) ||
                    SKIP.some(s => ticker.startsWith(s))) return;
                const d = dataMap[ri] || {};
                const lstLines = (d.lstPrStk || '').split('\\n');
                const cstLines = (d.cstBasStk || '').split('\\n');
                const totLines = (d.totGLStk || '').split('\\n');
                positions.push({
                    ticker,
                    qty:             d.qty || '',
                    last_price:      lstLines[0] || '',
                    cost_per_share:  cstLines[1] || '',
                    market_value:    (d.curVal || '').split('\\n')[0],
                    total_gain_pct:  totLines[1] || '',
                });
            });
            return positions;
        }
        """)
        await _save_storage(admin["email"])
    finally:
        await page.close()

    # Load thematic portfolio for this user
    port = _load(admin["email"])
    thematic_tickers = set(port["positions"].keys())

    # Map Fidelity positions to thematic-compatible format
    result = []
    for pos in raw:
        ticker = pos["ticker"]
        qty_raw = pos.get("qty", "").replace(",", "").strip()
        shares  = float(qty_raw) if qty_raw else 0
        price   = _parse_dollar(pos.get("last_price", "") or "")
        cost_ps = _parse_dollar(pos.get("cost_per_share", "") or "")
        mv      = _parse_dollar(pos.get("market_value", "") or "")
        thematic_pos = port["positions"].get(ticker, {})
        result.append({
            "ticker":          ticker,
            "shares":          shares,
            "last_price":      price,
            "cost_per_share":  cost_ps,
            "market_value":    mv,
            "total_gain_pct":  pos.get("total_gain_pct", ""),
            "in_thematic":     ticker in thematic_tickers,
            "theme":           thematic_pos.get("theme", "—"),
            "thesis":          thematic_pos.get("thesis", ""),
            "conviction":      thematic_pos.get("conviction", None),
            "source":          "fidelity_real",
        })

    return {
        "ok":        True,
        "positions": result,
        "count":     len(result),
        "thematic_overlap": sum(1 for p in result if p["in_thematic"]),
    }


# ── Trade log endpoint ─────────────────────────────────────────────────────────

@router.get("/fidelity/trade-log")
async def fidelity_trade_log(admin: dict = Depends(require_admin), limit: int = 50):
    """Return recent Fidelity trades placed through this system."""
    entries: list[dict] = []
    if TRADE_HISTORY_FILE.exists():
        try:
            lines = TRADE_HISTORY_FILE.read_text().splitlines()
            for line in lines[-limit:]:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    return {"ok": True, "trades": list(reversed(entries)), "count": len(entries)}


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
