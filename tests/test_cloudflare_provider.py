"""Tests for the Cloudflare Workers AI provider wiring."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_factory_routes_cloudflare_to_openai_client(monkeypatch):
    """factory.create_llm_client('cloudflare', ...) returns an OpenAIClient."""
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    from tradingagents.llm_clients.factory import create_llm_client
    from tradingagents.llm_clients.openai_client import OpenAIClient
    client = create_llm_client("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    assert isinstance(client, OpenAIClient)
    assert client.provider == "cloudflare"


def test_cloudflare_base_url_uses_account_id(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.delenv("CLOUDFLARE_AI_GATEWAY_URL", raising=False)
    from tradingagents.llm_clients.openai_client import OpenAIClient

    # Stub ChatOpenAI so we don't need the real SDK / network.
    captured = {}
    import tradingagents.llm_clients.openai_client as mod

    class FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(mod, "CloudflareWorkersAIChatOpenAI", FakeChat)

    client = OpenAIClient("@cf/meta/llama-3.3-70b-instruct-fp8-fast", provider="cloudflare")
    client.get_llm()
    assert captured["base_url"] == (
        "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1"
    )
    assert captured["api_key"] == "tok"


def test_cloudflare_prefers_gateway_url(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv(
        "CLOUDFLARE_AI_GATEWAY_URL",
        "https://gateway.ai.cloudflare.com/v1/acct123/tradingagents",
    )
    from tradingagents.llm_clients.openai_client import OpenAIClient
    import tradingagents.llm_clients.openai_client as mod

    captured = {}

    class FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(mod, "CloudflareWorkersAIChatOpenAI", FakeChat)

    OpenAIClient("@cf/meta/llama-3.3-70b-instruct-fp8-fast", provider="cloudflare").get_llm()
    # `/compat` is auto-appended for OpenAI compatibility.
    assert captured["base_url"].endswith("/compat")
    assert "gateway.ai.cloudflare.com" in captured["base_url"]


def test_cloudflare_missing_creds_raises(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_AI_GATEWAY_URL", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    from tradingagents.llm_clients.openai_client import OpenAIClient
    with pytest.raises(ValueError, match="Cloudflare provider requires"):
        OpenAIClient("@cf/meta/llama-3.3-70b-instruct-fp8-fast", provider="cloudflare").get_llm()


def test_cloudflare_chat_input_flattens_typed_content_blocks():
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from tradingagents.llm_clients.openai_client import _coerce_cloudflare_chat_input

    messages = [
        SystemMessage(content=[{"type": "text", "text": "system rules"}]),
        HumanMessage(content=[{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]),
        AIMessage(content="already plain"),
        {"role": "user", "content": [{"type": "text", "text": "dict text"}]},
        ("human", [{"type": "text", "text": "tuple text"}]),
    ]

    coerced = _coerce_cloudflare_chat_input(messages)

    assert coerced[0].content == "system rules"
    assert coerced[1].content == "first\nsecond"
    assert coerced[2].content == "already plain"
    assert coerced[3]["content"] == "dict text"
    assert coerced[4] == ("human", "tuple text")


def test_cloudflare_chat_input_converts_null_dict_content_to_empty_string():
    from tradingagents.llm_clients.openai_client import _coerce_cloudflare_chat_input

    assert _coerce_cloudflare_chat_input({"role": "assistant", "content": None}) == {
        "role": "assistant",
        "content": "",
    }
