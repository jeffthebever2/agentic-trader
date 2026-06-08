"""Portfolio comparison engine.

Reads state.json + events.jsonl from all portfolio directories, computes
performance metrics, and returns a ranked leaderboard.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from tradingagents.portfolios.registry import PORTFOLIO_REGISTRY, PortfolioConfig


# ── Metric helpers ────────────────────────────────────────────────────────────

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _sharpe(returns: list[float], risk_free: float = 0.0) -> Optional[float]:
    if len(returns) < 4:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(var)
    if std == 0:
        return None
    return round((mean - risk_free) / std * math.sqrt(252), 3)


def _max_drawdown(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


# ── State loader ─────────────────────────────────────────────────────────────

def _load_state(state_path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_events(events_path: Path) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    if not events_path.exists():
        return events
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return events


# ── Portfolio stats ───────────────────────────────────────────────────────────

def compute_portfolio_stats(
    portfolio: PortfolioConfig,
    base_dir: Path,
) -> Dict[str, Any]:
    """Load state + events for one portfolio, compute performance metrics."""
    port_dir = base_dir / portfolio.name
    state_path = port_dir / "state.json"
    events_path = port_dir / "events.jsonl"

    state = _load_state(state_path)
    events = _load_events(events_path)

    if state is None:
        return {
            "name": portfolio.name,
            "label": portfolio.label,
            "description": portfolio.description,
            "group": portfolio.group,
            "color": portfolio.color,
            "emoji": portfolio.emoji,
            "status": "no_data",
            "total_return_pct": 0.0,
            "win_rate": None,
            "trade_count": 0,
            "open_positions": 0,
            "cash": 0.0,
            "equity": 0.0,
            "starting_cash": 0.0,
            "realized_pnl": 0.0,
            "sharpe": None,
            "max_drawdown": 0.0,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "profit_factor": None,
            "rank": None,
            "source_strategy": portfolio.source_strategy,
        }

    starting_cash = float(state.get("starting_cash", 10000.0))
    cash = float(state.get("cash", starting_cash))
    realized_pnl = float(state.get("realized_pnl", 0.0))
    positions = state.get("positions", {})
    trades = state.get("trades", [])

    # Estimate open position market value from state (uses last stored price if available)
    open_value = sum(
        float(p.get("shares", 0)) * float(p.get("entry_price", 0))
        for p in positions.values()
    )
    equity = cash + open_value
    total_return_pct = round((equity - starting_cash) / starting_cash * 100, 3) if starting_cash else 0.0

    # Per-trade win/loss from events
    sell_events = [e for e in events if e.get("type") == "SELL"]
    wins = [e for e in sell_events if float(e.get("pnl", e.get("realized_pnl", 0))) > 0]
    losses = [e for e in sell_events if float(e.get("pnl", e.get("realized_pnl", 0))) <= 0]

    trade_count = len(sell_events)
    win_count = len(wins)
    win_rate = round(win_count / trade_count, 4) if trade_count > 0 else None

    def _pnl_pct(e: Dict) -> float:
        pnl = float(e.get("pnl", e.get("realized_pnl", 0)))
        ep = float(e.get("entry_price", 0) or e.get("price", 1))
        shares = int(e.get("shares", 1) or 1)
        cost = ep * shares
        return round(pnl / cost * 100, 4) if cost > 0 else 0.0

    win_pcts = [_pnl_pct(e) for e in wins]
    loss_pcts = [_pnl_pct(e) for e in losses]

    avg_win_pct = round(sum(win_pcts) / len(win_pcts), 3) if win_pcts else None
    avg_loss_pct = round(sum(loss_pcts) / len(loss_pcts), 3) if loss_pcts else None

    gross_wins = sum(max(0.0, float(e.get("pnl", e.get("realized_pnl", 0)))) for e in wins)
    gross_losses = abs(sum(min(0.0, float(e.get("pnl", e.get("realized_pnl", 0)))) for e in losses))
    profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else (None if not gross_wins else 999.0)

    # Build equity curve from buy/sell events for Sharpe + drawdown
    equity_curve: list[float] = [starting_cash]
    running = starting_cash
    for e in sorted(events, key=lambda x: x.get("timestamp", x.get("time", ""))):
        if e.get("type") == "SELL":
            pnl = float(e.get("pnl", e.get("realized_pnl", 0)))
            running += pnl
            equity_curve.append(running)

    daily_returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]

    return {
        "name": portfolio.name,
        "label": portfolio.label,
        "description": portfolio.description,
        "group": portfolio.group,
        "color": portfolio.color,
        "emoji": portfolio.emoji,
        "status": "active",
        "total_return_pct": total_return_pct,
        "win_rate": win_rate,
        "trade_count": trade_count,
        "open_positions": len(positions),
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "starting_cash": round(starting_cash, 2),
        "realized_pnl": round(realized_pnl, 2),
        "sharpe": _sharpe(daily_returns),
        "max_drawdown": _max_drawdown(equity_curve),
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "profit_factor": profit_factor,
        "equity_curve": equity_curve[-50:],  # last 50 points for sparkline
        "rank": None,  # filled by rank_portfolios()
        "source_strategy": portfolio.source_strategy,
    }


# ── Leaderboard ──────────────────────────────────────────────────────────────

def rank_portfolios(base_dir: Path, min_trades: int = 0) -> list[Dict[str, Any]]:
    """Load all portfolios, compute stats, rank by total return.

    Parameters
    ----------
    base_dir : Path
        Directory containing per-portfolio subdirectories (e.g. tmp/paper_trading_today/20260607/).
    min_trades : int
        Portfolios with fewer closed trades than this are ranked last.

    Returns
    -------
    list of stats dicts, sorted descending by total_return_pct. Rank is 1-based.
    """
    stats_list = [
        compute_portfolio_stats(p, base_dir)
        for p in PORTFOLIO_REGISTRY
    ]

    # Sort: active first, then by total_return_pct descending
    def _sort_key(s: Dict) -> tuple:
        active = 0 if s["status"] == "active" else 1
        meets_min = 0 if s["trade_count"] >= min_trades else 1
        return (active, meets_min, -s["total_return_pct"])

    stats_list.sort(key=_sort_key)
    for i, s in enumerate(stats_list):
        s["rank"] = i + 1

    return stats_list


def leaderboard_summary(base_dir: Path) -> Dict[str, Any]:
    """Return full leaderboard + metadata for the comparison dashboard."""
    ranked = rank_portfolios(base_dir)
    active = [s for s in ranked if s["status"] == "active"]
    best = active[0] if active else None

    return {
        "as_of": datetime.utcnow().isoformat() + "Z",
        "base_dir": str(base_dir),
        "portfolio_count": len(PORTFOLIO_REGISTRY),
        "active_count": len(active),
        "best_portfolio": best["name"] if best else None,
        "best_return_pct": best["total_return_pct"] if best else None,
        "portfolios": ranked,
        "groups": {
            g: [s["name"] for s in ranked if s["group"] == g]
            for g in ("signal", "risk", "hold", "filter")
        },
    }
