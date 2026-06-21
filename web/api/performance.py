"""Portfolio performance tracking — daily Fidelity account snapshots + history.

Pulls the REAL Fidelity account (reusing the existing scraper + encrypted session;
NO new credential handling), saves ONE append-only snapshot per trading day, and
serves deposit-adjusted P&L, dashboard metrics, a performance calendar, charts,
trade attribution, validation, and JSON/CSV export.

Pure math lives in tradingagents/portfolio/performance.py; this module is the IO
layer (scrape, persist, routes, daily loop) — mirrors the holdings_brain split.

Persistence (per-user, keyed by the same sha256(email)[:16] digest as the Fidelity
session/snapshot files):
    tmp/performance_history_<digest>.jsonl     one Snapshot per trading day
    tmp/performance_cashflows_<digest>.json    manual deposit/withdrawal/dividend ledger
    tmp/performance_synclog_<digest>.jsonl     every sync result (success or failure)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import time as _time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from web.auth import get_current_user
from tradingagents.portfolio import performance as perf
from tradingagents.portfolio.performance import Snapshot, Position, CashFlow

log = logging.getLogger("performance")
router = APIRouter()

ROOT = Path(__file__).parent.parent.parent


# ── per-user paths ────────────────────────────────────────────────────────────
def _digest(email: str) -> str:
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:16]


def _history_path(email: str) -> Path:
    return ROOT / "tmp" / f"performance_history_{_digest(email)}.jsonl"


def _cashflow_path(email: str) -> Path:
    return ROOT / "tmp" / f"performance_cashflows_{_digest(email)}.json"


def _synclog_path(email: str) -> Path:
    return ROOT / "tmp" / f"performance_synclog_{_digest(email)}.jsonl"


# ── persistence ───────────────────────────────────────────────────────────────
def _load_snapshots(email: str) -> list:
    p = _history_path(email)
    out: list = []
    if not p.exists():
        return out
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(Snapshot.from_dict(json.loads(line)))
    except Exception as e:
        log.warning("load snapshots: %s", e)
    # dedup by date (keep last occurrence) → one snapshot per trading day
    by_date = {s.date: s for s in out}
    return [by_date[d] for d in sorted(by_date)]


def _save_snapshots(email: str, snaps: list) -> None:
    p = _history_path(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(s.to_dict()) for s in snaps) + "\n")
    tmp.replace(p)


def _load_cashflows(email: str) -> list:
    p = _cashflow_path(email)
    if not p.exists():
        return []
    try:
        return [CashFlow(**c) for c in json.loads(p.read_text())]
    except Exception as e:
        log.warning("load cashflows: %s", e)
        return []


def _save_cashflows(email: str, flows: list) -> None:
    p = _cashflow_path(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps([c.to_dict() for c in flows], indent=2))
    tmp.replace(p)


def _log_sync(email: str, result: dict) -> None:
    try:
        result = {**result, "ts": _dt.datetime.now().isoformat(timespec="seconds")}
        p = _synclog_path(email)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(result) + "\n")
    except Exception as e:
        log.warning("sync log write: %s", e)


def _read_synclog(email: str, limit: int = 50) -> list:
    p = _synclog_path(email)
    if not p.exists():
        return []
    try:
        lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return list(reversed(lines))[:limit]
    except Exception:
        return []


# ── trade log → realized P&L (no realized field exists; we pair buys↔sells) ────
def _parse_money(v) -> float:
    from web.api.fidelity import _parse_dollar
    f = _parse_dollar(str(v)) if v is not None else None
    return float(f) if f is not None else 0.0


def _load_trades(email: str) -> list:
    """Executed trades from the Fidelity trade log, normalized to
    {date, ticker, side, shares, cost, proceeds, realized_gl}. Realized P&L is
    computed by avg-cost pairing of BUY cost ↔ SELL proceeds per ticker (the log
    has no realized field and no lot linking — this is an approximation, labelled)."""
    from web.api.fidelity import TRADE_HISTORY_FILE
    p = Path(TRADE_HISTORY_FILE)
    if not p.exists():
        return []
    raw = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw.append(json.loads(line))
        except Exception:
            continue
    raw.sort(key=lambda r: str(r.get("ts", "")))
    book: dict[str, dict] = {}          # ticker → {shares, cost}
    trades: list = []
    for r in raw:
        status = str(r.get("status", "")).lower()
        if status in ("preview", "rejected", "failed", "cancelled", "canceled"):
            continue
        tk = str(r.get("ticker") or "").upper()
        if not tk:
            continue
        date = str(r.get("ts", ""))[:10]
        is_sell = str(r.get("action", "")).lower() == "sell" or r.get("proceeds") is not None
        shares = abs(_parse_money(r.get("shares")))
        if is_sell:
            proceeds = _parse_money(r.get("proceeds")) or (_parse_money(r.get("price")) * shares)
            b = book.get(tk, {"shares": 0.0, "cost": 0.0})
            avg = (b["cost"] / b["shares"]) if b["shares"] > 1e-9 else 0.0
            sold = min(shares, b["shares"]) if b["shares"] > 0 else shares
            realized = proceeds - avg * sold if avg else 0.0
            b["shares"] = max(0.0, b["shares"] - sold)
            b["cost"] = max(0.0, b["cost"] - avg * sold)
            book[tk] = b
            trades.append({"date": date, "ticker": tk, "side": "sell", "shares": shares,
                           "proceeds": round(proceeds, 2), "realized_gl": round(realized, 2)})
        else:
            cost = _parse_money(r.get("cost")) or (_parse_money(r.get("entry")) * shares)
            b = book.setdefault(tk, {"shares": 0.0, "cost": 0.0})
            b["shares"] += shares
            b["cost"] += cost
            trades.append({"date": date, "ticker": tk, "side": "buy", "shares": shares,
                           "cost": round(cost, 2), "realized_gl": None})
    return trades


def _cumulative_realized(trades: list) -> float:
    return round(sum(float(t.get("realized_gl") or 0) for t in trades if t.get("side") == "sell"), 2)


# ── capture one daily snapshot from the live Fidelity account ──────────────────
async def capture_snapshot(email: str, *, force: bool = False, from_cache: bool = False) -> dict:
    """Append today's snapshot of the REAL Fidelity account (one per trading day).
    ``from_cache`` builds it from the warm positions/accounts snapshot the keepalive
    loop already scraped (no extra browser work); otherwise it scrapes fresh.
    Returns a sync-result dict (also written to the sync log)."""
    from web.api.fidelity import _scrape_positions, _get_fidelity_balances, _read_snapshot
    today = _dt.date.today().isoformat()
    snaps = _load_snapshots(email)
    if not force and any(s.date == today and s.ok for s in snaps):
        res = {"ok": True, "skipped": True, "reason": "already captured today", "date": today}
        _log_sync(email, res)
        return res

    try:
        if from_cache:
            pos_data = _read_snapshot(email, "positions") or {}
            bal = _read_snapshot(email, "accounts") or {}
            if not pos_data.get("positions") and not bal.get("total_value"):
                raise RuntimeError("no warm Fidelity snapshot available to capture from")
            balances = {"total_value": bal.get("total_value"),
                        "available_cash": bal.get("available_cash")}
        else:
            pos_data = await _scrape_positions(email)
            balances = await _get_fidelity_balances(email)
    except Exception as e:
        snap = Snapshot(date=today, ts=_time.time(), ok=False, note=f"scrape failed: {e}")
        snaps = perf.merge_snapshot(snaps, snap)
        _save_snapshots(email, snaps)
        res = {"ok": False, "date": today, "error": str(e), "stage": "scrape"}
        _log_sync(email, res)
        return res

    positions = []
    for p in pos_data.get("positions", []):
        positions.append(Position(
            symbol=str(p.get("symbol", "")).upper(),
            qty=_parse_money(p.get("qty")),
            last_price=_parse_money(p.get("last_price")),
            market_value=_parse_money(p.get("market_value")),
            cost_basis=_parse_money(p.get("cost_basis")),
            unrealized_gl=_parse_money(p.get("total_gain_loss")),
            unrealized_gl_pct=_parse_money(str(p.get("total_gain_pct", "")).replace("%", "")),
            account=str(p.get("account_number", "")),
        ))
    total_value = _parse_money(balances.get("total_value")) or \
        _parse_money((pos_data.get("grand_totals") or {}).get("total_value"))
    cash = _parse_money(balances.get("available_cash"))
    trades = _load_trades(email)
    realized = _cumulative_realized(trades)

    snap = Snapshot(
        date=today, ts=_time.time(), total_value=round(total_value, 2),
        cash=round(cash, 2), invested_value=round(total_value - cash, 2),
        realized_gl=realized, positions=positions, source="fidelity",
        ok=total_value > 0,
        note="" if total_value > 0 else "zero account value scraped",
    )
    snaps = perf.merge_snapshot(snaps, snap)
    _save_snapshots(email, snaps)
    res = {"ok": snap.ok, "date": today, "total_value": snap.total_value,
           "cash": snap.cash, "n_positions": len(positions), "realized_gl": realized}
    _log_sync(email, res)
    return res


# ── range filter ───────────────────────────────────────────────────────────────
def _filter_range(snaps: list, rng: str) -> list:
    if rng in ("all", "max", ""):
        return snaps
    today = _dt.date.today()
    if rng == "ytd":
        cutoff = _dt.date(today.year, 1, 1)
    else:
        days = {"1w": 7, "1m": 31, "3m": 93, "1y": 366}.get(rng)
        if days is None:
            return snaps
        cutoff = today - _dt.timedelta(days=days)
    cutoff_s = cutoff.isoformat()
    return [s for s in snaps if s.date >= cutoff_s]


# ── routes ────────────────────────────────────────────────────────────────────
@router.get("/performance/summary")
async def performance_summary(user: dict = Depends(get_current_user),
                              range: str = Query("all")):
    email = user["email"]
    snaps = _filter_range(_load_snapshots(email), range)
    flows = _load_cashflows(email)
    m = perf.compute_metrics(snaps, flows)
    m["range"] = range
    m["snapshot_count"] = len(snaps)
    last_sync = _read_synclog(email, 1)
    m["last_sync"] = last_sync[0] if last_sync else None
    return m


@router.get("/performance/history")
async def performance_history(user: dict = Depends(get_current_user),
                              range: str = Query("all")):
    email = user["email"]
    snaps = _filter_range(_load_snapshots(email), range)
    flows = _load_cashflows(email)
    return {"rows": perf.enrich_series(snaps, flows), "range": range}


@router.get("/performance/day/{date}")
async def performance_day(date: str, user: dict = Depends(get_current_user)):
    email = user["email"]
    snaps = _load_snapshots(email)
    flows = _load_cashflows(email)
    rows = perf.enrich_series(snaps, flows)
    row = next((r for r in rows if r["date"] == date), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No snapshot for {date}")
    idx = next(i for i, s in enumerate(snaps) if s.date == date)
    prev = next((snaps[j] for j in range(idx - 1, -1, -1) if snaps[j].ok), None) if idx > 0 else None
    curr = snaps[idx]
    trades = [t for t in _load_trades(email) if t["date"] == date]
    attr = perf.attribution(prev, curr, trades)
    return {"row": row, "positions": [p.to_dict() for p in curr.positions],
            "attribution": attr}


@router.get("/performance/positions")
async def performance_positions(user: dict = Depends(get_current_user)):
    email = user["email"]
    snaps = _load_snapshots(email)
    if not snaps:
        return {"positions": [], "date": None}
    last = snaps[-1]
    poss = sorted((p.to_dict() for p in last.positions),
                  key=lambda p: p.get("unrealized_gl", 0), reverse=True)
    return {"positions": poss, "date": last.date,
            "total_value": last.total_value, "cash": last.cash}


@router.get("/performance/validate")
async def performance_validate(user: dict = Depends(get_current_user)):
    email = user["email"]
    snaps = _load_snapshots(email)
    flows = _load_cashflows(email)
    return {"issues": perf.validate(snaps, flows)}


@router.get("/performance/synclog")
async def performance_synclog(user: dict = Depends(get_current_user), limit: int = 50):
    return {"log": _read_synclog(user["email"], limit)}


@router.get("/performance/export")
async def performance_export(user: dict = Depends(get_current_user),
                             fmt: str = Query("json"), range: str = Query("all")):
    email = user["email"]
    snaps = _filter_range(_load_snapshots(email), range)
    flows = _load_cashflows(email)
    stamp = _dt.date.today().isoformat()
    if fmt == "csv":
        return PlainTextResponse(
            perf.to_csv(snaps, flows),
            headers={"Content-Disposition": f"attachment; filename=performance_{stamp}.csv"},
            media_type="text/csv")
    return JSONResponse(
        json.loads(perf.to_json(snaps, flows)),
        headers={"Content-Disposition": f"attachment; filename=performance_{stamp}.json"})


@router.post("/performance/sync")
async def performance_sync(user: dict = Depends(get_current_user), force: bool = True):
    """Manually capture a snapshot NOW from the live Fidelity account."""
    return await capture_snapshot(user["email"], force=force)


class CashFlowIn(BaseModel):
    date: str
    kind: str            # deposit | withdrawal | dividend
    amount: float
    note: str = ""


@router.get("/performance/cashflows")
async def get_cashflows(user: dict = Depends(get_current_user)):
    return {"cashflows": [c.to_dict() for c in _load_cashflows(user["email"])]}


@router.post("/performance/cashflows")
async def add_cashflow(body: CashFlowIn, user: dict = Depends(get_current_user)):
    if body.kind not in ("deposit", "withdrawal", "dividend"):
        raise HTTPException(status_code=400, detail="kind must be deposit|withdrawal|dividend")
    flows = _load_cashflows(user["email"])
    flows.append(CashFlow(date=body.date, kind=body.kind, amount=abs(body.amount), note=body.note))
    flows.sort(key=lambda c: c.date)
    _save_cashflows(user["email"], flows)
    return {"ok": True, "cashflows": [c.to_dict() for c in flows]}


@router.delete("/performance/cashflows")
async def delete_cashflow(user: dict = Depends(get_current_user),
                          date: str = Query(...), kind: str = Query(...),
                          amount: float = Query(...)):
    flows = _load_cashflows(user["email"])
    kept = [c for c in flows if not (c.date == date and c.kind == kind and abs(c.amount - abs(amount)) < 1e-6)]
    _save_cashflows(user["email"], kept)
    return {"ok": True, "cashflows": [c.to_dict() for c in kept]}
