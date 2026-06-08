"""Portfolio comparison REST API (FastAPI).

GET /api/portfolios/leaderboard        — ranked portfolio stats
GET /api/portfolios/leaderboard/groups — per-group aggregated stats
GET /api/portfolios/groups             — portfolio definitions by group
GET /api/portfolios/{name}             — single portfolio stats + config
GET /api/portfolios/{name}/config      — portfolio configuration params
GET /api/portfolios/model-health       — deployed model staleness + gate metrics
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# Simple TTL cache for leaderboard — recomputing from disk on every request is
# expensive when 15 portfolios each need state.json + events.jsonl reads.
_LEADERBOARD_CACHE: Dict[str, Any] = {}
_CACHE_TTL_SECONDS = 30


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


def _get_leaderboard(base_dir: Path) -> Dict[str, Any]:
    """Return cached leaderboard summary, refreshing if stale."""
    from tradingagents.portfolios.comparison import leaderboard_summary

    cache_key = str(base_dir)
    cached = _LEADERBOARD_CACHE.get(cache_key)
    if cached and (time.monotonic() - cached["_ts"]) < _CACHE_TTL_SECONDS:
        return cached["data"]

    data = leaderboard_summary(base_dir)
    _LEADERBOARD_CACHE[cache_key] = {"data": data, "_ts": time.monotonic()}
    return data


def _invalidate_leaderboard_cache(base_dir: Path) -> None:
    _LEADERBOARD_CACHE.pop(str(base_dir), None)


_SORT_FIELDS = {
    "rank", "total_return_pct", "win_rate", "sharpe", "max_drawdown",
    "trade_count", "profit_factor", "equity", "open_positions",
    "avg_win_pct", "avg_loss_pct",
}


@router.get("/api/portfolios/leaderboard")
async def portfolio_leaderboard(
    min_trades: int = Query(default=0, ge=0, description="Exclude portfolios with fewer closed trades"),
    sort_by: str = Query(default="rank", description=f"Sort field. Options: {', '.join(sorted(_SORT_FIELDS))}"),
    sort_dir: Literal["asc", "desc"] = Query(default="asc", description="Sort direction"),
    group: Optional[str] = Query(default=None, description="Filter to group: signal|risk|hold|filter"),
    limit: int = Query(default=0, ge=0, description="Max results (0 = all)"),
):
    """Return ranked portfolio stats with optional filtering and sorting."""
    if sort_by not in _SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"sort_by must be one of: {', '.join(sorted(_SORT_FIELDS))}")
    if group and group not in ("signal", "risk", "hold", "filter"):
        raise HTTPException(status_code=400, detail="group must be: signal|risk|hold|filter")

    base_dir = _base_dir()
    try:
        summary = _get_leaderboard(base_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    portfolios = summary["portfolios"]

    if min_trades > 0:
        portfolios = [p for p in portfolios if p["trade_count"] >= min_trades or p["status"] == "no_data"]

    if group:
        portfolios = [p for p in portfolios if p["group"] == group]

    if sort_by != "rank":
        reverse = sort_dir == "desc"
        portfolios = sorted(
            portfolios,
            key=lambda p: (p.get(sort_by) is None, -(p.get(sort_by) or 0) if reverse else (p.get(sort_by) or 0)),
        )

    if limit > 0:
        portfolios = portfolios[:limit]

    return {**summary, "portfolios": portfolios}


@router.get("/api/portfolios/leaderboard/groups")
async def portfolio_leaderboard_groups():
    """Return per-group aggregated stats: best portfolio, avg return, avg win rate."""
    base_dir = _base_dir()
    try:
        summary = _get_leaderboard(base_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    group_stats: Dict[str, Any] = {}
    for g in ("signal", "risk", "hold", "filter"):
        members = [p for p in summary["portfolios"] if p["group"] == g]
        active = [p for p in members if p["status"] == "active"]
        if active:
            best = max(active, key=lambda p: p["total_return_pct"])
            returns = [p["total_return_pct"] for p in active]
            win_rates = [p["win_rate"] for p in active if p["win_rate"] is not None]
            group_stats[g] = {
                "count": len(members),
                "active": len(active),
                "best_name": best["name"],
                "best_label": best["label"],
                "best_return_pct": best["total_return_pct"],
                "avg_return_pct": round(sum(returns) / len(returns), 3),
                "avg_win_rate": round(sum(win_rates) / len(win_rates), 4) if win_rates else None,
                "total_trades": sum(p["trade_count"] for p in active),
            }
        else:
            group_stats[g] = {"count": len(members), "active": 0, "best_name": None}

    return {"groups": group_stats, "as_of": summary["as_of"]}


@router.get("/api/portfolios/model-health")
async def portfolio_model_health():
    """Return deployed ML model health: WF ROC, Brier, staleness, Qlib flag."""
    from pathlib import Path as _Path
    import datetime as dt

    root = _Path(__file__).resolve().parents[2]
    model_dir = root / "ml_models" / "latest"
    report_path = model_dir / "training_report.json"
    bundle_path = model_dir / "model_bundle.joblib"

    result: Dict[str, Any] = {
        "model_dir": str(model_dir),
        "bundle_exists": bundle_path.exists(),
        "report_exists": report_path.exists(),
        "healthy": False,
        "wf_roc": None,
        "brier_after": None,
        "calibrated": None,
        "trained_at": None,
        "age_days": None,
        "qlib_features": False,
        "feature_count": None,
        "warnings": [],
    }

    if not bundle_path.exists():
        result["warnings"].append("model_bundle.joblib missing — run ./start.sh train")
        return result

    if not report_path.exists():
        result["warnings"].append("training_report.json missing — model may be from old cycle")
        result["healthy"] = True
        return result

    try:
        report = json.loads(report_path.read_text())
    except Exception as exc:
        result["warnings"].append(f"Cannot parse training_report.json: {exc}")
        return result

    wf = report.get("walk_forward", {}) or {}
    wf_roc = wf.get("roc_auc")
    cal = report.get("models", {}).get("win_probability", {}).get("calibration", {}) or {}
    brier_after = cal.get("brier_after")
    calibrated = report.get("settings", {}).get("calibrated", False)
    trained_at = report.get("trained_at") or report.get("timestamp")
    feature_names: list = report.get("feature_names", [])
    qlib_features = any(f.startswith("qlib_") for f in feature_names)

    age_days = None
    if trained_at:
        try:
            ts = dt.datetime.fromisoformat(trained_at.replace("Z", "+00:00"))
            age_days = (dt.datetime.now(dt.timezone.utc) - ts).days
        except Exception:
            pass

    result.update({
        "wf_roc": wf_roc,
        "brier_after": brier_after,
        "calibrated": calibrated,
        "trained_at": trained_at,
        "age_days": age_days,
        "qlib_features": qlib_features,
        "feature_count": len(feature_names) if feature_names else None,
        "high_conf_win_rate": wf.get("high_conf_win_rate"),
        "rows_used": report.get("settings", {}).get("rows_used"),
    })

    # Health checks
    healthy = True
    if wf_roc is not None and wf_roc < 0.49:
        result["warnings"].append(f"WF ROC {wf_roc:.4f} below gate threshold 0.49")
        healthy = False
    if not calibrated:
        result["warnings"].append("Model was not calibrated — probabilities may be miscalibrated")
    if age_days is not None and age_days > 14:
        result["warnings"].append(f"Model is {age_days} days old — consider retraining (./start.sh retrain)")
    if not qlib_features:
        result["warnings"].append("Model trained without Qlib features — retrain with --include-qlib-features")

    result["healthy"] = healthy
    return result


@router.get("/api/portfolios/groups")
async def portfolio_groups():
    """Return portfolio definitions organized by group."""
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


@router.get("/api/portfolios/{name}/config")
async def portfolio_config(name: str):
    """Return the full configuration for a portfolio (stop/target/sizing/filters)."""
    from tradingagents.portfolios.registry import get_portfolio
    from dataclasses import asdict

    try:
        portfolio = get_portfolio(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")

    d = asdict(portfolio)
    # Add human-readable param summary
    params = portfolio.as_param_dict()
    d["active_params"] = params
    return d


@router.get("/api/portfolios/{name}")
async def portfolio_detail(name: str):
    """Return full stats + config for a single portfolio."""
    from tradingagents.portfolios.comparison import compute_portfolio_stats
    from tradingagents.portfolios.registry import get_portfolio

    try:
        portfolio = get_portfolio(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")

    base_dir = _base_dir()
    stats = compute_portfolio_stats(portfolio, base_dir)
    # Attach config params so callers get everything in one request
    stats["config"] = portfolio.as_param_dict()
    return stats
