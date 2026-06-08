import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends
from pydantic import BaseModel, validator, field_validator

from web.auth import get_current_user, require_admin

router = APIRouter()

SENSITIVE_KEYS = [
    "OPENROUTER_API_KEY", "NVIDIA_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "XAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY", "FMP_API_KEY",
    "ALPHA_VANTAGE_API_KEY", "FIDELITY_USERNAME", "FIDELITY_PASSWORD",
    "TEXTNOW_USERNAME", "TEXTNOW_SID",
    "TEXTBELT_KEY",
    # Cloudflare Workers AI + Access
    "CLOUDFLARE_API_TOKEN",
    "CF_ACCESS_AUD",
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
    "LIVE_TRADING_ENABLED",
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
    # Cloudflare Workers AI (non-secret config)
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_AI_GATEWAY_URL",
    "CLOUDFLARE_DEFAULT_QUICK_MODEL",
    "CLOUDFLARE_DEFAULT_DEEP_MODEL",
    "CLOUDFLARE_D1_DATABASE_ID",
    # Cloudflare Access (non-secret config)
    "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_REQUIRED",
    "CF_ACCESS_BOOTSTRAP_ADMIN",
    # Default LLM provider for the app
    "LLM_PROVIDER",
    # Route all supported AI providers through the Cloudflare AI Gateway.
    "CLOUDFLARE_GATEWAY_ALL",
    # Per-provider on/off toggles (absent/blank = enabled).
    "PROVIDER_OPENAI_ENABLED",
    "PROVIDER_ANTHROPIC_ENABLED",
    "PROVIDER_GOOGLE_ENABLED",
    "PROVIDER_OPENROUTER_ENABLED",
    "PROVIDER_DEEPSEEK_ENABLED",
    "PROVIDER_XAI_ENABLED",
    "PROVIDER_CLOUDFLARE_ENABLED",
    "PROVIDER_NVIDIA_ENABLED",
]

CHANGE_CONTROL_LOG = ROOT / "paper_accounts" / "algorithm" / "change_control.jsonl"
RISKY_ENV_SETTINGS = {
    "TRADINGAGENTS_MAX_POSITIONS": "max_positions",
    "TRADINGAGENTS_MAX_POSITION_SIZE": "position_cap_pct",
    "TRADINGAGENTS_MAX_DAILY_LOSS": "max_drawdown_halt_pct",
    "LIVE_TRADING_ENABLED": "live_trading_enabled",
}


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
    content = "\n".join(all_lines) + "\n"
    import tempfile as _tf, os as _os
    fd, tmp = _tf.mkstemp(dir=env_path.parent, prefix=".tmp_env_")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f: f.write(content)
        _os.replace(tmp, env_path)
    except Exception:
        try: _os.unlink(tmp)
        except Exception: pass
        raise


def _split_change_controlled_updates(
    updates: dict[str, str],
    *,
    proposed_by: str,
    cc_path: Path = CHANGE_CONTROL_LOG,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Return (safe_updates, proposals) for Settings API env writes.

    Risk-sensitive env keys are proposal-only.  They are not written to .env
    here; an operator must review the proposal and apply it intentionally.
    """
    if not updates:
        return {}, []
    current = _load_env_file()
    safe: dict[str, str] = {}
    proposals: list[dict[str, str]] = []
    try:
        from tradingagents.portfolio.change_control import ChangeControl
        cc = ChangeControl(cc_path)
    except Exception:
        cc = None

    for key, value in updates.items():
        setting = RISKY_ENV_SETTINGS.get(key)
        if not setting:
            safe[key] = value
            continue
        current_value = current.get(key, os.environ.get(key, ""))
        if str(current_value) == str(value):
            continue
        if cc is None:
            proposals.append({"key": key, "setting": setting, "proposal_id": "", "status": "proposal_failed"})
            continue
        proposal = cc.propose(
            setting=setting,
            current_value=current_value,
            proposed_value=value,
            reason=f"Settings API requested env update for {key}",
            proposed_by=proposed_by,
        )
        proposals.append({"key": key, "setting": setting, "proposal_id": proposal.proposal_id, "status": proposal.status})
    return safe, proposals


def _mask(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 4:
        return "****"
    return val[:4] + "*" * min(len(val) - 4, 20)


@router.get("/settings")
async def get_settings(_user: dict = Depends(get_current_user)):
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

    @field_validator("updates")
    @classmethod
    def validate_updates(cls, v: dict) -> dict:
        if not v:
            raise ValueError("updates must not be empty")
        if len(v) > 50:
            raise ValueError("too many keys in a single update")
        return v


@router.post("/settings")
async def update_settings(body: SettingsUpdate, admin: dict = Depends(require_admin)):
    allowed = set(SENSITIVE_KEYS + CONFIG_KEYS)
    safe = {}
    for k, v in body.updates.items():
        if k not in allowed:
            continue
        # Strip whitespace; convert non-strings to string
        val = str(v).strip() if v is not None else ""
        # Never write obviously injected values (newlines in env values break the file)
        if "\n" in val or "\r" in val:
            continue
        safe[k] = val

    if not safe:
        return {"success": False, "error": "No valid keys to update"}
    try:
        proposed_by = f"settings_api:{admin.get('email', 'admin')}"
        env_updates, proposals = _split_change_controlled_updates(safe, proposed_by=proposed_by)
        if env_updates:
            _save_env_file(env_updates)
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env", override=True)
        if not env_updates and proposals:
            return {"success": True, "updated": [], "proposals_created": proposals}
        return {"success": True, "updated": list(env_updates.keys()), "proposals_created": proposals}
    except Exception:
        import logging
        logging.exception("Error saving settings")
        return {"success": False, "error": "An internal error occurred"}
