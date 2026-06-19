"""Small SMS alert backends for paper-trading notifications."""

from __future__ import annotations

import os
from pathlib import Path

import requests


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


def default_sms_number() -> str:
    load_env_defaults()
    return (
        os.getenv("PAPER_SMS_NUMBER")
        or os.getenv("TEXTNOW_PHONE")
        or os.getenv("TEXTNOW_ALERT_NUMBER")
        or os.getenv("SMS_NUMBER")
        or ""
    ).strip()


def _safe_sender(raw: str) -> str:
    """TextBelt sender is a short display label. Strip to a delivery-safe
    alphanumeric/space token so non-ASCII, '@', or overlong values can't
    cause route-level rejection or truncation."""
    import re
    s = re.sub(r"[^A-Za-z0-9 ]", "", (raw or "")).strip()
    return (s or "TradingAgents")[:24]


def send_textbelt(to: str, message: str) -> dict:
    load_env_defaults()
    key = os.getenv("TEXTBELT_KEY", "").strip() or "textbelt"
    sender = _safe_sender(os.getenv("TEXTBELT_SENDER", "TradingAgents"))
    # A2P compliance: the sender name must appear in the message body.
    if sender.lower() not in message.lower():
        message = f"{sender}: {message}"
    response = requests.post(
        "https://textbelt.com/text",
        data={
            "phone": to,
            "message": message,
            "key": key,
            "sender": sender,
        },
        timeout=15,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"success": False, "error": response.text[:500]}
    if not response.ok and "error" not in payload:
        payload["error"] = f"HTTP {response.status_code}"
    return payload


def send_textnow(to: str, message: str) -> dict:
    from scripts.textnow_sms import TextNowSMS

    TextNowSMS().send(to, message)
    return {"success": True}


def send_sendblue(to: str, message: str, media_url: str | None = None) -> dict:
    """Sendblue (iMessage/SMS) backend. Primary texting service.

    Requires SENDBLUE_API_KEY_ID and SENDBLUE_API_SECRET in the environment.
    Optional SENDBLUE_FROM_NUMBER pins the sending number on multi-number
    accounts. `media_url` (publicly reachable) attaches an image (e.g. a trade
    chart) as MMS/iMessage. Normalizes to the shared {success, error, ...} shape.
    """
    load_env_defaults()
    key_id = os.getenv("SENDBLUE_API_KEY_ID", "").strip()
    secret = os.getenv("SENDBLUE_API_SECRET", "").strip()
    if not key_id or not secret:
        return {
            "success": False,
            "error": "Sendblue not configured: set SENDBLUE_API_KEY_ID and SENDBLUE_API_SECRET in .env",
        }
    body = {"number": to, "content": message}
    if media_url:
        body["media_url"] = media_url
    from_number = os.getenv("SENDBLUE_FROM_NUMBER", "").strip()
    if from_number:
        body["from_number"] = from_number
    try:
        response = requests.post(
            "https://api.sendblue.co/api/send-message",
            headers={
                "sb-api-key-id": key_id,
                "sb-api-secret-key": secret,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )
    except requests.RequestException as exc:
        import logging; logging.error(f"Sendblue request failed: {exc}")
        return {"success": False, "error": "Sendblue request failed due to an internal error."}
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:500]}
    # Sendblue reports delivery via `status`: QUEUED/SENT/DELIVERED on success,
    # ERROR on failure. Map to the unified contract used by callers.
    status = str(payload.get("status", "")).upper()
    ok = response.ok and status not in ("ERROR", "DECLINED", "")
    result = {"success": ok, "provider": "sendblue", "status": status or None}
    if not ok:
        result["error"] = (
            payload.get("error_message")
            or payload.get("message")
            or payload.get("raw")
            or f"HTTP {response.status_code}"
        )
    result["response"] = payload
    return result


def evaluate_sendblue(number: str) -> dict:
    """Sendblue contact verification via the evaluate-service endpoint.

    Confirms a number is reachable and reports whether it can receive iMessage
    or only SMS — without sending a message. Returns the unified shape:
    {success, service, number, error}. `service` is "iMessage" | "SMS" | None.
    """
    load_env_defaults()
    key_id = os.getenv("SENDBLUE_API_KEY_ID", "").strip()
    secret = os.getenv("SENDBLUE_API_SECRET", "").strip()
    if not key_id or not secret:
        return {
            "success": False,
            "error": "Sendblue not configured: set SENDBLUE_API_KEY_ID and SENDBLUE_API_SECRET in .env",
        }
    try:
        response = requests.get(
            "https://api.sendblue.co/api/evaluate-service",
            headers={
                "sb-api-key-id": key_id,
                "sb-api-secret-key": secret,
                "Content-Type": "application/json",
            },
            params={"number": number},
            timeout=20,
        )
    except requests.RequestException as exc:
        import logging; logging.error(f"Sendblue request failed: {exc}")
        return {"success": False, "error": "Sendblue request failed due to an internal error."}
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:500]}
    service = payload.get("service") or None
    ok = bool(response.ok and service)
    result = {
        "success": ok,
        "provider": "sendblue",
        "number": payload.get("number") or number,
        "service": service,  # "iMessage" | "SMS" | None
        "response": payload,
    }
    if not ok:
        result["error"] = (
            payload.get("error_message")
            or payload.get("message")
            or payload.get("raw")
            or f"HTTP {response.status_code}"
        )
    return result


def send_sms(to: str, message: str, provider: str | None = None,
             media_url: str | None = None) -> dict:
    load_env_defaults()
    # Sendblue is the primary texting service; falls back only if explicitly
    # overridden via SMS_PROVIDER or the provider argument.
    provider = (provider or os.getenv("SMS_PROVIDER") or "sendblue").strip().lower()
    if provider in ("sendblue", "send_blue"):
        return send_sendblue(to, message, media_url=media_url)
    # Other providers are SMS-text-only here; media is dropped (Sendblue is primary).
    if provider == "textnow":
        return send_textnow(to, message)
    if provider == "textbelt":
        return send_textbelt(to, message)
    raise ValueError(f"Unsupported SMS_PROVIDER: {provider}")
