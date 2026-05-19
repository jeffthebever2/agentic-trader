import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Query

router = APIRouter()


def _cfg():
    from tradingagents.default_config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


@router.get("/logs/stats")
async def get_stats():
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
                # Check reports subdir or flat
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
async def get_memory():
    path = Path(_cfg()["memory_log_path"])
    if not path.exists():
        return {"content": "", "exists": False, "size_bytes": 0}
    content = path.read_text(encoding="utf-8", errors="ignore")
    return {"content": content, "exists": True, "size_bytes": path.stat().st_size}


@router.get("/logs/paper-decisions")
async def get_paper_decisions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    path = Path(_cfg()["paper_decision_log_path"])
    entries = _read_jsonl(path)
    return _paginate(entries, page, page_size)


@router.get("/logs/trades")
async def get_trade_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    path = Path(_cfg()["trade_log_path"])
    entries = _read_jsonl(path)
    return _paginate(entries, page, page_size)


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
