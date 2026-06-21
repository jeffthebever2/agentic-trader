"""Portfolio performance engine — PURE, deterministic, network-free.

Computes daily P&L (deposit/withdrawal adjusted), dashboard metrics, trade
attribution, validation flags, and CSV/JSON export from a list of daily account
snapshots + a manual cash-flow ledger. All IO (Fidelity scrape, persistence,
HTTP routes) lives in web/api/performance.py — this module only does math, so it
is fully unit-testable offline (mirrors the holdings_brain pure/IO split).

P&L semantics (the spec's formula, applied per trading day vs the PRIOR snapshot):
    daily_pnl   = ending_value - starting_value - deposits + withdrawals
    daily_pct   = daily_pnl / starting_value * 100
Deposits/withdrawals come from the cash-flow ledger so ADDED CASH IS NOT COUNTED
AS PROFIT. The first snapshot has no prior day → daily_pnl is None (baseline).
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Optional


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    last_price: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0           # total cost across the lot
    unrealized_gl: float = 0.0        # $ (Fidelity "total gain/loss")
    unrealized_gl_pct: float = 0.0
    account: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CashFlow:
    date: str                          # YYYY-MM-DD
    kind: str                          # 'deposit' | 'withdrawal' | 'dividend'
    amount: float                      # always positive; sign comes from kind
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Snapshot:
    date: str                          # YYYY-MM-DD trading day (one per day)
    ts: float = 0.0                    # capture epoch seconds
    total_value: float = 0.0           # grand total incl cash
    cash: float = 0.0                  # available cash (SPAXX etc.)
    invested_value: float = 0.0        # total_value - cash
    realized_gl: float = 0.0           # cumulative realized P&L if known (else 0)
    positions: list = field(default_factory=list)   # list[Position]
    source: str = "fidelity"
    ok: bool = True                    # False = capture failed (API failure marker)
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["positions"] = [p.to_dict() if isinstance(p, Position) else dict(p) for p in self.positions]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        poss = [Position(**{k: p.get(k) for k in Position.__annotations__ if k in p})
                for p in (d.get("positions") or [])]
        fields = {k: d.get(k) for k in Snapshot.__annotations__ if k in d and k != "positions"}
        return Snapshot(positions=poss, **fields)


_EPS = 1e-9


# ── persistence-agnostic helpers (web layer does the file IO) ─────────────────
def merge_snapshot(snapshots: list, snap: Snapshot) -> list:
    """Append-or-replace a snapshot, keyed by trading day. ONE snapshot per date —
    a same-day recapture REPLACES that day (never duplicates), and history for
    other days is preserved untouched. Returns a new date-sorted list."""
    by_date = {s.date: s for s in snapshots}
    by_date[snap.date] = snap
    return [by_date[d] for d in sorted(by_date)]


def _flows_for(date: str, cashflows: list) -> tuple[float, float, float]:
    """(deposits, withdrawals, dividends) recorded ON ``date``."""
    dep = wd = div = 0.0
    for cf in cashflows:
        c = cf if isinstance(cf, CashFlow) else CashFlow(**cf)
        if c.date != date:
            continue
        amt = abs(float(c.amount or 0))
        if c.kind == "deposit":
            dep += amt
        elif c.kind == "withdrawal":
            wd += amt
        elif c.kind == "dividend":
            div += amt
    return dep, wd, div


def daily_pnl(starting_value: float, ending_value: float,
              deposits: float, withdrawals: float) -> float:
    """ending - starting - deposits + withdrawals (added cash is not profit)."""
    return float(ending_value) - float(starting_value) - float(deposits) + float(withdrawals)


def daily_percent(pnl: float, starting_value: float) -> float:
    sv = float(starting_value or 0)
    if abs(sv) < _EPS:
        return 0.0
    return pnl / sv * 100.0


# ── time series ───────────────────────────────────────────────────────────────
def enrich_series(snapshots: list, cashflows: Optional[list] = None) -> list:
    """Walk date-sorted snapshots → per-day rows with daily_pnl/pct (vs the prior
    snapshot, deposit-adjusted), cumulative return %, dividends, and a color.
    The first day is the baseline (daily_pnl None)."""
    cashflows = cashflows or []
    snaps = sorted(snapshots, key=lambda s: s.date)
    rows: list = []
    cum_factor = 1.0
    prev: Optional[Snapshot] = None
    for s in snaps:
        dep, wd, div = _flows_for(s.date, cashflows)
        if prev is None or not s.ok:
            pnl = None
            pct = None
        else:
            pnl = daily_pnl(prev.total_value, s.total_value, dep, wd)
            pct = daily_percent(pnl, prev.total_value)
            cum_factor *= (1.0 + pct / 100.0)
        rows.append({
            "date": s.date,
            "ending_value": round(s.total_value, 2),
            "starting_value": round(prev.total_value, 2) if prev else None,
            "cash": round(s.cash, 2),
            "invested_value": round(s.invested_value, 2),
            "deposits": round(dep, 2),
            "withdrawals": round(wd, 2),
            "dividends": round(div, 2),
            "realized_gl": round(s.realized_gl, 2),
            "unrealized_gl": round(sum(_pos_field(p, "unrealized_gl") for p in s.positions), 2),
            "daily_pnl": None if pnl is None else round(pnl, 2),
            "daily_pct": None if pct is None else round(pct, 4),
            "cumulative_pct": round((cum_factor - 1.0) * 100.0, 4),
            "color": _day_color(pnl),
            "ok": s.ok,
            "n_positions": len(s.positions),
        })
        if s.ok:
            prev = s
    return rows


def _pos_field(p, name: str) -> float:
    v = getattr(p, name, None) if not isinstance(p, dict) else p.get(name)
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _day_color(pnl: Optional[float]) -> str:
    if pnl is None:
        return "gray"           # no prior data / market closed / capture failed
    if pnl > _EPS:
        return "green"
    if pnl < -_EPS:
        return "red"
    return "gray"               # exactly flat


# ── dashboard metrics ─────────────────────────────────────────────────────────
def compute_metrics(snapshots: list, cashflows: Optional[list] = None,
                    as_of: Optional[str] = None) -> dict:
    rows = enrich_series(snapshots, cashflows)
    pnl_rows = [r for r in rows if r["daily_pnl"] is not None]
    if not rows:
        return {"has_data": False}

    last = rows[-1]
    greens = [r for r in pnl_rows if r["daily_pnl"] > 0]
    reds = [r for r in pnl_rows if r["daily_pnl"] < 0]
    total_pnl = round(sum(r["daily_pnl"] for r in pnl_rows), 2)
    # compounded, deposit-adjusted cumulative return
    cum = 1.0
    for r in pnl_rows:
        cum *= (1.0 + r["daily_pct"] / 100.0)
    total_return_pct = round((cum - 1.0) * 100.0, 4)

    best = max(pnl_rows, key=lambda r: r["daily_pnl"], default=None)
    worst = min(pnl_rows, key=lambda r: r["daily_pnl"], default=None)

    as_of = as_of or last["date"]
    y, m = as_of[:4], as_of[:7]
    mtd = _period_return([r for r in pnl_rows if r["date"][:7] == m])
    ytd = _period_return([r for r in pnl_rows if r["date"][:4] == y])

    return {
        "has_data": True,
        "as_of": as_of,
        "current_value": last["ending_value"],
        "cash": last["cash"],
        "invested_value": last["invested_value"],
        "today_pnl": last["daily_pnl"],
        "today_pct": last["daily_pct"],
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "best_day": _day_ref(best),
        "worst_day": _day_ref(worst),
        "trading_days": len(pnl_rows),
        "green_days": len(greens),
        "red_days": len(reds),
        "win_rate": round(len(greens) / len(pnl_rows) * 100.0, 1) if pnl_rows else 0.0,
        "avg_green": round(sum(r["daily_pnl"] for r in greens) / len(greens), 2) if greens else 0.0,
        "avg_red": round(sum(r["daily_pnl"] for r in reds) / len(reds), 2) if reds else 0.0,
        "max_drawdown_pct": _max_drawdown(pnl_rows),
        "mtd_pct": mtd,
        "ytd_pct": ytd,
        "total_deposits": round(sum(r["deposits"] for r in rows), 2),
        "total_withdrawals": round(sum(r["withdrawals"] for r in rows), 2),
        "total_dividends": round(sum(r["dividends"] for r in rows), 2),
    }


def _period_return(rows: list) -> float:
    cum = 1.0
    for r in rows:
        cum *= (1.0 + r["daily_pct"] / 100.0)
    return round((cum - 1.0) * 100.0, 4)


def _day_ref(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {"date": r["date"], "pnl": r["daily_pnl"], "pct": r["daily_pct"],
            "ending_value": r["ending_value"]}


def _max_drawdown(pnl_rows: list) -> float:
    """Max peak-to-trough drawdown of the compounded return index (%)."""
    if not pnl_rows:
        return 0.0
    idx = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in pnl_rows:
        idx *= (1.0 + r["daily_pct"] / 100.0)
        peak = max(peak, idx)
        dd = (idx - peak) / peak * 100.0
        max_dd = min(max_dd, dd)
    return round(max_dd, 4)


# ── trade attribution ─────────────────────────────────────────────────────────
def attribution(prev: Optional[Snapshot], curr: Snapshot,
                trades: Optional[list] = None) -> dict:
    """Per-ticker contribution to the day's P&L.

    Contribution ~ change in a name's unrealized G/L between snapshots, plus any
    realized P&L from trades closed that day. Held-both-days names use the delta of
    Fidelity's per-position total_gain_loss; new/closed names fall back to trades.
    Honest + data-driven — no contribution invented for names we can't measure.
    """
    trades = trades or []
    prev_by = {_pos_field(p, "symbol") or _sym(p): p for p in (prev.positions if prev else [])}
    contribs: dict[str, dict] = {}

    for p in curr.positions:
        sym = _sym(p)
        if not sym:
            continue
        prev_p = prev_by.get(sym)
        if prev_p is not None:
            delta = _pos_field(p, "unrealized_gl") - _pos_field(prev_p, "unrealized_gl")
        else:
            delta = 0.0     # newly opened — today's unrealized move isn't a full-day P&L
        contribs[sym] = {"symbol": sym, "contribution": round(delta, 2),
                         "unrealized_gl": round(_pos_field(p, "unrealized_gl"), 2),
                         "market_value": round(_pos_field(p, "market_value"), 2)}

    realized_by: dict[str, float] = {}
    day_trades = [t for t in trades if str(t.get("date", t.get("ts", "")))[:10] == curr.date]
    for t in day_trades:
        sym = str(t.get("ticker") or t.get("symbol") or "").upper()
        r = t.get("realized_gl") or t.get("realized") or t.get("pnl")
        if sym and r is not None:
            try:
                realized_by[sym] = realized_by.get(sym, 0.0) + float(r)
            except (TypeError, ValueError):
                pass
    for sym, r in realized_by.items():
        contribs.setdefault(sym, {"symbol": sym, "contribution": 0.0,
                                  "unrealized_gl": 0.0, "market_value": 0.0})
        contribs[sym]["contribution"] = round(contribs[sym]["contribution"] + r, 2)
        contribs[sym]["realized_gl"] = round(r, 2)

    ranked = sorted(contribs.values(), key=lambda c: c["contribution"], reverse=True)
    winners = [c for c in ranked if c["contribution"] > 0][:10]
    losers = [c for c in ranked if c["contribution"] < 0]
    losers = sorted(losers, key=lambda c: c["contribution"])[:10]
    return {
        "date": curr.date,
        "top_winners": winners,
        "top_losers": losers,
        "contributions": ranked,
        "trades": day_trades,
    }


def _sym(p) -> str:
    v = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "")
    return str(v or "").upper()


# ── validation ────────────────────────────────────────────────────────────────
def validate(snapshots: list, cashflows: Optional[list] = None,
             jump_pct: float = 20.0) -> list:
    """Data-integrity flags: missing trading days, duplicate dates, API failures,
    unusual account jumps, and likely-unrecorded deposits/withdrawals."""
    cashflows = cashflows or []
    issues: list = []
    seen: dict[str, int] = {}
    for s in snapshots:
        seen[s.date] = seen.get(s.date, 0) + 1
    for d, n in seen.items():
        if n > 1:
            issues.append({"type": "duplicate_snapshot", "date": d, "count": n,
                           "severity": "warn"})

    rows = enrich_series(snapshots, cashflows)
    ok_dates = sorted(r["date"] for r in rows if r["ok"])
    # missing trading days (business-day gaps > 1)
    for a, b in zip(ok_dates, ok_dates[1:]):
        da = _dt.date.fromisoformat(a)
        db = _dt.date.fromisoformat(b)
        gap = _business_days_between(da, db)
        if gap > 1:
            issues.append({"type": "missing_snapshot", "after": a, "before": b,
                           "missing_business_days": gap - 1, "severity": "warn"})

    for s in snapshots:
        if not s.ok:
            issues.append({"type": "api_failure", "date": s.date,
                           "note": s.note, "severity": "error"})
        if s.total_value <= 0 and s.ok:
            issues.append({"type": "api_failure", "date": s.date,
                           "note": "zero/blank account value", "severity": "error"})

    for r in rows:
        if r["daily_pct"] is None:
            continue
        recorded_flow = abs(r["deposits"]) + abs(r["withdrawals"]) > _EPS
        if abs(r["daily_pct"]) >= jump_pct and not recorded_flow:
            issues.append({"type": "unusual_jump", "date": r["date"],
                           "daily_pct": r["daily_pct"], "daily_pnl": r["daily_pnl"],
                           "hint": "large move with no recorded deposit/withdrawal — "
                                   "classify as a cash flow if it was one",
                           "severity": "warn"})
        if recorded_flow:
            issues.append({"type": "cash_flow_recorded", "date": r["date"],
                           "deposits": r["deposits"], "withdrawals": r["withdrawals"],
                           "severity": "info"})
    return issues


def _business_days_between(a: _dt.date, b: _dt.date) -> int:
    """Count business days from a→b inclusive of endpoints span (Mon-Fri only).
    Ignores market holidays (best-effort; a holiday gap reads as 1 extra)."""
    if b <= a:
        return 0
    days = 0
    cur = a
    while cur < b:
        cur += _dt.timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


# ── export ────────────────────────────────────────────────────────────────────
def to_json(snapshots: list, cashflows: Optional[list] = None) -> str:
    return json.dumps(enrich_series(snapshots, cashflows), indent=2)


def to_csv(snapshots: list, cashflows: Optional[list] = None) -> str:
    rows = enrich_series(snapshots, cashflows)
    cols = ["date", "ending_value", "starting_value", "cash", "invested_value",
            "deposits", "withdrawals", "dividends", "realized_gl", "unrealized_gl",
            "daily_pnl", "daily_pct", "cumulative_pct", "color", "n_positions"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
