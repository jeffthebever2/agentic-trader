"""Local configuration helpers for optional alternative data sources."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import get_config


def load_alt_config() -> dict[str, Any]:
    config = get_config()
    path = Path(
        os.getenv(
            "TRADINGAGENTS_ALT_DATA_CONFIG",
            config.get("alt_data_config_path", "~/.tradingagents/alt_data_sources.json"),
        )
    ).expanduser()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_secret(name: str, section: str | None = None) -> str | None:
    """Read a secret from env first, then the ignored local alt-data config."""
    env_value = os.getenv(name)
    if env_value:
        return env_value
    data = load_alt_config()
    if section:
        section_data = data.get(section, {})
        return section_data.get(name) or section_data.get(name.lower())
    return data.get(name) or data.get(name.lower())
