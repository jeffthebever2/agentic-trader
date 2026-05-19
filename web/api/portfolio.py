import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _get_config():
    from tradingagents.default_config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


def _load_portfolio() -> dict:
    cfg = _get_config()
    path = Path(cfg["portfolio_state_path"])
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"positions": {}, "cash": cfg.get("starting_cash", 100000.0), "paper_trades": []}


def _save_portfolio(data: dict):
    cfg = _get_config()
    path = Path(cfg["portfolio_state_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _get_price(ticker: str) -> float:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


@router.get("/portfolio")
async def get_portfolio():
    data = _load_portfolio()
    positions = []
    total_invested = 0.0

    for ticker, pos in data.get("positions", {}).items():
        shares = float(pos.get("shares", 0))
        entry_price = float(pos.get("entry_price", 0))
        current_price = _get_price(ticker) or entry_price
        market_value = shares * current_price
        cost_basis = shares * entry_price
        pnl = market_value - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        total_invested += cost_basis

        positions.append({
            "ticker": ticker,
            "shares": shares,
            "entry_price": entry_price,
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "cost_basis": round(cost_basis, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss": pos.get("stop_loss"),
            "take_profit": pos.get("take_profit"),
            "entry_date": pos.get("entry_date"),
            "sector": pos.get("sector", "Unknown"),
            "thesis": pos.get("thesis", ""),
        })

    cash = float(data.get("cash", 0))
    total_market_value = sum(p["market_value"] for p in positions)
    total_value = cash + total_market_value
    total_pnl = total_market_value - total_invested

    return {
        "positions": sorted(positions, key=lambda x: x["market_value"], reverse=True),
        "cash": round(cash, 2),
        "total_invested": round(total_invested, 2),
        "total_market_value": round(total_market_value, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 2),
    }


class AddPositionRequest(BaseModel):
    ticker: str
    shares: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    sector: Optional[str] = "Unknown"
    thesis: Optional[str] = ""


@router.post("/portfolio/positions")
async def add_position(req: AddPositionRequest):
    data = _load_portfolio()
    positions = data.get("positions", {})
    ticker = req.ticker.upper().strip()

    cost = req.shares * req.entry_price
    cash = float(data.get("cash", 0))
    if cost > cash:
        raise HTTPException(status_code=400, detail=f"Insufficient cash: need ${cost:.2f}, have ${cash:.2f}")

    from datetime import datetime
    positions[ticker] = {
        "shares": req.shares,
        "entry_price": req.entry_price,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "sector": req.sector or "Unknown",
        "thesis": req.thesis or "",
    }
    data["positions"] = positions
    data["cash"] = cash - cost
    _save_portfolio(data)
    return {"success": True, "ticker": ticker}


@router.delete("/portfolio/positions/{ticker}")
async def remove_position(ticker: str):
    data = _load_portfolio()
    positions = data.get("positions", {})
    ticker = ticker.upper()

    if ticker not in positions:
        raise HTTPException(status_code=404, detail="Position not found")

    pos = positions.pop(ticker)
    shares = float(pos.get("shares", 0))
    current_price = _get_price(ticker) or float(pos.get("entry_price", 0))
    proceeds = shares * current_price
    data["positions"] = positions
    data["cash"] = float(data.get("cash", 0)) + proceeds
    _save_portfolio(data)
    return {"success": True, "ticker": ticker, "proceeds": round(proceeds, 2)}


@router.get("/portfolio/history")
async def get_portfolio_history():
    cfg = _get_config()
    log_path = Path(cfg["trade_log_path"])
    trades = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except Exception:
                    pass
    return {"trades": list(reversed(trades))}


@router.get("/portfolio/paper-trades")
async def get_paper_trades():
    data = _load_portfolio()
    return {"paper_trades": data.get("paper_trades", [])}
