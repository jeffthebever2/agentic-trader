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
from tradingagents.config import env_bool
from tradingagents.compliance import valid_symbol
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

import time as _time

log = logging.getLogger("fidelity")

# Per-(user, ticker) idempotency lock — prevents duplicate simultaneous orders
_ORDER_LOCKS: dict[str, asyncio.Lock] = {}
_ORDER_LOCKS_META: dict[str, float] = {}  # key → last-acquire timestamp
_ORDER_LOCK_TTL = 120.0  # seconds before lock is considered stale

# Connection status cache: email → (timestamp, is_connected)
# Prevents every /fidelity/status call from launching a 20-30s Playwright navigation.
_SESSION_CACHE: dict[str, tuple[float, bool]] = {}
_SESSION_CACHE_TTL = 300.0  # 5 minutes — recheck after this

def _set_session_cache(email: str, connected: bool) -> None:
    _SESSION_CACHE[_user_key(email)] = (_time.time(), connected)

def _get_session_cache(email: str) -> bool | None:
    entry = _SESSION_CACHE.get(_user_key(email))
    if entry and (_time.time() - entry[0]) < _SESSION_CACHE_TTL:
        return entry[1]
    return None  # cache miss or expired


# ── Holdings snapshot cache (stale-while-revalidate) ──────────────────────────
# DISPLAY reads (/fidelity/positions, /fidelity/accounts) serve the LAST scraped
# snapshot instantly from disk, then refresh in the background — so the page never
# blocks on a 20-40s Playwright scrape. The keepalive loop also refreshes these
# every 10 min, so the snapshot stays warm with zero user activity.
# SAFETY: this caches DISPLAY reads ONLY. The real-money sizing path calls
# _get_fidelity_balances() directly and always scrapes FRESH — never sized off cache.
_POS_CACHE_TTL = float(os.getenv("FIDELITY_CACHE_TTL_SECONDS", "600") or 600)  # older → revalidate
_POS_REFRESH_INFLIGHT: set[str] = set()  # keys currently revalidating (dedup)


def _snapshot_path(email: str, kind: str) -> Path:
    digest = hashlib.sha256(_user_key(email).encode()).hexdigest()[:16]
    return ROOT / "tmp" / f"fidelity_{kind}_{digest}.json"


def _read_snapshot(email: str, kind: str) -> "dict | None":
    try:
        p = _snapshot_path(email, kind)
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        log.debug("snapshot read (%s): %s", kind, e)
    return None


def _pending_fills_path(email: str) -> Path:
    return _snapshot_path(email, "pending_fills")


def _load_pending_fills(email: str) -> list[dict]:
    try:
        p = _pending_fills_path(email)
        if p.exists():
            data = json.loads(p.read_text())
            return data.get("pending", []) if isinstance(data, dict) else []
    except Exception as e:
        log.debug("pending-fill ledger read: %s", e)
    return []


def _save_pending_fills(email: str, pending: list[dict]) -> None:
    try:
        p = _pending_fills_path(email)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pending": pending[-200:], "updated_at": _time.time()}))
        tmp.replace(p)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
    except Exception as e:
        log.warning("pending-fill ledger write failed: %s", e)


def _record_pending_fill(email: str, *, ticker: str, shares: float, side: str,
                         shares_before: float, limit_price: float | None = None) -> None:
    """Remember that we SUBMITTED an order, so a later holdings snapshot can tell
    us whether it actually filled.

    Fidelity accepting a limit order is not a fill — these are DAY orders, so one
    that never trades expires at 16:00 with no notification while internal state
    (written on acceptance) claims we hold the position. Holdings are the source
    of truth; this ledger is what lets us compare against them.
    """
    try:
        import datetime as _dt
        from tradingagents.brokers.fill_verifier import PendingFill
        entry = PendingFill(
            ticker=str(ticker).upper(), intended_shares=float(shares or 0),
            shares_before=float(shares_before or 0), side=str(side or "buy").lower(),
            submitted_at=_dt.datetime.now().isoformat(timespec="seconds"),
            limit_price=limit_price,
            order_id=f"{ticker}-{int(_time.time() * 1000)}",
        ).as_dict()
        pend = _load_pending_fills(email)
        pend.append(entry)
        _save_pending_fills(email, pend)
        log.info("Pending fill recorded: %s %s x%s (had %s)",
                 side, ticker, shares, shares_before)
    except Exception as e:
        log.warning("could not record pending fill for %s: %s", ticker, e)


def _current_shares(email: str, ticker: str) -> float:
    """Shares currently held per the cached positions snapshot (0 when unknown)."""
    try:
        from tradingagents.brokers.fill_verifier import holdings_share_map
        snap = _read_snapshot(email, "positions") or {}
        return holdings_share_map(snap.get("positions") or []).get(str(ticker).upper(), 0.0)
    except Exception:
        return 0.0


def verify_pending_fills(email: str, *, session_expired: bool = False) -> list[dict]:
    """Compare the pending-fill ledger against real broker holdings.

    Returns the terminal verdicts (filled / partial / unfilled). An UNFILLED
    verdict means internal state is describing a position that does not exist —
    the caller must remove it and alert rather than keep marking it to market.

    Fails safe: if the holdings snapshot is missing we pass ``None`` and every
    verdict is UNKNOWN, so nothing is ever cleaned up on a transient scrape
    failure.
    """
    from tradingagents.brokers.fill_verifier import (
        PendingFill, holdings_share_map, reconcile_pending_fills, STATUS_PENDING,
        STATUS_UNKNOWN,
    )
    raw = _load_pending_fills(email)
    if not raw:
        return []
    snap = _read_snapshot(email, "positions")
    holdings = holdings_share_map(snap.get("positions") or []) if snap else None

    pendings = [PendingFill.from_dict(d) for d in raw]
    expired = [p.ticker for p in pendings] if session_expired else []
    verdicts = reconcile_pending_fills(pendings, holdings, expired_tickers=expired,
                                       tolerance_shares=0.5)

    keep, terminal = [], []
    for src, v in zip(raw, verdicts):
        if v.status in (STATUS_PENDING, STATUS_UNKNOWN):
            keep.append(src)
        else:
            terminal.append(v.as_dict())
            level = log.warning if v.status == "unfilled" else log.info
            level("Fill verdict %s: %s (%s)", v.ticker, v.status, v.reason)
    if len(keep) != len(raw):
        _save_pending_fills(email, keep)
    return terminal


def _write_snapshot(email: str, kind: str, data: dict) -> None:
    try:
        payload = dict(data)
        payload["scraped_at"] = _time.time()
        path = _snapshot_path(email, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)  # atomic
    except Exception as e:
        log.warning("snapshot write (%s): %s", kind, e)


def _snapshot_meta(cached: dict) -> dict:
    age = _time.time() - float(cached.get("scraped_at", 0) or 0)
    return {"cached": True, "age_seconds": round(age, 1), "stale": age > _POS_CACHE_TTL}


# Action words that appear inside historical lock keys. They identify the verb,
# never the instrument, so they must not create a separate lock namespace.
_ORDER_KEY_VERBS = frozenset({"buy", "sell", "exit", "auto-exit", "trim", "close"})


def _canonical_order_key(key: str) -> str:
    """Collapse the historical key dialects down to one lock per (user, ticker).

    Four namespaces existed for the SAME position — ``{email}:{ticker}``,
    ``{email}:{ticker}:{buy|sell}``, ``{email}:exit:{ticker}`` and
    ``{email}:auto-exit:{ticker}``. Different strings mean different
    ``asyncio.Lock`` objects, so they serialised nothing against each other.

    The dangerous pair is on the sell side: the armed autonomous exit executor
    takes ``auto-exit:`` while a human approving the *same* proposal takes
    ``exit:``. Both pass, both scrape the same share count, both sell it — a
    100-share holding becomes 100 short. That is precisely the position
    PROHIBITED_ORDER_TYPES exists to prevent, and compliance cannot see it
    because each order is individually a valid "Sell 100". The proposal status
    only flips to executed after the ~40s Playwright round-trip, so there is no
    status-based dedup either.

    Serialising more is strictly safer, so collapse rather than split.

    Implementation note: strip only the VERB tokens and keep every other segment
    in order. Key shapes differ per broker — Fidelity uses ``{email}:{ticker}``
    and Webull uses ``webull:{email}:{ticker}:{ACTION}`` — so anything that
    assumes a fixed position for the ticker will collapse *different* tickers
    onto one lock and block legitimate concurrent orders. Order-preserving verb
    removal handles both shapes and keeps distinct tickers, users and brokers
    distinct.
    """
    parts = [p for p in str(key).split(":") if p]
    if len(parts) < 2:
        return str(key)
    kept = [p for p in parts if p.lower() not in _ORDER_KEY_VERBS]
    if not kept:
        return str(key)
    # Normalise the instrument (always the trailing segment once verbs are gone).
    kept[-1] = kept[-1].upper()
    return ":".join(kept)


def _get_order_lock(key: str) -> asyncio.Lock:
    """Return (or create) a per-(user,ticker) lock to prevent duplicate orders.

    The key is canonicalised first — see ``_canonical_order_key`` — so buy, sell,
    exit and auto-exit for one ticker all contend for a single lock."""
    canonical = _canonical_order_key(key)
    if canonical not in _ORDER_LOCKS:
        _ORDER_LOCKS[canonical] = asyncio.Lock()
    return _ORDER_LOCKS[canonical]


def _mask_account(account: str | None) -> str:
    """Mask a brokerage account number for logs — show only the last 4 digits.
    Account numbers are sensitive financial identifiers and must not appear in
    plaintext logs. '262502469' → '•••••2469'; short/empty → '••••'."""
    s = "" if account is None else str(account).strip()
    if len(s) <= 4:
        return "••••"
    return "•" * (len(s) - 4) + s[-4:]


def _validate_account_number(account: str | None) -> str | None:
    """Fidelity account numbers are numeric, 8-12 digits. Reject anything else."""
    if account is None:
        return None
    cleaned = account.strip().replace("-", "").replace(" ", "")
    if not cleaned.isdigit() or not (6 <= len(cleaned) <= 15):
        raise ValueError(f"Invalid account number '{account}' — must be 6-15 digits only.")
    return cleaned


def _protected_account_numbers() -> set[str]:
    """Account numbers that must NEVER be traded (Roth IRA / retirement / etc.).

    Configured via env ``FIDELITY_PROTECTED_ACCOUNTS`` (comma-separated). This is
    a broker-level kill switch independent of the Holdings-Brain account scrape —
    it blocks ANY order routed to these accounts, from any code path.
    """
    raw = os.getenv("FIDELITY_PROTECTED_ACCOUNTS", "")
    out = set()
    for tok in raw.replace(" ", "").split(","):
        t = tok.replace("-", "").strip()
        if t:
            out.add(t)
    return out


def _assert_account_tradeable(account: str | None) -> None:
    """Raise 403 if the target account is on the protected (no-trade) list.

    A protected list with an EMPTY/None target account is also refused — we never
    trade the broker's default account when protected accounts exist, because the
    default could resolve to a retirement account."""
    from fastapi import HTTPException
    protected = _protected_account_numbers()
    if not protected:
        return
    cleaned = (account or "").replace("-", "").replace(" ", "").strip()
    if cleaned in protected:
        raise HTTPException(
            status_code=403,
            detail=f"Account {cleaned} is protected (FIDELITY_PROTECTED_ACCOUNTS) — trading is blocked.",
        )
    # Strict mode: refuse the broker's default account too, so an order can never
    # silently land on whichever account Fidelity has selected (could be the Roth).
    # Defaults ON whenever a protected list exists; set to "false" to opt out.
    if not cleaned and env_bool("FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", True):
        raise HTTPException(
            status_code=403,
            detail="FIDELITY_REQUIRE_EXPLICIT_ACCOUNT is enabled — specify an explicit, allowed "
                   "account number (refusing the default account).",
        )


def _resolve_trade_account(explicit: str | None = None) -> str | None:
    """Resolve the target account for HIL flows that don't carry one per-request.

    Order: ``explicit`` (request field / per-user setting) → env
    ``FIDELITY_TRADE_ACCOUNT`` → None. The result ALWAYS passes
    ``_assert_account_tradeable``, so a protected account — or no account at all
    while ``FIDELITY_REQUIRE_EXPLICIT_ACCOUNT`` strict mode is active — raises
    (403) here instead of falling through to whatever account the broker has
    pre-selected. This only NARROWS the account gates: the order paths still
    re-validate, re-assert, and read-back-verify the ticket selection.
    """
    raw = str(explicit).strip() if explicit else os.getenv("FIDELITY_TRADE_ACCOUNT", "").strip()
    account = _validate_account_number(raw) if raw else None  # ValueError on garbage
    _assert_account_tradeable(account)  # 403: protected, or empty in strict mode
    return account


async def _select_and_verify_account(page, account: str | None) -> None:
    """Select ``account`` in the trade-ticket dropdown and verify the ticket took it.

    Fail-closed: any failure to select OR verify aborts the order. We never fall
    back to whatever account Fidelity has pre-selected — the pre-selected default
    could be a protected retirement account that ``_assert_account_tradeable``
    validated the *requested* account against, not the one the order would hit.
    """
    from fastapi import HTTPException

    if not account:
        return
    dropdown = page.locator('#dest-acct-dropdown')
    try:
        await dropdown.click()
        await asyncio.sleep(1)
        await page.locator(f'[data-value="{account}"], li:text-is("{account}")').first.click(timeout=2000)
        await asyncio.sleep(1)
    except Exception as exc:
        log.error("Could not select account %s — aborting order (no default-account fallback): %s",
                  _mask_account(account), exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not select account {_mask_account(account)} on the trade ticket — order aborted.",
        )
    # Read back what the ticket actually shows; the selection click above can
    # silently no-op while the page keeps the pre-selected account.
    try:
        shown = (await dropdown.inner_text(timeout=2000)) or ""
    except Exception:
        shown = ""
    # Match against extracted digit RUNS — never a raw concatenation of every
    # digit on the widget. A concatenation lets a rendered balance ("$15,260.00"),
    # ANOTHER account number (default 40552604 contains "5260"), or a still-open
    # option list false-pass the last-4 check by digit coincidence while the
    # ticket keeps the broker default account. Fail-closed rules:
    #   * full match  → some digit run equals the requested account exactly;
    #   * last-4 match → the last 4 digits appear as a standalone token (masked
    #     display like "...6789"), never embedded inside a longer digit run;
    #   * any OTHER account-like run (≥6 digits ≠ requested) on the widget —
    #     open option list or a different selected account — aborts outright.
    digit_runs = re.findall(r"\d{4,}", shown)
    other_accounts = [r for r in digit_runs if len(r) >= 6 and r != account]
    full_match = account in digit_runs
    last4_match = re.search(rf"(?:^|\D){re.escape(account[-4:])}(?:\D|$)", shown) is not None
    if other_accounts or not (full_match or last4_match):
        log.error("Account read-back mismatch: requested %s, ticket shows %r — aborting order",
                  _mask_account(account), shown.strip()[:80])
        raise HTTPException(
            status_code=502,
            detail="Trade ticket did not confirm the requested account — order aborted.",
        )


# Confirmation patterns on Fidelity order success page
_ORDER_CONFIRM_PATTERNS = [
    "order received", "order submitted", "order number", "confirmation",
    "order #", "order has been placed", "order was placed",
]
# Matched as substrings against the WHOLE page innerText, and checked BEFORE the
# confirm patterns — so any token that appears in ordinary Fidelity chrome makes
# a successful order look rejected. Post-submit that is the dangerous direction:
# the order is already live, but `order_status` stays "sized", the paper mirror
# is skipped and `_mark_owned` never runs, leaving real shares that no part of
# the system knows about — no stop, no target, not in the exit guard, and
# copy-trade will buy the name again.
#
# Dropped as pure noise: "buying power" (a standard ticket label), "after hours"
# (extended-hours UI), "error" and "invalid" (hidden aria-live templates and
# validation scaffolding present on every page). What remains only appears in a
# genuine rejection message.
#
# The opposite direction — missing a real rejection — is now recoverable:
# verify_pending_fills() checks submitted orders against actual holdings, so a
# rejected order that we recorded as filled surfaces as a PHANTOM alert instead
# of silently persisting. That safety net is what makes trimming this list the
# right trade.
_ORDER_ERROR_PATTERNS = [
    "insufficient", "not enough", "failed",
    "unable to process", "cannot process", "rejected",
    "market is closed",
]

# Natural-language rejection phrasings the flat substring list cannot express.
# "cannot process" does NOT match "cannot be processed" — and that exact string
# was this function's own documented example of a page that must be rejected, so
# the documented behaviour and the real behaviour disagreed: a rejected order
# carrying an order number read as CONFIRMED, and state was written for shares
# that were never bought. Anchored on the verb so they cannot fire on chrome.
# Anchored on YOUR order specifically. The first draft matched the bare verb
# phrase, which fires on ordinary Fidelity copy that has nothing to do with the
# order just submitted — all of these are benign and were being rejected:
#   "Orders cannot be placed for restricted securities."   (boilerplate)
#   "If you are unable to complete your order, call us."   (help text)
#   "Tell us why your task was not completed."             (feedback widget)
#   a "Recent orders" row reading "Order canceled"          (a DIFFERENT order)
# Post-submit a false positive is the dangerous direction: the order is already
# live, so a spurious rejection leaves real shares nothing tracks. Requiring a
# possessive/definite reference to *this* order keeps the genuine rejections
# ("Your order cannot be processed", "This order was not placed") and drops the
# generic copy.
# Rejection detection is SENTENCE-SCOPED, not proximity-based.
#
# A proximity regex over the whole page fails both ways. `innerText` joins DOM
# elements with newlines, and `[^.]` matches a newline, so an unrelated heading
# and unrelated boilerplate three elements apart look adjacent — that is how
# "Your order / Summary / Orders cannot be placed for restricted securities"
# read as a rejection. In the other direction a bounded gap breaks on the first
# period, and any rejection that echoes the ticket contains a price ("$4.12"),
# so real rejections were missed. Word order also varies: "unable to place YOUR
# ORDER" puts the anchor after the verb.
#
# Instead: split into sentences/lines, and require the anchor and the rejection
# verb to appear in the SAME segment, in either order.
_THIS_ORDER_RX = re.compile(
    r"(?:(?:your|this)\s+order|order\s*(?:#|number)\s*[:\s]?\s*\d+)", re.I)
_REJECT_VERB_RX = re.compile(
    r"(?:(?:cannot|can\s?not|could\s+not|unable\s+to|will\s+not|has\s+not|"
    r"have\s+not)\s+(?:be\s+)?(?:process|complet|plac|submit|execut|accept|fill)"
    r"|(?:was|were|is|are)\s+not\s+"
    r"(?:placed|accepted|submitted|executed|processed|completed|filled)\b"
    r"|\b(?:declined|refused|rejected|cancell?ed|voided)\b)", re.I)
# Conditional / advisory framing — help text and feedback widgets, not verdicts.
_BENIGN_CONTEXT_RX = re.compile(
    r"\b(?:if\s+you|if\s+your|should\s+you|tell\s+us|please\s+call|contact\s+us|"
    r"why\s+your|in\s+the\s+event|may\s+not\s+be)\b", re.I)


def _order_rejection_segment(page_text: str) -> "str | None":
    """Return the sentence rejecting THIS order, or None."""
    # Collapse punctuation INSIDE numbers so "$4.12" / "1,000" do not split a
    # sentence or hide a quantity.
    norm = re.sub(r"(?<=\d)[,.](?=\d)", "", page_text or "")
    for seg in re.split(r"[.\n;]", norm):
        if not seg.strip():
            continue
        if _BENIGN_CONTEXT_RX.search(seg):
            continue
        if _THIS_ORDER_RX.search(seg) and _REJECT_VERB_RX.search(seg):
            return seg.strip()[:160]
    return None


def _verify_fidelity_order_page(page_text: str) -> tuple[bool, str]:
    """
    Return (confirmed, reason) from Fidelity post-submit page text.
    confirmed=True means the page shows order acceptance with no error signals.
    Error patterns are checked FIRST — a page with both confirm and error text
    (e.g. 'order #12345 cannot be processed due to an error') is treated as rejected.
    """
    lower = page_text.lower()
    for pat in _ORDER_ERROR_PATTERNS:
        if pat in lower:
            return False, f"order rejected by Fidelity: '{pat}' found in page"
    seg = _order_rejection_segment(page_text)
    if seg:
        return False, f"order rejected by Fidelity: {seg!r}"
    for pat in _ORDER_CONFIRM_PATTERNS:
        if pat in lower:
            return True, f"confirmed: '{pat}' found in page"
    return False, "order confirmation NOT found in page — status unknown"

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from web.auth import require_admin, require_step_up
from web.secure_store import (
    encrypted_temp_file, is_encrypted_path, write_encrypted,
    write_encrypted_json, read_encrypted_json, broker_session_key_configured,
)
from pydantic import BaseModel, Field, field_validator

router = APIRouter()

# Persistent browser state so re-login not required each server restart.
# Fidelity sessions are keyed by the authenticated Access email. Never share a
# browser context between users because the context contains live broker cookies.
_LEGACY_STORAGE_STATE = ROOT / ".fidelity_session.json"
_PW_CONTEXTS: dict[str, object] = {}
_PW_INSTANCES: dict[str, object] = {}
_PW_BROWSERS: dict[str, object] = {}
_FIDELITY_STORAGE_PURPOSE = "fidelity-playwright-storage-state"
_FIDELITY_CREDS_PURPOSE = "fidelity-login-credentials"

# Per-user auto-relogin lock — prevents N concurrent endpoints all driving a
# fresh browser login at once when a session expires.
_RELOGIN_LOCKS: dict[str, asyncio.Lock] = {}
# Per-user relogin failure backoff: key → (next_allowed_ts, consecutive_fails).
# Stops repeated failed logins from locking the real Fidelity account when the
# stored password is stale.
_RELOGIN_BACKOFF: dict[str, tuple[float, int]] = {}
_RELOGIN_BACKOFF_BASE = 300.0   # 5 min after first failure
_RELOGIN_BACKOFF_MAX = 3600.0   # cap at 1 hour

# Users with a live order on the shared browser context. While set, background
# loops (keepalive / auto-relogin / exit-guard) must NOT reset or relogin that
# user's browser — doing so closes the context and crashes the in-flight order.
# REFCOUNTED (ticker → depth), not a set. `key in _ORDER_IN_FLIGHT` still reads
# naturally on a dict, so the consumers in web/app.py and _auto_relogin are
# unchanged.
_ORDER_IN_FLIGHT: dict[str, int] = {}


def _order_in_flight_acquire(key: str) -> None:
    """Mark one more in-flight order for this user.

    This was a set, so two concurrent orders for the same user — a buy on AAPL
    and an exit on NVDA, which take different per-ticker locks and are both
    legal — each add()ed the same key, and whichever finished FIRST discard()ed
    it. That un-protected the second order while it sat between its Place Order
    click and its confirmation read: the keepalive loop saw an idle browser,
    called _reset_browser_state, and the live order died with TargetClosedError.
    The order was already submitted at Fidelity, so the position became real,
    untracked, and unstopped.
    """
    _ORDER_IN_FLIGHT[key] = _ORDER_IN_FLIGHT.get(key, 0) + 1


def _order_in_flight_release(key: str) -> None:
    """Drop one in-flight order; the key disappears only at depth zero."""
    remaining = _ORDER_IN_FLIGHT.get(key, 0) - 1
    if remaining > 0:
        _ORDER_IN_FLIGHT[key] = remaining
    else:
        _ORDER_IN_FLIGHT.pop(key, None)


class _order_in_flight:
    """Context manager marking an active order so background loops leave the
    browser alone (prevents TargetClosedError mid-order)."""
    def __init__(self, email: str):
        self.key = _user_key(email)

    def __enter__(self):
        _order_in_flight_acquire(self.key)
        return self

    def __exit__(self, *exc):
        _order_in_flight_release(self.key)
        return False


# Users with an interactive (WS) login in progress. While set, background loops
# and status polls must NOT navigate or reset that user's shared browser context
# — doing so closes the page the login flow is driving (TargetClosedError →
# Fidelity shows "Sorry, we can't complete this action right now").
_LOGIN_IN_FLIGHT: set[str] = set()

LOGIN_URL = "https://digital.fidelity.com/ftgw/digital/login/full-page"
PORTFOLIO_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/positions"
SUMMARY_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"

# ── Playwright helpers ─────────────────────────────────────────

def _user_key(email: str) -> str:
    return (email or "").strip().lower()


# Fidelity bounces an expired session to a SIGN-IN url, not a "login" one:
#   https://digital.fidelity.com/prgw/digital/signin/retail?...
# Detecting only "login" missed this → false "connected" + failed scrapes.
_LOGIN_URL_MARKERS = ("login", "signin", "sign-in", "/prgw/digital/signin", "/ftgw/digital/login")


def _is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in _LOGIN_URL_MARKERS)


def _is_authenticated_url(url: str) -> bool:
    u = (url or "").lower()
    return "digital.fidelity" in u and not _is_login_url(u)


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
    # Dev kill-switch: refuse to launch the Playwright/Chrome automation at all.
    # Fidelity blocks true headless, so any launch is a real (if off-screen) Chrome
    # window — annoying on a frontend-dev box. This only ever *denies* browser
    # access, so it weakens no compliance gate. Callers already catch this.
    if env_bool("FIDELITY_BROWSER_DISABLED", False):
        raise RuntimeError("Fidelity browser automation disabled (FIDELITY_BROWSER_DISABLED=true)")
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
    visible_args = launch_args + ["--window-position=80,60", "--window-size=1280,900"]
    # Fidelity blocks *headless* Chrome (bot detection → positions grid never
    # renders → TargetClosedError). So we ALWAYS run a real headed browser; the
    # only knob is whether it's on-screen or parked off-screen (invisible).
    #   FIDELITY_HEADLESS=false → on-screen (debug login/scrape)
    #   otherwise (default)      → headed but off-screen at -32000,-32000 (hidden)
    on_screen = not env_bool("FIDELITY_HEADLESS", True)
    win_args = visible_args if on_screen else hidden_args
    # Prefer system Edge/Chrome (less detectable than bundled Chromium), all headed.
    for channel in ("msedge", "chrome", None):
        try:
            kwargs = {"headless": False, "args": win_args}
            if channel:
                kwargs["channel"] = channel
            browser = await instance.chromium.launch(**kwargs)
            break
        except Exception:
            browser = None
    if browser is None:
        raise RuntimeError("Could not launch any Chromium browser for Fidelity automation")
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
        # Best-effort re-encrypt migration. A storage-save failure (e.g. the
        # context was closed by a background loop) must NEVER crash context
        # creation — same contract as _save_storage.
        try:
            await _save_context_storage(context, storage_path)
        except Exception as e:
            log.warning("storage re-encrypt skipped for %s: %s", key[:16], e)
    # Suppress automation flags
    await context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    _PW_CONTEXTS[key] = context
    return context


async def _save_storage(email: str):
    """Persist the browser session (best-effort). NEVER raises — a storage-save
    failure (e.g. the context was closed by a background loop) must not crash a
    trade or any caller. A confirmed order must surface as success regardless."""
    key = _user_key(email)
    context = _PW_CONTEXTS.get(key)
    if not context:
        return
    try:
        await _save_context_storage(context, _fidelity_state_path(key))
    except Exception as e:
        log.warning("session save skipped for %s: %s", key[:16], e)


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
    # During an interactive login, never touch the shared browser context — a
    # status-poll navigation here would close the page the login is driving.
    if _user_key(email) in _LOGIN_IN_FLIGHT:
        return _get_session_cache(email) is True
    # Device known-untrusted / session known-dead and NOT silently recoverable:
    # do NOT open a browser or navigate to Fidelity on every status poll / loop
    # tick. That repeated login-page traffic is exactly what rate-limits the
    # account. Report not-connected until an interactive login clears the gate.
    if _is_manual_login_required(email):
        return False
    # Return cached state if fresh — avoids 20-30s Playwright navigation on every status poll
    cached = _get_session_cache(email)
    if cached is not None:
        return cached

    key = _user_key(email)
    if _PW_CONTEXTS.get(key) is None:
        if not _fidelity_state_path(key).exists():
            _set_session_cache(email, False)
            return False
        try:
            await _ensure_browser(key)
        except Exception:
            _set_session_cache(email, False)
            return False
    try:
        ctx = await _ensure_browser(key)
        page = await ctx.new_page()
        try:
            await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)
            url = page.url
            result = _is_authenticated_url(url)
            _set_session_cache(email, result)
        finally:
            try:
                await page.close()
            except Exception:
                pass
        if result:
            return True
    except Exception:
        _set_session_cache(email, False)
    # Session looks dead — try a silent re-login from stored creds (device must
    # be trusted; otherwise returns False and the user logs in manually).
    return await _auto_relogin(email)


async def _handle_login_redirect(email: str, page) -> bool:
    """Call when a *data* page unexpectedly redirected to Fidelity login.

    Marks the cache dead immediately (so /status stops lying) and attempts a
    silent re-login. Returns True if recovered — caller should re-navigate.
    """
    _set_session_cache(email, False)
    return await _auto_relogin(email)


async def _close_session(email: str):
    key = _user_key(email)
    await _reset_browser_state(key)
    path = _fidelity_state_path(key)
    if path.exists():
        path.unlink()
    # Wipe stored credentials too — logout means no silent re-login.
    cpath = _fidelity_creds_path(key)
    if cpath.exists():
        try:
            cpath.unlink()
        except Exception:
            pass


# ── Credential storage (for silent auto re-login) ──────────────
# Username + password are stored ENCRYPTED (Fernet, BROKER_SESSION_KEY) so the
# server can silently re-login when a session expires. No TOTP secret is stored;
# silent re-login only works once the device is trusted (Fidelity skips 2FA).
def _fidelity_creds_path(email: str) -> Path:
    digest = hashlib.sha256(_user_key(email).encode()).hexdigest()[:16]
    return ROOT / f".fidelity_creds_{digest}.json"


def _manual_login_required_path(email: str) -> Path:
    digest = hashlib.sha256(_user_key(email).encode()).hexdigest()[:16]
    return ROOT / f".fidelity_manual_login_{digest}.flag"


def _is_manual_login_required(email: str) -> bool:
    """True once silent re-login has *proven* the device isn't trusted.

    Persisted to disk (survives server restarts) so launchd/autofix restarts
    can't wipe the state and resume hammering Fidelity's login — repeated
    credential submissions with no human to enter the TOTP get the account
    rate-limited/locked. Only an interactive login (which seeds device trust)
    clears it. See [[project_fidelity_auto_relogin_2026-06-16]].
    """
    return _manual_login_required_path(email).exists()


def _set_manual_login_required(email: str) -> None:
    try:
        _manual_login_required_path(email).write_text(str(_time.time()))
    except Exception:
        pass


def _clear_manual_login_required(email: str) -> None:
    try:
        p = _manual_login_required_path(email)
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _credential_storage_enabled() -> bool:
    """Opt-in. Storing a recoverable password is off unless explicitly enabled."""
    return env_bool("FIDELITY_STORE_CREDENTIALS", False)


def _save_credentials(email: str, username: str, password: str) -> None:
    if not _credential_storage_enabled():
        log.info("FIDELITY_STORE_CREDENTIALS not enabled — not storing creds (manual re-login).")
        return
    if not broker_session_key_configured():
        log.warning("BROKER_SESSION_KEY not configured — NOT storing Fidelity credentials (no auto re-login).")
        return
    # The on-disk auto-generated key sits next to the ciphertext: anyone with the
    # box gets both. An env-supplied key is materially stronger. Warn, don't block.
    try:
        from web.secure_store import broker_session_key_status
        if broker_session_key_status().get("source") == "local_file":
            log.warning(
                "Storing Fidelity creds with an ON-DISK encryption key (tmp/broker_session.key). "
                "For stronger protection set BROKER_SESSION_KEY in the environment instead."
            )
    except Exception:
        pass
    try:
        write_encrypted_json(
            _fidelity_creds_path(email),
            {"username": username, "password": password},
            _FIDELITY_CREDS_PURPOSE,
        )
    except Exception as e:
        log.warning("Failed to store Fidelity credentials: %s", e)


def _load_credentials(email: str) -> dict | None:
    path = _fidelity_creds_path(email)
    if not path.exists():
        return None
    try:
        data = read_encrypted_json(path, _FIDELITY_CREDS_PURPOSE)
        if data.get("username") and data.get("password"):
            return data
    except Exception as e:
        log.warning("Failed to read Fidelity credentials: %s", e)
    return None


async def _tick_trust_in_frame(frame) -> bool:
    """Find + tick the 'remember/trust this device' checkbox within ONE frame.

    Fidelity's 2FA page renders a PVD design-system checkbox
    (id="dom-trust-device-checkbox") whose native <input class="pvd-checkbox__checkbox">
    is visually hidden for screen-reader styling
    (clip:rect(1px 1px 1px 1px); position:absolute; height/width:1px; overflow:hidden).
    A plain Playwright .check() fails the actionability *visibility* wait on that
    hidden input and silently does nothing. We read state with .is_checked() (no
    visibility required) and tick with .check(force=True), then fall back to
    clicking the visible <label for="...">. Returns True only if the box ends
    checked. Safe to call when no such control is present in this frame.
    """
    # Exact Fidelity id first, then generic fallbacks for other/legacy layouts.
    for sel in (
        "#dom-trust-device-checkbox",
        "input[type='checkbox'][id*='trust' i]",
        "input[type='checkbox'][id*='remember' i]",
        "input[type='checkbox'][name*='trust' i]",
        "input[type='checkbox'][name*='remember' i]",
        "input[type='checkbox'][aria-label*='remember' i]",
        "input[type='checkbox'][aria-label*='trust' i]",
    ):
        try:
            loc = frame.locator(sel).first
            await loc.wait_for(state="attached", timeout=2500)
        except Exception:
            continue
        # is_checked() reads the input's state without requiring it to be visible.
        try:
            if await loc.is_checked():
                log.info("Trust-device already checked (%s)", sel)
                return True
        except Exception:
            pass
        # 1) force-check the hidden native input (force=True bypasses the
        #    visibility/actionability wait that clip-hidden inputs always fail).
        try:
            await loc.check(force=True, timeout=2500)
            if await loc.is_checked():
                log.info("Trust-device checkbox ticked (%s)", sel)
                return True
        except Exception:
            pass
        # 2) fall back to clicking the visible <label for="..."> (toggles the
        #    input; only reached while still unchecked, so it turns it on).
        try:
            cid = await loc.get_attribute("id")
            if cid:
                await frame.locator(f"label[for='{cid}']").first.click(timeout=2500)
                if await loc.is_checked():
                    log.info("Trust-device ticked via label (%s)", cid)
                    return True
        except Exception:
            pass
    # 3) accessibility-role + label-text fallback (layout-independent): the
    #    "remember/trust/don't ask" checkbox by its accessible name.
    for name_rx in (r"remember", r"trust this", r"don't ask", r"do not ask"):
        try:
            loc = frame.get_by_role("checkbox", name=__import__("re").compile(name_rx, __import__("re").I)).first
            await loc.wait_for(state="attached", timeout=1500)
            if await loc.is_checked():
                return True
            await loc.check(force=True, timeout=2000)
            if await loc.is_checked():
                log.info("Trust-device ticked via role/name (%s)", name_rx)
                return True
        except Exception:
            continue
    return False


async def _check_trust_device(page) -> bool:
    """Tick 'remember/trust this device' so future logins skip 2FA — across ALL
    frames.

    The previous version searched only the main frame. Fidelity sometimes renders
    the 2FA step (and its trust checkbox) inside an IFRAME, so every selector
    missed and the device was never trusted → 2FA demanded on every login, and
    silent re-login could never succeed. We now try the main frame AND each child
    frame. Returns True if the box ends checked anywhere.
    """
    frames = []
    try:
        frames = list(page.frames)          # includes the main frame
    except Exception:
        frames = []
    if page not in frames:
        frames = [page] + frames
    for fr in frames:
        try:
            if await _tick_trust_in_frame(fr):
                return True
        except Exception:
            continue
    log.warning("Trust-device checkbox not found/ticked — 2FA may be required next login")
    return False


def _fidelity_totp_code() -> "str | None":
    """Current 6-digit TOTP from the OPTIONAL ``FIDELITY_TOTP_SECRET`` env (the
    base32 authenticator seed). Lets silent re-login answer 2FA fully unattended.

    Returns None when unset/invalid (silent re-login then falls back to requiring
    an interactive login). The secret is never logged. Only enables broker SESSION
    auth — every real order still passes compliance + per-trade step-up 2FA, so
    this does not weaken order authorization.
    """
    secret = os.getenv("FIDELITY_TOTP_SECRET", "").strip().replace(" ", "")
    if not secret:
        return None
    try:
        import pyotp
        return pyotp.TOTP(secret).now()
    except Exception as e:
        log.warning("FIDELITY_TOTP_SECRET present but TOTP generation failed: %s", e)
        return None


def _get_relogin_lock(email: str) -> asyncio.Lock:
    key = _user_key(email)
    if key not in _RELOGIN_LOCKS:
        _RELOGIN_LOCKS[key] = asyncio.Lock()
    return _RELOGIN_LOCKS[key]


async def _auto_relogin(email: str) -> bool:
    """Silently re-establish a Fidelity session from stored credentials.

    Returns True only if it lands on an authenticated page WITHOUT needing 2FA
    (relies on a previously trusted device). Never prompts; if 2FA is required
    it gives up and returns False so the caller surfaces a manual-login error.
    """
    # Device proven untrusted — do NOT keep submitting credentials on a timer;
    # there is no human in silent mode to enter the TOTP, so every attempt just
    # burns a Fidelity login and risks a rate-limit/lockout. Stay parked until an
    # interactive login seeds device trust (which clears this flag).
    if _is_manual_login_required(email):
        log.debug("Silent re-login skipped — manual login required (device not trusted): %s", email[:20])
        return False
    creds = _load_credentials(email)
    if not creds:
        return False
    key = _user_key(email)
    # Never reset/relogin the browser while an order OR an interactive login is in
    # flight — it would close the context they're driving (TargetClosedError).
    if key in _ORDER_IN_FLIGHT or key in _LOGIN_IN_FLIGHT:
        return _get_session_cache(email) is True
    # Respect failure backoff so stale creds can't hammer Fidelity into a lockout.
    bo = _RELOGIN_BACKOFF.get(key)
    if bo and _time.time() < bo[0]:
        log.debug("Silent re-login in backoff for %s (%.0fs left)", email[:20], bo[0] - _time.time())
        return False

    def _note_failure(wipe: bool = False) -> None:
        fails = (_RELOGIN_BACKOFF.get(key, (0.0, 0))[1]) + 1
        delay = min(_RELOGIN_BACKOFF_BASE * (2 ** (fails - 1)), _RELOGIN_BACKOFF_MAX)
        _RELOGIN_BACKOFF[key] = (_time.time() + delay, fails)
        _set_session_cache(email, False)
        if wipe:
            # Credentials definitively rejected — delete them so we stop trying
            # with a known-bad password and force a clean manual re-login.
            cpath = _fidelity_creds_path(key)
            if cpath.exists():
                try:
                    cpath.unlink()
                except Exception:
                    pass
            log.warning("Stored Fidelity creds rejected — wiped; manual login required: %s", email[:20])

    lock = _get_relogin_lock(email)
    # If another coroutine is already relogging in, wait for it then report state.
    if lock.locked():
        async with lock:
            return _get_session_cache(email) is True
    async with lock:
        log.info("Attempting silent Fidelity re-login: %s", email[:20])
        try:
            await _reset_browser_state(email)  # drop any half-dead context
            ctx = await _ensure_browser(email)
            page = await ctx.new_page()
            try:
                await page.goto(LOGIN_URL, wait_until="commit", timeout=60_000)
                await asyncio.sleep(3)
                state = await _login_fill(page, creds["username"], creds["password"])
                if state == "need_totp":
                    # Autonomous 2FA: if a TOTP seed is configured, answer the
                    # challenge ourselves AND tick trust-device so the NEXT silent
                    # re-login won't even need a code. Without a seed we can't
                    # proceed unattended — park until an interactive login.
                    code = _fidelity_totp_code()
                    if not code:
                        log.warning("Silent re-login needs 2FA and no FIDELITY_TOTP_SECRET set — manual login required: %s", email[:20])
                        _set_manual_login_required(email)
                        _note_failure()
                        return False
                    await _check_trust_device(page)   # tick BEFORE submitting the code
                    filled = await _try_fill(page, [
                        "input[name='OTP']",
                        "input[id*='otp' i]",
                        "input[id*='totp' i]",
                        "input[placeholder*='code' i]",
                        "input[type='number']",
                        "input[maxlength='6']",
                        "input[maxlength='8']",
                        "input[type='text']",
                    ], code, timeout=8000)
                    if not filled:
                        log.warning("Silent re-login: TOTP field not found — manual login required: %s", email[:20])
                        _set_manual_login_required(email)
                        _note_failure()
                        return False
                    await _try_click(page, [
                        "button[type='submit']",
                        "button[data-testid='submitBtn']",
                        "button[id*='continue' i]",
                        "button[id*='submit' i]",
                        "button[id*='verify' i]",
                    ], timeout=5000)
                    await asyncio.sleep(3)
                    state = await _detect_page_state(page)
                    if state != "authenticated":
                        await _check_trust_device(page)   # post-submit interstitial
                    if state == "need_totp":
                        # Generated code rejected (clock skew / wrong seed) — don't
                        # loop on a bad secret; require an interactive login.
                        log.warning("Silent re-login: generated TOTP rejected — manual login required: %s", email[:20])
                        _set_manual_login_required(email)
                        _note_failure()
                        return False
                if state == "login_error":
                    _note_failure(wipe=True)
                    return False
                if state != "authenticated":
                    await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20_000)
                    await asyncio.sleep(2)
                    state = await _detect_page_state(page)
                if state == "authenticated":
                    await _save_storage(email)
                    _set_session_cache(email, True)
                    _RELOGIN_BACKOFF.pop(key, None)  # clear backoff on success
                    log.info("Silent re-login succeeded: %s", email[:20])
                    return True
                _note_failure()
                return False
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        except Exception as e:
            log.warning("Silent re-login failed for %s: %s", email[:20], e)
            _note_failure()
            return False


async def _login_fill(page, username: str, password: str) -> str:
    """Fill username → next → password → submit, tick trust-device, return page state.

    Shared by the interactive WS auth flow and silent re-login. Does NOT handle
    TOTP entry (caller does); it only ticks the trust-device box when present.
    """
    filled_user = await _try_fill(page, [
        "#dom-username-input",
        "input[name='userId']",
        "input[id*='username' i]",
        "input[placeholder*='username' i]",
        "input[type='text']",
    ], username, timeout=8000)
    if not filled_user:
        return "login_page"
    next_clicked = await _try_click(page, [
        "button[data-testid='nextBtn']",
        "button[id*='next' i]",
        "#dom-username-go-button",
        "button[type='submit']",
    ], timeout=3000)
    if next_clicked:
        await asyncio.sleep(1.5)
    filled_pw = await _try_fill(page, [
        "#dom-pswd-input",
        "input[name='password']",
        "input[type='password']",
        "input[id*='password' i]",
    ], password, timeout=8000)
    if not filled_pw:
        return "login_page"
    # NOTE: the trust-device checkbox lives ONLY on the 2FA page, never on this
    # credential page. Calling _check_trust_device here just burned ~16s waiting
    # for a checkbox that can't exist and logged a misleading "not found". The
    # real tick happens in the need_totp branch of the WS auth flow.
    await _try_click(page, [
        "#dom-login-button",
        "button[data-testid='loginBtn']",
        "button[type='submit']",
        "#fs-login-button",
    ], timeout=5000)
    await asyncio.sleep(3)
    return await _detect_page_state(page)


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

    # Authenticated: must be on digital.fidelity.com on an authenticated path.
    # Restricting to digital.fidelity.com prevents www.fidelity.com homepage
    # (reachable after bad credentials) from being misclassified as authenticated.
    if "digital.fidelity.com" in url and any(
        k in url for k in ("portfolio", "accounts", "summary", "balances", "positions", "dashboard", "ftgw/digital/pntgate")
    ):
        return "authenticated"
    if any(k in url for k in ("twofactor", "mfa", "verify", "otp", "2fa")):
        return "need_totp"
    if any(k in html for k in ("verification code", "one-time", "authenticator", "security code", "enter the code")):
        return "need_totp"
    if _is_login_url(url) or "username" in html or "sign in" in html:
        return "login_page"
    if any(k in html for k in ("incorrect", "invalid", "failed", "error", "wrong")):
        return "login_error"
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

    # Freeze background browser resets (keepalive / auto-relogin / status polls)
    # for the whole interactive login — otherwise they close the shared Playwright
    # context mid-login (TargetClosedError → "can't complete this action" loop).
    _login_key = _user_key(user_email)
    _LOGIN_IN_FLIGHT.add(_login_key)
    try:
        await _reset_browser_state(user_email)
        ctx = await _ensure_browser(user_email)
        page = await ctx.new_page()

        # Inner try/finally ensures page is always closed, even on client disconnect
        try:
            await send({"step": "logging_in", "message": "Navigating to Fidelity login…"})
            await page.goto(LOGIN_URL, wait_until="commit", timeout=60_000)
            await asyncio.sleep(4)

            # --- Username + password (shared with silent re-login) ---
            await send({"step": "logging_in", "message": "Entering credentials…"})
            state = await _login_fill(page, username, password)
            log.info("WS login %s: post-fill state=%s url=%s", user_email[:16], state, page.url)

            # --- TOTP / 2FA ---
            if state == "need_totp":
                log.info("WS login %s: 2FA required → prompting client for code", user_email[:16])
                # Tick the trust-device box on the 2FA page so future logins skip 2FA.
                trusted = await _check_trust_device(page)
                log.info("WS login %s: trust-device ticked=%s", user_email[:16], trusted)
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
                    return

                code = str(totp_msg.get("totp", "")).strip()
                if not code:
                    await send({"step": "error", "message": "No verification code provided"})
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
                    return

                # Tick trust-device again NOW (code is filled, checkbox is on this
                # page) so the choice is included in the verify submission — the
                # most reliable timing. The earlier pre-prompt tick can miss when
                # the control renders only after the code field is populated.
                trusted2 = await _check_trust_device(page)
                log.info("WS login %s: trust-device ticked (pre-submit)=%s", user_email[:16], trusted2)

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
                # Some flows show a SEPARATE "remember this device?" interstitial
                # after the code is accepted — tick its checkbox if present.
                if state != "authenticated":
                    await _check_trust_device(page)

                # Wrong TOTP: Fidelity stays on MFA page or shows error — never authenticated.
                if state == "need_totp":
                    html_snippet = await page.locator("body").inner_text()
                    err_lines = [l.strip() for l in html_snippet.splitlines() if any(k in l.lower() for k in ("incorrect", "invalid", "failed", "error", "wrong", "expired"))]
                    await send({"step": "error", "message": "Verification code rejected: " + (err_lines[0] if err_lines else "incorrect or expired code")})
                    return

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
                return

            if state != "authenticated":
                # Try navigating to portfolio directly
                await page.goto(PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(2)
                state = await _detect_page_state(page)

            if state != "authenticated":
                log.warning("WS login %s: did not complete, final state=%s url=%s", user_email[:16], state, page.url)
                await send({"step": "error", "message": f"Login did not complete. Current URL: {page.url}"})
                return

            log.info("WS login %s: AUTHENTICATED", user_email[:16])
            await _save_storage(user_email)
            _set_session_cache(user_email, True)
            # This interactive login ticked the trust-device box, so silent
            # re-login can resume: clear the manual-login gate and any backoff.
            _clear_manual_login_required(user_email)
            _RELOGIN_BACKOFF.pop(_user_key(user_email), None)
            # Store creds (encrypted) so the server can silently re-login when
            # the session later expires — no manual login on trade approval.
            _save_credentials(user_email, username, password)
            await send({"step": "authenticated", "message": "Connected to Fidelity successfully"})

        finally:
            try:
                await page.close()
            except Exception:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.exception("ws_fidelity_auth unhandled error")
        await send({"step": "error", "message": str(e)})
    finally:
        _LOGIN_IN_FLIGHT.discard(_login_key)


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
        # True ⇒ server can reconnect silently when the session expires (no
        # manual login on trade approval), provided the device stays trusted.
        "auto_relogin": _load_credentials(email) is not None,
        "broker_key_configured": broker_session_key_configured(),
        "credential_storage_enabled": _credential_storage_enabled(),
    }


@router.post("/fidelity/logout")
async def fidelity_logout(admin: dict = Depends(require_admin)):
    _set_session_cache(admin["email"], False)
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


async def _scrape_positions(email: str) -> dict:
    """Cold Playwright scrape of the Fidelity positions grid — the slow path the
    snapshot cache shields. Raises HTTPException on auth/load failure."""
    from fastapi import HTTPException
    ctx = await _ensure_browser(email)
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)

        if _is_login_url(page.url):
            # Session died mid-flight — try a silent re-login, then re-navigate once.
            if await _handle_login_redirect(email, page):
                await _nav(page, PORTFOLIO_URL, sleep=6)
            if _is_login_url(page.url):
                raise HTTPException(status_code=401, detail="Not authenticated with Fidelity. Log in at /broker.")

        grid_loaded = True
        try:
            await page.wait_for_selector(
                '.ag-pinned-left-cols-container .ag-row[row-index]', timeout=15_000
            )
        except Exception:
            grid_loaded = False
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

            // Iterate in numeric row-index order so account-group header rows are
            // seen BEFORE the positions that belong to them. Fidelity renders an
            // 'Account: <name> <number>' header row above each account's holdings.
            let curAcctName = '';
            let curAcctNum = '';
            const ordered = Object.entries(symMap).sort((a, b) => Number(a[0]) - Number(b[0]));

            ordered.forEach(([ri, symText]) => {
                const lines = symText.split('\\n').map(l => l.trim()).filter(Boolean);
                const ticker = lines[0] || '';

                // Account-group header row → update current account context.
                if (/^Account/i.test(symText) || /^[A-Z]?\\d{6,12}$/.test(ticker)) {
                    const numMatch = symText.match(/\\b([A-Z]?\\d{6,12})\\b/);
                    curAcctNum = numMatch ? numMatch[1] : '';
                    curAcctName = symText.replace(/^Account:?/i, '')
                        .replace(/\\b[A-Z]?\\d{6,12}\\b/, '').replace(/\\s+/g, ' ').trim();
                    return;
                }

                // Capture grand total
                if (ticker.startsWith('Grand total')) {
                    const d = dataMap[ri] || {};
                    grandTotals.total_value = (d.curVal || '').split('\\n')[0];
                    const todLines = (d.todGLStk || '').split('\\n').filter(l => l && !/Not Priced/.test(l));
                    grandTotals.daily_change = todLines[0] || '';
                    grandTotals.daily_change_pct = todLines[1] || '';
                    return;
                }

                // Skip non-position rows (core money-market funds are cash, not equity)
                const _mm = ['SPAXX','FDRXX','FZFXX','SPRXX','FCASH','FMPXX','FGTXX','FNSXX','FZCXX','QACDS','FDLXX','FZDXX'];
                const _tk = ticker.toUpperCase().replace(/[^A-Z0-9]/g, '');
                if (!ticker || ticker.length > 10 || !/^[A-Z]/.test(ticker) ||
                    SKIP.some(s => ticker.startsWith(s)) ||
                    _mm.some(m => _tk.startsWith(m))) return;

                const desc = lines.find((l, i) => i > 0 && l.length > 2 && !/^Not Priced|^\\$|^[+-]/.test(l)) || '';
                const d = dataMap[ri] || {};

                const lstLines = (d.lstPrStk || '').split('\\n');
                const todLines = (d.todGLStk || '').split('\\n').filter(l => l && !/Not Priced/.test(l));
                const totLines = (d.totGLStk || '').split('\\n');
                const cstLines = (d.cstBasStk || '').split('\\n');

                positions.push({
                    symbol:          ticker,
                    description:     desc,
                    account_number:  curAcctNum,
                    account_name:    curAcctName,
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

        await _save_storage(email)
        positions = result.get("positions", [])
        grand = result.get("grandTotals", {})
        # Grid never rendered AND nothing parsed → treat as a transient load
        # failure, not a real empty account. Caller can retry instead of
        # showing a misleading "0 positions" on a live, funded account.
        if not grid_loaded and not positions and not grand:
            raise HTTPException(
                status_code=503,
                detail="Fidelity positions grid did not load (session may be stale). Refresh and retry.",
            )
        return {"positions": positions, "grand_totals": grand, "url": page.url,
                "count": len(positions), "grid_loaded": grid_loaded}
    finally:
        await page.close()


async def _revalidate_positions(email: str) -> None:
    """Background snapshot refresh (deduped per user). A failure KEEPS the last good
    snapshot rather than clobbering it with an empty/error result."""
    key = "pos:" + _user_key(email)
    if key in _POS_REFRESH_INFLIGHT:
        return
    _POS_REFRESH_INFLIGHT.add(key)
    try:
        data = await _scrape_positions(email)
        _write_snapshot(email, "positions", data)
    except Exception as e:
        log.info("positions revalidate skipped for %s (keeping snapshot): %s",
                 _session_owner_hash(email), e)
    finally:
        _POS_REFRESH_INFLIGHT.discard(key)


@router.get("/fidelity/positions")
async def fidelity_positions(admin: dict = Depends(require_admin), refresh: bool = False):
    """Holdings for the Broker page. Serves the last snapshot INSTANTLY from disk
    (no browser) and refreshes in the background; ``?refresh=1`` forces a fresh
    scrape. Only the first-ever load (no snapshot yet) blocks on a scrape."""
    from fastapi import HTTPException
    email = admin["email"]
    if not refresh:
        cached = _read_snapshot(email, "positions")
        if cached is not None:
            meta = _snapshot_meta(cached)
            if meta["stale"]:
                asyncio.create_task(_revalidate_positions(email))  # refresh in bg
            return {**cached, **meta}
    # No snapshot yet, or a forced refresh → scrape now (and cache it).
    try:
        data = await _scrape_positions(email)
    except HTTPException:
        if not refresh:
            cached = _read_snapshot(email, "positions")
            if cached is not None:
                return {**cached, **_snapshot_meta(cached)}  # serve last good on failure
        raise
    _write_snapshot(email, "positions", data)
    return {**data, "cached": False, "age_seconds": 0.0, "stale": False,
            "scraped_at": _time.time()}


@router.get("/fidelity/summary")
async def fidelity_summary(admin: dict = Depends(require_admin)):
    """Pull summary from positions page (grand total row) — no separate navigation."""
    from fastapi import HTTPException
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)

        if _is_login_url(page.url):
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
    quote_time:   Optional[str] = None
    quote_source: Optional[str] = None
    backup_sources: list[str] = Field(default_factory=list)
    consensus_ok: Optional[bool] = None
    bid:          Optional[float] = None
    ask:          Optional[float] = None
    market_open: Optional[bool] = None

    @classmethod
    def __get_validators__(cls):
        yield cls.validate_model

    def model_post_init(self, __context) -> None:
        from fastapi import HTTPException
        self.symbol = self.symbol.upper().strip()
        if not valid_symbol(self.symbol):
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
        _assert_account_tradeable(body.account)  # block protected (Roth/IRA) accounts
        import datetime as _dt
        order = body.model_dump()
        # Naive-local 'now' matches the gateway's naive-local quote_time (F3) —
        # compliance otherwise falls back to utcnow() and skews the age check.
        order["now"] = _dt.datetime.now().isoformat(timespec="seconds")
        if body.execute:
            # F2: the server's trusted gateway quote is the SOLE execution
            # evidence — caller-supplied quote_* is overwritten, never trusted.
            # limit_price is an order parameter here (user may deliberately place
            # away from market), so limit_factor=None leaves it untouched.
            _loop = asyncio.get_running_loop()
            _tq = await _loop.run_in_executor(None, _trusted_quote_fields, body.symbol)
            try:
                _apply_execution_quote(order, _tq, limit_factor=None)
            except ValueError as e:
                raise HTTPException(status_code=503, detail=str(e))
        decision = validate_live_order(order)
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

            if _is_login_url(page.url):
                raise HTTPException(status_code=401, detail="Not authenticated with Fidelity")

            await _select_and_verify_account(page, account)

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
                # Playwright fill — a JS native-setter does NOT register with
                # Fidelity's input framework ("Please enter a quantity" at preview).
                qty_val = str(body.quantity)
                qty_loc = page.locator('#eqt-shared-quantity')
                await qty_loc.wait_for(state="visible", timeout=8000)
                await qty_loc.fill(qty_val)
                await qty_loc.press("Tab")
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

            # Block on any non-confirmed preview — prevents placing against error/unknown page
            preview_ok, preview_msg = _verify_fidelity_order_page(preview_text)
            if not preview_ok:
                raise HTTPException(status_code=400, detail=f"Preview not confirmed: {preview_msg}\n\nPage excerpt:\n{preview_text[:400]}")

            order_status = "previewed"
            if body.execute:
                try:
                    place_btn = page.locator('button:has-text("Place Order")').first
                    await place_btn.wait_for(state="visible", timeout=8000)
                    await place_btn.click()
                    # Poll up to 15 s for confirmation or rejection phrase
                    confirm_text = ""
                    for _ in range(5):
                        await asyncio.sleep(3)
                        confirm_text = await page.evaluate("() => document.body.innerText")
                        c_ok, _ = _verify_fidelity_order_page(confirm_text)
                        if c_ok or any(p in confirm_text.lower() for p in _ORDER_ERROR_PATTERNS):
                            break

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
ORDER_AUDIT_FILE = ROOT / "tmp" / "fidelity_order_audit.jsonl"
ORDER_AUDIT_SHOTS = ROOT / "tmp" / "order_audit"


async def _presubmit_audit(page, email: str, intent, ok: bool, reasons: list) -> None:
    """Capture a pre-submit screenshot + structured audit record right before the
    'Place Order' click (SnapTrade migration hardening). Best-effort — a failed
    screenshot/log must never block or crash the trade path. Account is masked;
    files are written owner-only (0600)."""
    import datetime as _dt, json as _json
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shot_rel = None
    try:
        ORDER_AUDIT_SHOTS.mkdir(parents=True, exist_ok=True)
        shot = ORDER_AUDIT_SHOTS / f"{stamp}_{intent.symbol}.png"
        await page.screenshot(path=str(shot))
        os.chmod(shot, 0o600)
        shot_rel = shot.name
    except Exception as e:
        log.debug("presubmit screenshot failed: %s", e)
    try:
        row = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "user": hashlib.sha256(_user_key(email).encode()).hexdigest()[:12],
            "account_mask": intent.account_mask,
            "symbol": intent.symbol,
            "side": intent.side,
            "quantity": intent.quantity,
            "order_type": intent.order_type,
            "limit_price": intent.limit_price,
            "est_cost": intent.est_cost,
            "ticket_verified": bool(ok),
            "reasons": list(reasons or []),
            "screenshot": shot_rel,
        }
        ORDER_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ORDER_AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(row) + "\n")
        os.chmod(ORDER_AUDIT_FILE, 0o600)
    except Exception as e:
        log.debug("presubmit audit log failed: %s", e)


def _snaptrade_recent_orders(email: str) -> list[dict]:
    """Best-effort: pull normalized executed orders across the user's SnapTrade
    Fidelity accounts. Returns [] if SnapTrade is off/unlinked/unavailable — never
    raises into the trade path. Fidelity order history via SnapTrade is executed-only
    and can lag, so reconciliation is a post-hoc confirmation, not a gate."""
    try:
        from web.broker.snaptrade_data import SnapTradeDataProvider, is_enabled
        from web.broker import snaptrade_store as _store
        if not (is_enabled() and _store.is_linked(email)):
            return []
        creds = _store.get_credentials(email)
        if not creds:
            return []
        uid, secret = creds
        prov = SnapTradeDataProvider()
        accts = prov.list_accounts(uid, secret).get("accounts", [])
        orders: list[dict] = []
        for a in accts:
            aid = a.get("account_id")
            if aid:
                orders.extend(prov.get_orders(uid, secret, aid).get("orders", []))
        return orders
    except Exception as e:
        log.debug("snaptrade recent-orders lookup failed: %s", e)
        return []


def _reconcile_fill(email: str, intent) -> dict:
    """Reconcile a just-submitted order against SnapTrade executed history when
    available. Returns the recon result dict (source='none' when SnapTrade lacks
    the order yet — expected given Fidelity's lag)."""
    try:
        from tradingagents.brokers.reconcile import reconcile_fill
        return reconcile_fill(intent, _snaptrade_recent_orders(email)).as_dict()
    except Exception as e:
        log.debug("fill reconcile failed: %s", e)
        return {"matched": False, "status": "no_data", "source": "none", "discrepancies": []}


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
        if _is_login_url(page.url):
            # Session died — try silent re-login, re-navigate once before giving up.
            if await _handle_login_redirect(email, page):
                await _nav(page, PORTFOLIO_URL, sleep=6)
            if _is_login_url(page.url):
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

            // Fall back to pinned-row Cash scraping.
            // Fidelity holds uninvested cash in a CORE money-market fund (e.g. SPAXX)
            // that renders as a position row "SPAXX**", NOT a row labelled "Cash".
            // Treat those core funds as available cash too, else buying power reads $0
            // (or only a tiny settled-cash row) and sizing never reflects the SPAXX balance.
            const MMKT = ['SPAXX','FDRXX','FZFXX','SPRXX','FCASH','FMPXX','FGTXX','FNSXX','FZCXX','QACDS','FDLXX','FZDXX'];
            const cashRows = [];
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const sym = row.querySelector('[col-id="sym"]')?.innerText?.trim() || '';
                const ticker = (sym.split('\\n')[0] || '').trim();
                const tU = ticker.toUpperCase().replace(/[^A-Z0-9& ]/g, '');
                const isCash = ticker.startsWith('Cash') || tU === 'CASH'
                    || tU.startsWith('CASH & CASH') || MMKT.some(m => tU.startsWith(m));
                if (isCash) {
                    const ri = row.getAttribute('row-index');
                    const center = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${ri}"]`);
                    const val = center?.querySelector('[col-id="curVal"]')?.innerText?.trim().split('\\n')[0] || '';
                    cashRows.push({label: ticker, value: val});
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

            // Per-account balances (F4): walk pinned-left rows in numeric
            // row-index order tracking 'Account: <name> <number>' header rows —
            // the same technique the positions scrape uses (live-verified; Roth
            // protection depends on it). Per account: value = every data row's
            // curVal in that section, cash = Cash/MMKT rows only.
            const accounts = [];
            let curAcct = null;
            const symMap = {};
            document.querySelectorAll('.ag-pinned-left-cols-container .ag-row[row-index]').forEach(row => {
                const cell = row.querySelector('[col-id="sym"]');
                if (cell) symMap[row.getAttribute('row-index')] = cell.innerText.trim();
            });
            const ordered = Object.entries(symMap).sort((a, b) => Number(a[0]) - Number(b[0]));
            ordered.forEach(([ri, symText]) => {
                const ticker = (symText.split('\\n')[0] || '').trim();
                if (/^Account/i.test(symText) || /^[A-Z]?\\d{6,12}$/.test(ticker)) {
                    const numMatch = symText.match(/\\b([A-Z]?\\d{6,12})\\b/);
                    curAcct = {
                        number: numMatch ? numMatch[1] : '',
                        name: symText.replace(/^Account:?/i, '')
                            .replace(/\\b[A-Z]?\\d{6,12}\\b/, '').replace(/\\s+/g, ' ').trim(),
                        valueRaw: [], cashRaw: [],
                    };
                    accounts.push(curAcct);
                    return;
                }
                if (ticker.startsWith('Grand total')) { curAcct = null; return; }
                if (!curAcct) return;
                const center = document.querySelector(`.ag-center-cols-container .ag-row[row-index="${ri}"]`);
                const val = center?.querySelector('[col-id="curVal"]')?.innerText?.trim().split('\\n')[0] || '';
                if (!val) return;
                curAcct.valueRaw.push(val);
                const tU = ticker.toUpperCase().replace(/[^A-Z0-9& ]/g, '');
                const isCash = ticker.startsWith('Cash') || tU === 'CASH'
                    || tU.startsWith('CASH & CASH') || MMKT.some(m => tU.startsWith(m));
                if (isCash) curAcct.cashRaw.push(val);
            });

            return { tiles, cashRows, grandTotal, accountOptions, accounts };
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

        # E2FP5: do NOT invent a balance when cash scrape fails — a safety system must
        # refuse to act on an unknown balance. Leave cash_val as None; callers will abort.

        # Per-account balances (F4). Unparseable value/cash stays None — the
        # sizing scope helper treats None as "cannot size" (fail closed).
        accounts = []
        for acct in result.get("accounts") or []:
            vals = [_parse_dollar(v) for v in acct.get("valueRaw") or []]
            vals = [v for v in vals if v is not None]
            cash_vals = [_parse_dollar(v) for v in acct.get("cashRaw") or []]
            cash_vals = [v for v in cash_vals if v is not None]
            accounts.append({
                "number": str(acct.get("number") or ""),
                "name":   str(acct.get("name") or ""),
                "value":  round(sum(vals), 2) if vals else None,
                "cash":   round(sum(cash_vals), 2) if cash_vals else None,
            })

        return {
            "total_value":     grand_total,
            "available_cash":  cash_val,
            "accounts":        accounts,
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
    import math

    # Fail closed on any non-finite / non-positive core input. A NaN slips past
    # 'price <= 0' (NaN compares False) and would reach int(alloc/price) →
    # int(NaN) → ValueError, crashing the real-money sizer. No trade on garbage.
    def _finite(x) -> float | None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    av, cash, px = _finite(account_value), _finite(available_cash), _finite(price)
    if av is None or cash is None or px is None or av <= 0 or cash <= 0 or px <= 0:
        return 0, 0.0
    account_value, available_cash, price = av, cash, px

    max_alloc = account_value * MAX_POSITION_PCT_OF_ACCOUNT / 100

    if dollar_amount and dollar_amount > 0 and math.isfinite(dollar_amount):
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


# Per-account values must reconcile with the scraped grand total within this
# fraction, else the balance parse is untrustworthy and sizing is refused.
_BALANCE_RECON_TOLERANCE = 0.05


def _account_scoped_balances(balances: dict, account: str | None) -> tuple[float, float]:
    """Return (total_value, available_cash) for the ONE account an order targets.

    Real-money sizing must never see household totals: the 10% position cap and
    the cash cap have to hold within the single target account, and protected
    (Roth/retirement) accounts must never contribute a dollar to sizing math.
    Every ambiguity — no per-account rows parsed, reconciliation off, target
    account missing, multi-account household without an explicit target,
    unscrapeable per-account value/cash — raises ValueError (fail closed).
    There is NO fallback to household totals.
    """
    accounts = balances.get("accounts") or []
    if not accounts:
        raise ValueError(
            "Per-account balances could not be parsed from Fidelity — refusing to size. "
            "Refresh the Broker page and retry."
        )

    def _digits(v) -> str:
        return "".join(ch for ch in str(v or "") if ch.isdigit())

    grand_total = balances.get("total_value")
    if grand_total is not None and grand_total > 0:
        acct_sum = sum(float(a.get("value") or 0) for a in accounts)
        if abs(acct_sum - float(grand_total)) / float(grand_total) > _BALANCE_RECON_TOLERANCE:
            raise ValueError(
                f"Per-account values (${acct_sum:,.2f}) do not reconcile with the grand total "
                f"(${float(grand_total):,.2f}) — balance parse unreliable, refusing to size."
            )

    protected = _protected_account_numbers()
    if account:
        target = _digits(account)
        if target in protected:
            # Defense in depth — _assert_account_tradeable already 403s this.
            raise ValueError(f"Account {_mask_account(account)} is protected — refusing to size.")
        matches = [a for a in accounts if _digits(a.get("number")) == target]
        if not matches:
            raise ValueError(
                f"Account {_mask_account(account)} not found in scraped Fidelity balances — refusing to size."
            )
        acct = matches[0]
    else:
        eligible = [a for a in accounts if _digits(a.get("number")) not in protected]
        if len(eligible) != 1:
            raise ValueError(
                "Cannot resolve a single target account for sizing — pass an explicit account number."
            )
        acct = eligible[0]

    value, cash = acct.get("value"), acct.get("cash")
    if value is None or value <= 0:
        raise ValueError(
            f"Account value could not be scraped for account {_mask_account(acct.get('number'))} — refusing to size."
        )
    if cash is None:
        # E2FP5: never size/trade against an invented balance.
        raise ValueError(
            f"Cash balance could not be scraped for account {_mask_account(acct.get('number'))} — refusing to size."
        )
    return float(value), float(cash)


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

def _accounts_payload(balances: dict) -> dict:
    return {
        "ok":             True,
        "total_value":    balances.get("total_value"),
        "available_cash": balances.get("available_cash"),
        "cash_rows":      balances.get("cash_rows", []),
        "accounts":       balances.get("account_options", []),
        "summary_tiles":  balances.get("summary_tiles", {}),
    }


async def _revalidate_accounts(email: str) -> None:
    """Background refresh of the cached accounts/balances DISPLAY snapshot. The
    execution sizing path always scrapes balances fresh and never reads this."""
    key = "acct:" + _user_key(email)
    if key in _POS_REFRESH_INFLIGHT:
        return
    _POS_REFRESH_INFLIGHT.add(key)
    try:
        balances = await _get_fidelity_balances(email)
        if "error" not in balances:
            _write_snapshot(email, "accounts", _accounts_payload(balances))
    except Exception as e:
        log.info("accounts revalidate skipped for %s: %s", _session_owner_hash(email), e)
    finally:
        _POS_REFRESH_INFLIGHT.discard(key)


@router.get("/fidelity/accounts")
async def fidelity_accounts(admin: dict = Depends(require_admin), refresh: bool = False):
    """Account balances + available cash for DISPLAY. Serves the last snapshot
    instantly and refreshes in the background; ``?refresh=1`` forces fresh. The
    real-money sizing path does NOT use this endpoint — it scrapes balances fresh."""
    from fastapi import HTTPException
    email = admin["email"]
    if not refresh:
        cached = _read_snapshot(email, "accounts")
        if cached is not None:
            meta = _snapshot_meta(cached)
            if meta["stale"]:
                asyncio.create_task(_revalidate_accounts(email))
            return {**cached, **meta}
    connected = await _is_logged_in(email)
    if not connected:
        if not refresh:
            cached = _read_snapshot(email, "accounts")
            if cached is not None:
                return {**cached, **_snapshot_meta(cached)}
        raise HTTPException(status_code=401, detail="Not authenticated with Fidelity. Log in first.")
    balances = await _get_fidelity_balances(email)
    if "error" in balances:
        if not refresh:
            cached = _read_snapshot(email, "accounts")
            if cached is not None:
                return {**cached, **_snapshot_meta(cached)}
        raise HTTPException(status_code=401, detail=balances["error"])
    payload = _accounts_payload(balances)
    _write_snapshot(email, "accounts", payload)
    return {**payload, "cached": False, "age_seconds": 0.0, "stale": False,
            "scraped_at": _time.time()}


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
    quote_time:       Optional[str] = None
    quote_source:     Optional[str] = None
    backup_sources:   list[str] = Field(default_factory=list)
    consensus_ok:     Optional[bool] = None
    bid:              Optional[float] = None
    ask:              Optional[float] = None
    market_open:      Optional[bool] = None

    # ── Input bounds (real money) ─────────────────────────────────────────────
    # A stop_pct >= 100 yields a stop price <= $0; a negative stop_pct / target_pct
    # inverts the protective stop / profit target; a negative dollar_amount or an
    # out-of-range pct_of_account corrupts position sizing. Reject at the boundary
    # — these never reach the Playwright order or the compliance quote gate.
    @field_validator("stop_pct")
    @classmethod
    def _stop_pct_sane(cls, v: float) -> float:
        if not (0 < v < 100):
            raise ValueError("stop_pct must be between 0 and 100 (exclusive)")
        return v

    @field_validator("target_pct")
    @classmethod
    def _target_pct_sane(cls, v: float) -> float:
        if not (0 < v <= 1000):
            raise ValueError("target_pct must be between 0 and 1000")
        return v

    @field_validator("dollar_amount")
    @classmethod
    def _dollar_amount_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("dollar_amount must be > 0")
        return v

    @field_validator("pct_of_account")
    @classmethod
    def _pct_of_account_sane(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0 < v <= 100):
            raise ValueError("pct_of_account must be between 0 and 100")
        return v


def _broker_quote_max_age_seconds() -> int:
    """Operator-tunable execution-freshness window for broker (Playwright) orders.

    The Fidelity order is placed via browser automation that itself takes
    ~20-30s, so the compliance default of 3s is unreachable on this path. We
    pass this bounded value through compliance's *supported* per-order
    ``max_quote_age_seconds`` parameter — it widens nothing inside compliance.py
    and never bypasses the trusted-source or spread checks.
    """
    try:
        v = int(float(os.getenv("BROKER_QUOTE_MAX_AGE_SECONDS", "120")))
    except Exception:
        v = 120
    return max(3, min(v, 600))


def _trusted_quote_fields(ticker: str) -> dict:
    """Fetch a trusted live quote via the multi-provider gateway (FMP/Finnhub/…).

    Returns order-dict fields that satisfy the compliance PreTradeGate's
    *trusted source* requirement, or ``{}`` when no trusted quote is available —
    in which case a live order stays correctly blocked (fail-safe).

    Note: the gateway stamps quote_time using the provider's exchange timestamp
    in *naive local* time, so we also pass a matching naive-local ``now`` to keep
    the freshness comparison like-for-like (compliance otherwise defaults to
    UTC, which would skew the age by the local UTC offset and falsely block).
    """
    try:
        import datetime as _dt
        from tradingagents.data.quote_gateway import get_gateway
        gw = get_gateway()
        if gw is None:
            return {}
        gq = gw.get_quote(ticker)
        if gq is None or not gq.best.trusted:
            return {}
        pk = gq.pretrade_kwargs()
        qt = pk.get("price_snapshot_time")
        qt_iso = qt.isoformat() if hasattr(qt, "isoformat") else (str(qt) if qt else None)
        return {
            "quote_price":    pk.get("price"),
            "quote_time":     qt_iso,
            "quote_source":   pk.get("quote_source"),
            "backup_sources": pk.get("backup_sources") or [],
            "consensus_ok":   pk.get("consensus_ok"),
            "bid":            pk.get("bid"),
            "ask":            pk.get("ask"),
            "now":            _dt.datetime.now().isoformat(),
            "max_quote_age_seconds": _broker_quote_max_age_seconds(),
            "_trusted_reference_price": gq.best.reference_price(),
        }
    except Exception as e:
        log.warning("Trusted quote fetch failed for %s: %s", ticker, e)
        return {}


def _apply_execution_quote(order_dict: dict, tq: dict, *, limit_factor: float | None) -> float:
    """Stamp an execute order's quote evidence from the server's trusted gateway
    quote (``_trusted_quote_fields`` output) — the SOLE source of execution
    evidence. Caller-supplied quote_source/quote_time/bid/ask/consensus/backup
    values are OVERWRITTEN, never trusted.

    Raises ValueError when the gateway returned no trusted quote or no usable
    reference price — callers convert that to HTTP 503 and the order stays
    blocked (fail closed; an empty gateway result must never degrade into
    "keep whatever quote fields were already in the dict").

    ``limit_factor=None`` leaves ``limit_price`` untouched (it is an order
    parameter on some paths — e.g. a deliberate far-from-market limit, or a
    market sell where it must stay None); otherwise ``limit_price`` is re-priced
    at the trusted reference. Returns the trusted reference price.
    """
    if not tq:
        raise ValueError("No trusted execution quote available — refusing to execute")
    try:
        ref = float(tq.get("_trusted_reference_price") or 0)
    except (TypeError, ValueError):
        ref = 0.0
    if ref <= 0:
        raise ValueError("No trusted execution quote available — refusing to execute")
    # Missing keys stamp None → compliance fails closed on absent quote_time.
    for key in ("quote_time", "quote_source", "backup_sources", "consensus_ok",
                "bid", "ask", "now", "max_quote_age_seconds"):
        order_dict[key] = tq.get(key)
    order_dict["quote_price"] = ref
    if limit_factor is not None:
        order_dict["limit_price"] = round(ref * limit_factor, 2)
    return ref


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
    if not valid_symbol(ticker):
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

    _assert_account_tradeable(account)  # block protected (Roth/IRA) accounts

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

    # F4: size against the TARGET account only — never household totals (which
    # include protected Roth/retirement money). Any scoping ambiguity blocks.
    try:
        total_value, available_cash = _account_scoped_balances(balances, account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Refuse to size if we can't confirm account balance — never use hardcoded fallback
    if total_value is None or total_value <= 0:
        raise HTTPException(status_code=400, detail="Cannot determine Fidelity account value — check session. Refresh the portfolio page and retry.")
    if available_cash is None:
        # E2FP5: hard-abort — never trade against an invented balance
        raise HTTPException(
            status_code=400,
            detail="Cash balance could not be scraped from Fidelity. Refusing to size/execute. "
                   "Refresh the Broker page to re-scrape, then retry."
        )
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
        "broker":      "fidelity",
        "account_rule_profile": os.getenv("FIDELITY_ACCOUNT_RULE_PROFILE", "fidelity_cash"),
        "order_type":  "Limit",
        "quantity":    shares,
        "limit_price": limit_price,
        "execute":     body.execute,
        "quote_price": price,
        "quote_time":  body.quote_time or now_iso,
        "quote_source": body.quote_source or "yfinance",
        "backup_sources": body.backup_sources,
        "consensus_ok": body.consensus_ok,
        "bid": body.bid,
        "ask": body.ask,
        "market_open": body.market_open,
        # Naive-local 'now' matches the gateway's naive-local quote_time (F3) —
        # compliance otherwise falls back to utcnow() and skews the age check.
        "now": now_iso,
    }
    if body.execute:
        # F2: the server's trusted gateway quote is the SOLE execution evidence
        # — caller-supplied quote_* is overwritten, never trusted. No trusted
        # quote ⇒ 503, order blocked (fail closed).
        _tq = await loop.run_in_executor(None, _trusted_quote_fields, ticker)
        try:
            _ref = _apply_execution_quote(order_dict, _tq, limit_factor=1.002)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e))
        limit_price = order_dict["limit_price"]
        if abs(_ref - price) / _ref > 0.05:
            log.warning("Trusted ref $%.2f diverges >5%% from yfinance $%.2f for %s — trusted ref wins",
                        _ref, price, ticker)
        # Re-size at the executable price so the 10% cap and the cash cap hold
        # at the price the order can actually fill at (not a stale yfinance close).
        shares, cost = _size_fidelity_position(
            account_value   = total_value,
            available_cash  = available_cash,
            price           = _ref,
            dollar_amount   = body.dollar_amount,
            pct_of_account  = body.pct_of_account,
        )
        if shares <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot size position at trusted price ${_ref:.2f} "
                       f"(alloc=${body.dollar_amount}, cash=${available_cash:.2f})",
            )
        order_dict["quantity"] = shares
        # Re-anchor stop/target to the TRUSTED reference. They were computed above
        # off the stale yfinance close; the order itself re-prices at _ref, but the
        # levels did not, so the real risk distance was wrong by exactly the gap
        # between the two — and these names gap 10%+ routinely, which is often WHY
        # they signalled. A "-8%" stop off a $10.00 close is -16.5% from an $11.00
        # fill: double the intended risk. It is these stored numbers that the exit
        # guard and _check_thematic_exits act on, so the error propagates into every
        # later exit decision. `price` is also replaced below as the recorded entry.
        price = _ref
        stop_price   = round(_ref * (1 - body.stop_pct / 100), 4)
        target_price = round(_ref * (1 + body.target_pct / 100), 4)
    elif not body.quote_source:
        # Preview: route a trusted quote in when available so the preview shows
        # executable numbers — but a missing quote never blocks a preview.
        _tq = await loop.run_in_executor(None, _trusted_quote_fields, ticker)
        if _tq:
            _ref = _tq.pop("_trusted_reference_price", 0) or 0
            order_dict.update({k: v for k, v in _tq.items() if not k.startswith("_")})
            if _ref > 0:
                limit_price = round(_ref * 1.002, 2)
                order_dict["limit_price"] = limit_price
                order_dict["quote_price"] = _ref
                # Preview the SAME levels execution will use — otherwise the
                # human approves a stop computed off the stale close and gets a
                # different one on the real order.
                stop_price   = round(_ref * (1 - body.stop_pct / 100), 4)
                target_price = round(_ref * (1 + body.target_pct / 100), 4)

    decision = validate_live_order(order_dict)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=f"Compliance: {decision.reason}")

    # Share count BEFORE submission. A fill is a DELTA, not mere presence —
    # otherwise adding to an existing position could never be verified.
    _shares_before_order = _current_shares(admin["email"], ticker)

    # ── 5. Place order on Fidelity ────────────────────────────────────────────
    order_status  = "sized"
    fidelity_resp = None

    if body.execute:
        if LIVE_TRADING_HARD_BLOCKED:
            raise HTTPException(status_code=403, detail="LIVE_TRADING_HARD_BLOCKED=True in compliance.py")
        if not live_trading_enabled():
            raise HTTPException(status_code=403, detail="LIVE_TRADING_ENABLED not set to true in .env")
        # Kill switch dedicated to the local Playwright execution route (SnapTrade
        # migration): disables order placement without touching data/read flows.
        if not env_bool("FIDELITY_LOCAL_EXECUTION_ENABLED", True):
            raise HTTPException(status_code=403, detail="FIDELITY_LOCAL_EXECUTION_ENABLED=false — local Fidelity execution is disabled.")

        ctx = await _ensure_browser(admin["email"])
        page = await ctx.new_page()
        _order_in_flight_acquire(_user_key(admin["email"]))  # freeze background browser resets
        try:
            trade_url = "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry"
            await _nav(page, trade_url, sleep=5)

            if _is_login_url(page.url):
                raise HTTPException(status_code=401, detail="Session expired — log in again")

            # Account — use pre-validated numeric account number only (fail-closed)
            await _select_and_verify_account(page, account)

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

            # Block on any non-confirmed preview — unknown/error state must not proceed
            preview_ok, preview_msg = _verify_fidelity_order_page(preview_text)
            if not preview_ok:
                raise HTTPException(status_code=400, detail=f"Preview not confirmed: {preview_msg}\n\nPage excerpt:\n{preview_text[:400]}")

            # ── Pre-submit order-ticket verification (SnapTrade migration hardening) ──
            # Confirm the ticket the browser is about to submit matches the intended
            # order (account/symbol/side/qty/type/limit/cost) AND that the live
            # preview page reflects it — then screenshot + audit BEFORE clicking.
            from tradingagents.brokers.order_verifier import OrderIntent, verify_order_ticket
            _intent = OrderIntent(
                account_mask=_mask_account(account),
                symbol=ticker, side="buy", quantity=int(shares),
                order_type="limit", limit_price=float(limit_price),
                est_cost=round(int(shares) * float(limit_price), 2),
            )
            _ticket_ok, _ticket_reasons = verify_order_ticket(_intent, preview_text)
            await _presubmit_audit(page, admin["email"], _intent, _ticket_ok, _ticket_reasons)
            if not _ticket_ok:
                raise HTTPException(
                    status_code=400,
                    detail=f"Order ticket verification FAILED — not submitted: {'; '.join(_ticket_reasons)}",
                )

            # Place order
            place_btn = page.locator('button:has-text("Place Order")').first
            await place_btn.wait_for(state="visible", timeout=8000)
            await place_btn.click()
            # Poll up to 15 s for confirmation or rejection phrase
            confirm_text = ""
            for _ in range(5):
                await asyncio.sleep(3)
                confirm_text = await page.evaluate("() => document.body.innerText")
                c_ok, _ = _verify_fidelity_order_page(confirm_text)
                if c_ok or any(p in confirm_text.lower() for p in _ORDER_ERROR_PATTERNS):
                    break

            # Verify confirmation — NEVER assume success
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
            # ACCEPTED is not FILLED. This is a DAY limit order; if it never
            # trades it expires at 16:00 silently while the state written below
            # claims we hold the position. Record the submission so a later
            # holdings snapshot can confirm or refute it — see
            # tradingagents/brokers/fill_verifier.py and verify_pending_fills().
            _record_pending_fill(
                admin["email"], ticker=ticker, shares=shares, side="buy",
                shares_before=_shares_before_order, limit_price=limit_price,
            )
            # Post-submit reconcile against SnapTrade executed history when available.
            _recon = _reconcile_fill(admin["email"], _intent)
            if _recon.get("source") == "snaptrade":
                log.info("SnapTrade reconcile %s: matched=%s status=%s disc=%s",
                         ticker, _recon.get("matched"), _recon.get("status"), _recon.get("discrepancies"))

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fidelity order failed: {e}")
        finally:
            _order_in_flight_release(_user_key(admin["email"]))
            await _save_storage(admin["email"])
            try:
                await page.close()
            except Exception:
                pass
    else:
        order_status = "preview"

    # ── 6. Mirror in paper account — ONLY when order actually executed ────────
    paper_result = None
    if body.also_paper_trade and body.execute and order_status == "executed":
        try:
            from web.api.thematic_auto import _paper_state_lock, PAPER_STATE_FILE
            from web.api.thematic_portfolio import _ensure_thematic_paper_state
            PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            async with _paper_state_lock:
                _ensure_thematic_paper_state()
                state: dict = {}
                if PAPER_STATE_FILE.exists():
                    try:
                        state = json.loads(PAPER_STATE_FILE.read_text())
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
                    # Use actual starting_cash as fallback — never invent a phantom balance
                    cash = float(state.get("cash", state.get("starting_cash", 0)))
                    settled = float(state.get("settled_cash", cash))
                    state["positions"]    = positions
                    state["cash"]         = round(cash - cost, 4)
                    state["settled_cash"] = round(settled - cost, 4)
                    # Atomic write
                    fd, tmp_p = tempfile.mkstemp(dir=PAPER_STATE_FILE.parent, prefix=".tmp_")
                    try:
                        with os.fdopen(fd, "w") as f:
                            f.write(json.dumps(state, indent=2))
                        os.replace(tmp_p, PAPER_STATE_FILE)
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
    limit_pct:  float = Field(-0.2, ge=-5.0, le=5.0)  # capped ±5% — prevents far-off-market limits
    account:    Optional[str] = None
    execute:    bool = False
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    backup_sources: list[str] = Field(default_factory=list)
    consensus_ok: Optional[bool] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    market_open: Optional[bool] = None


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
    if not valid_symbol(ticker):
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

    _assert_account_tradeable(account)  # block protected (Roth/IRA) accounts

    # ── Fetch current Fidelity positions to find share count ──────────────────
    ctx = await _ensure_browser(admin["email"])
    page = await ctx.new_page()
    fidelity_shares = None
    last_price      = None

    try:
        await _nav(page, PORTFOLIO_URL, sleep=6)
        if _is_login_url(page.url):
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

        price_source = None
        if pos_data:
            try:
                fidelity_shares = float(str(pos_data.get("qty", "")).replace(",", ""))
            except Exception:
                pass
            last_price = _parse_dollar(pos_data.get("price", "") or "")
            if last_price:
                price_source = "fidelity_realtime"  # broker's own price = trusted

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
        if last_price:
            price_source = "yfinance"  # untrusted — will need an FMP trusted quote

    if not last_price:
        raise HTTPException(status_code=400, detail=f"Cannot determine price for {ticker}")

    # Exit order type: Limit (default, marketable) or Market (sell-only, gated by
    # compliance ALLOW_MARKET_SELL). Market exits carry no limit price.
    is_market = str(body.order_type or "Limit").strip().lower() == "market"
    limit_price = None if is_market else round(last_price * (1 + body.limit_pct / 100), 2)

    import datetime as _qdt
    _broker_now = _qdt.datetime.now().isoformat(timespec="seconds")  # naive local

    order_dict = {
        "symbol": ticker, "action": "Sell",
        "broker": "fidelity",
        "account_rule_profile": os.getenv("FIDELITY_ACCOUNT_RULE_PROFILE", "fidelity_cash"),
        "order_type": "Market" if is_market else "Limit",
        "quantity": shares_to_sell,
        "limit_price": limit_price,
        "execute": body.execute,
        "quote_price": last_price,
        "quote_time": None,
        "quote_source": None,
        "backup_sources": body.backup_sources,
        "consensus_ok": body.consensus_ok,
        "bid": body.bid,
        "ask": body.ask,
        "market_open": body.market_open,
        "now": _broker_now,
        "max_quote_age_seconds": _broker_quote_max_age_seconds(),
    }
    if body.execute:
        # F1/F2: the server's trusted gateway quote is the SOLE execution
        # evidence. The scraped grid price is display/shares-lookup only — the
        # old self-stamped 'fidelity_realtime' + scrape-time now() made the
        # freshness gate a permanent no-op — and caller-supplied quote_* is
        # overwritten, never trusted. No trusted quote ⇒ 503 (fail closed).
        _exit_loop = asyncio.get_running_loop()
        _tq = await _exit_loop.run_in_executor(None, _trusted_quote_fields, ticker)
        try:
            _apply_execution_quote(
                order_dict, _tq,
                # Market sells keep limit_price=None; quote_price=ref still
                # feeds the compliance $50k per-order cap.
                limit_factor=None if is_market else 1 + body.limit_pct / 100,
            )
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e))
        limit_price = order_dict["limit_price"]
    else:
        # Preview-only: scraped/caller quote fields are fine for display —
        # validate_live_order skips the quote gate when execute is falsy.
        order_dict["quote_source"] = body.quote_source or price_source or "yfinance"
        order_dict["quote_time"] = body.quote_time or (
            _broker_now if price_source == "fidelity_realtime" else None
        )
        if not body.quote_source and price_source != "fidelity_realtime":
            _exit_loop = asyncio.get_running_loop()
            _tq = await _exit_loop.run_in_executor(None, _trusted_quote_fields, ticker)
            if _tq:
                _ref = _tq.pop("_trusted_reference_price", 0) or 0
                order_dict.update({k: v for k, v in _tq.items() if not k.startswith("_")})
                if _ref > 0:
                    limit_price = round(_ref * (1 + body.limit_pct / 100), 2)
                    order_dict["limit_price"] = limit_price
                    order_dict["quote_price"] = _ref

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
        if not env_bool("FIDELITY_LOCAL_EXECUTION_ENABLED", True):
            raise HTTPException(status_code=403, detail="FIDELITY_LOCAL_EXECUTION_ENABLED=false — local Fidelity execution is disabled.")

        exit_page = None  # define before try so finally can safely check
        _order_in_flight_acquire(_user_key(admin["email"]))  # freeze background browser resets
        try:
            exit_page = await ctx.new_page()
            await _nav(exit_page, "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry", sleep=5)

            # Account — pre-validated numeric only, injection-safe (fail-closed)
            await _select_and_verify_account(exit_page, account)

            sym_input = exit_page.locator('#eq-ticket-dest-symbol')
            await sym_input.wait_for(state="visible", timeout=20000)  # ticket form loads slowly
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

            # Quantity — integer shares only. Use Playwright fill (NOT a JS
            # native-setter: that does not register with Fidelity's input
            # framework → "Please enter a quantity" at preview). fill() avoids the
            # overlay <label> watermark that intercepts a real mouse click.
            qty_val = str(int(shares_to_sell))
            qty_loc = exit_page.locator('#eqt-shared-quantity')
            await qty_loc.wait_for(state="visible", timeout=8000)
            await qty_loc.fill(qty_val)
            await qty_loc.press("Tab")
            await asyncio.sleep(0.8)

            # Order type — Limit (default) or Market (sell-only, compliance-gated)
            await exit_page.locator('#dest-dropdownlist-button-ordertype').click()
            await asyncio.sleep(1)
            order_type_label = "Market" if is_market else "Limit"
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

            # ── Pre-submit order-ticket verification + audit (SnapTrade hardening) ──
            from tradingagents.brokers.order_verifier import OrderIntent, verify_order_ticket
            _lp = float(limit_price) if limit_price else 0.0
            _exit_intent = OrderIntent(
                account_mask=_mask_account(account),
                symbol=ticker, side="sell", quantity=int(shares_to_sell),
                order_type="limit" if _lp > 0 else "market",
                limit_price=_lp,
                est_cost=round(int(shares_to_sell) * _lp, 2) if _lp > 0 else 0.0,
            )
            if _lp > 0:
                _eok, _ereasons = verify_order_ticket(_exit_intent, preview_text)
            else:
                # Market sell: strict price-match can't apply; audit only, don't hard-block.
                _eok, _ereasons = True, ["market sell — ticket price check skipped"]
            await _presubmit_audit(exit_page, admin["email"], _exit_intent, _eok, _ereasons)
            if not _eok:
                raise HTTPException(
                    status_code=400,
                    detail=f"Exit ticket verification FAILED — not submitted: {'; '.join(_ereasons)}",
                )

            place_btn = exit_page.locator('button:has-text("Place Order")').first
            await place_btn.wait_for(state="visible", timeout=8000)
            await place_btn.click()
            # Poll up to 15 s for confirmation or rejection phrase
            confirm_text = ""
            for _ in range(5):
                await asyncio.sleep(3)
                confirm_text = await exit_page.evaluate("() => document.body.innerText")
                c_ok, _ = _verify_fidelity_order_page(confirm_text)
                if c_ok or any(p in confirm_text.lower() for p in _ORDER_ERROR_PATTERNS):
                    break

            # Verify confirmation — NEVER assume success
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
            _order_in_flight_release(_user_key(admin["email"]))
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
        if _is_login_url(page.url):
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
                const _mm = ['SPAXX','FDRXX','FZFXX','SPRXX','FCASH','FMPXX','FGTXX','FNSXX','FZCXX','QACDS','FDLXX','FZDXX'];
                const _tk = ticker.toUpperCase().replace(/[^A-Z0-9]/g, '');
                if (!ticker || ticker.length > 10 || !/^[A-Z]/.test(ticker) ||
                    SKIP.some(s => ticker.startsWith(s)) ||
                    _mm.some(m => _tk.startsWith(m))) return;
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
        if _is_login_url(current_url):
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
