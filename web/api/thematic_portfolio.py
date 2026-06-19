"""
Thematic Portfolio API — per-user storage of positions grouped by theme.

Positions are stored in tmp/thematic_portfolio_{user_hash}.json.
Live prices fetched from yfinance on demand (cached 5 min).
No real trading execution — read/write of thesis/conviction/metadata only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from web.auth import get_current_user

log = logging.getLogger("thematic_portfolio")

ROOT = Path(__file__).parent.parent.parent
TMP  = ROOT / "tmp"

router = APIRouter()

# ── Default themes ────────────────────────────────────────────────────────────
DEFAULT_THEMES: dict[str, dict] = {
    "ai_leaders":       {"name": "AI Leaders",              "color": "#6366f1", "emoji": "🧠"},
    "ai_infrastructure":{"name": "AI Infrastructure",       "color": "#8b5cf6", "emoji": "⚡"},
    "optical_network":  {"name": "Optical Networking",      "color": "#06b6d4", "emoji": "🔗"},
    "memory_hbm":       {"name": "Memory / HBM",            "color": "#0ea5e9", "emoji": "💾"},
    "datacenter_power": {"name": "Data Center Power",       "color": "#f59e0b", "emoji": "🔋"},
    "nuclear_energy":   {"name": "Nuclear / Energy",        "color": "#10b981", "emoji": "☢️"},
    "space_defense":    {"name": "Space & Defense",         "color": "#64748b", "emoji": "🚀"},
    "quantum_future":   {"name": "Quantum / Future Compute","color": "#a78bfa", "emoji": "⚛️"},
    "critical_minerals":{"name": "Critical Minerals",       "color": "#d97706", "emoji": "⛏️"},
    "reshoring":        {"name": "Reshoring / Industrial",  "color": "#78716c", "emoji": "🏭"},
    "fintech_consumer": {"name": "Fintech / Consumer",      "color": "#ec4899", "emoji": "💳"},
    "future_tech":      {"name": "Future Tech / Biotech",   "color": "#14b8a6", "emoji": "🔬"},
}

CATEGORIES = ["core", "growth", "satellite", "speculative", "watchlist", "avoid"]
RISK_LEVELS = ["low", "medium", "high", "very_high"]

# ── Price cache (in-process, 5 min TTL) ──────────────────────────────────────
_price_cache: dict[str, tuple[float, float]] = {}  # ticker -> (price, ts)
_PRICE_TTL = 300.0

def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    now = time.time()
    needed = [t for t in tickers if t not in _price_cache or now - _price_cache[t][1] > _PRICE_TTL]
    if needed:
        try:
            import yfinance as yf
            data = yf.download(needed, period="2d", auto_adjust=True, progress=False)
            closes = data["Close"] if hasattr(data["Close"], "columns") else data["Close"].to_frame()
            for t in needed:
                try:
                    # P6: only assign when the ticker's own column exists.
                    # Previously fell back to columns[0] which could assign one ticker's price to another.
                    if t not in closes.columns:
                        continue
                    price = float(closes[t].dropna().iloc[-1])
                    # Only cache a real, usable price. A 0 / negative / inf / NaN
                    # would poison the cache and corrupt downstream sizing and
                    # entry-price math (division, position dollars).
                    if not math.isfinite(price) or price <= 0:
                        continue
                    _price_cache[t] = (price, now)
                except Exception:
                    pass
        except Exception as e:
            log.warning("yfinance fetch failed: %s", e)
    return {t: _price_cache[t][0] for t in tickers if t in _price_cache}

# ── Atomic write helper ───────────────────────────────────────────────────────
def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

# ── Per-user file storage ─────────────────────────────────────────────────────
def _user_file(email: str) -> Path:
    digest = hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    return TMP / f"thematic_portfolio_{digest}.json"

def _load(email: str) -> dict:
    f = _user_file(email)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            backup = f.with_suffix(".json.bak")
            if backup.exists():
                try:
                    return json.loads(backup.read_text())
                except Exception:
                    pass
    return {"positions": {}, "themes": DEFAULT_THEMES.copy(), "notes": ""}

def _save(email: str, data: dict) -> None:
    path = _user_file(email)
    if path.exists():
        try:
            import shutil
            shutil.copy2(path, path.with_suffix(".json.bak"))
        except Exception:
            pass
    _atomic_write(path, data)

# ── Scoring ───────────────────────────────────────────────────────────────────

_SCORE_HISTORY_FILE = ROOT / "tmp" / "thematic_score_history.jsonl"
_HISTORY_CACHE: tuple[float, dict[str, float]] = (0.0, {})  # (mtime, scores)


def _get_social_score_from_history(ticker: str) -> float:
    """Pull ticker's normalized scan score (0-10) from the latest scan snapshot."""
    global _HISTORY_CACHE
    if not _SCORE_HISTORY_FILE.exists():
        return 5.0
    try:
        mtime = _SCORE_HISTORY_FILE.stat().st_mtime
        if mtime != _HISTORY_CACHE[0]:
            lines = _SCORE_HISTORY_FILE.read_text().splitlines()
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                    scores_raw = dict(rec.get("ranked", []))
                    if scores_raw:
                        max_s = max(scores_raw.values()) if scores_raw else 1
                        normalized = {t: min(s / max_s * 10, 10.0) for t, s in scores_raw.items()}
                        _HISTORY_CACHE = (mtime, normalized)
                        break
                except Exception:
                    continue
        return round(_HISTORY_CACHE[1].get(ticker.upper(), 5.0), 1)
    except Exception:
        return 5.0


def _score_position(pos: dict, prices: dict[str, float]) -> dict[str, float]:
    ticker = pos["ticker"]
    entry  = pos.get("entry_price", 0) or 0
    curr   = prices.get(ticker, 0)
    conv   = pos.get("conviction", 5)
    risk   = {"low": 1, "medium": 2, "high": 3, "very_high": 4}.get(pos.get("risk_level", "medium"), 2)

    # Price-based metrics — only meaningful if we have both entry and current price
    has_prices = entry > 0 and curr > 0
    chase_pct = ((curr - entry) / entry * 100) if has_prices else 0.0

    # Entry quality: 10 = entered at/near current, 0 = chased 50%+ above entry
    entry_score = max(0.0, 10.0 - min(chase_pct / 5.0, 10.0)) if has_prices else 5.0

    # Momentum: up 20%+ = 10, flat = 5, down 20% = 0
    momentum = min(max((chase_pct + 20.0) / 4.0, 0.0), 10.0) if has_prices else 5.0

    # Risk score (inverse: lower risk = higher score)
    risk_score = max(0.0, 10.0 - risk * 2.0)

    # Conviction score (1-10 raw)
    conviction_score = float(conv)

    # Chase risk (higher = more chased, penalizes late entries)
    chase_risk = min(max(chase_pct / 5.0, 0.0), 10.0) if has_prices else 0.0

    # Theme score — premium AI/infra themes score highest
    premium_themes = {"ai_leaders", "ai_infrastructure", "memory_hbm", "datacenter_power"}
    growth_themes  = {"optical_network", "space_defense", "quantum_future", "nuclear_energy"}
    theme_score = 9.0 if pos.get("theme") in premium_themes else 7.0 if pos.get("theme") in growth_themes else 5.5

    # Catalyst score: 9 = specific near-term catalyst, 5 = general thesis, 2 = none
    cat = pos.get("catalyst", "").strip()
    catalyst_score = 9.0 if len(cat) > 30 else 5.0 if cat else 2.0

    # Thesis quality: has bull/bear case = 8, thesis only = 6, none = 3
    has_bull = bool(pos.get("thesis_bull", "").strip())
    has_bear = bool(pos.get("thesis_bear", "").strip())
    thesis_score = 8.0 if (has_bull and has_bear) else 6.0 if pos.get("thesis", "").strip() else 3.0

    # Social/news: pull from latest thematic scan history (0-10 normalized)
    social_score = _get_social_score_from_history(ticker)

    # Supply-chain beneficiary bonus
    supply_chain_themes = {"ai_infrastructure", "optical_network", "memory_hbm", "critical_minerals"}
    supply_score = 8.0 if pos.get("theme") in supply_chain_themes else 5.0

    # Weights sum to 1.0 exactly
    final = (
        theme_score      * 0.15 +
        catalyst_score   * 0.15 +
        conviction_score * 0.15 +
        thesis_score     * 0.10 +
        entry_score      * 0.10 +
        momentum         * 0.10 +
        risk_score       * 0.10 +
        supply_score     * 0.08 +
        social_score     * 0.07 -
        chase_risk       * 0.10   # penalty for chasing
    )

    return {
        "theme_score":        round(theme_score, 1),
        "catalyst_score":     round(catalyst_score, 1),
        "momentum_score":     round(momentum, 1),
        "fundamental_score":  round(thesis_score, 1),   # kept key name for UI compat
        "supply_chain_score": round(supply_score, 1),
        "social_score":       round(social_score, 1),
        "entry_quality":      round(entry_score, 1),
        "risk_score":         round(risk_score, 1),
        "chase_risk":         round(chase_risk, 1),
        "final_score":        round(min(max(final, 0), 10), 2),
    }

# ── Enrich positions with live data ──────────────────────────────────────────
def _enrich(positions: dict, themes: dict) -> list[dict]:
    tickers = list(positions.keys())
    prices = _fetch_prices(tickers) if tickers else {}
    result = []
    for ticker, pos in positions.items():
        entry = pos.get("entry_price") or 0
        shares = pos.get("shares") or 0
        curr = prices.get(ticker)
        gain_pct = ((curr - entry) / entry * 100) if (curr and entry > 0) else None
        gain_usd = ((curr - entry) * shares) if (curr and entry > 0 and shares > 0) else None
        market_val = (curr * shares) if (curr and shares > 0) else None
        score = _score_position(pos, prices)
        theme_meta = themes.get(pos.get("theme", ""), {})
        result.append({
            **pos,
            "current_price": curr,
            "gain_pct":      round(gain_pct, 2) if gain_pct is not None else None,
            "gain_usd":      round(gain_usd, 2) if gain_usd is not None else None,
            "market_value":  round(market_val, 2) if market_val is not None else None,
            "theme_name":    theme_meta.get("name", pos.get("theme", "")),
            "theme_color":   theme_meta.get("color", "#6366f1"),
            "theme_emoji":   theme_meta.get("emoji", "📊"),
            "scores":        score,
        })
    result.sort(key=lambda p: p["scores"]["final_score"], reverse=True)
    return result

# ── Pydantic models ───────────────────────────────────────────────────────────
class PositionIn(BaseModel):
    ticker: str
    name: str = ""
    theme: str = "ai_leaders"
    entry_price: float = 0.0
    shares: float = 0.0
    conviction: int = 5
    risk_level: str = "medium"
    category: str = "watchlist"
    thesis: str = ""
    catalyst: str = ""
    thesis_bull: str = ""
    thesis_bear: str = ""
    risk_warning: str = ""
    review_date: str = ""
    tags: list[str] = []

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("conviction")
    @classmethod
    def clamp_conviction(cls, v: int) -> int:
        return max(1, min(10, v))

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        return v

    @field_validator("risk_level")
    @classmethod
    def valid_risk(cls, v: str) -> str:
        if v not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {RISK_LEVELS}")
        return v

class ThemeIn(BaseModel):
    name: str
    color: str = "#6366f1"
    emoji: str = "📊"
    description: str = ""

class NotesIn(BaseModel):
    notes: str

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/thematic/portfolio")
async def get_thematic_portfolio(user: dict = Depends(get_current_user)):
    """Return full enriched portfolio: positions, themes, summary stats."""
    data = _load(user["email"])
    positions = data["positions"]
    themes = data.get("themes", DEFAULT_THEMES)

    enriched = _enrich(positions, themes)

    # Summary stats
    total_market_val = sum(p["market_value"] for p in enriched if p["market_value"])
    total_cost       = sum((p.get("entry_price") or 0) * (p.get("shares") or 0) for p in enriched)
    total_gain_usd   = sum(p["gain_usd"] for p in enriched if p["gain_usd"] is not None)
    winners  = sorted([p for p in enriched if (p["gain_pct"] or 0) > 0], key=lambda p: p["gain_pct"] or 0, reverse=True)
    losers   = sorted([p for p in enriched if (p["gain_pct"] or 0) < 0], key=lambda p: p["gain_pct"] or 0)

    # Theme breakdown
    theme_groups: dict[str, list] = {}
    for p in enriched:
        theme_groups.setdefault(p.get("theme", "other"), []).append(p)

    theme_summary = {}
    for theme_key, theme_positions in theme_groups.items():
        th = themes.get(theme_key, {})
        mv = sum(p["market_value"] for p in theme_positions if p["market_value"])
        theme_summary[theme_key] = {
            "name":         th.get("name", theme_key),
            "color":        th.get("color", "#6366f1"),
            "emoji":        th.get("emoji", "📊"),
            "count":        len(theme_positions),
            "market_value": round(mv, 2),
            "allocation_pct": round(mv / total_market_val * 100, 1) if total_market_val > 0 else 0,
        }

    return {
        "ok": True,
        "positions": enriched,
        "themes": themes,
        "theme_groups": theme_groups,
        "theme_summary": theme_summary,
        "summary": {
            "position_count": len(enriched),
            "total_market_value": round(total_market_val, 2),
            "total_cost_basis": round(total_cost, 2),
            "total_gain_usd": round(total_gain_usd, 2),
            "total_gain_pct": round(total_gain_usd / total_cost * 100, 2) if total_cost > 0 else 0,
            "winners_count": len(winners),
            "losers_count":  len(losers),
            "best_winner":   winners[0]["ticker"] if winners else None,
            "worst_loser":   losers[0]["ticker"] if losers else None,
            "data_note": "Prices from yfinance — 15-min delayed. Not real broker data.",
        },
        "notes": data.get("notes", ""),
    }


@router.post("/thematic/portfolio/position")
async def add_position(body: PositionIn, user: dict = Depends(get_current_user)):
    data = _load(user["email"])
    pos  = body.model_dump()
    pos["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["positions"][body.ticker] = pos
    _save(user["email"], data)
    return {"ok": True, "ticker": body.ticker}


@router.put("/thematic/portfolio/position/{ticker}")
async def edit_position(ticker: str, body: PositionIn, user: dict = Depends(get_current_user)):
    data = _load(user["email"])
    ticker = ticker.upper()
    if ticker not in data["positions"]:
        raise HTTPException(status_code=404, detail=f"{ticker} not in portfolio")
    existing = data["positions"][ticker]
    updated  = {**existing, **body.model_dump(exclude_unset=True)}
    updated["ticker"] = ticker
    data["positions"][ticker] = updated
    _save(user["email"], data)
    return {"ok": True, "ticker": ticker}


@router.delete("/thematic/portfolio/position/{ticker}")
async def remove_position(ticker: str, user: dict = Depends(get_current_user)):
    data = _load(user["email"])
    ticker = ticker.upper()
    if ticker not in data["positions"]:
        raise HTTPException(status_code=404, detail=f"{ticker} not found")
    del data["positions"][ticker]
    _save(user["email"], data)
    return {"ok": True, "ticker": ticker}


@router.get("/thematic/portfolio/themes")
async def get_themes(user: dict = Depends(get_current_user)):
    data = _load(user["email"])
    return {"ok": True, "themes": data.get("themes", DEFAULT_THEMES)}


@router.post("/thematic/portfolio/themes/{theme_key}")
async def add_theme(theme_key: str, body: ThemeIn, user: dict = Depends(get_current_user)):
    key = theme_key.lower().replace(" ", "_")[:32]
    data = _load(user["email"])
    data.setdefault("themes", DEFAULT_THEMES.copy())[key] = body.model_dump()
    _save(user["email"], data)
    return {"ok": True, "key": key}


@router.post("/thematic/portfolio/notes")
async def save_notes(body: NotesIn, user: dict = Depends(get_current_user)):
    data = _load(user["email"])
    data["notes"] = body.notes[:4000]
    _save(user["email"], data)
    return {"ok": True}


@router.get("/thematic/portfolio/score/{ticker}")
async def score_ticker(ticker: str, user: dict = Depends(get_current_user)):
    """Score a single position. If not in portfolio, scores with defaults."""
    data = _load(user["email"])
    ticker = ticker.upper()
    pos  = data["positions"].get(ticker, {"ticker": ticker, "conviction": 5, "risk_level": "medium"})
    prices = _fetch_prices([ticker])
    score = _score_position(pos, prices)
    return {"ok": True, "ticker": ticker, "scores": score, "current_price": prices.get(ticker)}


@router.get("/thematic/portfolio/defaults")
async def get_defaults(_user: dict = Depends(get_current_user)):
    """Return default themes, categories, risk levels for UI dropdowns."""
    return {
        "ok": True,
        "default_themes": DEFAULT_THEMES,
        "categories": CATEGORIES,
        "risk_levels": RISK_LEVELS,
    }


# ── Paper-trade injection ─────────────────────────────────────────────────────
import datetime as _dt

PAPER_STATE_FILE = ROOT / "tmp" / "thematic_paper" / "state.json"
THEMATIC_TRADES_FILE = ROOT / "tmp" / "thematic_trades.jsonl"

# Dedicated thematic paper book — isolated from the 15-portfolio competition
# state.json (tmp/paper_trading_today/unified_brain/state.json) so thematic P&L
# is tracked cleanly and the papertrader process never races writes on it.
THEMATIC_PAPER_START_CASH = float(os.getenv("THEMATIC_PAPER_START_CASH", "100000") or 100000)


def _ensure_thematic_paper_state() -> None:
    """Create the dedicated thematic paper book on first use.

    A fresh book must start with a realistic balance (not the legacy $10k
    default scattered through `state.get("cash", ...)` reads) so conviction-
    scaled ~$1k positions fit under the position caps, and every reader sees a
    consistent cash figure within a single approve call. Idempotent.
    """
    if PAPER_STATE_FILE.exists():
        return
    PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(PAPER_STATE_FILE, {
        "cash": THEMATIC_PAPER_START_CASH,
        "settled_cash": THEMATIC_PAPER_START_CASH,
        "starting_cash": THEMATIC_PAPER_START_CASH,
        "positions": {},
    })


class ThematicTradeIn(BaseModel):
    ticker: str
    dollar_amount: float | None = None   # allocate this many dollars
    shares: int | None = None            # or explicit share count
    entry_price: float | None = None     # if None, fetch current price
    stop_pct: float = 5.0                # % below entry for stop
    target_pct: float = 10.0            # % above entry for target

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.upper().strip()


@router.post("/thematic/trade")
async def thematic_paper_trade(body: ThematicTradeIn, user: dict = Depends(get_current_user)):
    """Inject a thematic conviction trade directly into the unified brain paper account."""
    import asyncio as _asyncio
    ticker = body.ticker

    # Fetch current price if entry not specified
    prices = _fetch_prices([ticker])
    price = body.entry_price or prices.get(ticker)
    if not price:
        raise HTTPException(status_code=400, detail=f"Cannot fetch price for {ticker}")

    price = round(float(price), 4)

    # Load user HIL settings for R:R / circuit-breaker / conviction-scale params
    from web import users as _us
    _urec = _us.get_user(user["email"]) or {}
    hil = _us.get_thematic_hil(_urec)

    # A4: R:R gate — reject sub-min-RR signals honestly instead of auto-widening the target.
    # Auto-widening manufactured a favorable ratio the trade never had (borderline Rule-1).
    stop_pct   = body.stop_pct
    target_pct = body.target_pct
    min_rr     = float(hil.get("min_rr", 1.5))
    rr         = target_pct / stop_pct if stop_pct > 0 else 0.0
    rr_warning = None
    if rr < min_rr:
        if not hil.get("allow_rr_widening", False):
            raise HTTPException(
                status_code=400,
                detail=f"R:R {rr:.2f} < min {min_rr:.1f} — tighten stop or widen target before submitting"
            )
        # Widening explicitly enabled by user setting — persist flag for honest reporting
        target_pct = round(stop_pct * min_rr, 1)
        rr_warning = f"R:R {rr:.2f} < min {min_rr:.1f} — target widened to {target_pct}% (rr_widened=True)"
        log.info("R:R gate: widening allowed for %s: target → %.1f%%", ticker, target_pct)

    stop   = round(price * (1 - stop_pct / 100), 4)
    target = round(price * (1 + target_pct / 100), 4)
    rr_widened = rr_warning is not None

    # Enrich with portfolio metadata for conviction-scaling
    port_data  = _load(user["email"])
    port_pos   = port_data["positions"].get(ticker, {})
    conviction = int(port_pos.get("conviction", 7))

    # Compute shares — conviction-scaled if dollar_amount provided
    if body.shares and body.shares > 0:
        shares = body.shares
        alloc  = round(price * shares, 2)
    elif body.dollar_amount and body.dollar_amount > 0:
        use_scale = hil.get("conviction_scale", True)
        scale     = (0.4 + (conviction - 1) / 9.0 * 1.1) if use_scale else 1.0
        alloc     = round(body.dollar_amount * scale, 2)
        shares    = int(alloc / price)
        if shares <= 0:
            raise HTTPException(status_code=400, detail=f"Dollar amount ${body.dollar_amount:.2f} too small for price ${price:.2f}")
    else:
        raise HTTPException(status_code=400, detail="Provide dollar_amount or shares")

    cost = round(price * shares, 2)

    # P4 / A11: fetch ATR (IO-bound, no state dependency) before acquiring lock
    from web.api.thematic_auto import (
        _check_portfolio_circuit_breakers,
        _real_atr,
        _paper_state_lock,
        PORTFOLIO_MAX_POSITIONS,
        PORTFOLIO_MAX_PER_THEME,
        PORTFOLIO_MAX_SPECULATIVE,
    )
    loop = _asyncio.get_running_loop()
    atr  = await loop.run_in_executor(None, _real_atr, ticker, price)

    now_iso    = _dt.datetime.now().isoformat(timespec="seconds")
    today      = _dt.date.today().isoformat()
    alpha_tier = "A+" if conviction >= 9 else "A" if conviction >= 7 else "B" if conviction >= 5 else "C"

    # A11/P8: lock the entire read-modify-write to prevent concurrent clobbers.
    # Concurrent approvals or auto-scans on different requests share the same
    # file; without the lock, two concurrent requests both read 10 positions and
    # both write 11, losing one entry.
    async with _paper_state_lock:
        PAPER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ensure_thematic_paper_state()
        state: dict = {}
        if PAPER_STATE_FILE.exists():
            try:
                state = json.loads(PAPER_STATE_FILE.read_text())
            except Exception:
                pass

        # P4: enforce same PORTFOLIO_MAX_* caps as the auto path
        open_positions = state.get("positions", {})
        _theme = port_pos.get("theme", "future_tech")
        _thematic_count = sum(1 for p in open_positions.values() if p.get("_source", "").startswith("thematic"))
        _theme_count    = sum(1 for p in open_positions.values() if p.get("theme") == _theme and p.get("sector") == "thematic")
        if len(open_positions) >= PORTFOLIO_MAX_POSITIONS:
            raise HTTPException(status_code=400, detail=f"Portfolio at max {PORTFOLIO_MAX_POSITIONS} positions")
        if _theme_count >= PORTFOLIO_MAX_PER_THEME:
            raise HTTPException(status_code=400, detail=f"Theme '{_theme}' at max {PORTFOLIO_MAX_PER_THEME} positions")
        if _thematic_count >= PORTFOLIO_MAX_SPECULATIVE:
            raise HTTPException(status_code=400, detail=f"Thematic positions at max {PORTFOLIO_MAX_SPECULATIVE}")

        # Portfolio heat + daily loss circuit breakers
        cb_ok, cb_reason = _check_portfolio_circuit_breakers(state, hil, cost)
        if not cb_ok:
            raise HTTPException(status_code=400, detail=cb_reason)

        cash    = float(state.get("cash", 10000.0))
        settled = float(state.get("settled_cash", cash))
        if cost > settled:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient settled cash: need ${cost:.2f}, have ${settled:.2f}"
            )

        if ticker in open_positions:
            raise HTTPException(status_code=400, detail=f"{ticker} already open in paper account")

        open_positions[ticker] = {
            "ticker":        ticker,
            "shares":        shares,
            "entry_price":   price,
            "stop":          stop,
            "target":        target,
            "entry_time":    now_iso,
            "signal_date":   today,
            "score":         float(conviction) * 10,
            "ml_probability":None,
            "expected_return":None,
            "large_loss_probability": None,
            "alpha_tier":    alpha_tier,
            "atr":           atr,
            "breakeven_moved": False,
            "peak_price":    price,
            "scans_held":    0,
            "partial_sold":  False,
            "defensive_trimmed": False,
            "scaled_in":     False,
            "sector":        "thematic",
            "theme":         _theme,
            "strategy_label":"thematic_conviction",
            "thesis":        port_pos.get("thesis", ""),
            "catalyst":      port_pos.get("catalyst", ""),
            "hold_days":     5,
            "exit_plan":     f"Target +{target_pct}%, stop -{stop_pct}% (R:R {round(target_pct/stop_pct,2)}), max 5 days",
            "entry_date":    today,
            "funded_by_unsettled": False,
            "unsettled_settle_date": "",
            "regime_at_entry": "thematic",
            "regime_score_at_entry": None,
            "crash_risk_at_entry": None,
            "regime_confidence_at_entry": None,
            "_source": "thematic",
            "rr_widened": rr_widened,   # A4: True if target was widened to meet min_rr
            # P1: set entry_raw_score from latest scan so buzz_decay exit can fire.
            "entry_raw_score": _get_social_score_from_history(ticker) * 10.0,
        }

        state["positions"]    = open_positions
        state["cash"]         = round(cash - cost, 4)
        state["settled_cash"] = round(settled - cost, 4)
        _atomic_write(PAPER_STATE_FILE, state)

    # Append to thematic trades log
    THEMATIC_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with THEMATIC_TRADES_FILE.open("a") as f:
        f.write(json.dumps({
            "ts": now_iso, "ticker": ticker, "shares": shares,
            "entry": price, "stop": stop, "target": target,
            "cost": cost, "rr": round(target_pct / stop_pct, 2),
            "conviction": conviction, "atr": atr,
            "user": user["email"],
        }) + "\n")

    resp = {
        "ok": True, "ticker": ticker, "shares": shares,
        "entry": price, "stop": stop, "target": target,
        "cost": cost, "rr": round(target_pct / stop_pct, 2),
        "rr_widened": rr_widened,
        "conviction": conviction, "atr": atr,
        "cash_remaining": state["cash"],
    }
    if rr_warning:
        resp["warnings"] = [rr_warning]
    return resp
