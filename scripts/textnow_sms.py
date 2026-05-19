"""
TextNow SMS sender using PyTextNow API — direct API calls, no browser automation.

Setup:
  1. Install PyTextNow: pip install PyTextNow
  2. Get your TextNow username from https://www.textnow.com (your profile)
  3. Extract cookies:
     - Log in at https://www.textnow.com
     - Open DevTools → Application → Cookies → textnow.com
     - Copy values for: 'connect.sid' and '_csrf' (or similar CSRF cookie)
  4. Set these in your .env file:
     TEXTNOW_USERNAME=your_username
     TEXTNOW_SID=connect_sid_cookie_value
     TEXTNOW_CSRF_COOKIE=csrf_cookie_value

Usage:
  from scripts.textnow_sms import send_sms
  send_sms("+15551234567", "AAPL bought @ $182.50")
"""

import os
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def _load_env_defaults() -> None:
    """Load .env file into os.environ if not already set."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class TextNowSMS:
    """Send SMS via TextNow using PyTextNow API (no browser needed)."""

    def __init__(
        self,
        username: str = "",
        sid_cookie: str = "",
        csrf_cookie: str = "",
    ):
        self.username = username or os.environ.get("TEXTNOW_USERNAME", "").strip()
        self.sid_cookie = sid_cookie or os.environ.get("TEXTNOW_SID", "").strip()
        self.csrf_cookie = csrf_cookie or os.environ.get("TEXTNOW_CSRF_COOKIE", "").strip()

        if not self.username:
            raise ValueError(
                "TextNow username not set (pass username= or set TEXTNOW_USERNAME in .env)"
            )
        if not self.sid_cookie:
            raise ValueError(
                "TextNow SID cookie not set (pass sid_cookie= or set TEXTNOW_SID in .env)"
            )
        if not self.csrf_cookie:
            raise ValueError(
                "TextNow CSRF cookie not set (pass csrf_cookie= or set TEXTNOW_CSRF_COOKIE in .env)"
            )

    def send(self, to: str, message: str) -> dict:
        """
        Send an SMS to `to` (E.164 format or 10-digit US).
        Returns the API response dict. Raises an exception on failure.
        """
        try:
            import pytextnow as pytn
        except ImportError:
            raise RuntimeError(
                "PyTextNow not installed. Install with: pip install PyTextNow"
            )

        # Normalize phone number
        to = to.strip()
        if not to.startswith("+"):
            to = "+1" + to.lstrip("1")

        try:
            # Create client with explicit credentials
            client = pytn.Client(
                username=self.username,
                sid_cookie=self.sid_cookie,
                csrf_cookie=self.csrf_cookie,
            )
            # Send SMS via API
            client.send_sms(to, message)
            return {"success": True, "message": "SMS sent successfully"}
        except Exception as e:
            raise RuntimeError(f"Failed to send SMS: {str(e)}")


# ── convenience function ─────────────────────────────────────────────────────


def send_sms(
    to: str, message: str, username: str = "", sid_cookie: str = ""
) -> bool:
    """
    One-shot helper. Returns True on success, False on error.
    Loads credentials from env if not passed.
    """
    try:
        _load_env_defaults()
        client = TextNowSMS(username=username, sid_cookie=sid_cookie)
        client.send(to, message)
        return True
    except Exception as e:
        print(f"[textnow_sms] send failed: {e}")
        return False


# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    if len(sys.argv) < 3:
        print("Usage: python textnow_sms.py <phone> <message>")
        print("Example: python textnow_sms.py +15551234567 'Test from TradingAgents'")
        sys.exit(1)

    phone, msg = sys.argv[1], " ".join(sys.argv[2:])
    print(f"Sending to {phone}: {msg!r}")
    ok = send_sms(phone, msg)
    print("✓ Sent OK" if ok else "✗ FAILED — check credentials in .env")
    sys.exit(0 if ok else 1)

