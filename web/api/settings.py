import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

SENSITIVE_KEYS = [
    "OPENROUTER_API_KEY", "NVIDIA_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "XAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY", "FMP_API_KEY",
    "ALPHA_VANTAGE_API_KEY", "FIDELITY_USERNAME", "FIDELITY_PASSWORD",
    "TEXTNOW_USERNAME", "TEXTNOW_SID",
    "TEXTBELT_KEY",
]

CONFIG_KEYS = [
    "TRADINGAGENTS_STARTING_CASH",
    "TRADINGAGENTS_MAX_POSITIONS",
    "TRADINGAGENTS_MAX_POSITION_SIZE",
    "TRADINGAGENTS_MAX_SECTOR_EXPOSURE",
    "TRADINGAGENTS_MAX_DAILY_LOSS",
    "TRADINGAGENTS_MAX_MONTHLY_LOSS",
    "TRADINGAGENTS_FMP_ENABLED",
    "TRADINGAGENTS_FMP_DAILY_LIMIT",
    "SEC_USER_AGENT",
    "TRADINGAGENTS_PAPER_TRADING_ENABLED",
    "TEXTNOW_PHONE",
    "PAPER_SMS_NUMBER",
    "TEXTNOW_ALERT_NUMBER",
    "SMS_NUMBER",
    "SMS_PROVIDER",
    "TEXTBELT_SENDER",
    "SENDBLUE_API_KEY_ID",
    "SENDBLUE_API_SECRET",
    "SENDBLUE_FROM_NUMBER",
    "SENDBLUE_INBOUND_SECRET",
]


def _load_env_file() -> dict:
    env_path = ROOT / ".env"
    env: dict = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _save_env_file(updates: dict):
    env_path = ROOT / ".env"
    lines_map: dict = {}
    header_lines: list = []

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=")[0].strip()
                lines_map[key] = line
            else:
                header_lines.append(line)

    for key, val in updates.items():
        if val is not None and str(val) != "":
            lines_map[key] = f'{key}={val}'

    all_lines = header_lines + list(lines_map.values())
    env_path.write_text("\n".join(all_lines) + "\n", encoding="utf-8")


def _mask(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 4:
        return "****"
    return val[:4] + "*" * min(len(val) - 4, 20)


@router.get("/settings")
async def get_settings():
    env = _load_env_file()
    result: dict = {}

    for key in SENSITIVE_KEYS:
        raw = env.get(key, os.environ.get(key, ""))
        result[key] = {"masked": _mask(raw), "set": bool(raw)}

    for key in CONFIG_KEYS:
        result[key] = env.get(key, os.environ.get(key, ""))

    from tradingagents.default_config import DEFAULT_CONFIG
    result["llm_defaults"] = {
        "llm_provider": DEFAULT_CONFIG["llm_provider"],
        "deep_think_llm": DEFAULT_CONFIG["deep_think_llm"],
        "quick_think_llm": DEFAULT_CONFIG["quick_think_llm"],
        "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
        "output_language": DEFAULT_CONFIG["output_language"],
    }
    result["paths"] = {
        "results_dir": DEFAULT_CONFIG["results_dir"],
        "memory_log_path": DEFAULT_CONFIG["memory_log_path"],
        "portfolio_state_path": DEFAULT_CONFIG["portfolio_state_path"],
        "trade_log_path": DEFAULT_CONFIG["trade_log_path"],
    }
    try:
        from tradingagents.openrouter_usage import get_openrouter_usage
        result["openrouter_usage"] = get_openrouter_usage()
    except Exception:
        result["openrouter_usage"] = {"date": "", "limit": 1000, "requests": 0, "remaining": 1000, "percent": 0}
    try:
        from tradingagents.compliance import LIVE_TRADING_HARD_BLOCKED, PROHIBITED_MARKET_ACTIONS
        result["compliance"] = {
            "live_trading_hard_blocked": bool(LIVE_TRADING_HARD_BLOCKED),
            "mode": "analysis_backtest_paper_only",
            "blocked_actions": list(PROHIBITED_MARKET_ACTIONS),
        }
    except Exception:
        result["compliance"] = {"live_trading_hard_blocked": True, "mode": "analysis_backtest_paper_only"}

    return result


class SettingsUpdate(BaseModel):
    updates: Dict[str, Any]


@router.post("/settings")
async def update_settings(body: SettingsUpdate):
    allowed = set(SENSITIVE_KEYS + CONFIG_KEYS)
    safe = {k: v for k, v in body.updates.items() if k in allowed}
    if not safe:
        return {"success": False, "error": "No valid keys to update"}
    try:
        _save_env_file(safe)
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
        return {"success": True, "updated": list(safe.keys())}
    except Exception as e:
        return {"success": False, "error": str(e)}
