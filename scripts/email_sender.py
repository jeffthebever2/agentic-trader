import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


def load_env_defaults() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _split_recipients(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    else:
        parts = list(value)
    return [str(p).strip() for p in parts if str(p).strip()]


def smtp_configured() -> bool:
    load_env_defaults()
    return bool(
        os.getenv("SMTP_HOST")
        and os.getenv("SMTP_PORT")
        and os.getenv("SMTP_USERNAME")
        and os.getenv("SMTP_PASSWORD")
        and os.getenv("EMAIL_FROM")
    )


def resend_configured() -> bool:
    load_env_defaults()
    return bool(os.getenv("RESEND_API_KEY", "").strip()
                and (os.getenv("RESEND_FROM", "").strip() or os.getenv("EMAIL_FROM", "").strip()))


def _use_resend() -> bool:
    """Prefer Resend when a provider is chosen or Resend is the only thing set."""
    provider = os.getenv("EMAIL_PROVIDER", "").strip().lower()
    if provider == "resend":
        return True
    if provider == "smtp":
        return False
    return resend_configured()   # auto: use Resend if it's configured


def _send_resend(recipients: list[str], subject: str, message: str) -> dict:
    """Send via the Resend HTTP API (stdlib only, no extra dependency)."""
    import json as _json
    import urllib.request as _rq
    import urllib.error as _err
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = (os.getenv("RESEND_FROM", "").strip() or os.getenv("EMAIL_FROM", "").strip())
    if not api_key or not sender:
        return {"success": False, "error": "Missing RESEND_API_KEY / RESEND_FROM"}
    if not recipients:
        return {"success": False, "error": "Missing recipient"}
    payload = {"from": sender, "to": recipients, "subject": subject, "text": message}
    reply_to = os.getenv("EMAIL_REPLY_TO", "").strip()
    if reply_to:
        payload["reply_to"] = reply_to
    req = _rq.Request(
        "https://api.resend.com/emails",
        data=_json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend's API sits behind Cloudflare, which blocks the default
            # "Python-urllib" User-Agent with "error code: 1010". Send a real one.
            "User-Agent": "AgenticTrader/1.0 (+https://agentictrader.org)",
            "Accept": "application/json",
        },
        method="POST",
    )
    # certifi CA bundle — some Python builds ship without system CA certs, which
    # would otherwise break HTTPS to the Resend API.
    import ssl as _ssl
    try:
        import certifi
        _ctx = _ssl.create_default_context(cafile=certifi.where())
    except Exception:
        _ctx = _ssl.create_default_context()
    try:
        with _rq.urlopen(req, timeout=20, context=_ctx) as resp:
            body = _json.loads(resp.read().decode("utf-8") or "{}")
        return {"success": True, "provider": "resend", "id": body.get("id"),
                "from": sender, "to_count": len(recipients)}
    except _err.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200] if hasattr(e, "read") else str(e)
        import logging; logging.error("Resend send failed (%s): %s", e.code, detail)
        return {"success": False, "provider": "resend", "error": f"Resend {e.code}: {detail}"}
    except Exception as exc:
        import logging; logging.error("Resend send error: %s", exc)
        return {"success": False, "provider": "resend", "error": "An internal error occurred."}


def send_email(to: str | Iterable[str], subject: str, message: str) -> dict:
    load_env_defaults()
    # Resend is the preferred transport when configured; SMTP is the fallback.
    if _use_resend():
        return _send_resend(_split_recipients(to), subject, message)
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", "").strip()
    reply_to = os.getenv("EMAIL_REPLY_TO", "").strip()
    recipients = _split_recipients(to)

    missing = []
    if not host: missing.append("SMTP_HOST")
    if not port: missing.append("SMTP_PORT")
    if not username: missing.append("SMTP_USERNAME")
    if not password: missing.append("SMTP_PASSWORD")
    if not sender: missing.append("EMAIL_FROM")
    if missing:
        return {"success": False, "error": "Missing " + ", ".join(missing)}
    if not recipients:
        return {"success": False, "error": "Missing recipient"}

    msg = EmailMessage()
    msg.set_content(message)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(msg)
        return {"success": True, "from": sender, "reply_to": reply_to, "to_count": len(recipients)}
    except Exception as exc:
        import logging; logging.error(f"Email sending error: {exc}")
        return {"success": False, "error": "An internal error occurred.", "from": sender, "to_count": len(recipients)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Send an Agentic Trader test email.")
    parser.add_argument("to")
    parser.add_argument("--subject", default="Agentic Trader email test")
    parser.add_argument("--message", default="Agentic Trader transactional email is configured.")
    args = parser.parse_args()
    print(send_email(args.to, args.subject, args.message))
