
from web.api.analysis import (
    AnalyzeRequest,
    _humanize_analysis_error,
    _validate_provider_auth,
)
from tradingagents.llm_clients.openai_client import OpenAIClient


def _analysis_request(provider: str = "openrouter") -> AnalyzeRequest:
    return AnalyzeRequest(
        ticker="AAPL",
        analysis_date="2026-05-11",
        analysts=["market"],
        llm_provider=provider,
        deep_think_llm="nvidia/nemotron-3-super-120b-a12b:free",
        quick_think_llm="nvidia/nemotron-3-super-120b-a12b:free",
    )


def test_openrouter_short_env_alias_is_accepted(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_KEY", "sk-or-v1-real-looking")

    assert _validate_provider_auth(_analysis_request()) is None


def test_openrouter_placeholder_key_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)

    message = _validate_provider_auth(_analysis_request())

    assert message is not None
    assert "placeholder" in message
    assert "OpenRouter" in message


def test_cloudflare_api_token_is_accepted(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-real-looking-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.delenv("CLOUDFLARE_AI_GATEWAY_URL", raising=False)

    assert _validate_provider_auth(_analysis_request("cloudflare")) is None


def test_cloudflare_requires_account_or_gateway(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-real-looking-token")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_AI_GATEWAY_URL", raising=False)

    message = _validate_provider_auth(_analysis_request("cloudflare"))

    assert message is not None
    assert "CLOUDFLARE_ACCOUNT_ID" in message
    assert "CLOUDFLARE_AI_GATEWAY_URL" in message


def test_openrouter_401_user_not_found_is_actionable():
    message = _humanize_analysis_error(
        Exception("Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"),
        "openrouter",
    )

    assert "OpenRouter authentication failed" in message
    assert "User not found" in message
    assert "Settings > AI Providers" in message


def test_openrouter_client_uses_short_env_alias(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_KEY", "sk-or-v1-alias")

    llm = OpenAIClient("nvidia/nemotron-3-super-120b-a12b:free", provider="openrouter").get_llm()

    secret = getattr(llm, "openai_api_key", None)
    assert secret is not None
    assert secret.get_secret_value() == "sk-or-v1-alias"
