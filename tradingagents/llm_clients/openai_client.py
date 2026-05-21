import os
from typing import Any, Optional

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from tradingagents.openrouter_usage import record_openrouter_request

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model


class NormalizedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with normalized content output.

    The Responses API returns content as a list of typed blocks
    (reasoning, text, etc.). This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))

    def with_structured_output(self, schema, *, method=None, **kwargs):
        """Wrap with structured output, defaulting to function_calling for OpenAI.

        langchain-openai's Responses-API-parse path (the default for json_schema
        when use_responses_api=True) calls response.model_dump(...) on the OpenAI
        SDK's union-typed parsed response, which makes Pydantic emit ~20
        PydanticSerializationUnexpectedValue warnings per call. The function-calling
        path returns a plain tool-call shape that does not trigger that
        serialization, so it is the cleaner choice for our combination of
        use_responses_api=True + with_structured_output. Both paths use OpenAI's
        strict mode and produce the same typed Pydantic instance.
        """
        if method is None:
            method = "function_calling"
        return super().with_structured_output(schema, method=method, **kwargs)


def _stringify_message_content(content: Any) -> str:
    """Flatten LangChain/OpenAI typed content blocks for stricter chat APIs."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text is None:
                    text = block.get("content")
                if text is not None:
                    parts.append(str(text))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return str(content)


def _coerce_cloudflare_chat_input(input: Any) -> Any:
    """Workers AI requires every chat message content value to be a string."""
    if hasattr(input, "to_messages"):
        return [_coerce_cloudflare_chat_input(msg) for msg in input.to_messages()]
    if isinstance(input, BaseMessage):
        content = _stringify_message_content(input.content)
        if hasattr(input, "model_copy"):
            return input.model_copy(update={"content": content})
        return input.copy(update={"content": content})
    if isinstance(input, dict):
        coerced = dict(input)
        if "content" in coerced:
            coerced["content"] = _stringify_message_content(coerced.get("content"))
        if "messages" in coerced and isinstance(coerced["messages"], list):
            coerced["messages"] = [
                _coerce_cloudflare_chat_input(message)
                for message in coerced["messages"]
            ]
        return coerced
    if isinstance(input, tuple) and len(input) >= 2:
        items = list(input)
        items[1] = _stringify_message_content(items[1])
        return tuple(items)
    if isinstance(input, list):
        return [_coerce_cloudflare_chat_input(item) for item in input]
    return input


class CloudflareWorkersAIChatOpenAI(NormalizedChatOpenAI):
    """ChatOpenAI adapter for Workers AI's stricter message schema."""

    def invoke(self, input, config=None, **kwargs):
        return super().invoke(_coerce_cloudflare_chat_input(input), config, **kwargs)


class OpenRouterTrackedChatOpenAI(NormalizedChatOpenAI):
    """ChatOpenAI wrapper that records one local OpenRouter request per invoke."""

    def invoke(self, input, config=None, **kwargs):
        record_openrouter_request("analysis_openrouter")
        return super().invoke(input, config, **kwargs)

# Kwargs forwarded from user config to ChatOpenAI
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "reasoning_effort",
    "api_key", "callbacks", "http_client", "http_async_client",
)

# Provider base URLs and API key env vars. Some users already have the shorter
# OpenRouter name from older scripts, so keep it as a fallback alias.
_PROVIDER_CONFIG = {
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY", "OPENROUTER_KEY")),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "glm": ("https://api.z.ai/api/paas/v4/", "ZHIPU_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}

# Maps our provider name -> (Cloudflare AI Gateway provider slug, API key env).
# When CLOUDFLARE_GATEWAY_ALL is enabled and the gateway is configured, every
# supported provider is routed through the gateway's OpenAI-compat endpoint
# (model prefixed with the slug) so all AI traffic is logged/secured in one
# place. Providers absent here (nvidia, qwen, glm, ollama) stay direct.
_CF_GATEWAY_SLUGS = {
    "openai":     ("openai", "OPENAI_API_KEY"),
    "anthropic":  ("anthropic", "ANTHROPIC_API_KEY"),
    "google":     ("google-ai-studio", ("GOOGLE_API_KEY", "GEMINI_API_KEY")),
    "openrouter": ("openrouter", ("OPENROUTER_API_KEY", "OPENROUTER_KEY")),
    "deepseek":   ("deepseek", "DEEPSEEK_API_KEY"),
    "xai":        ("grok", "XAI_API_KEY"),
    "cloudflare": ("workers-ai", "CLOUDFLARE_API_TOKEN"),
}


def _cf_gateway_compat_base() -> str:
    """Gateway compat base ('.../compat'), or '' if no gateway configured."""
    gateway = os.environ.get("CLOUDFLARE_AI_GATEWAY_URL", "").strip().rstrip("/")
    if not gateway:
        return ""
    if gateway.endswith("/chat/completions"):
        gateway = gateway[: -len("/chat/completions")].rstrip("/")
    return gateway if gateway.endswith("/compat") else gateway + "/compat"


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and xAI providers.

    For native OpenAI models, uses the Responses API (/v1/responses) which
    supports reasoning_effort with function tools across all model families
    (GPT-4.1, GPT-5). Third-party compatible providers (xAI, OpenRouter,
    Ollama) use standard Chat Completions.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance."""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        # Route every supported provider through the Cloudflare AI Gateway when
        # CLOUDFLARE_GATEWAY_ALL is enabled — one secure, logged egress for all
        # AI keys. Model is prefixed with the gateway provider slug; the bearer
        # token stays the downstream provider's own key.
        routed_via_gateway = False
        gateway_all = os.environ.get("CLOUDFLARE_GATEWAY_ALL", "").strip().lower() in ("1", "true", "yes", "on")
        compat_base = _cf_gateway_compat_base() if gateway_all else ""
        if compat_base and self.provider in _CF_GATEWAY_SLUGS:
            slug, key_env = _CF_GATEWAY_SLUGS[self.provider]
            env_names = key_env if isinstance(key_env, tuple) else (key_env,)
            api_key = next((os.environ.get(n) for n in env_names if os.environ.get(n)), None)
            if api_key:
                llm_kwargs["base_url"] = compat_base
                llm_kwargs["api_key"] = api_key
                if "/" not in self.model:  # don't double-prefix
                    llm_kwargs["model"] = f"{slug}/{self.model}"
                routed_via_gateway = True

        # Provider-specific base URL and auth
        if routed_via_gateway:
            pass
        elif self.provider == "cloudflare":
            # Workers AI OpenAI-compatible endpoint or AI Gateway URL.
            # Gateway is preferred when set (logging, caching, retries).
            gateway = os.environ.get("CLOUDFLARE_AI_GATEWAY_URL", "").rstrip("/")
            account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
            token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
            via_gateway = bool(gateway)
            if gateway:
                # Tolerate a gateway URL that already includes the compat chat
                # path so the OpenAI SDK doesn't append a second one.
                if gateway.endswith("/chat/completions"):
                    gateway = gateway[: -len("/chat/completions")].rstrip("/")
                base = gateway if gateway.endswith("/compat") else gateway + "/compat"
            elif account:
                base = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"
            else:
                raise ValueError(
                    "Cloudflare provider requires CLOUDFLARE_AI_GATEWAY_URL or CLOUDFLARE_ACCOUNT_ID"
                )
            llm_kwargs["base_url"] = base
            # The AI Gateway OpenAI-compat endpoint routes by provider, so a
            # Workers AI model ("@cf/...") must be prefixed "workers-ai/".
            # The direct Workers AI endpoint takes the bare "@cf/..." id.
            if via_gateway and self.model.startswith("@cf/"):
                llm_kwargs["model"] = "workers-ai/" + self.model
            if token:
                llm_kwargs["api_key"] = token
            # Workers AI chat completions behaves like an OpenAI-compatible
            # chat endpoint, not OpenAI's native Responses API.
        elif self.provider in _PROVIDER_CONFIG:
            base_url, api_key_env = _PROVIDER_CONFIG[self.provider]
            llm_kwargs["base_url"] = base_url
            if api_key_env:
                env_names = api_key_env if isinstance(api_key_env, tuple) else (api_key_env,)
                api_key = next((os.environ.get(name) for name in env_names if os.environ.get(name)), None)
                if api_key:
                    llm_kwargs["api_key"] = api_key
            else:
                llm_kwargs["api_key"] = "ollama"
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Forward user-provided kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Native OpenAI: use Responses API for consistent behavior across
        # all model families. Third-party providers use Chat Completions.
        if self.provider == "openai" and not routed_via_gateway:
            llm_kwargs["use_responses_api"] = True

        if self.provider == "cloudflare":
            chat_cls = CloudflareWorkersAIChatOpenAI
        elif self.provider == "openrouter":
            chat_cls = OpenRouterTrackedChatOpenAI
        else:
            chat_cls = NormalizedChatOpenAI
        return chat_cls(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for the provider."""
        return validate_model(self.provider, self.model)
