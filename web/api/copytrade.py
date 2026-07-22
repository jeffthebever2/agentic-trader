"""Copy-trade endpoints: follow a paper competition portfolio into real Fidelity.

HIL mode queues actions for one-tap approval (step-up 2FA on the approve route);
auto mode executes + texts (gated by the COPYTRADE_AUTONOMOUS env kill-switch and
every real-money compliance gate inside the Fidelity execution layer).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import get_current_user, require_admin, require_step_up
from web import copytrade as ct
from tradingagents.config import env_bool
from tradingagents.portfolio.paper_configs import all_portfolios

router = APIRouter(prefix="/copytrade", tags=["copytrade"])


class SettingsPatch(BaseModel):
    enabled: bool | None = None
    follow_portfolio_id: str | None = None
    mode: str | None = None          # "hil" | "auto"
    account: str | None = None
    stop_pct: float | None = None
    target_pct: float | None = None
    min_weight: float | None = None
    max_weight: float | None = None
    max_new_buys_per_sync: int | None = None


def _public(cfg: dict) -> dict:
    """Client view: config + light runtime, plus whether autonomy is unlocked."""
    return {
        "enabled": cfg.get("enabled", False),
        "follow_portfolio_id": cfg.get("follow_portfolio_id"),
        "mode": cfg.get("mode", "hil"),
        "account": cfg.get("account"),
        "stop_pct": cfg.get("stop_pct"),
        "target_pct": cfg.get("target_pct"),
        "min_weight": cfg.get("min_weight"),
        "max_weight": cfg.get("max_weight"),
        "max_new_buys_per_sync": cfg.get("max_new_buys_per_sync"),
        "owned": cfg.get("owned", {}),
        "last_sync": cfg.get("last_sync"),
        "last_error": cfg.get("last_error"),
        "last_actions": cfg.get("last_actions", []),
        "pending_count": len([p for p in cfg.get("pending", []) if p.get("status") == "pending"]),
        # Autonomy is only real when the server kill-switch is on — surface it so
        # the UI can warn "auto selected but locked → still HIL".
        "autonomous_unlocked": env_bool("COPYTRADE_AUTONOMOUS", False),
        "live_trading_enabled": env_bool("LIVE_TRADING_ENABLED", False),
    }


@router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return _public(ct.get_config(user["email"]))


@router.post("/settings")
async def update_settings(patch: SettingsPatch, admin: dict = Depends(require_admin)):
    cfg = ct.set_config(admin["email"], patch.model_dump(exclude_none=True))
    return _public(cfg)


@router.get("/portfolios")
async def list_followable(_user: dict = Depends(get_current_user)):
    """Portfolios you can follow, with all-time ROR for ranking the picker."""
    from web.api.paper_portfolios import _load
    from tradingagents.portfolio.paper_metrics import compute_metrics

    out = []
    for cfg in all_portfolios():
        try:
            m = compute_metrics(_load(cfg.portfolio_id))
            out.append({
                "portfolio_id": cfg.portfolio_id,
                "name": cfg.name,
                "source_strategy": cfg.source_strategy,
                "all_time_ror": round(m.all_time_ror, 2),
                "current_equity": round(m.current_equity, 2),
                "open_positions": m.open_positions,
                "win_rate": round(m.win_rate, 1),
                "total_trades": m.total_trades,
            })
        except Exception:
            continue
    out.sort(key=lambda p: p["all_time_ror"], reverse=True)
    return {"portfolios": out}


@router.get("/pending")
async def get_pending(user: dict = Depends(get_current_user)):
    return {"pending": ct.list_pending(user["email"])}


@router.post("/sync")
async def force_sync(admin: dict = Depends(require_admin)):
    """Run a reconcile now. Computes the diff and ENQUEUES actions for approval —
    it never places a real order (execute_allowed=False), even in autonomous mode.
    Autonomous execution happens only via the env-gated background loop; approving
    a queued action still requires step-up 2FA. Bypasses only the enabled flag."""
    return await ct.reconcile(admin["email"], force_execute=True, execute_allowed=False)


@router.post("/pending/{pending_id}/approve")
async def approve(pending_id: str, admin: dict = Depends(require_step_up)):
    """Execute a queued copy action. Real money — full step-up 2FA enforced."""
    try:
        res = await ct.approve_pending(admin["email"], pending_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"execution failed: {e}")
    return {"ok": True, **res}


@router.post("/pending/{pending_id}/skip")
async def skip(pending_id: str, admin: dict = Depends(require_admin)):
    resolved = ct.resolve_pending(admin["email"], pending_id, "skipped")
    if not resolved:
        raise HTTPException(status_code=404, detail="pending not found")
    return {"ok": True, "resolved": resolved}
