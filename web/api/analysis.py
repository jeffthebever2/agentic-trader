import asyncio
import concurrent.futures
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter()
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

_PROVIDER_KEY_ENV = {
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_KEY"),
    "nvidia": ("NVIDIA_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY",),
    "glm": ("ZHIPU_API_KEY",),
    "azure": ("AZURE_OPENAI_API_KEY",),
}

_PROVIDER_LABEL = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "nvidia": "NVIDIA",
    "anthropic": "Anthropic",
    "google": "Google",
    "xai": "xAI",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "glm": "GLM",
    "azure": "Azure OpenAI",
    "ollama": "Ollama",
}

_PLACEHOLDER_KEYS = {"", "placeholder", "changeme", "your-api-key", "sk-...", "sk-or-v1-..."}


class AnalyzeRequest(BaseModel):
    ticker: str
    analysis_date: str
    analysts: list[str]
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    output_language: str = "English"
    backend_url: Optional[str] = None
    google_thinking_level: Optional[str] = None
    openai_reasoning_effort: Optional[str] = None
    anthropic_effort: Optional[str] = None
    checkpoint_enabled: bool = False


def _configured_provider_key(provider: str) -> tuple[str | None, str | None]:
    provider = provider.lower()
    for env_name in _PROVIDER_KEY_ENV.get(provider, ()):
        value = os.environ.get(env_name, "").strip()
        if value:
            return env_name, value
    return None, None


def _validate_provider_auth(req: AnalyzeRequest) -> str | None:
    provider = req.llm_provider.lower()
    if provider == "ollama":
        return None

    env_name, key = _configured_provider_key(provider)
    label = _PROVIDER_LABEL.get(provider, provider)
    expected = " or ".join(_PROVIDER_KEY_ENV.get(provider, ()))

    if not key:
        return (
            f"{label} is selected, but no API key is configured. "
            f"Set {expected} in Settings > AI Providers, then run the analysis again."
        )

    if key.strip().lower() in _PLACEHOLDER_KEYS:
        return (
            f"{label} is selected, but {env_name} still looks like a placeholder. "
            "Replace it in Settings > AI Providers, then run the analysis again."
        )

    return None


def _extract_provider_error_message(message: str) -> str:
    match = re.search(r"'message':\s*'([^']+)'", message)
    if match:
        return match.group(1)
    match = re.search(r'"message":\s*"([^"]+)"', message)
    if match:
        return match.group(1)
    return message


def _humanize_analysis_error(exc: Exception, provider: str) -> str:
    raw = str(exc)
    provider_label = _PROVIDER_LABEL.get(provider.lower(), provider)
    lower = raw.lower()

    if "401" in raw or "authentication" in lower or "unauthorized" in lower:
        provider_message = _extract_provider_error_message(raw)
        return (
            f"{provider_label} authentication failed. "
            f"{provider_label} rejected the configured API key"
            f"{': ' + provider_message if provider_message and provider_message != raw else ''}. "
            "Open Settings > AI Providers, replace the API key for this provider, save, and try again."
        )

    if "api key" in lower and ("missing" in lower or "not set" in lower or "not configured" in lower):
        return (
            f"{provider_label} needs an API key before analysis can run. "
            "Open Settings > AI Providers, add the key, save, and try again."
        )

    return raw


def _extract_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return content.get("text", "").strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", "").strip())
            elif isinstance(item, str):
                parts.append(item.strip())
        return " ".join(p for p in parts if p)
    return str(content).strip()


def _serialize_chunk(chunk: dict) -> dict:
    try:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    except ImportError:
        return {}

    result: dict = {}

    messages = []
    for msg in chunk.get("messages", []):
        content = _extract_content(getattr(msg, "content", None))
        if isinstance(msg, HumanMessage):
            msg_type = "user"
        elif isinstance(msg, ToolMessage):
            msg_type = "tool_result"
        elif isinstance(msg, AIMessage):
            msg_type = "agent"
        else:
            msg_type = "system"

        tool_calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "args": {k: str(v)[:120] for k, v in tc.get("args", {}).items()},
                    })
                else:
                    try:
                        tool_calls.append({
                            "name": tc.name,
                            "args": {k: str(v)[:120] for k, v in (tc.args or {}).items()},
                        })
                    except Exception:
                        pass

        msg_id = getattr(msg, "id", None)
        if content or tool_calls:
            messages.append({
                "id": msg_id,
                "type": msg_type,
                "content": content,
                "tool_calls": tool_calls,
            })

    if messages:
        result["messages"] = messages

    for key in [
        "market_report", "sentiment_report", "news_report",
        "fundamentals_report", "trader_investment_plan", "final_trade_decision",
    ]:
        if chunk.get(key):
            result[key] = chunk[key]

    if chunk.get("investment_debate_state"):
        d = chunk["investment_debate_state"]
        result["investment_debate_state"] = {
            "bull_history": d.get("bull_history", ""),
            "bear_history": d.get("bear_history", ""),
            "judge_decision": d.get("judge_decision", ""),
        }

    if chunk.get("risk_debate_state"):
        r = chunk["risk_debate_state"]
        result["risk_debate_state"] = {
            "aggressive_history": r.get("aggressive_history", ""),
            "conservative_history": r.get("conservative_history", ""),
            "neutral_history": r.get("neutral_history", ""),
            "judge_decision": r.get("judge_decision", ""),
        }

    return result


def _serialize_final_state(state: dict) -> dict:
    result: dict = {}
    for key in [
        "market_report", "sentiment_report", "news_report",
        "fundamentals_report", "investment_plan",
        "trader_investment_plan", "final_trade_decision",
    ]:
        if state.get(key):
            result[key] = state[key]

    if state.get("investment_debate_state"):
        d = state["investment_debate_state"]
        result["investment_debate_state"] = {
            "bull_history": d.get("bull_history", ""),
            "bear_history": d.get("bear_history", ""),
            "judge_decision": d.get("judge_decision", ""),
        }

    if state.get("risk_debate_state"):
        r = state["risk_debate_state"]
        result["risk_debate_state"] = {
            "aggressive_history": r.get("aggressive_history", ""),
            "conservative_history": r.get("conservative_history", ""),
            "neutral_history": r.get("neutral_history", ""),
            "judge_decision": r.get("judge_decision", ""),
        }

    return result


@router.websocket("/ws/analyze")
async def ws_analyze(websocket: WebSocket):
    await websocket.accept()

    queue: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()

    try:
        config_data = await websocket.receive_json()
        req = AnalyzeRequest(**config_data)
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"Invalid request: {e}"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "status",
        "message": f"Initializing analysis for {req.ticker} on {req.analysis_date}...",
    })

    auth_error = _validate_provider_auth(req)
    if auth_error:
        await websocket.send_json({"type": "error", "message": auth_error})
        await websocket.close()
        return

    def run_sync():
        try:
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            from tradingagents.default_config import DEFAULT_CONFIG

            config = DEFAULT_CONFIG.copy()
            config["max_debate_rounds"] = req.max_debate_rounds
            config["max_risk_discuss_rounds"] = req.max_risk_discuss_rounds
            config["quick_think_llm"] = req.quick_think_llm
            config["deep_think_llm"] = req.deep_think_llm
            config["backend_url"] = req.backend_url
            config["llm_provider"] = req.llm_provider
            config["google_thinking_level"] = req.google_thinking_level
            config["openai_reasoning_effort"] = req.openai_reasoning_effort
            config["anthropic_effort"] = req.anthropic_effort
            config["output_language"] = req.output_language
            config["checkpoint_enabled"] = req.checkpoint_enabled

            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "status", "message": "Building agent graph..."}),
                main_loop,
            )

            graph = TradingAgentsGraph(req.analysts, config=config, debug=False)

            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "status", "message": "Starting agent pipeline..."}),
                main_loop,
            )

            past_context = graph.memory_log.get_past_context(req.ticker)
            portfolio_context = graph._build_portfolio_context(req.ticker)
            execution_context = graph._build_execution_context(req.ticker)
            init_state = graph.propagator.create_initial_state(
                req.ticker,
                req.analysis_date,
                past_context=past_context,
                portfolio_context=portfolio_context,
                execution_context=execution_context,
            )
            args = graph.propagator.get_graph_args()

            seen_ids: set = set()
            trace = []

            for chunk in graph.graph.stream(init_state, **args):
                trace.append(chunk)
                serialized = _serialize_chunk(chunk)

                # Deduplicate messages by ID
                if "messages" in serialized:
                    unique = []
                    for m in serialized["messages"]:
                        mid = m.get("id")
                        if mid is None or mid not in seen_ids:
                            unique.append(m)
                            if mid is not None:
                                seen_ids.add(mid)
                    if unique:
                        serialized["messages"] = unique
                    else:
                        serialized.pop("messages", None)

                if serialized:
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "chunk", "data": serialized}),
                        main_loop,
                    )

            final_state = trace[-1] if trace else {}
            decision = graph.process_signal(final_state.get("final_trade_decision", ""))

            try:
                graph._log_state(req.analysis_date, final_state)
                graph.memory_log.store_decision(
                    req.ticker, req.analysis_date,
                    final_state.get("final_trade_decision", ""),
                )
            except Exception:
                pass

            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "complete",
                    "decision": decision,
                    "final_state": _serialize_final_state(final_state),
                }),
                main_loop,
            )

        except Exception as e:
            import traceback
            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "error",
                    "message": _humanize_analysis_error(e, req.llm_provider),
                    "traceback": traceback.format_exc(),
                }),
                main_loop,
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), main_loop)

    future = main_loop.run_in_executor(_executor, run_sync)

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await future
        except Exception:
            pass
