"""Holdings Brain API — AI-assisted, human-in-the-loop management of a real
Fidelity (and later Webull) brokerage account.

Endpoints (all under /api):
  GET  /thematic/brain/holdings              read real holdings + per-position assessment
  POST /thematic/brain/assess                run a full brain cycle, write proposals
  GET  /thematic/brain/proposals             list pending HIL proposals
  POST /thematic/brain/proposals/{id}/approve  execute one proposal (step-up 2FA)
  POST /thematic/brain/proposals/{id}/skip   dismiss a proposal

This layer holds NO trading logic of its own — the decisions come from the pure,
tested ``tradingagents.portfolio.holdings_brain`` module, and every live order is
routed through the existing compliance-gated, step-up-2FA Fidelity endpoints.
Nothing here fires an order without an explicit ``execute=true`` from the human
AND ``LIVE_TRADING_ENABLED`` (both off by default).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from tradingagents.config import env_bool
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from web.auth import require_admin, require_step_up
from tradingagents.portfolio import holdings_brain as hb

log = logging.getLogger("holdings_brain")
router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "tmp"
_SAFETY_REPORT = ROOT / "tmp" / "paper_trading_today" / "unified_brain" / "safety_report.json"


# ── small atomic JSON writer (proposals) ────────────────────────────────────────
def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_hbp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _proposals_path(email: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_.@-" else "_" for c in (email or "default").lower())
    return TMP / f"holdings_brain_proposals_{safe}.json"


def _load_proposals(email: str) -> list[dict]:
    path = _proposals_path(email)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("proposals", [])
    except Exception:
        return []


def _load_deferred(email: str) -> list[dict]:
    """Trades held back last cycle (per-cycle budget / minimum-hold). Informational."""
    path = _proposals_path(email)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("deferred", [])
    except Exception:
        return []


def _save_proposals(email: str, proposals: list[dict], deferred: list[dict] | None = None) -> None:
    # Preserve the existing deferred list unless an explicit one is supplied, so
    # approve/skip don't wipe what the last cycle held back.
    if deferred is None:
        deferred = _load_deferred(email)
    _atomic_write(_proposals_path(email), {
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "proposals": proposals,
        "deferred": deferred,
    })


def _hil_approval_link() -> str:
    base = os.getenv("PUBLIC_DASHBOARD_URL", "https://app.agentictrader.org").rstrip("/")
    return f"{base}/app/hil?tab=approvals"


def _brain_sms_enabled() -> bool:
    return env_bool("HOLDINGS_BRAIN_SMS", True)


def _trail_ratchet_mult() -> Optional[float]:
    """HOLDINGS_BRAIN_TRAIL_ATR_MULT — trail width for the exit-guard stop ratchet.
    Unset/invalid → None (ExitManager default 0.5, the tighter/safer width);
    accepted values clamped to [0.25, 3.0]."""
    raw = os.getenv("HOLDINGS_BRAIN_TRAIL_ATR_MULT", "").strip()
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    if not (0 < val < float("inf")):  # rejects NaN/inf/non-positive
        return None
    return max(0.25, min(val, 3.0))


def _summarize_for_sms(proposals: list[dict]) -> str:
    """e.g. 'DROP ASTS, ONDS · TRIM NVDA' — compact, deterministic."""
    verb = {"EXIT": "DROP", "TRIM": "TRIM", "ADD": "ADD", "ADOPT": "KEEP", "SET_STOP": "STOP"}
    by_kind: dict[str, list[str]] = {}
    for p in proposals:
        k = str((p.get("action") or {}).get("kind", "")).upper()
        by_kind.setdefault(verb.get(k, k or "?"), []).append(p.get("ticker", "?"))
    parts = [f"{v} {', '.join(tks[:4])}{f' +{len(tks)-4}' if len(tks) > 4 else ''}"
             for v, tks in by_kind.items()]
    return " · ".join(parts)


async def _notify_brain_pending(email: str, proposals: list[dict]) -> None:
    """Text the user a trade request + a deep link to the HIL Approvals tab.

    Opt-in: requires a phone on the account and (per-user) thematic-HIL sms_notify,
    plus HOLDINGS_BRAIN_SMS!=false. Never blocks the cycle; failures are logged.
    """
    if not proposals or not _brain_sms_enabled():
        return
    try:
        import asyncio
        from web import users as user_store
        from scripts.sms_alerts import send_sms

        rec = user_store.get_user(email) or {}
        phone = (rec.get("phone_number") or os.getenv("PAPER_SMS_NUMBER", "")).strip()
        if not phone:
            return
        # Reuse the existing SMS opt-in the user already toggled for HIL.
        try:
            thil = user_store.get_thematic_hil(rec)
            if thil.get("sms_notify") is False:
                return
        except Exception:
            pass

        # Cooldown: the brain rebuilds proposals every cycle, so an unactioned
        # persistent condition (e.g. NVDA over-cap) would otherwise re-page each
        # cycle. Only page about proposals not alerted within ALERT_COOLDOWN_HOURS
        # whose action kind is unchanged (a HOLD→TRIM→EXIT escalation re-pages).
        from web import alert_cooldown

        def _conv(p):
            return float((p.get("action") or {}).get("conviction", 0) or 0)

        def _kind(p):
            return (p.get("action") or {}).get("kind", "")

        fresh = [
            p for p in proposals
            if alert_cooldown.should_alert(f"brain:{email}", p.get("ticker", ""),
                                           score=_conv(p) * 10.0, kind=_kind(p))
        ]
        if not fresh:
            log.info("Brain pending-SMS suppressed (cooldown) for %s", email[:16])
            return
        n = len(fresh)
        msg = (
            f"🧠 Agentic Trader — {n} holding{'s' if n != 1 else ''} to manage\n\n"
            f"{_summarize_for_sms(fresh)}\n\n"
            f"Review & approve 👉 {_hil_approval_link()}"
        )
        await asyncio.to_thread(send_sms, phone, msg)
        for p in fresh:
            alert_cooldown.record_alert(f"brain:{email}", p.get("ticker", ""),
                                        score=_conv(p) * 10.0, kind=_kind(p))
        log.info("Brain trade-request SMS sent to %s (%d proposals)", email[:16], n)
    except Exception as e:
        log.warning("_notify_brain_pending failed for %s: %s", email[:16], e)


# ── context builders (reuse already-computed signals) ───────────────────────────
def _load_regime() -> dict:
    """Reuse the regime the running paper-trader already computes (no recompute)."""
    try:
        rpt = json.loads(_SAFETY_REPORT.read_text(encoding="utf-8"))
        mkt = rpt.get("market", {})
        crash = float(mkt.get("crash_risk_score", 0) or 0)
        return {
            "regime": mkt.get("regime", "unknown"),
            "crash_risk_score": crash,
            "no_trade": (crash >= 0.85) or (not rpt.get("safe_to_trade", True)),
        }
    except Exception:
        return {"regime": "unknown", "crash_risk_score": 0.0, "no_trade": False}


def _load_social_scores() -> dict:
    try:
        from web.api.thematic_auto import _get_latest_scan_scores
        return _get_latest_scan_scores() or {}
    except Exception:
        return {}


def _trusted_quotes(tickers: list[str]) -> dict[str, float]:
    """Best trusted live price per ticker via the quote gateway (FMP/Finnhub/…)."""
    out: dict[str, float] = {}
    try:
        from tradingagents.data.quote_gateway import get_gateway
        gw = get_gateway()
        if gw is None:
            return out
        for t, gq in gw.get_quotes(tickers).items():
            if gq is not None:
                out[t] = gq.best.reference_price()
    except Exception as e:
        log.warning("trusted quote batch failed: %s", e)
    return out


# ATR is a 14-day statistic; refetching it on every guard tick (default 15 min)
# would hammer yfinance and leak sqlite fds — the known Errno 24 source — for no
# extra signal. Cache per ticker for a few hours.
_ATR_CACHE: dict[str, tuple[float, float]] = {}   # ticker → (atr, unix_ts)
_ATR_CACHE_TTL_SECONDS = 6 * 3600


def _real_atr_map(holdings: list, quotes: dict[str, float]) -> dict[str, float]:
    """Real 14-day ATR per held ticker. Synchronous — call via run_in_executor.

    Nothing used to supply this, so both the rule engine (`_atr_for`) and the
    stop ratchet fell back to `_ATR_FALLBACK_PCT` — a synthetic 2% of price.
    That put adoption targets at ~+2.4% against ~-9.6% stops (R:R 0.25) and made
    the trailing ratchet sit ~1% under the high-water mark, so a real holding was
    proposed for exit after a 1.8% move. Systematically cutting winners while
    letting losers run is the single most expensive thing a stop engine can do.

    Best-effort by design: any failure degrades to the previous 2% fallback
    rather than blocking the guard, so a yfinance outage never leaves the live
    book unwatched.
    """
    out: dict[str, float] = {}
    now = time.time()
    for h in holdings:
        ticker = getattr(h, "ticker", None)
        if not ticker:
            continue
        cached = _ATR_CACHE.get(ticker)
        if cached and (now - cached[1]) < _ATR_CACHE_TTL_SECONDS:
            if cached[0] > 0:
                out[ticker] = cached[0]
            continue
        atr = 0.0
        try:
            price = float(quotes.get(ticker) or getattr(h, "last", 0) or 0)
            if price > 0:
                from web.api.thematic_auto import _real_atr
                atr = float(_real_atr(ticker, price) or 0)
        except Exception as e:
            log.warning("ATR fetch failed for %s: %s", ticker, e)
            atr = 0.0
        _ATR_CACHE[ticker] = (atr, now)
        if atr > 0:
            out[ticker] = atr
    return out


async def _build_ctx(holdings: list, quotes: dict[str, float] | None = None) -> dict:
    """Assessment context for the rule engine.

    Carries a REAL ATR map so `hb._atr_for` never silently degrades to its
    synthetic 2%-of-price estimate on the live book — that fallback is what
    produced R:R 0.25 adoption levels. ATR is cached, so this is cheap.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return {
        "regime": _load_regime(),
        "social_scores": _load_social_scores(),
        "atr": await loop.run_in_executor(None, _real_atr_map, holdings, quotes or {}),
    }


def _make_llm_fn():
    """Return a sync llm_fn(prompt)->str|None, or None if no model configured.

    FREE models only (Cloudflare Workers AI + OpenRouter free tier) via the shared
    budget-guarded `_ai_complete_sync` — never the paid Anthropic/credit path, per
    the free-only mandate. The brain's deterministic rule engine remains the safety
    floor; assess_holding clamps any LLM suggestion and never softens a mandatory
    exit. Any failure ⇒ None ⇒ rule engine used unchanged.
    """
    if not env_bool("HOLDINGS_BRAIN_LLM", True):
        return None
    try:
        from web.api.thematic_auto import _ai_complete_sync, _ai_intent_enabled
    except Exception:
        return None
    if not _ai_intent_enabled():        # same free-model availability gate
        return None

    _SYS = ("You are a disciplined portfolio risk manager refining a rule-based "
            "assessment of ONE stock holding. Respond ONLY with the requested JSON.")

    def _fn(prompt: str) -> Optional[str]:
        # "smart" prefers the strong OR free models (gpt-oss-120b etc.); falls back
        # to CF 70B under the neuron budget. Real-money path → quality first.
        return _ai_complete_sync(_SYS, prompt, prefer="smart", max_tokens=400)

    return _fn


# ── broker readers ──────────────────────────────────────────────────────────────
async def _read_raw_rows(email: str, broker: str) -> tuple[str, list[dict]]:
    """Fetch the broker's raw position rows (pre-normalization). Raises
    HTTPException (401) if the broker session is not active."""
    broker = (broker or "fidelity").lower()
    if broker == "webull":
        try:
            from web.api.webull_portfolio import _get_wb
            wb = _get_wb(email)
            if not getattr(wb, "_access_token", None):
                raise HTTPException(status_code=401, detail="Not connected to Webull")
            raw = wb.get_positions() or []
            rows = []
            for p in raw:
                tkr = p.get("ticker", {}) if isinstance(p, dict) else {}
                rows.append({
                    "symbol": (tkr.get("symbol") if isinstance(tkr, dict) else "") or p.get("symbol", ""),
                    "qty": p.get("position", 0),
                    "cost_price": p.get("costPrice", 0),
                    "last_price": p.get("lastPrice", 0) or p.get("marketPrice", 0),
                })
            return "webull", rows
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Webull read failed: {e}")

    # Fidelity (default) — reuse the existing positions scraper
    from web.api.fidelity import fidelity_positions
    data = await fidelity_positions({"email": email})
    return "fidelity", data.get("positions", [])


async def _read_holdings(email: str, broker: str) -> list[hb.Holding]:
    """Read + normalize real holdings. Protected accounts (Roth/IRA/retirement)
    and non-equity instruments are dropped inside ``normalize_holdings``."""
    b, rows = await _read_raw_rows(email, broker)
    return hb.normalize_holdings(rows, b)


def _progress(entry: Any, last: Any, target: Any) -> float:
    """Fraction of the way from entry to target (0..1.5). 0 when unknown."""
    try:
        entry, last, target = float(entry), float(last), float(target)
        if target > entry > 0 and last > 0:
            return max(0.0, min(1.5, (last - entry) / (target - entry)))
    except Exception:
        pass
    return 0.0


def _upside(last: Any, target: Any) -> float:
    """Remaining upside %% from last price to target. 0 when at/over target."""
    try:
        last, target = float(last), float(target)
        if last > 0 and target > last:
            return (target - last) / last * 100.0
    except Exception:
        pass
    return 0.0


async def get_unified_existing(
    email: str,
    broker: str = "fidelity",
    *,
    include_broker: bool = True,
    broker_timeout: float = 60.0,
) -> tuple[list, float]:
    """Unified 'existing positions' view for the portfolio policy.

    Real broker holdings (conviction/status from the deterministic rule engine)
    unioned with open thematic paper positions, deduped by ticker (broker wins).
    Returns ``(positions, account_value)``.

    Network-resilient: a broker read failure/timeout falls back to the thematic
    paper book only, so the 4h background scan never dies on a broker hiccup.
    """
    import asyncio
    from tradingagents.portfolio import portfolio_policy as pol

    positions: dict[str, pol.ExistingPosition] = {}
    account_value = 0.0

    if include_broker and email:
        try:
            holdings = await asyncio.wait_for(_read_holdings(email, broker), timeout=broker_timeout)
            store = hb.load_store(email, base_dir=TMP)
            ctx = await _build_ctx(holdings)
            account_value = sum(float(h.market_value or 0) for h in holdings)
            # Prefer the TOTAL account value (incl. cash), implied by any weight.
            implied = [
                h.market_value / (h.pct_of_account / 100.0)
                for h in holdings if h.pct_of_account and h.market_value
            ]
            if implied:
                account_value = max(implied)
            for h in holdings:
                plan = store.get(h.ticker) or {}
                try:
                    action = hb.assess_holding(h, store.get(h.ticker), ctx, llm_fn=None)
                    conv, status = int(action.conviction), action.kind
                except Exception:
                    conv, status = 5, hb.ACTION_HOLD
                target = plan.get("target")
                positions[h.ticker.upper()] = pol.ExistingPosition(
                    ticker=h.ticker.upper(),
                    conviction=conv,
                    pct_of_account=float(h.pct_of_account or 0),
                    unrealized_pct=float(h.unrealized_pct or 0),
                    status=status,
                    target_progress=_progress(h.avg_cost, h.last, target),
                    time_progress=0.0,
                    expected_return=_upside(h.last, target),
                )
        except Exception as e:
            log.warning("get_unified_existing: broker read failed (%s) — thematic-only", e)

    # Union open thematic paper positions (broker wins on duplicate ticker).
    try:
        from web.api.thematic_portfolio import PAPER_STATE_FILE
        if PAPER_STATE_FILE.exists():
            state = json.loads(PAPER_STATE_FILE.read_text() or "{}")
            tpos = state.get("positions", {}) or {}
            if account_value <= 0:
                cash = float(state.get("cash", 0) or 0)
                held = sum(
                    float(p.get("shares", 0) or 0) * float(p.get("peak_price") or p.get("entry_price") or 0)
                    for p in tpos.values()
                )
                account_value = cash + held
            for tkr, p in tpos.items():
                key = str(tkr).upper()
                if key in positions:
                    continue
                conv = max(1, min(10, int(round(float(p.get("score", 50) or 50) / 10.0)) or 5))
                entry = p.get("entry_price")
                last = p.get("peak_price") or entry
                target = p.get("target")
                positions[key] = pol.ExistingPosition(
                    ticker=key, conviction=conv,
                    pct_of_account=0.0, unrealized_pct=0.0,
                    status="thematic",
                    target_progress=_progress(entry, last, target),
                    time_progress=0.0,
                    expected_return=_upside(last, target),
                )
    except Exception as e:
        log.warning("get_unified_existing: thematic book read failed: %s", e)

    return list(positions.values()), round(account_value, 2)


def _assess_all(holdings: list[hb.Holding], store: dict, ctx: dict, use_ai: bool) -> list[dict]:
    """Assess every holding (in a worker thread — may call the LLM)."""
    llm_fn = _make_llm_fn() if use_ai else None
    rows = []
    for h in holdings:
        plan = store.get(h.ticker)
        try:
            action = hb.assess_holding(h, plan, ctx, llm_fn=llm_fn)
        except Exception as e:
            log.warning("assess failed for %s: %s", h.ticker, e)
            action = hb.Action(h.ticker, hb.ACTION_HOLD, reason=f"assess error: {e}")
        rows.append({"holding": h.to_dict(), "plan": plan, "action": action.to_dict()})
    return rows


# ── shared cycle logic (used by endpoints AND background loops) ─────────────────
async def run_brain_cycle(email: str, broker: str = "fidelity", use_ai: bool = True) -> dict:
    """Read → reconcile → assess → write HIL proposals. Places no orders."""
    import asyncio
    holdings = await _read_holdings(email, broker)
    store = hb.load_store(email, base_dir=TMP)
    recon = hb.reconcile(holdings, store)
    ctx = await _build_ctx(holdings)

    for tkr in recon.closed:
        if tkr in store:
            store[tkr]["status"] = "closed"
            store[tkr]["closed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    if recon.closed:
        hb.save_store(email, store, base_dir=TMP)

    loop = asyncio.get_running_loop()
    assessed = await loop.run_in_executor(None, _assess_all, holdings, store, ctx, use_ai)

    existing = [p for p in _load_proposals(email) if p.get("status") != "pending"]
    now = dt.datetime.now().isoformat(timespec="seconds")

    # Churn control: cap order-placing trades per cycle + honour a minimum hold
    # period. Mandatory risk exits bypass both. ADOPT/SET_STOP are store-only.
    surfaced, deferred = hb.prioritize_actions(assessed)

    new_pending: dict[str, dict] = {}
    for row in surfaced:
        h = row["holding"]
        new_pending[h["ticker"]] = {
            "id": f"{h['ticker']}_{int(time.time()*1000)}",
            "ticker": h["ticker"], "broker": broker, "action": row["action"],
            "holding": h, "status": "pending", "created_at": now,
        }
    deferred_summary = [
        {"ticker": d["holding"]["ticker"], "kind": d["action"]["kind"],
         "conviction": d["action"].get("conviction"),
         "holding": d.get("holding"),
         "reason": d.get("defer_reason", "")}
        for d in deferred
    ]
    _save_proposals(email, (list(new_pending.values()) + existing)[:200], deferred=deferred_summary)
    # Seamless approval: text the user a trade request + deep link to approve.
    if new_pending:
        await _notify_brain_pending(email, list(new_pending.values()))
    # Takeover triage summary: on first contact, which holdings the brain proposes
    # to KEEP (adopt) vs DROP (full exit). Only meaningful when takeover is enabled.
    adopt_t = {h.ticker for h in recon.to_adopt}
    keep = [r["holding"]["ticker"] for r in assessed
            if r["holding"]["ticker"] in adopt_t and r["action"]["kind"] == hb.ACTION_ADOPT]
    drop = [r["holding"]["ticker"] for r in assessed
            if r["holding"]["ticker"] in adopt_t and "takeover_drop" in (r["action"].get("risk_flags") or [])]
    return {
        "assessed": len(assessed),
        "proposals_pending": len(new_pending),
        "deferred": deferred_summary,
        "max_trades_per_cycle": hb._max_trades_per_cycle(),
        "min_hold_days": hb._min_hold_days(),
        "closed_detected": recon.closed,
        "to_adopt": [h.ticker for h in recon.to_adopt],
        "takeover_active": hb._takeover_enabled(),
        "keep": keep,
        "drop": drop,
        "ai_used": bool(use_ai and _make_llm_fn() is not None),
    }


async def run_exit_guard(email: str, broker: str = "fidelity") -> list[dict]:
    """Fast stop/target check on managed holdings. Raises *priority* EXIT proposals
    on breach (human still approves with step-up 2FA — never auto-fires)."""
    import asyncio
    store = hb.load_store(email, base_dir=TMP)
    if not store:
        return []
    holdings = await _read_holdings(email, broker)
    loop = asyncio.get_running_loop()
    quotes = await loop.run_in_executor(None, _trusted_quotes, [h.ticker for h in holdings])
    # Reload after the awaits to shrink the load→await→save race window with
    # run_brain_cycle; the ratchet is monotonic + idempotent so applying it to
    # the freshest copy is always safe.
    store = hb.load_store(email, base_dir=TMP)
    # Real ATR, not the synthetic 2%-of-price fallback — see _real_atr_map.
    atr_map = await loop.run_in_executor(None, _real_atr_map, holdings, quotes)
    raised = hb.ratchet_stops(holdings, store, quotes, atr_map=atr_map,
                              trail_atr_mult=_trail_ratchet_mult())
    if raised:
        hb.save_store(email, store, base_dir=TMP)
        for r in raised:
            if r.get("stop_raised"):
                log.info("exit-guard ratchet %s: stop %.2f -> %.2f (trail_high %.2f)",
                         r["ticker"], r["old_stop"], r["new_stop"], r["trail_high"])
    # Ratchet-then-check: a mid-interval round-trip is caught at the RAISED level
    # in the same pass (mirrors the paper book, where trail update and stop check
    # share a pass).
    breaches = hb.check_stops(holdings, store, quotes)
    if not breaches:
        return []

    proposals = _load_proposals(email)
    pending = {p["ticker"] for p in proposals if p.get("status") == "pending"}
    hmap = {h.ticker: h for h in holdings}
    now = dt.datetime.now().isoformat(timespec="seconds")
    for b in breaches:
        if b.ticker in pending:
            continue
        h = hmap.get(b.ticker)
        # A target is not a stop. The considered rule engine TRIMs 33% on
        # `target_reached` (holdings_brain.py:628-634); this fast guard used to
        # propose a 100% liquidation for the same event, and it beats the slow
        # loop into the queue. Combined with the guard's synthetic 2%-of-price
        # "ATR" that puts targets at ~+2.4%, it was proposing to dump the entire
        # real position after a 2.4% gain — the exact inverse of let-winners-run.
        # Only a genuine downside breach warrants a full exit.
        if b.reason == "target_hit":
            act_kind, act_fraction = hb.ACTION_TRIM, 0.33
        else:
            act_kind, act_fraction = hb.ACTION_EXIT, 1.0
        action = hb.Action(
            b.ticker, act_kind,
            reason=f"{b.reason}: price {b.price:.2f} crossed level {b.level:.2f}",
            fraction=act_fraction, risk_flags=[b.reason], source="exit_guard",
        ).to_dict()
        proposals.insert(0, {
            "id": f"{b.ticker}_{int(time.time()*1000)}",
            "ticker": b.ticker, "broker": broker, "action": action,
            "holding": h.to_dict() if h else {"ticker": b.ticker},
            "status": "pending", "created_at": now, "priority": True,
        })
    _save_proposals(email, proposals[:200])
    return [asdict(b) for b in breaches]


# ── endpoints ───────────────────────────────────────────────────────────────────
@router.get("/thematic/brain/holdings")
async def brain_holdings(
    broker: str = "fidelity",
    use_ai: bool = False,
    admin: dict = Depends(require_admin),
):
    """Read real holdings + a live (rule, or rule+AI) assessment for each. Read-only."""
    import asyncio
    email = admin["email"]
    b, raw_rows = await _read_raw_rows(email, broker)
    holdings = hb.normalize_holdings(raw_rows, b)
    excluded = hb.excluded_holdings(raw_rows, b)
    store = hb.load_store(email, base_dir=TMP)
    recon = hb.reconcile(holdings, store)
    ctx = await _build_ctx(holdings)

    loop = asyncio.get_running_loop()
    assessed = await loop.run_in_executor(None, _assess_all, holdings, store, ctx, use_ai)
    portfolio = hb.assess_portfolio(holdings, ctx)

    return {
        "ok": True,
        "broker": broker,
        "count": len(holdings),
        "regime": ctx["regime"],
        "portfolio": portfolio,
        "holdings": assessed,
        "to_adopt": [h.ticker for h in recon.to_adopt],
        "closed": recon.closed,
        # Protected/untouched positions (Roth IRA, money-market, mutual funds).
        "excluded": excluded,
        "ai_used": bool(use_ai and _make_llm_fn() is not None),
    }


@router.post("/thematic/brain/assess")
async def brain_assess(
    broker: str = "fidelity",
    use_ai: bool = True,
    admin: dict = Depends(require_admin),
):
    """Run one full brain cycle: read → reconcile → assess → write HIL proposals.

    Does NOT place any order. One pending proposal per ticker (newest wins).
    Auto-closes store plans for positions sold outside the system.
    """
    summary = await run_brain_cycle(admin["email"], broker=broker, use_ai=use_ai)
    return {"ok": True, "broker": broker, **summary}


@router.get("/thematic/brain/proposals")
async def brain_proposals(admin: dict = Depends(require_admin)):
    proposals = _load_proposals(admin["email"])
    pending = [p for p in proposals if p.get("status") == "pending"]
    return {"ok": True, "pending": pending, "count": len(pending),
            "deferred": _load_deferred(admin["email"]),
            "history": [p for p in proposals if p.get("status") != "pending"][:50]}


class ApproveBrainBody(BaseModel):
    execute: bool = False          # False = compliance preview only (no order placed)
    account: Optional[str] = None  # Fidelity account number (None = default)
    dollar_amount: Optional[float] = None  # ADD override; else derived from fraction


def _live_exit_auto_enabled() -> bool:
    return env_bool("THEMATIC_LIVE_EXIT_AUTONOMOUS", False)


def _live_exit_arm_path(email: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_.@-" else "_" for c in (email or "default").lower())
    return TMP / f"live_exit_arm_{safe}.json"


def _load_live_exit_arm(email: str) -> dict:
    path = _live_exit_arm_path(email)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if float(data.get("expires_at", 0) or 0) <= time.time():
            return {}
        return data
    except Exception:
        return {}


class ArmLiveExitBody(BaseModel):
    account: str
    ttl_minutes: int = 60


@router.post("/thematic/brain/live-exits/arm")
async def arm_autonomous_live_exits(
    body: ArmLiveExitBody,
    admin: dict = Depends(require_step_up),
):
    """Short-lived authorization for autonomous live Fidelity exits.

    This does not place an order. It only lets the separate background executor
    act on priority EXIT proposals created by the stop/crash guard, and only for
    the explicit Fidelity account supplied here.
    """
    from web.api.fidelity import _validate_account_number

    try:
        account = _validate_account_number(body.account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not account:
        raise HTTPException(status_code=400, detail="Autonomous live exits require an explicit Fidelity account")

    ttl = max(5, min(int(body.ttl_minutes or 60), 240))
    record = {
        "account": account,
        "armed_at": time.time(),
        "expires_at": time.time() + ttl * 60,
        "armed_by": admin["email"],
    }
    _atomic_write(_live_exit_arm_path(admin["email"]), record)
    return {"ok": True, "armed": True, "account": account[-4:], "ttl_minutes": ttl}


#: Flags that permit autonomous live execution. DELIBERATELY DISJOINT from what
#: `check_stops` emits ("stop_hit"/"target_hit"), so an exit-guard breach can
#: never auto-fire — the guard is propose-only and a human + step-up 2FA is
#: required. `tests/test_p1_exit_guard_trailing.py::test_no_autonomous_eligible_output`
#: pins that boundary; do not "fix" the mismatch without an explicit decision to
#: enable autonomous real-money selling. `_warn_if_armed_but_inert` keeps the
#: consequence visible rather than silent.
AUTO_LIVE_EXIT_FLAGS = frozenset({
    "below_managed_stop", "regime_crash_risk", "trailing_stop_breach", "target_breach",
})


def _proposal_is_auto_live_exit_eligible(prop: dict) -> bool:
    action = prop.get("action") or {}
    flags = set(action.get("risk_flags") or [])
    return bool(
        prop.get("status") == "pending"
        and prop.get("priority") is True
        and action.get("kind") == hb.ACTION_EXIT
        and action.get("source") == "exit_guard"
        and (flags & AUTO_LIVE_EXIT_FLAGS)
    )


async def run_autonomous_live_exit_executor(email: str, broker: str = "fidelity") -> list[dict]:
    """Execute only armed, priority exit-guard proposals. This is deliberately
    separate from run_exit_guard(), which remains propose-only."""
    if not _live_exit_auto_enabled():
        return []
    if broker != "fidelity":
        return []
    arm = _load_live_exit_arm(email)
    account_raw = arm.get("account")
    if not account_raw:
        return []

    from web.api.fidelity import (
        FidelityExitRequest,
        _validate_account_number,
        _fidelity_thematic_exit_inner,
        _get_order_lock,
    )
    from tradingagents.compliance import market_sell_allowed

    try:
        account = _validate_account_number(str(account_raw))
    except ValueError as e:
        log.warning("auto live exit arm invalid for %s: %s", email[:16], e)
        return []
    if not account:
        return []

    proposals = _load_proposals(email)
    eligible = [p for p in proposals if _proposal_is_auto_live_exit_eligible(p)]
    if not eligible:
        # Armed, but nothing can qualify. The operator completed step-up 2FA and
        # believes stop breaches now fire autonomously — say plainly that they do
        # not, rather than returning an empty list that reads as "all clear".
        _blocked = [
            p for p in proposals
            if p.get("status") == "pending" and p.get("priority") is True
            and (p.get("action") or {}).get("source") == "exit_guard"
        ]
        if _blocked:
            log.warning(
                "AUTO-LIVE-EXIT ARMED BUT INERT for %s: %d priority exit-guard "
                "proposal(s) pending, 0 eligible. The guard emits %s while "
                "autonomous execution requires %s — by design, the exit guard is "
                "propose-only and needs human approval + step-up 2FA. These stops "
                "will NOT self-execute.",
                email[:16], len(_blocked),
                sorted({f for p in _blocked
                        for f in ((p.get("action") or {}).get("risk_flags") or [])}),
                sorted(AUTO_LIVE_EXIT_FLAGS),
            )
        return []

    max_orders = max(1, min(int(float(os.getenv("THEMATIC_LIVE_EXIT_MAX_ORDERS", "3"))), 10))
    executed: list[dict] = []
    admin = {"email": email}
    for prop in eligible[:max_orders]:
        ticker = prop["ticker"]
        action = prop.get("action") or {}
        flags = set(action.get("risk_flags") or [])
        urgent = bool(flags & {"regime_crash_risk", "below_managed_stop"})
        try:
            limit_pct = max(-5.0, min(0.0, float(os.getenv("THEMATIC_LIVE_EXIT_LIMIT_PCT", "-2.0"))))
        except ValueError:
            limit_pct = -2.0
        order_type = "Market" if (urgent and market_sell_allowed()) else "Limit"
        body = FidelityExitRequest(
            ticker=ticker,
            shares=None,
            account=account,
            execute=True,
            limit_pct=limit_pct,
            order_type=order_type,
        )
        lock = _get_order_lock(f"{email}:auto-exit:{ticker}")
        if lock.locked():
            continue
        try:
            async with lock:
                res = await _fidelity_thematic_exit_inner(body, admin, ticker, account)
            prop["status"] = "executed"
            prop["auto_executed"] = True
            prop["executed_at"] = dt.datetime.now().isoformat(timespec="seconds")
            prop["execution"] = res
            executed.append({"ticker": ticker, "result": res})
        except Exception as e:
            prop["last_auto_error"] = str(e)
            prop["last_auto_error_at"] = dt.datetime.now().isoformat(timespec="seconds")
            log.warning("autonomous live exit failed %s for %s: %s", ticker, email[:16], e)
    if executed:
        _save_proposals(email, proposals)
    return executed


@router.post("/thematic/brain/proposals/{proposal_id}/approve")
async def brain_approve(
    proposal_id: str,
    body: ApproveBrainBody,
    request: Request,
    admin: dict = Depends(require_admin),
):
    """Approve one proposal.

    Store-only actions (ADOPT/SET_STOP/HOLD) just update the managed plan — no
    money moves, so they only need an authenticated admin (no step-up friction).
    Order actions (EXIT/TRIM/ADD) that actually execute route through the
    compliance-gated Fidelity path and require step-up 2FA, enforced below.
    """
    from web.auth import enforce_step_up

    email = admin["email"]
    proposals = _load_proposals(email)
    prop = next((p for p in proposals if p["id"] == proposal_id and p.get("status") == "pending"), None)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found or not pending")

    action = prop["action"]
    holding = prop.get("holding", {})
    kind = action["kind"]
    broker = prop.get("broker", "fidelity")
    store = hb.load_store(email, base_dir=TMP)
    result: dict = {"kind": kind, "ticker": prop["ticker"]}

    # Step-up 2FA only when a real order will be placed (EXIT/TRIM/ADD + execute).
    if kind in hb.ORDER_ACTIONS and body.execute:
        await enforce_step_up(request, admin)

    # ── Store-only actions ─────────────────────────────────────────────────────
    if kind in (hb.ACTION_ADOPT, hb.ACTION_SET_STOP, hb.ACTION_HOLD):
        plan = store.get(prop["ticker"]) or {}
        if kind == hb.ACTION_ADOPT or not plan:
            h = hb.Holding(**{k: holding[k] for k in (
                "ticker", "shares", "avg_cost", "last", "market_value",
                "pct_of_account", "unrealized_pct", "broker", "name") if k in holding})
            plan = hb.adopt_plan(hb.Action(**{**action, "ticker": prop["ticker"]}), h,
                                 theme=(plan.get("theme") or "unclassified"))
        if action.get("stop") is not None:
            plan["stop"] = action["stop"]
        if action.get("target") is not None:
            plan["target"] = action["target"]
        plan["status"] = "managed"
        plan["last_assessment_ts"] = dt.datetime.now().isoformat(timespec="seconds")
        store[prop["ticker"]] = plan
        hb.save_store(email, store, base_dir=TMP)
        result["stored"] = True
        prop["status"] = "executed"
        _save_proposals(email, proposals)
        return {"ok": True, **result}

    # ── Order actions (EXIT / TRIM / ADD) ──────────────────────────────────────
    if broker != "fidelity":
        raise HTTPException(status_code=501,
                            detail="Live execution via Webull is not yet supported (Fidelity only).")

    from web.api.fidelity import (
        FidelityExitRequest, FidelityThematicTradeRequest,
        _resolve_trade_account, _fidelity_thematic_exit_inner,
        _fidelity_thematic_trade_inner, _get_order_lock,
    )
    ticker = prop["ticker"]
    # Resolve request account → env FIDELITY_TRADE_ACCOUNT. The HIL frontend
    # posts {execute} with no account, and FIDELITY_REQUIRE_EXPLICIT_ACCOUNT
    # strict mode (default on with a protected list) refuses account-less
    # orders — without this fallback every brain EXIT/TRIM/ADD approval (and
    # preview) dies with a 403. Protected accounts still 403 here, up-front.
    try:
        account = _resolve_trade_account(body.account)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if kind in (hb.ACTION_EXIT, hb.ACTION_TRIM):
        shares = None  # EXIT → inner looks up full Fidelity share count
        if kind == hb.ACTION_TRIM:
            snap_shares = float(holding.get("shares", 0) or 0)
            shares = max(1, int(snap_shares * float(action.get("fraction", 0.33) or 0.33)))
        # Marketable-limit pricing: sell limit set BELOW market so it fills now.
        # Urgent exits (crash / below-stop) price aggressively; normal trims tight.
        # Stays within the limit-only compliance gate while behaving like a market
        # sell, with the negative %% acting as a slippage cap.
        urgent = bool(set(action.get("risk_flags") or []) & {"regime_crash_risk", "below_managed_stop"})

        def _f(env, default):
            try:
                return max(-5.0, min(0.0, float(os.getenv(env, default))))
            except ValueError:
                return float(default)
        limit_pct = _f("HOLDINGS_BRAIN_URGENT_LIMIT_PCT", "-2.0") if urgent \
            else _f("HOLDINGS_BRAIN_TRIM_LIMIT_PCT", "-0.5")
        # Urgent exits → true Market sell when ALLOW_MARKET_SELL is enabled (else
        # the aggressive marketable limit above). Normal trims always use Limit.
        from tradingagents.compliance import market_sell_allowed
        order_type = "Market" if (urgent and market_sell_allowed()) else "Limit"
        exit_body = FidelityExitRequest(
            ticker=ticker, shares=shares, account=account, execute=body.execute,
            limit_pct=limit_pct, order_type=order_type,
        )
        lock = _get_order_lock(f"{email}:exit:{ticker}")
        if lock.locked():
            raise HTTPException(status_code=429, detail=f"Exit for {ticker} already in progress.")
        async with lock:
            exec_result = await _fidelity_thematic_exit_inner(exit_body, admin, ticker, account)
        result["execution"] = exec_result
        result["urgent"] = urgent
        result["order_type"] = order_type
        result["limit_pct"] = None if order_type == "Market" else limit_pct

    elif kind == hb.ACTION_ADD:
        dollar = body.dollar_amount
        if dollar is None:
            mv = float(holding.get("market_value", 0) or 0)
            dollar = round(mv * float(action.get("fraction", 0.1) or 0.1), 2)
        if dollar <= 0:
            raise HTTPException(status_code=400, detail="ADD requires a positive dollar amount")
        trade_body = FidelityThematicTradeRequest(
            ticker=ticker, dollar_amount=dollar,
            stop_pct=5.0, target_pct=10.0,
            account=body.account, execute=body.execute, also_paper_trade=False,
        )
        lock = _get_order_lock(f"{email}:{ticker}")
        if lock.locked():
            raise HTTPException(status_code=429, detail=f"Order for {ticker} already in progress.")
        async with lock:
            exec_result = await _fidelity_thematic_trade_inner(trade_body, admin, ticker, account)
        result["execution"] = exec_result
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action kind: {kind}")

    # Mark executed only when a real order was placed; previews stay pending.
    if body.execute:
        prop["status"] = "executed"
        # Update the managed plan's stop/target snapshot for the exit guard.
        plan = store.get(ticker) or {}
        if action.get("stop") is not None:
            plan["stop"] = action["stop"]
        if action.get("target") is not None:
            plan["target"] = action["target"]
        if plan:
            plan["status"] = "managed"
            store[ticker] = plan
            hb.save_store(email, store, base_dir=TMP)
    _save_proposals(email, proposals)
    return {"ok": True, "previewed_only": not body.execute, **result}


@router.post("/thematic/brain/proposals/{proposal_id}/skip")
async def brain_skip(proposal_id: str, admin: dict = Depends(require_admin)):
    email = admin["email"]
    proposals = _load_proposals(email)
    prop = next((p for p in proposals if p["id"] == proposal_id), None)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")
    prop["status"] = "skipped"
    _save_proposals(email, proposals)
    return {"ok": True, "id": proposal_id}
