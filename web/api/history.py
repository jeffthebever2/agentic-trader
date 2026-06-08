import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query
from web.auth import get_current_user

router = APIRouter()

# Path-component allowlists. Reject anything that could escape results_dir.
_TICKER_RE = re.compile(r"^[A-Z0-9.\-_]{1,16}$")
_DATE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,32}$")


def _safe_join(base: Path, *parts: str) -> Path:
    """Join under `base` and verify the resolved path stays inside `base`.

    Raises HTTPException(400) on traversal attempts.
    """
    joined = (base / Path(*parts)).resolve()
    base_resolved = base.resolve()
    try:
        joined.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return joined


def _results_dir() -> Path:
    from tradingagents.default_config import DEFAULT_CONFIG
    return Path(DEFAULT_CONFIG["results_dir"])


@router.get("/history")
async def get_history(
    ticker: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    results_dir = _results_dir()
    entries = []

    if results_dir.exists():
        for ticker_dir in sorted(results_dir.iterdir()):
            if not ticker_dir.is_dir():
                continue
            if ticker and ticker_dir.name.upper() != ticker.strip().upper():
                continue

            for date_dir in sorted(ticker_dir.iterdir(), reverse=True):
                if not date_dir.is_dir():
                    continue

                entry = {
                    "ticker": ticker_dir.name,
                    "date": date_dir.name,
                    "decision": None,
                    "has_report": False,
                }

                # Try reports subdir first (new format), then flat files
                reports_dir = date_dir / "reports"
                decision_file = None

                if reports_dir.exists():
                    decision_file = reports_dir / "final_trade_decision.md"
                    if not decision_file.exists():
                        # Look for any .md file with "decision" in name
                        for f in reports_dir.glob("*decision*.md"):
                            decision_file = f
                            break
                else:
                    for f in date_dir.glob("*.md"):
                        if "decision" in f.name.lower():
                            decision_file = f
                            break

                if decision_file and decision_file.exists():
                    entry["has_report"] = True
                    content = decision_file.read_text(encoding="utf-8", errors="ignore")
                    entry["decision_snippet"] = content[:500]
                    for d in ["Buy", "Overweight", "Hold", "Underweight", "Sell"]:
                        if d in content:
                            entry["decision"] = d
                            break

                if decision and entry.get("decision") and entry["decision"].lower() != decision.lower():
                    continue

                entries.append(entry)

    total = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start: start + page_size]

    return {
        "entries": page_entries,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/history/stats")
async def get_history_stats(_user: dict = Depends(get_current_user)):
    results_dir = _results_dir()
    by_decision: dict = {}
    tickers: set = set()
    total = 0

    if results_dir.exists():
        for ticker_dir in results_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            tickers.add(ticker_dir.name)
            for date_dir in ticker_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                total += 1
                decision = None
                for search_dir in [date_dir / "reports", date_dir]:
                    if search_dir.exists():
                        for f in search_dir.glob("*decision*.md"):
                            content = f.read_text(encoding="utf-8", errors="ignore")[:500]
                            for d in ["Buy", "Overweight", "Hold", "Underweight", "Sell"]:
                                if d in content:
                                    decision = d
                                    break
                            break
                    if decision:
                        break
                by_decision[decision or "Unknown"] = by_decision.get(decision or "Unknown", 0) + 1

    return {"total": total, "tickers": len(tickers), "by_decision": by_decision}


@router.get("/history/{ticker}/{date}")
async def get_history_entry(ticker: str, date: str, _user: dict = Depends(get_current_user)):
    # Whitelist path components so /history/../../etc/passwd can't escape
    # results_dir. Tickers are uppercase symbols; dates are short tokens.
    if not _TICKER_RE.match(ticker) or not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="Invalid ticker or date")
    results_dir = _results_dir()
    entry_dir = _safe_join(results_dir, ticker, date)
    if not entry_dir.exists():
        raise HTTPException(status_code=404, detail="Entry not found")

    reports: dict = {}

    reports_dir = entry_dir / "reports"
    search_dirs = [reports_dir, entry_dir]
    for subdir in search_dirs:
        if subdir.exists():
            for f in sorted(subdir.iterdir()):
                if f.suffix == ".md" and f.stem not in reports:
                    reports[f.stem] = f.read_text(encoding="utf-8", errors="ignore")

    # Also check numbered sub-dirs (1_analysts, 2_research, etc.)
    for numbered_dir in sorted(entry_dir.iterdir()):
        if numbered_dir.is_dir() and numbered_dir.name[0].isdigit():
            for f in sorted(numbered_dir.iterdir()):
                if f.suffix == ".md":
                    key = f"{numbered_dir.name}/{f.stem}"
                    reports[key] = f.read_text(encoding="utf-8", errors="ignore")

    return {"ticker": ticker, "date": date, "reports": reports}


@router.get("/history/tickers")
async def list_tickers(_user: dict = Depends(get_current_user)):
    results_dir = _results_dir()
    tickers = []
    if results_dir.exists():
        tickers = sorted(d.name for d in results_dir.iterdir() if d.is_dir())
    return {"tickers": tickers}
