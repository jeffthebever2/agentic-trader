import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
USAGE_PATH = ROOT / "tmp" / "openrouter_usage.json"
DAILY_LIMIT = 1000


def _today() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


def _empty(day: str | None = None) -> dict[str, Any]:
    return {
        "date": day or _today(),
        "limit": DAILY_LIMIT,
        "requests": 0,
        "by_source": {},
        "updated_at": None,
    }


def _read() -> dict[str, Any]:
    day = _today()
    if not USAGE_PATH.exists():
        return _empty(day)
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _empty(day)
    if data.get("date") != day:
        return _empty(day)
    data.setdefault("limit", DAILY_LIMIT)
    data.setdefault("requests", 0)
    data.setdefault("by_source", {})
    return data


def get_openrouter_usage() -> dict[str, Any]:
    data = _read()
    used = int(data.get("requests") or 0)
    limit = int(data.get("limit") or DAILY_LIMIT)
    data["remaining"] = max(0, limit - used)
    data["percent"] = round((used / limit) * 100, 1) if limit > 0 else 0.0
    return data


def record_openrouter_request(source: str = "unknown", count: int = 1) -> dict[str, Any]:
    if os.environ.get("TRADINGAGENTS_DISABLE_OPENROUTER_USAGE_TRACKING") == "1":
        return get_openrouter_usage()
    data = _read()
    source = str(source or "unknown")
    count = max(1, int(count or 1))
    data["requests"] = int(data.get("requests") or 0) + count
    by_source = data.setdefault("by_source", {})
    by_source[source] = int(by_source.get(source) or 0) + count
    data["updated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USAGE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(USAGE_PATH)
    return get_openrouter_usage()
