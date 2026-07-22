"""Per-ticker enrichment DOSSIER — pure parsers + derived signals (revamp Stage 1).

The revamp enriches the top ~20-40 ranked names ONCE, post-merge, then reuses that
dossier three times (ground the AI pick, size the position, drive data-aware exits).
This module is the PURE core: it parses raw API payloads (schemas verified live
against the user's FMP free tier + Finnhub) into a normalized ``TickerDossier`` and
computes the derived signals — earnings-blackout gate, low-float squeeze flag,
analyst skew, 52-week positioning. No network, no I/O; the web/api layer fetches and
feeds raw payloads in. Every parser is tolerant of missing/variant keys and returns
None rather than raising.

Verified endpoint shapes (2026-07-06, FMP /stable/ + Finnhub):
  FMP earnings-calendar row: {symbol, date, epsEstimated, revenueEstimated, ...}
  FMP shares-float:          {symbol, freeFloat(%), floatShares, outstandingShares}
  FMP grades-consensus:      {symbol, strongBuy, buy, hold, sell, strongSell, consensus}
  FMP most-actives:          [{symbol, changesPercentage, ...}]
  Finnhub /stock/metric:     {"metric": {"beta", "52WeekHigh", "52WeekLow", ...}}
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Optional


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


@dataclass
class TickerDossier:
    ticker: str
    earnings_date: Optional[str] = None        # ISO date of the NEXT earnings print
    days_to_earnings: Optional[int] = None      # trading-agnostic calendar days (>=0 future)
    eps_estimate: Optional[float] = None
    revenue_estimate: Optional[float] = None
    float_pct: Optional[float] = None           # free float as % of shares outstanding
    float_shares: Optional[float] = None
    low_float: bool = False                     # squeeze-prone (< LOW_FLOAT_SHARES)
    analyst_consensus: Optional[str] = None     # "Strong Buy".."Strong Sell"
    analyst_skew: Optional[float] = None         # -1..+1 (bearish..bullish)
    beta: Optional[float] = None
    hi_52w: Optional[float] = None
    lo_52w: Optional[float] = None
    pct_from_52w_high: Optional[float] = None    # <0 = below the high
    is_mover: bool = False                       # on today's most-actives
    mover_chg_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v is not False}


LOW_FLOAT_SHARES = 50_000_000.0  # below this free float → squeeze-prone


# ── Parsers (raw payload → normalized fields) ────────────────────────────────

def parse_earnings_calendar(rows, today: Optional[_dt.date] = None) -> dict:
    """FMP earnings-calendar rows → {TICKER: {date, days_to, eps_est, rev_est}} for
    the NEXT print at/after ``today`` (a single date-range fetch covers all names)."""
    today = today or _dt.date.today()
    out: dict = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol", "")).upper().strip()
        ds = str(r.get("date", "")).strip()[:10]
        if not sym or not ds:
            continue
        try:
            d = _dt.date.fromisoformat(ds)
        except ValueError:
            continue
        days = (d - today).days
        if days < 0:
            continue  # past print — we want the forward date
        prev = out.get(sym)
        if prev is None or days < prev["days_to"]:
            out[sym] = {"date": ds, "days_to": days,
                        "eps_est": _f(r.get("epsEstimated")),
                        "rev_est": _f(r.get("revenueEstimated"))}
    return out


def parse_shares_float(obj) -> dict:
    """FMP shares-float object (or 1-item list) → {float_pct, float_shares}."""
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return {}
    return {"float_pct": _f(obj.get("freeFloat")),
            "float_shares": _f(obj.get("floatShares")),
            "outstanding": _f(obj.get("outstandingShares"))}


def parse_grades_consensus(obj) -> dict:
    """FMP grades-consensus → {consensus, skew} where skew ∈ [-1,+1].

    skew = (strongBuy·2 + buy − sell − strongSell·2) / (2·total)."""
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return {}
    sb = _f(obj.get("strongBuy")) or 0.0
    b = _f(obj.get("buy")) or 0.0
    h = _f(obj.get("hold")) or 0.0
    s = _f(obj.get("sell")) or 0.0
    ss = _f(obj.get("strongSell")) or 0.0
    total = sb + b + h + s + ss
    skew = ((sb * 2 + b - s - ss * 2) / (2 * total)) if total > 0 else None
    return {"consensus": obj.get("consensus"),
            "skew": (round(skew, 3) if skew is not None else None),
            "analyst_count": int(total)}


def parse_finnhub_metric(obj) -> dict:
    """Finnhub /stock/metric?metric=all → {beta, hi_52w, lo_52w}."""
    if not isinstance(obj, dict):
        return {}
    m = obj.get("metric") if isinstance(obj.get("metric"), dict) else {}
    return {"beta": _f(m.get("beta")),
            "hi_52w": _f(m.get("52WeekHigh")),
            "lo_52w": _f(m.get("52WeekLow"))}


def parse_most_actives(rows) -> dict:
    """FMP most-actives → {TICKER: change_pct}. One call covers the whole market."""
    out: dict = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol", "")).upper().strip()
        if sym:
            out[sym] = _f(r.get("changesPercentage"))
    return out


# ── Assembly + derived signals ───────────────────────────────────────────────

def build_dossier(ticker: str, *, earnings: Optional[dict] = None,
                  float_data: Optional[dict] = None, grades: Optional[dict] = None,
                  metric: Optional[dict] = None, mover_chg: Optional[float] = None,
                  price: Optional[float] = None) -> TickerDossier:
    """Fuse already-parsed pieces into one dossier for ``ticker``. Each piece is the
    output of the corresponding parser (or None when that fetch had no data)."""
    d = TickerDossier(ticker=str(ticker).upper())
    if earnings:
        d.earnings_date = earnings.get("date")
        d.days_to_earnings = earnings.get("days_to")
        d.eps_estimate = earnings.get("eps_est")
        d.revenue_estimate = earnings.get("rev_est")
    if float_data:
        d.float_pct = float_data.get("float_pct")
        d.float_shares = float_data.get("float_shares")
        d.low_float = bool(d.float_shares is not None and 0 < d.float_shares < LOW_FLOAT_SHARES)
    if grades:
        d.analyst_consensus = grades.get("consensus")
        d.analyst_skew = grades.get("skew")
    if metric:
        d.beta = metric.get("beta")
        d.hi_52w = metric.get("hi_52w")
        d.lo_52w = metric.get("lo_52w")
    if mover_chg is not None:
        d.is_mover = True
        d.mover_chg_pct = _f(mover_chg)
    px = _f(price)
    if px and d.hi_52w:
        d.pct_from_52w_high = round((px - d.hi_52w) / d.hi_52w * 100.0, 2)
    return d


def earnings_gate(days_to_earnings: Optional[int], *,
                  block_days: int = 2, halfsize_days: int = 5) -> tuple[bool, float, str]:
    """The earnings-blackout ENTRY gate (product decision 2026-07-06): hard-block a
    new entry within ``block_days`` of a print, half-size within ``halfsize_days``,
    full size otherwise. Returns (allowed, size_factor, reason).

    None (no known earnings date) or a past print → full size, no gate."""
    d = days_to_earnings
    if d is None or d < 0:
        return True, 1.0, ""
    if d <= block_days:
        return False, 0.0, f"earnings in {d}d (blackout <= {block_days}d)"
    if d <= halfsize_days:
        return True, 0.5, f"earnings in {d}d (half-size <= {halfsize_days}d)"
    return True, 1.0, ""
