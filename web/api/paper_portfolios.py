"""15-portfolio paper trading endpoints.

Leaderboard (default: all-time ROR), ROR comparison chart, per-portfolio detail,
candidates, compliance log, and reset controls. All paper-only — never live.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web.auth import get_current_user, require_admin
from tradingagents.portfolio.paper_configs import all_portfolios, get_portfolio, PaperPortfolioConfig
from tradingagents.portfolio.paper_account import PaperPortfolioAccount
from tradingagents.portfolio.paper_metrics import compute_metrics, leaderboard_sort_key

router = APIRouter(prefix="/paper", tags=["paper-portfolios"])

ROOT = Path(__file__).parent.parent.parent
PAPER_ACCOUNTS_BASE = ROOT / "tmp" / "paper_portfolios"
CANDIDATES_FILE = PAPER_ACCOUNTS_BASE / "candidates_latest.json"

# source_strategy → human badge
SOURCE_BADGE = {
    "algorithm": "Breakout",
    "machine_learning": "ML",
    "ml_new": "ML New",
    "combined": "Combined",
    "pure_ai": "AI",
    "long_hold": "Long Hold",
    "unified_brain": "UnifiedBrain",
    "thematic": "Thematic",
}


def _badge(cfg) -> str:
    """Display badge — single-source name, or the combine style for multi-tool."""
    if getattr(cfg, "source_strategies", None):
        return {"union": "Blend", "intersection": "Intersect"}.get(getattr(cfg, "combine_mode", ""), "Consensus")
    return SOURCE_BADGE.get(cfg.source_strategy, cfg.source_strategy)


# ── Response models ──────────────────────────────────────────────────────────

class PortfolioCard(BaseModel):
    portfolio_id: str
    name: str
    source_strategy: str
    badge: str
    ml_threshold: Optional[float]
    current_equity: float
    initial_cash: float
    cash: float
    settled_cash: float
    unsettled_cash: float
    realized_pnl: float
    unrealized_pnl: float
    all_time_ror: float
    daily_ror: float
    weekly_ror: float
    monthly_ror: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    win_rate: float
    total_trades: int
    open_positions: int
    avg_hold_days: float
    day_trades_last_5: int
    compliance_skips: int


class LeaderboardEntry(BaseModel):
    rank: int
    portfolio_id: str
    name: str
    badge: str
    all_time_ror: float
    current_equity: float
    sharpe_ratio: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    open_positions: int
    total_trades: int


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load(portfolio_id: str) -> PaperPortfolioAccount:
    PAPER_ACCOUNTS_BASE.mkdir(parents=True, exist_ok=True)
    return PaperPortfolioAccount.load(portfolio_id, PAPER_ACCOUNTS_BASE)


def _card(cfg: PaperPortfolioConfig) -> PortfolioCard:
    acc = _load(cfg.portfolio_id)
    m = compute_metrics(acc)
    return PortfolioCard(
        portfolio_id=cfg.portfolio_id,
        name=cfg.name,
        source_strategy=cfg.source_strategy,
        badge=_badge(cfg),
        ml_threshold=cfg.ml_probability_threshold,
        current_equity=m.current_equity,
        initial_cash=m.initial_cash,
        cash=m.cash,
        settled_cash=m.settled_cash,
        unsettled_cash=m.unsettled_cash,
        realized_pnl=m.realized_pnl,
        unrealized_pnl=m.unrealized_pnl,
        all_time_ror=m.all_time_ror,
        daily_ror=m.daily_ror,
        weekly_ror=m.weekly_ror,
        monthly_ror=m.monthly_ror,
        max_drawdown=m.max_drawdown,
        sharpe_ratio=m.sharpe_ratio,
        profit_factor=m.profit_factor,
        win_rate=m.win_rate,
        total_trades=m.total_trades,
        open_positions=m.open_positions,
        avg_hold_days=m.avg_hold_days,
        day_trades_last_5=m.day_trades_last_5,
        compliance_skips=m.compliance_skips,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/portfolios")
async def list_portfolio_configs(_user: dict = Depends(get_current_user)):
    """List the 15 portfolio configs (static)."""
    return {
        "portfolios": [
            {
                "portfolio_id": p.portfolio_id,
                "name": p.name,
                "source_strategy": p.source_strategy,
                "badge": _badge(p),
                "stop_mult": p.stop_mult,
                "target_mult": p.target_mult,
                "max_hold_days": p.max_hold_days,
                "risk_per_trade_pct": p.risk_per_trade_pct,
                "ml_probability_threshold": p.ml_probability_threshold,
                "initial_cash": p.initial_cash,
            }
            for p in all_portfolios()
        ]
    }


@router.get("/accounts")
async def get_all_portfolios(_user: dict = Depends(get_current_user)):
    """All 15 portfolio cards with live metrics."""
    return {"accounts": [_card(cfg) for cfg in all_portfolios()]}


@router.get("/leaderboard")
async def get_leaderboard(
    sort_by: str = Query("all_time_ror"),
    _user: dict = Depends(get_current_user),
):
    """All 15 ranked. Default: all-time ROR, tiebreak Sharpe → drawdown → PF → equity."""
    metrics = [compute_metrics(_load(cfg.portfolio_id)) for cfg in all_portfolios()]

    single = {
        "current_equity": lambda m: m.current_equity,
        "sharpe_ratio": lambda m: m.sharpe_ratio,
        "max_drawdown": lambda m: -m.max_drawdown,
        "profit_factor": lambda m: m.profit_factor,
        "win_rate": lambda m: m.win_rate,
    }
    key = single.get(sort_by, leaderboard_sort_key)  # default = full tiebreak chain
    metrics.sort(key=key, reverse=True)

    badge = {p.portfolio_id: _badge(p) for p in all_portfolios()}
    entries = [
        LeaderboardEntry(
            rank=i + 1,
            portfolio_id=m.portfolio_id,
            name=m.name,
            badge=badge.get(m.portfolio_id, ""),
            all_time_ror=m.all_time_ror,
            current_equity=m.current_equity,
            sharpe_ratio=m.sharpe_ratio,
            max_drawdown=m.max_drawdown,
            profit_factor=m.profit_factor,
            win_rate=m.win_rate,
            open_positions=m.open_positions,
            total_trades=m.total_trades,
        )
        for i, m in enumerate(metrics)
    ]
    return {"timestamp": datetime.now(ZoneInfo("US/Eastern")).isoformat(),
            "sort_by": sort_by, "entries": entries}


@router.get("/ror-chart")
async def get_ror_chart(_user: dict = Depends(get_current_user)):
    """Per-portfolio ROR time series for the comparison chart (one line each)."""
    series = []
    for cfg in all_portfolios():
        acc = _load(cfg.portfolio_id)
        points = [{"t": s.timestamp, "ror": s.all_time_ror} for s in acc.equity_snapshots]
        series.append({
            "portfolio_id": cfg.portfolio_id,
            "name": cfg.name,
            "badge": _badge(cfg),
            "points": points,
        })
    return {"series": series}


@router.get("/candidates")
async def get_candidates(_user: dict = Depends(get_current_user)):
    """Latest candidates grouped by source strategy (written by the runner)."""
    if not CANDIDATES_FILE.exists():
        return {"candidates": {}, "note": "no candidate snapshot yet — runner has not written one"}
    try:
        data = json.loads(CANDIDATES_FILE.read_text())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"candidates unreadable: {e}")
    return {"candidates": data}


@router.get("/compliance-log")
async def get_compliance_log(
    portfolio_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    _user: dict = Depends(get_current_user),
):
    """Skipped trades + compliance events, newest first. Optionally filter by portfolio."""
    cfgs = [get_portfolio(portfolio_id)] if portfolio_id else all_portfolios()
    if portfolio_id and portfolio_id not in {p.portfolio_id for p in all_portfolios()}:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")

    events: list[dict[str, Any]] = []
    for cfg in cfgs:
        acc = _load(cfg.portfolio_id)
        for ev in acc.compliance_log:
            events.append({
                "timestamp": ev.timestamp,
                "portfolio_id": ev.portfolio_id,
                "portfolio_name": acc.name,
                "ticker": ev.ticker,
                "action": ev.action,
                "reason": ev.reason,
                "details": ev.details,
            })
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"events": events[:limit], "total": len(events)}


@router.get("/accounts/{portfolio_id}")
async def get_portfolio_detail(portfolio_id: str, _user: dict = Depends(get_current_user)):
    """Full state for one portfolio: config, metrics, positions, trades, equity curve, compliance."""
    if portfolio_id not in {p.portfolio_id for p in all_portfolios()}:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")

    acc = _load(portfolio_id)
    now = datetime.now(ZoneInfo("US/Eastern"))

    def _days_held(entry_ts: str) -> float:
        try:
            return round((now - datetime.fromisoformat(entry_ts)).total_seconds() / 86400.0, 2)
        except Exception:
            return 0.0

    return {
        "portfolio_id": acc.portfolio_id,
        "name": acc.name,
        "config": acc.config.__dict__,
        "card": _card(acc.config),
        "positions": [
            {
                "ticker": p.ticker, "shares": p.shares, "entry_price": p.entry_price,
                "current_price": p.current_price, "stop": p.stop, "target": p.target,
                "trailing_stop": p.trailing_stop, "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pct": p.unrealized_pct, "entry_date": p.entry_date,
                "days_held": _days_held(p.entry_timestamp), "source_strategy": p.source_strategy,
                "used_ml": p.used_ml, "used_unified_brain": p.used_unified_brain, "used_ai": p.used_ai,
                "ml_probability": p.ml_probability, "entry_reason": p.entry_reason,
            }
            for p in acc.positions
        ],
        "trades": [
            {
                "ticker": t.ticker, "shares": t.shares, "entry_price": t.entry_price,
                "exit_price": t.exit_price, "entry_date": t.entry_date, "exit_date": t.exit_date,
                "realized_pnl": t.realized_pnl, "realized_pct": t.realized_pct,
                "exit_reason": t.exit_reason, "source_strategy": t.source_strategy,
                "ml_probability": t.ml_probability,
            }
            for t in acc.trades[-50:]
        ],
        "equity_curve": [{"t": s.timestamp, "equity": s.equity, "ror": s.all_time_ror} for s in acc.equity_snapshots],
        "compliance_log": [
            {"timestamp": c.timestamp, "ticker": c.ticker, "action": c.action, "reason": c.reason, "details": c.details}
            for c in acc.compliance_log[-100:]
        ],
    }


@router.post("/reset")
async def reset_all_portfolios(admin: dict = Depends(require_admin)):
    """Reset all 15 portfolios to $10,000. Paper only — never touches live accounts."""
    PAPER_ACCOUNTS_BASE.mkdir(parents=True, exist_ok=True)
    count = 0
    for cfg in all_portfolios():
        PaperPortfolioAccount.reset(cfg.portfolio_id, PAPER_ACCOUNTS_BASE)
        count += 1
    return {"status": "reset", "reset_count": count, "total": len(all_portfolios())}


@router.post("/reset/{portfolio_id}")
async def reset_one_portfolio(portfolio_id: str, admin: dict = Depends(require_admin)):
    """Reset one portfolio to $10,000. Paper only."""
    if portfolio_id not in {p.portfolio_id for p in all_portfolios()}:
        raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
    PaperPortfolioAccount.reset(portfolio_id, PAPER_ACCOUNTS_BASE)
    cfg = get_portfolio(portfolio_id)
    return {"status": "reset", "portfolio_id": portfolio_id, "name": cfg.name, "cash": cfg.initial_cash}
