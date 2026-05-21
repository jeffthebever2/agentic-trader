"""
Two-way SMS command router.

Dispatches inbound text messages (already authenticated by Sendblue
webhook secret) to per-user command handlers. Each handler returns a
short reply string the caller will send back via Sendblue.

Commands (case-insensitive, leading slash optional):
  HELP / ?               -> list available commands
  STATUS                 -> paper runner status + equity
  POSITIONS              -> current open positions (top 10)
  PRICE <ticker>         -> last quote for ticker
  HIL                    -> show pending human-in-the-loop trade if any
  STOP / UNSUBSCRIBE     -> opt out of all SMS alerts
  START / SUBSCRIBE      -> re-enable SMS alerts
  WHOAMI                 -> show your email + role
  ROLE <email> <admin|user>  (admin only) -> change another user's role

Unknown commands get a one-line "send HELP" reply.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web import users as user_store

ROOT = Path(__file__).parent.parent
HIL_STATE_FILE = ROOT / "tmp" / "hil_state.json"
PAPER_DAY_DIR = ROOT / "tmp" / "paper_trading_today"

HELP_TEXT = (
    "Agentic Trader commands:\n"
    "STATUS - runner state + equity\n"
    "POSITIONS - open positions\n"
    "HIL - pending trade approval\n"
    "WHOAMI - your account\n"
    "STOP - unsubscribe\n"
    "HELP - this message"
)


def _norm(text: str) -> tuple[str, list[str]]:
    """Lowercase + tokenize. Returns (command, args)."""
    if not text:
        return "", []
    cleaned = text.strip().lstrip("/").strip()
    parts = cleaned.split()
    if not parts:
        return "", []
    return parts[0].lower(), parts[1:]


def _format_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _latest_paper_day() -> Path | None:
    if not PAPER_DAY_DIR.exists():
        return None
    days = sorted([p for p in PAPER_DAY_DIR.iterdir() if p.is_dir()])
    return days[-1] if days else None


def _read_json_safe(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cmd_status(user: dict, args: list[str]) -> str:
    # Process status pulled from paper module helper without importing the
    # whole API (which would pull FastAPI deps and trigger circular import).
    import psutil
    running = False
    pid = None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "paper_trade_today.py" in cmd:
                running = True
                pid = proc.info["pid"]
                break
        except Exception:
            continue
    day = _latest_paper_day()
    state_line = ""
    if day:
        # Try the first strategy account we find.
        for sub in day.iterdir():
            if not sub.is_dir():
                continue
            summary = _read_json_safe(sub / "summary.json")
            if not summary:
                continue
            equity = summary.get("equity") or summary.get("total_value")
            cash = summary.get("cash")
            pcount = summary.get("position_count")
            state_line = (
                f"\n{sub.name}: equity {_format_money(equity or 0)}, "
                f"cash {_format_money(cash or 0)}, positions {pcount or 0}"
            )
            break
    return (
        f"Paper runner: {'RUNNING (pid ' + str(pid) + ')' if running else 'STOPPED'}"
        + state_line
    )


def _cmd_positions(user: dict, args: list[str]) -> str:
    day = _latest_paper_day()
    if not day:
        return "No paper-trading day directory yet."
    lines: list[str] = []
    for sub in day.iterdir():
        if not sub.is_dir():
            continue
        state = _read_json_safe(sub / "state.json")
        if not state:
            continue
        positions = state.get("positions") or {}
        if not positions:
            continue
        lines.append(f"-- {sub.name} --")
        items = list(positions.items())[:10]
        for ticker, p in items:
            try:
                qty = p.get("shares") or p.get("qty") or 0
                px = float(p.get("avg_price") or p.get("entry_price") or 0)
                lines.append(f"{ticker} {qty}@{_format_money(px)}")
            except Exception:
                lines.append(f"{ticker} ?")
        break  # first non-empty strategy is enough for a text
    return "\n".join(lines) if lines else "No open positions."


def _cmd_hil(user: dict, args: list[str]) -> str:
    if not HIL_STATE_FILE.exists():
        return "No pending HIL trade."
    state = _read_json_safe(HIL_STATE_FILE) or {}
    if state.get("status") != "pending":
        return f"No pending HIL trade (last: {state.get('status', 'unknown')})."
    ticker = state.get("ticker", "?")
    shares = state.get("shares", 0)
    price = state.get("price", 0)
    return (
        f"PENDING: BUY {shares} {ticker} @ ${price}\n"
        f"Open https://app.agentictrader.org to approve or reject."
    )


def _cmd_whoami(user: dict, args: list[str]) -> str:
    return (
        f"{user['email']}\n"
        f"role: {user.get('role', 'user')}\n"
        f"phone: {user.get('phone_number', '')}\n"
        f"verified: {user.get('sms_verified', False)}\n"
        f"opted_out: {user.get('sms_opted_out', False)}"
    )


def _cmd_stop(user: dict, args: list[str]) -> str:
    user_store.set_sms_opt_out(user["email"], True)
    return "You will no longer receive Agentic Trader SMS alerts. Reply START to re-enable."


def _cmd_start(user: dict, args: list[str]) -> str:
    user_store.set_sms_opt_out(user["email"], False)
    return "SMS alerts re-enabled. Reply HELP for available commands."


def _cmd_role(user: dict, args: list[str]) -> str:
    if user.get("role") != "admin":
        return "Admin only."
    if len(args) < 2:
        return "Usage: ROLE <email> <admin|user>"
    target_email, new_role = args[0], args[1].lower()
    if new_role not in ("admin", "user"):
        return "Role must be 'admin' or 'user'."
    try:
        rec = user_store.set_role(target_email, new_role)
        return f"OK. {rec['email']} is now {rec['role']}."
    except KeyError:
        return f"User {target_email} not found."


_HANDLERS = {
    "help": lambda u, a: HELP_TEXT,
    "?": lambda u, a: HELP_TEXT,
    "status": _cmd_status,
    "s": _cmd_status,
    "positions": _cmd_positions,
    "pos": _cmd_positions,
    "p": _cmd_positions,
    "hil": _cmd_hil,
    "whoami": _cmd_whoami,
    "me": _cmd_whoami,
    "stop": _cmd_stop,
    "unsubscribe": _cmd_stop,
    "start": _cmd_start,
    "subscribe": _cmd_start,
    "role": _cmd_role,
}


def dispatch(from_number: str, text: str) -> dict[str, Any]:
    """Run the appropriate handler for an inbound SMS.

    Returns a dict with `reply` (text to send back, or empty to stay
    silent) and `matched` (command name or None for fallback).
    """
    user = user_store.find_by_phone(from_number)
    if user is None:
        return {
            "reply": (
                "Number not registered. Visit https://app.agentictrader.org "
                "to enable SMS alerts."
            ),
            "matched": None,
            "user": None,
        }
    cmd, args = _norm(text)
    handler = _HANDLERS.get(cmd)
    if not handler:
        return {
            "reply": f"Unknown command '{cmd or '(empty)'}'. Reply HELP for commands.",
            "matched": None,
            "user": user["email"],
        }
    try:
        reply = handler(user, args)
    except Exception as exc:
        reply = "Command error: An internal error occurred."
    return {"reply": reply, "matched": cmd, "user": user["email"]}
