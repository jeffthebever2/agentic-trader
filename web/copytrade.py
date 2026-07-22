"""Copy-trade follower: mirror a paper competition portfolio into real Fidelity.

Two modes, chosen per followed portfolio:
  * ``hil``  — reconciler enqueues proposed BUY/SELL actions; the user approves
    each in the HIL page (step-up 2FA intact) before it fills.
  * ``auto`` — reconciler executes actions immediately and texts every fill.
    Gated behind the ``COPYTRADE_AUTONOMOUS`` env kill-switch AND all the usual
    real-money gates inside ``_fidelity_thematic_*_inner`` (LIVE_TRADING_HARD_BLOCKED,
    live_trading_enabled(), validate_live_order → limit-only / 10% / $50k /
    trusted fresh quote). Autonomy replaces ONLY the interactive 2FA tap.

Sizing mirrors WEIGHT, not dollars: a paper position at 8% of its book becomes an
8%-of-your-account order (``pct_of_account``), clamped to the compliance 10% cap.
Weight is a unitless paper ratio — it can never inject a paper-dollar figure into
the real book (the scale-mix trap a prior audit caught).

The pure reconciliation math lives in
``tradingagents/portfolio/copytrade_reconcile.py``; this module is the wiring
(state persistence, broker execution, SMS).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.config import env_bool
from tradingagents.portfolio.copytrade_reconcile import compute_copy_actions

log = logging.getLogger("copytrade")

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "tmp" / "copytrade"
STATE_FILE = STATE_DIR / "state.json"

_FILE_LOCK = threading.Lock()          # guards STATE_FILE read/modify/write
_reconcile_locks: dict[str, asyncio.Lock] = {}   # per-email, prevents overlap

# Config keys a client is allowed to set, with coercers + defaults.
_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "follow_portfolio_id": None,
    "mode": "hil",                     # "hil" | "auto"
    "account": None,                   # Fidelity account override (None = resolved)
    "stop_pct": 8.0,
    "target_pct": 20.0,
    "min_weight": 0.01,                # ignore dust < 1% of book
    "max_weight": 0.10,                # compliance cap
    "max_new_buys_per_sync": 3,        # throttle: never dump a whole book at once
}
_RUNTIME_KEYS = ("owned", "pending", "last_sync", "last_error", "last_actions")


# ── Persistence ───────────────────────────────────────────────────────────────

def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _read_all() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        log.warning("copytrade state read failed: %s", e)
    return {}


def _write_all(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STATE_FILE)  # atomic


def _blank_record() -> dict:
    rec = dict(_DEFAULT_CONFIG)
    rec.update({"owned": {}, "pending": [], "last_sync": None,
                "last_error": None, "last_actions": []})
    return rec


def get_config(email: str) -> dict:
    """Full per-user copy-trade record (config + runtime), defaults filled in."""
    key = _norm_email(email)
    with _FILE_LOCK:
        rec = _read_all().get(key) or {}
    out = _blank_record()
    out.update({k: v for k, v in rec.items() if k in _DEFAULT_CONFIG or k in _RUNTIME_KEYS})
    return out


def _coerce_patch(patch: dict) -> dict:
    """Whitelist + type-coerce a client-supplied config patch."""
    out: dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k not in _DEFAULT_CONFIG:
            continue
        if k == "mode":
            v = str(v).lower().strip()
            if v not in ("hil", "auto"):
                continue
        elif k == "enabled":
            v = bool(v)
        elif k == "follow_portfolio_id":
            v = str(v).strip() if v else None
        elif k == "account":
            v = str(v).strip() if v else None
        elif k in ("stop_pct", "target_pct", "min_weight", "max_weight"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
        elif k == "max_new_buys_per_sync":
            try:
                v = max(1, int(v))
            except (TypeError, ValueError):
                continue
        out[k] = v
    # Bounds — real money: refuse insane sizing.
    if "max_weight" in out:
        out["max_weight"] = min(max(out["max_weight"], 0.005), 0.10)  # never above compliance 10%
    if "min_weight" in out:
        out["min_weight"] = min(max(out["min_weight"], 0.0), 0.10)
    if "stop_pct" in out:
        out["stop_pct"] = min(max(out["stop_pct"], 0.5), 90.0)
    if "target_pct" in out:
        out["target_pct"] = min(max(out["target_pct"], 1.0), 500.0)
    return out


def set_config(email: str, patch: dict) -> dict:
    """Merge a whitelisted config patch; returns the updated record."""
    key = _norm_email(email)
    coerced = _coerce_patch(patch)
    with _FILE_LOCK:
        data = _read_all()
        rec = data.get(key) or _blank_record()
        rec.update(coerced)
        data[key] = rec
        _write_all(data)
    return get_config(email)


def _update_record(email: str, mutate) -> dict:
    """Atomically read-modify-write one user's record via ``mutate(rec)``."""
    key = _norm_email(email)
    with _FILE_LOCK:
        data = _read_all()
        rec = data.get(key) or _blank_record()
        mutate(rec)
        data[key] = rec
        _write_all(data)
        return dict(rec)


# ── Pending (HIL) queue ────────────────────────────────────────────────────────

def list_pending(email: str) -> list[dict]:
    return [p for p in get_config(email).get("pending", []) if p.get("status") == "pending"]


def _add_pending(email: str, actions: list[dict]) -> int:
    if not actions:
        return 0
    now = datetime.now(ZoneInfo("America/New_York")).isoformat()

    def mut(rec):
        existing = {(p["action"], p["ticker"]) for p in rec.get("pending", [])
                    if p.get("status") == "pending"}
        for a in actions:
            k = (a["action"], a["ticker"])
            if k in existing:
                continue  # already queued, don't duplicate
            rec.setdefault("pending", []).append({
                "id": uuid.uuid4().hex[:12],
                "status": "pending",
                "created_at": now,
                **a,
            })
        # Trim resolved history to last 50.
        resolved = [p for p in rec.get("pending", []) if p.get("status") != "pending"]
        keep = [p for p in rec.get("pending", []) if p.get("status") == "pending"]
        rec["pending"] = keep + resolved[-50:]
    _update_record(email, mut)
    return len(actions)


def resolve_pending(email: str, pending_id: str, status: str) -> dict | None:
    found: dict | None = None

    def mut(rec):
        nonlocal found
        for p in rec.get("pending", []):
            if p.get("id") == pending_id and p.get("status") == "pending":
                p["status"] = status
                p["resolved_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat()
                found = dict(p)
                break
    _update_record(email, mut)
    return found


# ── Reads: followed portfolio + external holdings ──────────────────────────────

def _portfolio_snapshot(portfolio_id: str) -> tuple[list[dict], float]:
    """Return (positions_as_dicts, current_equity) for a followed paper portfolio."""
    from web.api.paper_portfolios import _load as _load_acc
    from tradingagents.portfolio.paper_metrics import compute_metrics

    acc = _load_acc(portfolio_id)
    positions = [
        {
            "ticker": p.ticker,
            "shares": p.shares,
            "current_price": p.current_price,
            "entry_price": p.entry_price,
            "stop": p.stop,
            "target": p.target,
        }
        for p in acc.positions
    ]
    equity = float(compute_metrics(acc).current_equity)
    return positions, equity


def _external_holdings(email: str) -> set[str]:
    """Symbols the user holds on the real broker RIGHT NOW (cached snapshot).

    Any name held anywhere in the account is off-limits to copy-buys (no stacking)
    and immune to copy-sells (not ours). Read-only cache — never triggers a scrape.
    """
    try:
        from web.api.fidelity import _read_snapshot
        snap = _read_snapshot(email, "positions") or {}
        return {
            str(p.get("symbol") or "").strip().upper()
            for p in (snap.get("positions") or [])
            if p.get("symbol")
        }
    except Exception as e:
        log.warning("external holdings read failed for %s: %s", email[:16], e)
        # Fail closed: unknown holdings → treat as "everything external" is unsafe
        # (would block all buys forever). Return empty but the reconciler still
        # runs the compliance/cash gates on execution. Empty = allow reconcile.
        return set()


def _resolve_account(email: str, cfg: dict) -> str | None:
    from web.api.fidelity import _resolve_trade_account, _validate_account_number
    acct = cfg.get("account")
    if acct:
        return _validate_account_number(acct)
    return _resolve_trade_account(None)


# ── SMS ────────────────────────────────────────────────────────────────────────

def _sms(email: str, message: str) -> None:
    try:
        from scripts.sms_alerts import send_sms
        from web import users as user_store
        rec = user_store.get_user(email) or {}
        phone = (rec.get("phone_number") or os.getenv("PAPER_SMS_NUMBER", "")).strip()
        if not phone:
            log.info("copytrade SMS skipped (no phone): %s", email[:16])
            return
        send_sms(phone, message, None, None)
    except Exception as e:
        log.warning("copytrade SMS failed for %s: %s", email[:16], e)


# ── Execution (real money) ─────────────────────────────────────────────────────

async def _execute_action(email: str, action: dict, account: str | None) -> dict:
    """Place ONE real Fidelity order for a reconcile action. All compliance /
    hard-block / live-enable / trusted-quote gates enforced inside the inner fns."""
    from web.api.fidelity import (
        _fidelity_thematic_trade_inner,
        _fidelity_thematic_exit_inner,
        FidelityThematicTradeRequest,
        FidelityExitRequest,
        _get_order_lock,
        _ORDER_LOCKS_META,
    )
    ticker = str(action["ticker"]).upper().strip()
    user = {"email": email}
    cfg = get_config(email)

    if action["action"] == "buy":
        body = FidelityThematicTradeRequest(
            ticker=ticker,
            pct_of_account=float(action["target_pct"]),   # mirror-weight sizing
            stop_pct=float(cfg.get("stop_pct", 8.0)),
            target_pct=float(cfg.get("target_pct", 20.0)),
            theme="copytrade",
            thesis=f"copy-follow {cfg.get('follow_portfolio_id')}",
            account=account,
            execute=True,
            also_paper_trade=False,
        )
        lock_key = f"{email}:{ticker}"
        lock = _get_order_lock(lock_key)
        if lock.locked():
            return {"ticker": ticker, "action": "buy", "skipped": "order in progress"}
        async with lock:
            _ORDER_LOCKS_META[lock_key] = time.time()
            res = await _fidelity_thematic_trade_inner(body, user, ticker, account)
        # Record ownership only on a placed/sized order.
        _mark_owned(email, ticker, action)
        return {"ticker": ticker, "action": "buy", "result": res}

    else:  # sell
        body = FidelityExitRequest(ticker=ticker, account=account, execute=True)
        lock_key = f"{email}:exit:{ticker}"
        lock = _get_order_lock(lock_key)
        if lock.locked():
            return {"ticker": ticker, "action": "sell", "skipped": "exit in progress"}
        async with lock:
            _ORDER_LOCKS_META[lock_key] = time.time()
            res = await _fidelity_thematic_exit_inner(body, user, ticker, account)
        _unmark_owned(email, ticker)
        return {"ticker": ticker, "action": "sell", "result": res}


def _mark_owned(email: str, ticker: str, action: dict) -> None:
    def mut(rec):
        rec.setdefault("owned", {})[ticker.upper()] = {
            "entry_ts": datetime.now(ZoneInfo("America/New_York")).isoformat(),
            "target_weight": action.get("target_weight"),
            "paper_shares": action.get("paper_shares"),
        }
    _update_record(email, mut)


def _unmark_owned(email: str, ticker: str) -> None:
    def mut(rec):
        rec.get("owned", {}).pop(ticker.upper(), None)
    _update_record(email, mut)


def _autonomous_allowed() -> bool:
    return env_bool("COPYTRADE_AUTONOMOUS", False)


# ── The reconciler ─────────────────────────────────────────────────────────────

def _get_reconcile_lock(email: str) -> asyncio.Lock:
    if email not in _reconcile_locks:
        _reconcile_locks[email] = asyncio.Lock()
    return _reconcile_locks[email]


async def reconcile(email: str, *, force_execute: bool = False, execute_allowed: bool = True) -> dict:
    """Compute + apply the copy-trade diff for one user.

    In ``hil`` mode (or when the ``COPYTRADE_AUTONOMOUS`` kill-switch is off) the
    actions are enqueued for approval and the user is texted a summary. In ``auto``
    mode with the kill-switch on AND ``execute_allowed``, actions execute
    immediately and each fill texts.

    ``force_execute`` only bypasses the ``enabled`` flag (for a manual sync), never
    the mode. ``execute_allowed`` is the hard gate on autonomous execution: the
    env-gated background loop passes ``True``; the HTTP ``/copytrade/sync`` endpoint
    passes ``False`` so a manual sync can NEVER place a real order without step-up
    2FA — it only computes and enqueues, leaving execution to the approve flow
    (which enforces ``require_step_up``). This closes the step-up bypass where an
    admin could trigger autonomous real-money execution over HTTP with no 2FA.
    """
    cfg = get_config(email)
    if not cfg.get("enabled") and not force_execute:
        return {"skipped": "disabled"}
    portfolio_id = cfg.get("follow_portfolio_id")
    if not portfolio_id:
        return {"skipped": "no portfolio selected"}

    lock = _get_reconcile_lock(email)
    if lock.locked():
        return {"skipped": "reconcile already running"}

    async with lock:
        try:
            positions, equity = await asyncio.to_thread(_portfolio_snapshot, portfolio_id)
        except Exception as e:
            _update_record(email, lambda r: r.update({"last_error": f"portfolio read: {e}"}))
            return {"error": f"cannot read portfolio {portfolio_id}: {e}"}

        external = await asyncio.to_thread(_external_holdings, email)
        owned = cfg.get("owned", {})
        actions = compute_copy_actions(
            positions, equity, owned, external,
            min_weight=float(cfg.get("min_weight", 0.01)),
            max_weight=float(cfg.get("max_weight", 0.10)),
        )
        action_dicts = [a.to_dict() for a in actions]

        # Throttle new buys per sync (sells always allowed — they de-risk).
        cap = int(cfg.get("max_new_buys_per_sync", 3))
        buys = [a for a in action_dicts if a["action"] == "buy"][:cap]
        sells = [a for a in action_dicts if a["action"] == "sell"]
        planned = sells + buys

        _update_record(email, lambda r: r.update({
            "last_sync": datetime.now(ZoneInfo("America/New_York")).isoformat(),
            "last_actions": planned,
            "last_error": None,
        }))

        mode = cfg.get("mode", "hil")
        autonomous = mode == "auto" and _autonomous_allowed() and execute_allowed

        if not planned:
            return {"mode": mode, "autonomous": autonomous, "actions": [], "note": "in sync"}

        if not autonomous:
            n = _add_pending(email, planned)
            if n:
                _sms(email, _pending_sms(portfolio_id, planned))
            return {"mode": "hil", "autonomous": False, "queued": n, "actions": planned}

        # ── Autonomous execution ─────────────────────────────────────────────
        account = _resolve_account(email, cfg)
        fills, errors = [], []
        for a in planned:
            try:
                r = await _execute_action(email, a, account)
                fills.append(r)
            except Exception as e:
                errors.append({"ticker": a["ticker"], "action": a["action"], "error": str(e)})
                log.warning("copytrade auto %s %s failed: %s", a["action"], a["ticker"], e)
        if fills:
            _sms(email, _fills_sms(portfolio_id, fills, errors))
        return {"mode": "auto", "autonomous": True, "fills": fills, "errors": errors}


async def approve_pending(email: str, pending_id: str) -> dict:
    """HIL path: execute one queued action, then mark it resolved. Caller must have
    already passed step-up 2FA (enforced at the route)."""
    pend = next((p for p in list_pending(email) if p.get("id") == pending_id), None)
    if not pend:
        raise ValueError(f"pending {pending_id} not found or already resolved")
    cfg = get_config(email)
    account = _resolve_account(email, cfg)
    try:
        res = await _execute_action(email, pend, account)
    except Exception as e:
        resolve_pending(email, pending_id, "error")
        raise
    resolve_pending(email, pending_id, "approved")
    _sms(email, _fills_sms(cfg.get("follow_portfolio_id"), [res], []))
    return res


# ── SMS bodies ─────────────────────────────────────────────────────────────────

def _dash_link() -> str:
    base = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org").rstrip("/")
    return f"{base}/app/hil?tab=copytrade"


def _pending_sms(portfolio_id: str, actions: list[dict]) -> str:
    lines = [f"📋 Copy-trade — {portfolio_id}", "", f"{len(actions)} action(s) waiting:"]
    for a in actions[:8]:
        if a["action"] == "buy":
            lines.append(f"• BUY {a['ticker']} ~{a['target_pct']:.1f}%")
        else:
            lines.append(f"• SELL {a['ticker']}")
    lines += ["", f"Approve 👉 {_dash_link()}"]
    return "\n".join(lines)


def _fills_sms(portfolio_id: str | None, fills: list[dict], errors: list[dict]) -> str:
    lines = [f"✅ Copy-trade executed — {portfolio_id or ''}".rstrip(), ""]
    for f in fills[:8]:
        tag = "BOUGHT" if f.get("action") == "buy" else "SOLD"
        skip = f.get("skipped")
        if skip:
            lines.append(f"• {tag} {f['ticker']} — skipped ({skip})")
        else:
            lines.append(f"• {tag} {f['ticker']}")
    if errors:
        lines.append("")
        lines.append(f"⚠️ {len(errors)} failed:")
        for e in errors[:5]:
            lines.append(f"• {e['action'].upper()} {e['ticker']}: {str(e['error'])[:60]}")
    return "\n".join(lines)


# ── Loop entry (called by web/app.py startup) ──────────────────────────────────

def _users_with_copytrade() -> list[str]:
    with _FILE_LOCK:
        data = _read_all()
    return [email for email, rec in data.items() if rec.get("enabled") and rec.get("follow_portfolio_id")]


async def run_copytrade_cycle() -> dict:
    """Reconcile every enabled follower. Returns a per-user summary."""
    out: dict[str, Any] = {}
    for email in _users_with_copytrade():
        try:
            out[email[:16]] = await reconcile(email)
        except Exception as e:
            out[email[:16]] = {"error": str(e)}
            log.warning("copytrade cycle failed for %s: %s", email[:16], e)
    return out
