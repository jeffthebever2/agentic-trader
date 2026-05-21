"""Shared model catalog for CLI selections and validation."""

from __future__ import annotations

from typing import Dict, List, Tuple

ModelOption = Tuple[str, str]
ProviderModeOptions = Dict[str, Dict[str, List[ModelOption]]]


MODEL_OPTIONS: ProviderModeOptions = {
    "openrouter": {
        "quick": [
            ("Nemotron 3 Super 120B (Free) ★", "nvidia/nemotron-3-super-120b-a12b:free"),
            ("GPT-OSS 120B (Free) ★", "openai/gpt-oss-120b:free"),
            ("Llama 3.3 70B (Free)", "meta-llama/llama-3.3-70b-instruct:free"),
            ("DeepSeek V4 Flash (Free)", "deepseek/deepseek-v4-flash:free"),
            ("Qwen3 Coder (Free)", "qwen/qwen3-coder:free"),
            ("Laguna M.1 Coding (Free)", "poolside/laguna-m.1:free"),
            ("GLM 4.5 Air (Free)", "z-ai/glm-4.5-air:free"),
            ("MiniMax M2.5 (Free)", "minimax/minimax-m2.5:free"),
            ("Nemotron 3 Nano 30B (Free)", "nvidia/nemotron-3-nano-30b-a3b:free"),
            ("GPT-OSS 20B (Free)", "openai/gpt-oss-20b:free"),
            ("Laguna XS.2 Coding (Free)", "poolside/laguna-xs.2:free"),
            ("Nemotron 3 Nano Omni (Free)", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"),
            ("Gemma 4 31B (Free)", "google/gemma-4-31b-it:free"),
            ("Nemotron Nano 12B VL (Free)", "nvidia/nemotron-nano-12b-v2-vl:free"),
            ("Nemotron Nano 9B V2 (Free)", "nvidia/nemotron-nano-9b-v2:free"),
            ("CoBuddy Code (Free)", "baidu/cobuddy:free"),
            ("Gemma 4 26B A4B (Free)", "google/gemma-4-26b-a4b-it:free"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Nemotron 3 Super 120B (Free) ★", "nvidia/nemotron-3-super-120b-a12b:free"),
            ("GPT-OSS 120B (Free) ★", "openai/gpt-oss-120b:free"),
            ("Llama 3.3 70B (Free)", "meta-llama/llama-3.3-70b-instruct:free"),
            ("Hermes 3 405B (Free)", "nousresearch/hermes-3-llama-3.1-405b:free"),
            ("Trinity Large Thinking (Free)", "arcee-ai/trinity-large-thinking:free"),
            ("Qwen3 Next 80B A3B (Free)", "qwen/qwen3-next-80b-a3b-instruct:free"),
            ("Qwen3 Coder (Free)", "qwen/qwen3-coder:free"),
            ("DeepSeek V4 Flash (Free)", "deepseek/deepseek-v4-flash:free"),
            ("Laguna M.1 Coding (Free)", "poolside/laguna-m.1:free"),
            ("MiniMax M2.5 (Free)", "minimax/minimax-m2.5:free"),
            ("GLM 4.5 Air (Free)", "z-ai/glm-4.5-air:free"),
            ("Nemotron 3 Nano 30B (Free)", "nvidia/nemotron-3-nano-30b-a3b:free"),
            ("Gemma 4 31B (Free)", "google/gemma-4-31b-it:free"),
            ("Custom model ID", "custom"),
        ],
    },
    "nvidia": {
        "quick": [
            ("Nemotron Ultra 253B - Flagship reasoning", "nvidia/llama-3.1-nemotron-ultra-253b-v1"),
            ("Nemotron Super 49B - Fast + accurate", "nvidia/llama-3.3-nemotron-super-49b-v1"),
            ("Llama 3.1 70B Instruct - Fast, balanced", "meta/llama-3.1-70b-instruct"),
            ("Llama 3.3 70B Instruct - Latest 70B", "meta/llama-3.3-70b-instruct"),
            ("Mistral 7B Instruct - Lightweight", "mistralai/mistral-7b-instruct-v0.3"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Nemotron Ultra 253B - Flagship reasoning", "nvidia/llama-3.1-nemotron-ultra-253b-v1"),
            ("Nemotron Super 49B - Fast + accurate", "nvidia/llama-3.3-nemotron-super-49b-v1"),
            ("Llama 3.1 405B Instruct - Max intelligence", "meta/llama-3.1-405b-instruct"),
            ("Mixtral 8x22B Instruct - MoE deep reasoning", "mistralai/mixtral-8x22b-instruct-v0.1"),
            ("Llama 3.1 70B Instruct - Balanced", "meta/llama-3.1-70b-instruct"),
            ("Custom model ID", "custom"),
        ],
    },
    "openai": {
        "quick": [
            ("GPT-5.4 Mini - Fast, strong coding and tool use", "gpt-5.4-mini"),
            ("GPT-5.4 Nano - Cheapest, high-volume tasks", "gpt-5.4-nano"),
            ("GPT-5.4 - Latest frontier, 1M context", "gpt-5.4"),
            ("GPT-4.1 - Smartest non-reasoning model", "gpt-4.1"),
        ],
        "deep": [
            ("GPT-5.4 - Latest frontier, 1M context", "gpt-5.4"),
            ("GPT-5.2 - Strong reasoning, cost-effective", "gpt-5.2"),
            ("GPT-5.4 Mini - Fast, strong coding and tool use", "gpt-5.4-mini"),
            ("GPT-5.4 Pro - Most capable, expensive ($30/$180 per 1M tokens)", "gpt-5.4-pro"),
        ],
    },
    "anthropic": {
        "quick": [
            ("Claude Sonnet 4.6 - Best speed and intelligence balance", "claude-sonnet-4-6"),
            ("Claude Haiku 4.5 - Fast, near-instant responses", "claude-haiku-4-5"),
            ("Claude Sonnet 4.5 - Agents and coding", "claude-sonnet-4-5"),
        ],
        "deep": [
            ("Claude Opus 4.6 - Most intelligent, agents and coding", "claude-opus-4-6"),
            ("Claude Opus 4.5 - Premium, max intelligence", "claude-opus-4-5"),
            ("Claude Sonnet 4.6 - Best speed and intelligence balance", "claude-sonnet-4-6"),
            ("Claude Sonnet 4.5 - Agents and coding", "claude-sonnet-4-5"),
        ],
    },
    "google": {
        "quick": [
            ("Gemini 2.5 Flash - FREE tier, balanced", "gemini-2.5-flash"),
            ("Gemini 2.5 Flash Lite - FREE tier, fastest/cheapest", "gemini-2.5-flash-lite"),
            ("Gemini 2.0 Flash - FREE tier, fast", "gemini-2.0-flash"),
            ("Gemini 2.0 Flash Lite - FREE tier, low-cost", "gemini-2.0-flash-lite"),
            ("Gemini 3 Flash - Next-gen fast", "gemini-3-flash-preview"),
            ("Gemini 3.1 Flash Lite - Most cost-efficient", "gemini-3.1-flash-lite-preview"),
        ],
        "deep": [
            ("Gemini 2.5 Flash - FREE tier, balanced", "gemini-2.5-flash"),
            ("Gemini 2.0 Flash - FREE tier, fast", "gemini-2.0-flash"),
            ("Gemini 3.1 Pro - Reasoning-first, complex workflows", "gemini-3.1-pro-preview"),
            ("Gemini 3 Flash - Next-gen fast", "gemini-3-flash-preview"),
            ("Gemini 2.5 Pro - Stable pro model", "gemini-2.5-pro"),
        ],
    },
    "xai": {
        "quick": [
            ("Grok 4.1 Fast (Non-Reasoning) - Speed optimized, 2M ctx", "grok-4-1-fast-non-reasoning"),
            ("Grok 4 Fast (Non-Reasoning) - Speed optimized", "grok-4-fast-non-reasoning"),
            ("Grok 4.1 Fast (Reasoning) - High-performance, 2M ctx", "grok-4-1-fast-reasoning"),
        ],
        "deep": [
            ("Grok 4 - Flagship model", "grok-4-0709"),
            ("Grok 4.1 Fast (Reasoning) - High-performance, 2M ctx", "grok-4-1-fast-reasoning"),
            ("Grok 4 Fast (Reasoning) - High-performance", "grok-4-fast-reasoning"),
            ("Grok 4.1 Fast (Non-Reasoning) - Speed optimized, 2M ctx", "grok-4-1-fast-non-reasoning"),
        ],
    },
    "deepseek": {
        "quick": [
            ("DeepSeek V3.2", "deepseek-chat"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("DeepSeek V3.2 (thinking)", "deepseek-reasoner"),
            ("DeepSeek V3.2", "deepseek-chat"),
            ("Custom model ID", "custom"),
        ],
    },
    "qwen": {
        "quick": [
            ("Qwen 3.5 Flash", "qwen3.5-flash"),
            ("Qwen Plus", "qwen-plus"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Qwen 3.6 Plus", "qwen3.6-plus"),
            ("Qwen 3.5 Plus", "qwen3.5-plus"),
            ("Qwen 3 Max", "qwen3-max"),
            ("Custom model ID", "custom"),
        ],
    },
    "glm": {
        "quick": [
            ("GLM-4.7", "glm-4.7"),
            ("GLM-5", "glm-5"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("GLM-5.1", "glm-5.1"),
            ("GLM-5", "glm-5"),
            ("Custom model ID", "custom"),
        ],
    },
    # Azure: any deployed model name.
    "ollama": {
        "quick": [
            ("Qwen3:latest (8B, local)", "qwen3:latest"),
            ("GPT-OSS:latest (20B, local)", "gpt-oss:latest"),
            ("GLM-4.7-Flash:latest (30B, local)", "glm-4.7-flash:latest"),
        ],
        "deep": [
            ("GLM-4.7-Flash:latest (30B, local)", "glm-4.7-flash:latest"),
            ("GPT-OSS:latest (20B, local)", "gpt-oss:latest"),
            ("Qwen3:latest (8B, local)", "qwen3:latest"),
        ],
    },
}


def get_model_options(provider: str, mode: str) -> List[ModelOption]:
    """Return shared model options for a provider and selection mode."""
    return MODEL_OPTIONS[provider.lower()][mode]


def get_known_models() -> Dict[str, List[str]]:
    """Build known model names from the shared CLI catalog."""
    return {
        provider: sorted(
            {
                value
                for options in mode_options.values()
                for _, value in options
            }
        )
        for provider, mode_options in MODEL_OPTIONS.items()
    }
