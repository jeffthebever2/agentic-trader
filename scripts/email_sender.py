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


def send_email(to: str | Iterable[str], subject: str, message: str) -> dict:
    load_env_defaults()
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", "").strip()
    reply_to = os.getenv("EMAIL_REPLY_TO", "").strip()
    recipients = _split_recipients(to)

    missing = [
        name
        for name, value in {
            "SMTP_HOST": host,
            "SMTP_PORT": str(port),
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
            "EMAIL_FROM": sender,
        }.items()
        if not value
    ]
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
        return {"success": False, "error": str(exc), "from": sender, "to_count": len(recipients)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Send an Agentic Trader test email.")
    parser.add_argument("to")
    parser.add_argument("--subject", default="Agentic Trader email test")
    parser.add_argument("--message", default="Agentic Trader transactional email is configured.")
    args = parser.parse_args()
    print(send_email(args.to, args.subject, args.message))
