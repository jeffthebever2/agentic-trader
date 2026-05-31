"""
Notification module — BlueBubbles iMessage + Gmail SMTP.

Env vars required:
  BlueBubbles: BLUEBUBBLES_SERVER_URL, BLUEBUBBLES_PASSWORD
  Email:       EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_SMTP_HOST (default smtp.gmail.com), EMAIL_SMTP_PORT (default 587)

Usage:
    from scripts.notify import notify_down, notify_fixed
    notify_down("papertrader", "TypeError: unexpected keyword argument")
    notify_fixed("papertrader", "Claude fixed: added data_path param")
"""

from __future__ import annotations

import os
import smtplib
import ssl
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# Load .env from repo root so credentials are available regardless of how this is invoked
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Config ────────────────────────────────────────────────────────────────────

IMESSAGE_TO   = "+16145078688"
EMAIL_TO      = "wtscott0603@gmail.com"

_SB_KEY       = lambda: os.getenv("SENDBLUE_API_KEY_ID", "")
_SB_SECRET    = lambda: os.getenv("SENDBLUE_API_SECRET", "")
_SB_FROM      = lambda: os.getenv("SENDBLUE_FROM_NUMBER", "")
# Brevo SMTP (primary) — uses SMTP_USERNAME / SMTP_PASSWORD
# Gmail fallback — uses GMAIL_ADDRESS / GMAIL_APP_PASSWORD + smtp.gmail.com
_EM_HOST      = lambda: os.getenv("SMTP_HOST", "smtp.gmail.com")
_EM_PORT      = lambda: int(os.getenv("SMTP_PORT", "587"))
_EM_USER      = lambda: os.getenv("SMTP_USERNAME", "")
_EM_PASS      = lambda: os.getenv("SMTP_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", ""))
_EM_FROM      = lambda: os.getenv("EMAIL_FROM", os.getenv("GMAIL_ADDRESS", _EM_USER()))


# ── SendBlue iMessage ─────────────────────────────────────────────────────────

def _e164(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def send_imessage(message: str, to: str = IMESSAGE_TO) -> bool:
    """Send iMessage via SendBlue API. Returns True on success."""
    import urllib.request
    import json as _json

    key    = _SB_KEY()
    secret = _SB_SECRET()
    if not key or not secret:
        print("[notify] SendBlue not configured (SENDBLUE_API_KEY_ID / SENDBLUE_API_SECRET missing)")
        return False

    payload: dict = {"number": _e164(to), "content": message}
    frm = _SB_FROM()
    if frm:
        payload["from_number"] = _e164(frm) if not frm.startswith("+") else frm

    data = _json.dumps(payload).encode()
    req  = urllib.request.Request(
        "https://api.sendblue.co/api/send-message",
        data=data,
        headers={
            "Content-Type":        "application/json",
            "sb-api-key-id":       key,
            "sb-api-secret-key":   secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            body = resp.read().decode(errors="replace")
            ok = resp.status < 300
            if not ok:
                print(f"[notify] SendBlue error {resp.status}: {body[:200]}")
            return ok
    except Exception as e:
        print(f"[notify] SendBlue send failed: {e}")
        return False


# ── Gmail SMTP ────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str, to: str = EMAIL_TO) -> bool:
    """Send email via SMTP. Returns True on success."""
    from_addr = _EM_FROM()
    username  = _EM_USER()
    password  = _EM_PASS()
    host      = _EM_HOST()
    port      = _EM_PORT()

    if not username or not password or not host:
        print("[notify] Email not configured (SMTP_USERNAME / GMAIL_APP_PASSWORD / SMTP_HOST missing)")
        return False

    try:
        msg             = MIMEText(body, "plain", "utf-8")
        msg["From"]     = from_addr or username
        msg["To"]       = to
        msg["Subject"]  = subject
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=_SSL_CTX)
            server.login(username, password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[notify] Email send failed: {e}")
        return False


# ── High-level alert helpers ──────────────────────────────────────────────────

def notify_down(service: str, error_summary: str) -> None:
    """Alert: service is down / crash-looping."""
    ts  = time.strftime("%Y-%m-%d %H:%M")
    sms = f"[AgenticTrader] {service} DOWN at {ts}\n{error_summary[:200]}"
    sub = f"[AgenticTrader] {service} is DOWN"
    body = f"{service} crashed at {ts}.\n\nError summary:\n{error_summary}\n\nClaude is auditing the crash."
    send_imessage(sms)
    send_email(sub, body)


def notify_fixed(service: str, fix_summary: str) -> None:
    """Alert: Claude fixed the crash."""
    ts  = time.strftime("%Y-%m-%d %H:%M")
    sms = f"[AgenticTrader] {service} FIXED at {ts} by Claude\n{fix_summary[:200]}"
    sub = f"[AgenticTrader] {service} fixed by Claude"
    body = f"{service} was repaired at {ts}.\n\nWhat Claude did:\n{fix_summary}\n\nService will restart automatically via launchd."
    send_imessage(sms)
    send_email(sub, body)


def notify_skip(service: str, skip_reason: str) -> None:
    """Alert: Claude audited but decided not to fix (non-code issue)."""
    ts  = time.strftime("%Y-%m-%d %H:%M")
    sms = f"[AgenticTrader] {service} CRASHED at {ts} — needs manual attention\n{skip_reason[:200]}"
    sub = f"[AgenticTrader] {service} crash — manual fix needed"
    body = f"{service} crashed at {ts} and Claude could not auto-fix it.\n\nReason:\n{skip_reason}\n\nPlease investigate manually.\nLog: /Users/williamscott/Desktop/TradingAgents-0.2.4 copy/logs/autofix.log"
    send_imessage(sms)
    send_email(sub, body)


if __name__ == "__main__":
    # Quick test
    print("Testing iMessage...")
    ok = send_imessage("[AgenticTrader] Test notification — system is live")
    print(f"iMessage: {'OK' if ok else 'FAILED'}")
    print("Testing email...")
    ok = send_email("[AgenticTrader] Test", "AgenticTrader notification system is live.")
    print(f"Email: {'OK' if ok else 'FAILED'}")
