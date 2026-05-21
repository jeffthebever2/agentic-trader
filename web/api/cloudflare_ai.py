"""Cloudflare Workers AI status and smoke-test endpoints."""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends

from web.auth import require_admin

router = APIRouter()


def _base_url() -> str:
    gateway = os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip().rstrip("/")
    if gateway:
        # Tolerate gateway URLs that already include the OpenAI-compat path
        # (e.g. ".../compat/chat/completions") so appending "/chat/completions"
        # downstream can't produce a doubled, invalid path.
        if gateway.endswith("/chat/completions"):
            gateway = gateway[: -len("/chat/completions")].rstrip("/")
        return gateway if gateway.endswith("/compat") else gateway + "/compat"
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1" if account else ""


def _configured() -> bool:
    return bool(_base_url() and os.getenv("CLOUDFLARE_API_TOKEN", "").strip())


@router.get("/cloudflare-ai/status")
async def cloudflare_ai_status(admin: dict = Depends(require_admin)):
    return {
        "configured": _configured(),
        "account_id_set": bool(os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()),
        "api_token_set": bool(os.getenv("CLOUDFLARE_API_TOKEN", "").strip()),
        "gateway_set": bool(os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip()),
        "quick_model": os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        "deep_model": os.getenv("CLOUDFLARE_DEFAULT_DEEP_MODEL", "@cf/openai/gpt-oss-120b"),
    }


@router.post("/cloudflare-ai/test")
async def cloudflare_ai_test(admin: dict = Depends(require_admin)):
    if not _configured():
        return {"success": False, "error": "Cloudflare Workers AI is not configured"}
    model = os.getenv("CLOUDFLARE_DEFAULT_QUICK_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    # AI Gateway compat routes by provider — prefix Workers AI models.
    via_gateway = bool(os.getenv("CLOUDFLARE_AI_GATEWAY_URL", "").strip())
    request_model = ("workers-ai/" + model) if (via_gateway and model.startswith("@cf/")) else model
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                _base_url() + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.getenv('CLOUDFLARE_API_TOKEN', '').strip()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": request_model,
                    "messages": [
                        {"role": "system", "content": "Reply with a short health check."},
                        {"role": "user", "content": "Say Agentic Trader Workers AI is online."},
                    ],
                    "max_tokens": 48,
                    "temperature": 0,
                },
            )
        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:500],
                "model": model,
            }
        payload = response.json()
        text = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return {"success": True, "model": model, "response": text}
    except Exception as exc:
        return {"success": False, "error": str(exc), "model": model}
