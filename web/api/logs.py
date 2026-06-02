"""Log access endpoints.

All endpoints require user auth (get_current_user) — log data is sensitive.
No raw env vars, passwords, or secrets are ever returned.
"""
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, Query

from web.auth import get_current_user

router = APIRouter()

TMP = ROOT / "tmp"

# Known safe log sources — whitelist prevents path traversal
_LOG_SOURCES = {
    "web": TMP / "web.screen.log",
    "cloudflared": TMP / "cloudflared.screen.log",
    "autofix": ROOT / "logs" / "autofix.log",
    "paper": ROOT / "logs" / "paper_trade.log",
    "retrain": TMP / "retrain_triggered.log",
}


def _cfg():
    from tradingagents.default_config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


@router.get("/logs/stats")
async def get_stats(_user: dict = Depends(get_current_user)):
    cfg = _cfg()
    results_dir = Path(cfg["results_dir"])

    total_analyses = 0
    unique_tickers: set = set()
    decisions: dict = {"Buy": 0, "Overweight": 0, "Hold": 0, "Underweight": 0, "Sell": 0}

    if results_dir.exists():
        for ticker_dir in results_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            unique_tickers.add(ticker_dir.name)
            for date_dir in ticker_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                total_analyses += 1
                for search in [date_dir / "reports" / "final_trade_decision.md", date_dir / "final_trade_decision.md"]:
                    if search.exists():
                        content = search.read_text(encoding="utf-8", errors="ignore")
                        for d in decisions:
                            if d in content:
                                decisions[d] += 1
                                break
                        break

    return {
        "total_analyses": total_analyses,
        "unique_tickers": len(unique_tickers),
        "decisions": decisions,
    }


@router.get("/logs/memory")
async def get_memory(_user: dict = Depends(get_current_user)):
    path = Path(_cfg()["memory_log_path"])
    if not path.exists():
        return {"content": "", "exists": False, "size_bytes": 0}
    content = path.read_text(encoding="utf-8", errors="ignore")
    return {"content": content, "exists": True, "size_bytes": path.stat().st_size}


@router.get("/logs/paper-decisions")
async def get_paper_decisions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: dict = Depends(get_current_user),
):
    path = Path(_cfg()["paper_decision_log_path"])
    entries = _read_jsonl(path)
    return _paginate(entries, page, page_size)


@router.get("/logs/trades")
async def get_trade_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: dict = Depends(get_current_user),
):
    path = Path(_cfg()["trade_log_path"])
    entries = _read_jsonl(path)
    return _paginate(entries, page, page_size)


@router.get("/logs/system")
async def get_system_log(
    source: str = Query("web", description="Log source: web, cloudflared, autofix, paper, retrain"),
    lines: int = Query(100, ge=1, le=2000, description="Number of tail lines to return"),
    _user: dict = Depends(get_current_user),
):
    """Tail a server-side log file.

    Sources: web, cloudflared, autofix, paper, retrain.
    Only whitelisted log paths are readable — no path traversal possible.
    """
    if source not in _LOG_SOURCES:
        return {
            "ok": False,
            "error": f"Unknown source '{source}'. Valid: {list(_LOG_SOURCES.keys())}",
            "entries": [],
        }

    log_path = _LOG_SOURCES[source]
    if not log_path.exists():
        return {"ok": True, "source": source, "lines": 0, "entries": [], "exists": False}

    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        all_lines = content.splitlines()
        tail = all_lines[-lines:]
        return {
            "ok": True,
            "source": source,
            "path": log_path.name,  # filename only, not full path
            "total_lines": len(all_lines),
            "lines": len(tail),
            "size_bytes": log_path.stat().st_size,
            "entries": tail,
        }
    except Exception as e:
        return {"ok": False, "source": source, "error": "Could not read log", "entries": []}


@router.get("/logs/sources")
async def list_log_sources(_user: dict = Depends(get_current_user)):
    """List available log sources and their current state."""
    result = []
    for name, path in _LOG_SOURCES.items():
        exists = path.exists()
        result.append({
            "source": name,
            "exists": exists,
            "size_bytes": path.stat().st_size if exists else 0,
        })
    return {"sources": result}


def _read_jsonl(path: Path) -> list:
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return list(reversed(entries))


def _paginate(entries: list, page: int, page_size: int) -> dict:
    total = len(entries)
    start = (page - 1) * page_size
    return {
        "entries": entries[start: start + page_size],
        "total": total,
        "page": page,
        "pages": max(1, (total + page_size - 1) // page_size),
    }
