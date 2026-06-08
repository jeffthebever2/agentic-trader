"""Portfolio comparison REST API (FastAPI).

GET /api/portfolios/leaderboard   — ranked portfolio stats for comparison dashboard
GET /api/portfolios/groups        — portfolios grouped by strategy type
GET /api/portfolios/{name}        — single portfolio detail
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _base_dir() -> Path:
    """Resolve the most-recent portfolio output directory."""
    base = os.environ.get("PAPER_OUTPUT_DIR", "tmp/paper_trading_today")
    base_path = Path(base)
    if base_path.exists():
        dated = sorted(
            [d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()],
            reverse=True,
        )
        if dated:
            return dated[0]
    return base_path


@router.get("/api/portfolios/leaderboard")
async def portfolio_leaderboard(min_trades: int = Query(default=0, ge=0)):
    """Return ranked portfolio stats. Optionally filter by min closed trades."""
    from tradingagents.portfolios.comparison import leaderboard_summary

    base_dir = _base_dir()
    try:
        summary = leaderboard_summary(base_dir)
        if min_trades > 0:
            summary["portfolios"] = [
                p for p in summary["portfolios"]
                if p["trade_count"] >= min_trades or p["status"] == "no_data"
            ]
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/portfolios/groups")
async def portfolio_groups():
    """Return portfolios organized by group with metadata."""
    from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY

    groups: dict = {}
    for p in PORTFOLIO_REGISTRY:
        groups.setdefault(p.group, []).append({
            "name": p.name,
            "label": p.label,
            "description": p.description,
            "color": p.color,
            "emoji": p.emoji,
            "source_strategy": p.source_strategy,
        })

    return {"groups": groups, "total": len(PORTFOLIO_REGISTRY)}


@router.get("/api/portfolios/{name}")
async def portfolio_detail(name: str):
    """Return full stats for a single portfolio."""
    from tradingagents.portfolios.comparison import compute_portfolio_stats
    from tradingagents.portfolios.registry import get_portfolio

    try:
        portfolio = get_portfolio(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")

    base_dir = _base_dir()
    return compute_portfolio_stats(portfolio, base_dir)
