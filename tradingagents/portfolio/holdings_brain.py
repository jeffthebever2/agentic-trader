"""Holdings Brain — AI-assisted management of an existing brokerage account.

This module is the decision core behind the human-in-the-loop (HIL) management of
*real* Fidelity/Webull holdings — including positions that were already in the
account before the brain ever got access ("adopt existing stocks").

Design principles (this is a REAL-MONEY decision path):
  * Pure logic. No FastAPI, no Playwright, no broker side effects, no network in
    the default path → fully unit-testable and deterministic.
  * The deterministic **rule engine** is the safety floor. It always produces a
    valid, compliance-respecting action even when no LLM is available.
  * The LLM is *augmentation*, injected as ``llm_fn``. Its suggestion is clamped
    back to the same guardrails (no prohibited actions, concentration cap,
    bounded trim fraction). If it fails or is absent, the rule action stands.
  * Nothing here places an order. It only proposes. Execution + compliance +
    step-up 2FA live in the web/broker layer.

Reused building blocks:
  * ``tradingagents.portfolio.exit_manager.ExitManager`` — ATR stop/target math.
  * ``tradingagents.compliance.MAX_POSITION_PCT_OF_ACCOUNT`` — concentration cap.

Typical flow (driven by web/api/holdings_brain.py):
    holdings = normalize_holdings(raw_rows, broker="fidelity")
    store    = load_store(email)
    diff     = reconcile(holdings, store)
    for h in holdings:
        action = assess_holding(h, store.get(h.ticker), ctx, llm_fn=my_llm)
    breaches = check_stops(holdings, store, quotes)
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tradingagents.portfolio.exit_manager import ExitManager

try:
    from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT
except Exception:  # pragma: no cover - compliance always importable in practice
    MAX_POSITION_PCT_OF_ACCOUNT = 10.0


# ── Action vocabulary ──────────────────────────────────────────────────────────
ACTION_HOLD = "HOLD"
ACTION_TRIM = "TRIM"
ACTION_ADD = "ADD"
ACTION_EXIT = "EXIT"
ACTION_SET_STOP = "SET_STOP"
ACTION_ADOPT = "ADOPT"

VALID_ACTIONS = frozenset(
    {ACTION_HOLD, ACTION_TRIM, ACTION_ADD, ACTION_EXIT, ACTION_SET_STOP, ACTION_ADOPT}
)
# Actions that place a live order when approved (used by the web layer to decide
# whether step-up 2FA + compliance must run). SET_STOP/ADOPT/HOLD are store-only.
ORDER_ACTIONS = frozenset({ACTION_TRIM, ACTION_ADD, ACTION_EXIT})

# Trim can never sell more than this fraction in one proposal (humans can repeat).
MAX_TRIM_FRACTION = 0.50
# Default ATR proxy when no price history is available (matches ExitManager).
_ATR_FALLBACK_PCT = 0.02


# ── Takeover triage config (full-account management) ────────────────────────────
# When the brain takes control of an existing account it must decide, per holding,
# whether to KEEP (adopt + manage) or DROP (propose a full exit) so the account is
# reshaped to a thematic-conviction-worthy core before new thematic buys are added.
# OFF by default → first contact just ADOPTs everything (the prior, conservative
# behaviour). Turn on with HOLDINGS_BRAIN_TAKEOVER=true.
def _takeover_enabled() -> bool:
    return os.getenv("HOLDINGS_BRAIN_TAKEOVER", "false").strip().lower() == "true"


def _takeover_strict() -> bool:
    """Strict: drop holdings with NO thematic conviction signal (not a current theme).
    Lenient (default): keep unknowns, only drop low-conviction or deep losers."""
    return os.getenv("HOLDINGS_BRAIN_TAKEOVER_STRICT", "false").strip().lower() == "true"


def _keep_min_conviction() -> int:
    try:
        return max(1, min(10, int(os.getenv("HOLDINGS_BRAIN_KEEP_MIN_CONVICTION", "5"))))
    except ValueError:
        return 5


def _takeover_drop_loss_pct() -> float:
    """Unrealized loss at/below which a holding is dropped on takeover regardless
    of thematic conviction (negative number, e.g. -25.0)."""
    try:
        return float(os.getenv("HOLDINGS_BRAIN_TAKEOVER_DROP_LOSS_PCT", "-25"))
    except ValueError:
        return -25.0


def _concentration_ceiling(conviction: int, base_cap: float) -> float:
    """Conviction-aware concentration ceiling for TRIM decisions.

    Trimming a high-conviction name (one the system *wants* to hold/buy) down to a
    flat base cap contradicts that conviction. So high conviction tolerates a
    larger position before trimming: conv≤5 → base_cap; scales linearly to
    ``HOLDINGS_BRAIN_CONC_MAX_PCT`` (default 25%) at conviction 10. Compliance's
    10% cap still governs NEW buys; this only relaxes when to TRIM an *existing*
    (appreciated) holding — never a compliance bypass.
    """
    import math
    try:
        abs_max = float(os.getenv("HOLDINGS_BRAIN_CONC_MAX_PCT", "25"))
    except ValueError:
        abs_max = 25.0
    if not math.isfinite(abs_max):
        abs_max = 25.0
    try:
        base_cap = float(base_cap)
    except (TypeError, ValueError):
        base_cap = 0.0
    if not math.isfinite(base_cap) or base_cap < 0:
        base_cap = 0.0
    abs_max = max(base_cap, abs_max)
    # Conviction may arrive malformed (None / NaN / "n/a") from an LLM pick — coerce
    # to the [1,10] band rather than crashing the whole trim decision.
    try:
        c = int(conviction)
    except (TypeError, ValueError):
        c = 1
    c = max(1, min(10, c))
    if c <= 5:
        return base_cap
    return round(base_cap + (abs_max - base_cap) * (c - 5) / 5.0, 2)


def _min_stop_distance_pct(conviction: int) -> float:
    """Minimum stop distance below price, conviction-aware. A high-conviction hold
    needs ROOM (don't get shaken out on noise); a tight ATR/fallback stop ~1% below
    price would exit a winner on a normal wiggle. conv≤5 → base; widens to base+8pp
    at conviction 10. Base from env HOLDINGS_BRAIN_MIN_STOP_PCT (default 8%)."""
    try:
        base = float(os.getenv("HOLDINGS_BRAIN_MIN_STOP_PCT", "8"))
    except ValueError:
        base = 8.0
    if not math.isfinite(base) or base < 0:
        base = 8.0
    # Conviction may arrive malformed (None / NaN / "n/a") — coerce to [1,10]
    # rather than crashing stop-setting and leaving a holding unprotected.
    try:
        c = int(conviction)
    except (TypeError, ValueError):
        c = 1
    c = max(1, min(10, c))
    return round(base + max(0, c - 5) * 1.6, 2)  # conv5→base, conv10→base+8


def _adoption_stop(price: float, conviction: int, atr_stop: float) -> float:
    """Protective stop for a newly-managed holding: never tighter than the
    conviction-aware minimum distance (wider/lower wins → more room to run).

    A NaN slips past ``price <= 0`` (NaN compares False), so guard non-finite
    inputs explicitly — a NaN protective stop would never fire correctly and
    leave a holding effectively unprotected. Never returns a non-finite value."""
    def _finite_pos(x: object) -> bool:
        return isinstance(x, (int, float)) and math.isfinite(x) and x > 0
    if not _finite_pos(price):
        return atr_stop if _finite_pos(atr_stop) else 0.0
    floor = round(price * (1 - _min_stop_distance_pct(conviction) / 100.0), 4)
    if not _finite_pos(atr_stop):
        return floor
    # min → further below price → wider stop → less likely to shake out a winner.
    return min(atr_stop, floor)


def takeover_verdict(holding: "Holding", ctx: dict) -> tuple[str, int, str]:
    """Decide KEEP vs DROP for a newly-encountered holding on account takeover.

    Returns ``(verdict, conviction, reason)`` where verdict ∈ {"KEEP","DROP"}.
    Pure + deterministic; thematic conviction comes from ``ctx['social_scores']``.
    """
    tc = _social_score(holding, ctx)          # 0..10 or None
    keep_min = _keep_min_conviction()
    drop_loss = _takeover_drop_loss_pct()

    # Deep loser → cut, regardless of theme (capital preservation).
    if holding.unrealized_pct <= drop_loss:
        return ("DROP", 3, f"Deep loss {holding.unrealized_pct:+.1f}% (≤ {drop_loss:.0f}%) — cut on takeover.")

    if tc is not None:
        conv = int(round(max(1.0, min(10.0, tc))))
        if tc < keep_min:
            return ("DROP", conv,
                    f"Low thematic conviction {tc:.1f}/10 (< keep-min {keep_min}) — drop to free capital.")
        return ("KEEP", conv, f"Thematic conviction {tc:.1f}/10 ≥ {keep_min} — keep and manage.")

    # No thematic signal for this ticker.
    if _takeover_strict():
        return ("DROP", 4, "No thematic conviction signal — not a current theme; drop on takeover.")
    # Lenient: keep, conviction from performance (mirrors prior ADOPT defaulting).
    conv = 7 if holding.unrealized_pct >= 15 else 4 if holding.unrealized_pct <= -15 else 6
    return ("KEEP", conv, "No thematic signal — keeping (lenient takeover); managed with a protective stop.")


# ── Data shapes ────────────────────────────────────────────────────────────────
@dataclass
class Holding:
    """A normalized broker position. ``shares``/``avg_cost``/``last`` come from the
    broker (source of truth); everything else is derived."""

    ticker: str
    shares: float
    avg_cost: float
    last: float
    market_value: float
    pct_of_account: float
    unrealized_pct: float
    broker: str = "fidelity"
    name: str = ""
    account_number: str = ""
    account_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Action:
    """A proposed management action for one holding. Advisory until a human approves."""

    ticker: str
    kind: str                       # one of VALID_ACTIONS
    reason: str = ""
    fraction: float = 0.0           # for TRIM/ADD: fraction of position (0..1)
    stop: Optional[float] = None
    target: Optional[float] = None
    conviction: int = 5             # 1..10
    risk_flags: List[str] = field(default_factory=list)
    source: str = "rule"            # "rule" | "llm" | "llm+rule"

    def places_order(self) -> bool:
        return self.kind in ORDER_ACTIONS

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReconcileResult:
    """Diff between broker reality and the brain's managed store."""

    to_adopt: List[Holding] = field(default_factory=list)   # in broker, not managed
    tracked: List[Holding] = field(default_factory=list)    # in both, managed
    closed: List[str] = field(default_factory=list)         # managed, gone from broker


@dataclass
class StopBreach:
    ticker: str
    reason: str           # "stop_hit" | "target_hit" | "trailing_stop"
    price: float
    level: float          # the stop/target that was crossed
    unrealized_pct: float


# ── Parsing helpers (broker rows are messy strings) ─────────────────────────────
def _parse_num(raw: Any) -> float:
    """Parse '$1,234.56', '+5.2%', '(12.30)', '10' → float. Empty/garbage → 0.0."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "")
    s = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip()
    if s in ("", "-", "--", "—", "N/A", "n/a", "Not Priced"):
        return 0.0
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return -val if neg else val


# ── Account / instrument protection (REAL-MONEY SAFETY) ─────────────────────────
# The brain manages a *taxable, self-directed equity* account only. Retirement and
# tax-advantaged accounts are NEVER touched: wash-sale/contribution/withdrawal rules
# make automated trading inappropriate, and the user explicitly protects them.
# These patterns are matched case-insensitively as substrings of the account name.
PROTECTED_ACCOUNT_PATTERNS = (
    "roth", "ira", "401k", "401(k)", "403b", "403(b)", "457", "sep-ira", "sep ira",
    "simple ira", "retirement", "rollover", "hsa", "529", "pension", "annuity",
    "beneficiary", "inherited",
)


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def is_protected_account(account_name: str = "", account_number: str = "") -> bool:
    """True if this account must never be traded by the brain.

    Default-deny for retirement/tax-advantaged accounts (pattern match on name).
    Extra patterns can be added via env ``HOLDINGS_BRAIN_PROTECTED_ACCOUNTS``.
    An optional allow-list of account *numbers*
    (``HOLDINGS_BRAIN_ALLOWED_ACCOUNTS``) flips to default-deny: when set, ONLY
    those account numbers are tradeable and everything else is protected.
    """
    name = (account_name or "").strip().lower()
    number = (account_number or "").strip()

    allowed = _env_list("HOLDINGS_BRAIN_ALLOWED_ACCOUNTS")
    if allowed:
        # Whitelist mode: protect anything not explicitly allowed.
        return number not in allowed

    patterns = list(PROTECTED_ACCOUNT_PATTERNS) + [p.lower() for p in _env_list("HOLDINGS_BRAIN_PROTECTED_ACCOUNTS")]
    return any(p in name for p in patterns)


# Money-market & mutual-fund symbols are not limit-orderable equities and must not
# be managed. Fidelity money-market tickers end in "XX" (SPAXX, FDRXX, FZFXX…);
# open-end mutual funds are 5-letter symbols ending in "X" (FSPGX, FXAIX…).
_NON_EQUITY_NAME_KEYWORDS = (
    "money market", "mutual fund", "index fund", " fund", "bond fund",
    "treasury", "held in money", "cash reserves",
)


def is_non_equity_symbol(ticker: str, name: str = "") -> bool:
    """True for money-market / mutual-fund holdings the brain must not trade."""
    t = (ticker or "").upper().strip()
    n = (name or "").lower()
    if any(k in n for k in _NON_EQUITY_NAME_KEYWORDS):
        return True
    if t.endswith("XX"):  # money-market sweep funds
        return True
    if len(t) == 5 and t.endswith("X"):  # open-end mutual funds (incl. index funds)
        return True
    return False


# ── Normalization ───────────────────────────────────────────────────────────────
def normalize_holdings(
    raw: List[dict],
    broker: str,
    *,
    exclude_protected_accounts: bool = True,
    exclude_non_equity: bool = True,
) -> List[Holding]:
    """Adapt a broker's position rows to a uniform list of ``Holding``.

    Parameters
    ----------
    raw : list of dict
        For ``fidelity``: the ``positions`` list from ``GET /fidelity/positions``.
        For ``webull``: the ``positions`` list from ``GET /webull/positions``.
    broker : str
        ``"fidelity"`` or ``"webull"``.
    """
    broker = (broker or "").lower()
    out: List[Holding] = []

    for row in raw or []:
        acct_num = str(row.get("account_number") or row.get("account") or "").strip()
        acct_name = str(row.get("account_name") or "").strip()
        if broker == "webull":
            ticker = str(row.get("symbol", "")).upper().strip()
            shares = _parse_num(row.get("qty"))
            avg_cost = _parse_num(row.get("cost_price"))
            last = _parse_num(row.get("last_price"))
            mv = _parse_num(row.get("market_value")) or (shares * last)
            unrl = _parse_num(row.get("unrealized_pnl_pct"))
            pct_acct = _parse_num(row.get("pct_of_account"))
            name = str(row.get("name", "")).strip()
        else:  # fidelity (default)
            ticker = str(row.get("symbol") or row.get("ticker", "")).upper().strip()
            shares = _parse_num(row.get("qty") or row.get("shares"))
            avg_cost = _parse_num(row.get("cost_per_share"))
            last = _parse_num(row.get("last_price"))
            mv = _parse_num(row.get("market_value")) or (shares * last)
            unrl = _parse_num(row.get("total_gain_pct"))
            pct_acct = _parse_num(row.get("pct_of_account"))
            name = str(row.get("description", "")).strip()
            # Fidelity sometimes omits cost basis on adopted lots; derive if possible.
            if avg_cost <= 0 and unrl != 0 and last > 0:
                # last = avg_cost * (1 + unrl/100)  →  avg_cost = last / (1+unrl/100)
                denom = 1.0 + unrl / 100.0
                if denom > 0:
                    avg_cost = round(last / denom, 4)

        # ── REAL-MONEY SAFETY: never surface protected accounts or non-equities ──
        if exclude_protected_accounts and is_protected_account(acct_name, acct_num):
            continue
        if exclude_non_equity and is_non_equity_symbol(ticker, name):
            continue

        if not ticker or not ticker.isalpha() or len(ticker) > 5:
            continue
        if shares <= 0:
            continue
        if last <= 0 and avg_cost > 0:
            last = avg_cost  # avoid div-by-zero downstream; flagged later
        if unrl == 0 and avg_cost > 0 and last > 0:
            unrl = round((last - avg_cost) / avg_cost * 100, 2)

        out.append(
            Holding(
                ticker=ticker,
                shares=shares,
                avg_cost=avg_cost,
                last=last,
                market_value=round(mv, 2),
                pct_of_account=round(pct_acct, 2),
                unrealized_pct=round(unrl, 2),
                broker=broker or "fidelity",
                name=name,
                account_number=acct_num,
                account_name=acct_name,
            )
        )
    return out


def excluded_holdings(raw: List[dict], broker: str) -> List[dict]:
    """Report rows the brain refuses to manage and why (protected account or
    non-equity instrument). For UI transparency — purely informational."""
    broker = (broker or "").lower()
    out: List[dict] = []
    for row in raw or []:
        acct_num = str(row.get("account_number") or row.get("account") or "").strip()
        acct_name = str(row.get("account_name") or "").strip()
        if broker == "webull":
            ticker = str(row.get("symbol", "")).upper().strip()
            name = str(row.get("name", "")).strip()
        else:
            ticker = str(row.get("symbol") or row.get("ticker", "")).upper().strip()
            name = str(row.get("description", "")).strip()
        reason = None
        if is_protected_account(acct_name, acct_num):
            reason = f"protected account ({acct_name or acct_num or 'unnamed'})"
        elif is_non_equity_symbol(ticker, name):
            reason = "non-equity instrument (money-market / mutual fund)"
        if reason:
            out.append({"symbol": ticker, "account_name": acct_name,
                        "account_number": acct_num, "reason": reason})
    return out


# ── Reconciliation (broker is the source of truth) ──────────────────────────────
_ACTIVE_STATUSES = frozenset({"adopted", "managed"})


def reconcile(broker_holdings: List[Holding], store: Dict[str, dict]) -> ReconcileResult:
    """Diff broker reality vs the managed store.

    A plan in the store with an active status (``adopted``/``managed``) whose ticker
    is no longer in the broker means the human sold it outside the system → ``closed``.
    A broker holding with no active plan → ``to_adopt``.
    """
    broker_by_ticker = {h.ticker: h for h in broker_holdings}
    store = store or {}

    res = ReconcileResult()
    for ticker, holding in broker_by_ticker.items():
        plan = store.get(ticker)
        if plan and str(plan.get("status")) in _ACTIVE_STATUSES:
            res.tracked.append(holding)
        else:
            res.to_adopt.append(holding)

    for ticker, plan in store.items():
        if str(plan.get("status")) in _ACTIVE_STATUSES and ticker not in broker_by_ticker:
            res.closed.append(ticker)

    return res


# ── Rule engine (the deterministic safety floor) ────────────────────────────────
def _atr_for(holding: Holding, ctx: dict) -> float:
    """Best-effort ATR: ctx['atr'][ticker] if provided, else 2% of last price."""
    atr_map = (ctx or {}).get("atr") or {}
    atr = _parse_num(atr_map.get(holding.ticker))
    if atr > 0:
        return atr
    return max(holding.last * _ATR_FALLBACK_PCT, 0.01)


def _exit_levels(entry: float, atr: float, conviction: int) -> Any:
    """Compute stop/target via the shared ExitManager (single source of math)."""
    em = ExitManager()
    return em.calculate(
        entry_price=entry,
        atr=atr,
        ml_probability=min(max(conviction / 10.0, 0.5), 0.85),
        direction="long",
    )


def _regime(ctx: dict) -> dict:
    return (ctx or {}).get("regime") or {}


def _social_score(holding: Holding, ctx: dict) -> Optional[float]:
    scores = (ctx or {}).get("social_scores") or {}
    if holding.ticker in scores:
        return _parse_num(scores.get(holding.ticker))
    return None


def _rule_assess(holding: Holding, plan: Optional[dict], ctx: dict) -> Action:
    """Deterministic assessment. Always returns a compliance-respecting Action.

    Priority order (first match wins for the primary action):
      1. ADOPT      — no active plan yet (existing stock the brain just got access to)
      2. EXIT       — crash-risk regime, or deep loss past managed stop
      3. TRIM       — concentration over the compliance cap, or target reached
      4. SET_STOP   — winner with a stale/looser stop than a fresh trailing stop
      5. ADD        — conviction winner with room under the cap in a constructive regime
      6. HOLD       — nothing actionable
    """
    flags: List[str] = []
    regime = _regime(ctx)
    no_trade = bool(regime.get("no_trade"))
    crash = _parse_num(regime.get("crash_risk_score"))
    atr = _atr_for(holding, ctx)
    cap = float(MAX_POSITION_PCT_OF_ACCOUNT)

    if holding.last <= 0:
        flags.append("no_live_price")

    # ── 1. First contact — KEEP (adopt) or DROP (exit) ─────────────────────────
    if plan is None or str((plan or {}).get("status")) not in _ACTIVE_STATUSES:
        # Crash regime still overrides everything below.
        if no_trade or crash >= 0.85:
            flags.append("regime_crash_risk")
            return Action(
                holding.ticker, ACTION_EXIT,
                reason=f"Crash-risk regime (crash_score={crash:.2f}) — do not adopt; exit.",
                fraction=1.0, conviction=4, risk_flags=flags, source="rule",
            )

        if _takeover_enabled():
            verdict, conviction, reason = takeover_verdict(holding, ctx)
            if verdict == "DROP":
                flags.append("takeover_drop")
                return Action(
                    holding.ticker, ACTION_EXIT,
                    reason=reason, fraction=1.0, conviction=conviction,
                    risk_flags=flags, source="rule",
                )
        else:
            conviction = 6
            if holding.unrealized_pct >= 15:
                conviction = 7
            elif holding.unrealized_pct <= -15:
                conviction = 4

        lv = _exit_levels(holding.last or holding.avg_cost, atr, conviction)
        # Give the protective stop real room — never a hair-trigger ~1% stop that
        # exits a high-conviction winner on noise.
        adopt_stop = _adoption_stop(holding.last or holding.avg_cost, conviction, lv.stop_price)
        if holding.pct_of_account > cap:
            flags.append(f"over_concentration_{holding.pct_of_account:.0f}pct")
        return Action(
            ticker=holding.ticker,
            kind=ACTION_ADOPT,
            reason=(
                f"Keep under management ({holding.shares:g} sh, "
                f"{holding.unrealized_pct:+.1f}% unrealized, conviction {conviction}/10). "
                f"Proposed protective stop {adopt_stop:.2f} / target {lv.target_price:.2f}."
            ),
            stop=adopt_stop,
            target=lv.target_price,
            conviction=conviction,
            risk_flags=flags,
            source="rule",
        )

    # Plan exists from here on.
    p_stop = _parse_num(plan.get("stop"))
    p_target = _parse_num(plan.get("target"))
    p_conv = int(_parse_num(plan.get("conviction")) or 5)

    # ── 2. EXIT ────────────────────────────────────────────────────────────────
    if no_trade or crash >= 0.85:
        flags.append("regime_crash_risk")
        return Action(
            holding.ticker, ACTION_EXIT,
            reason=f"Crash-risk regime (crash_score={crash:.2f}) — protect capital.",
            fraction=1.0, conviction=p_conv, risk_flags=flags, source="rule",
        )
    if p_stop > 0 and holding.last > 0 and holding.last <= p_stop:
        flags.append("below_managed_stop")
        return Action(
            holding.ticker, ACTION_EXIT,
            reason=f"Price {holding.last:.2f} at/below managed stop {p_stop:.2f}.",
            fraction=1.0, stop=p_stop, conviction=p_conv, risk_flags=flags, source="rule",
        )

    # ── 3. TRIM ─────────────────────────────────────────────────────────────────
    # Conviction-aware ceiling: don't trim a name the system rates highly down to
    # a flat cap (that contradicts wanting it). High conviction tolerates more.
    ceiling = _concentration_ceiling(p_conv, cap)
    if holding.pct_of_account > ceiling + 1.0:  # 1pp tolerance to avoid churn
        # Trim back toward the conviction-adjusted ceiling.
        over = holding.pct_of_account - ceiling
        frac = min(MAX_TRIM_FRACTION, max(0.05, over / max(holding.pct_of_account, 1e-9)))
        flags.append(f"over_concentration_{holding.pct_of_account:.0f}pct")
        return Action(
            holding.ticker, ACTION_TRIM,
            reason=(
                f"Position is {holding.pct_of_account:.1f}% of account vs "
                f"conviction-{p_conv} ceiling {ceiling:.0f}% (base cap {cap:.0f}%). "
                f"Trim ~{frac*100:.0f}% to reduce concentration."
            ),
            fraction=round(frac, 3), conviction=p_conv, risk_flags=flags, source="rule",
        )
    if p_target > 0 and holding.last >= p_target:
        flags.append("target_reached")
        return Action(
            holding.ticker, ACTION_TRIM,
            reason=f"Target {p_target:.2f} reached at {holding.last:.2f} — take partial profit.",
            fraction=0.33, target=p_target, conviction=p_conv, risk_flags=flags, source="rule",
        )

    # ── 4. SET_STOP — ratchet a trailing stop up on a winner ────────────────────
    if holding.unrealized_pct >= 8 and holding.last > 0:
        em = ExitManager()
        trail = em.update_trailing_stop(
            current_stop=p_stop if p_stop > 0 else holding.last - atr,
            peak_price=max(holding.last, _parse_num(plan.get("trail_high")) or holding.last),
            atr=atr,
        )
        if trail > p_stop + 0.01:  # only if it actually raises the stop
            return Action(
                holding.ticker, ACTION_SET_STOP,
                reason=(
                    f"Up {holding.unrealized_pct:+.1f}% — raise trailing stop "
                    f"{p_stop:.2f} → {trail:.2f} to lock gains."
                ),
                stop=round(trail, 2), conviction=p_conv, risk_flags=flags, source="rule",
            )

    # ── 5. ADD — conservative: strong conviction, winning, room under cap ────────
    constructive = regime.get("regime") in (None, "bull", "uptrend", "crash_rebound", "unknown")
    if (
        p_conv >= 8
        and 0 <= holding.unrealized_pct < 25
        and holding.pct_of_account < cap - 2.0
        and constructive
    ):
        room = cap - holding.pct_of_account
        frac = min(0.25, max(0.05, room / max(holding.pct_of_account, 1e-9)))
        return Action(
            holding.ticker, ACTION_ADD,
            reason=(
                f"High conviction ({p_conv}/10), {holding.unrealized_pct:+.1f}%, "
                f"only {holding.pct_of_account:.1f}% of account — room to add ~{frac*100:.0f}%."
            ),
            fraction=round(frac, 3), conviction=p_conv, risk_flags=flags, source="rule",
        )

    # ── 6. HOLD ─────────────────────────────────────────────────────────────────
    return Action(
        holding.ticker, ACTION_HOLD,
        reason=f"Within plan ({holding.unrealized_pct:+.1f}%, {holding.pct_of_account:.1f}% of account).",
        conviction=p_conv, risk_flags=flags, source="rule",
    )


# ── LLM augmentation (optional, clamped back to guardrails) ──────────────────────
def build_assessment_prompt(holding: Holding, plan: Optional[dict], ctx: dict, rule_action: Action) -> str:
    """Build a compact prompt asking an LLM to confirm/refine the rule action."""
    regime = _regime(ctx)
    social = _social_score(holding, ctx)
    plan = plan or {}
    return (
        "You are a disciplined portfolio risk manager reviewing ONE existing stock "
        "position in a real brokerage account. Human approval is required before any "
        "trade, so be decisive but conservative.\n\n"
        f"Ticker: {holding.ticker} ({holding.name or 'n/a'})\n"
        f"Shares: {holding.shares:g}  AvgCost: {holding.avg_cost:.2f}  Last: {holding.last:.2f}\n"
        f"Unrealized: {holding.unrealized_pct:+.1f}%  PctOfAccount: {holding.pct_of_account:.1f}%\n"
        f"Existing thesis: {plan.get('thesis') or 'none (newly adopted)'}\n"
        f"Managed stop: {plan.get('stop') or 'none'}  target: {plan.get('target') or 'none'}\n"
        f"Market regime: {regime.get('regime', 'unknown')} "
        f"(crash_score={regime.get('crash_risk_score', 0)})\n"
        f"Social/buzz score (0-10): {social if social is not None else 'n/a'}\n"
        f"Rule-engine recommendation: {rule_action.kind} — {rule_action.reason}\n\n"
        "Respond ONLY with JSON (no markdown):\n"
        '{"action":"HOLD|TRIM|ADD|EXIT|SET_STOP|ADOPT","fraction":0.0,'
        '"conviction":1-10,"thesis":"one sentence","reason":"one sentence why"}'
    )


def _parse_llm_action(text: str, holding: Holding, rule_action: Action) -> Optional[Action]:
    """Parse + clamp an LLM JSON response into a safe Action. None on any problem."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None

    kind = str(data.get("action", "")).upper().strip()
    if kind not in VALID_ACTIONS:
        return None

    # Clamp fraction into safe bounds; TRIM is hard-capped.
    frac = 0.0
    try:
        frac = float(data.get("fraction", 0) or 0)
    except Exception:
        frac = 0.0
    # float("nan") does NOT raise, and max/min won't clamp a NaN — it would carry
    # through to an Action.fraction (a NaN trim size). Coerce non-finite to 0.
    if not math.isfinite(frac):
        frac = 0.0
    frac = max(0.0, min(1.0, frac))
    if kind == ACTION_TRIM:
        frac = min(frac or 0.33, MAX_TRIM_FRACTION)
    if kind == ACTION_ADD:
        frac = min(frac or 0.1, 0.25)

    try:
        conv = int(float(data.get("conviction", rule_action.conviction)))
    except Exception:
        conv = rule_action.conviction
    conv = max(1, min(10, conv))

    reason = str(data.get("reason") or data.get("thesis") or "").strip()[:280]

    # Preserve the rule engine's mechanical stop/target (deterministic math wins).
    return Action(
        ticker=holding.ticker,
        kind=kind,
        reason=reason or rule_action.reason,
        fraction=round(frac, 3),
        stop=rule_action.stop,
        target=rule_action.target,
        conviction=conv,
        risk_flags=list(rule_action.risk_flags),
        source="llm+rule",
    )


def assess_holding(
    holding: Holding,
    plan: Optional[dict],
    ctx: Optional[dict] = None,
    llm_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> Action:
    """Assess one holding. Returns the rule action, optionally refined by an LLM.

    Parameters
    ----------
    holding : Holding
    plan : dict | None
        The managed plan for this ticker from the store (None ⇒ adoption).
    ctx : dict
        Optional context: ``regime`` (dict), ``social_scores`` (ticker→0-10),
        ``atr`` (ticker→float).
    llm_fn : callable(prompt) -> str | None
        Optional synchronous LLM call. If it raises or returns unparseable text,
        the deterministic rule action is used unchanged.
    """
    ctx = ctx or {}
    rule_action = _rule_assess(holding, plan, ctx)

    if llm_fn is None:
        return rule_action

    # EXIT for crash-risk / below-stop is non-negotiable — don't let the LLM soften it.
    if rule_action.kind == ACTION_EXIT and (
        "regime_crash_risk" in rule_action.risk_flags
        or "below_managed_stop" in rule_action.risk_flags
    ):
        return rule_action

    try:
        prompt = build_assessment_prompt(holding, plan, ctx, rule_action)
        text = llm_fn(prompt)
    except Exception:
        return rule_action

    llm_action = _parse_llm_action(text or "", holding, rule_action)
    return llm_action or rule_action


# ── Portfolio-level posture ─────────────────────────────────────────────────────
def assess_portfolio(holdings: List[Holding], ctx: Optional[dict] = None) -> dict:
    """Portfolio-level risk read: concentration, regime, cash, net posture."""
    ctx = ctx or {}
    regime = _regime(ctx)
    cap = float(MAX_POSITION_PCT_OF_ACCOUNT)

    total_mv = sum(h.market_value for h in holdings)
    flags: List[str] = []

    over_cap = [h.ticker for h in holdings if h.pct_of_account > cap + 1.0]
    if over_cap:
        flags.append(f"concentration: {', '.join(over_cap)} over {cap:.0f}% cap")

    if bool(regime.get("no_trade")) or _parse_num(regime.get("crash_risk_score")) >= 0.85:
        flags.append("regime: crash-risk / no-trade — defensive posture")

    losers = [h for h in holdings if h.unrealized_pct <= -10]
    if len(losers) >= max(3, len(holdings) // 2) and holdings:
        flags.append(f"breadth: {len(losers)}/{len(holdings)} positions down >10%")

    posture = "neutral"
    if any(f.startswith("regime:") for f in flags):
        posture = "reduce_risk"
    elif over_cap:
        posture = "rebalance"

    return {
        "n_positions": len(holdings),
        "total_market_value": round(total_mv, 2),
        "regime": regime.get("regime", "unknown"),
        "posture": posture,
        "risk_flags": flags,
        "concentration_cap_pct": cap,
    }


# ── Trade-budget / churn control ────────────────────────────────────────────────
# A real account should not be churned: cap how many *order-placing* actions the
# brain proposes per cycle, and don't re-trade a position that was just entered.
# Risk exits (stop breach / crash) are MANDATORY and bypass both limits.
_MANDATORY_FLAGS = frozenset({"regime_crash_risk", "below_managed_stop"})


def _max_trades_per_cycle() -> int:
    try:
        return max(1, int(os.getenv("HOLDINGS_BRAIN_MAX_TRADES_PER_CYCLE", "2")))
    except ValueError:
        return 2


def _min_hold_days() -> float:
    try:
        return max(0.0, float(os.getenv("HOLDINGS_BRAIN_MIN_HOLD_DAYS", "5")))
    except ValueError:
        return 5.0


def _is_mandatory(action_kind: str, risk_flags: list) -> bool:
    return action_kind == ACTION_EXIT and bool(set(risk_flags or []) & _MANDATORY_FLAGS)


def _action_priority(action_kind: str, risk_flags: list, holding: "Holding", conviction: int) -> float:
    """Higher = more urgent. Used to pick which discretionary trades surface first."""
    flags = set(risk_flags or [])
    if _is_mandatory(action_kind, list(flags)):
        return 1000.0
    if action_kind == ACTION_TRIM and any(f.startswith("over_concentration") for f in flags):
        # The more over the cap, the more urgent the risk reduction.
        return 70.0 + min(float(getattr(holding, "pct_of_account", 0.0)), 60.0)
    if action_kind == ACTION_EXIT and "takeover_drop" in flags:
        # Weaker conviction → drop sooner.
        return 60.0 - float(conviction)
    if action_kind == ACTION_TRIM and "target_reached" in flags:
        return 50.0
    if action_kind == ACTION_EXIT:
        return 55.0
    if action_kind == ACTION_ADD:
        return 20.0
    return 10.0


def _plan_age_days(plan: Optional[dict], now: Optional[dt.datetime]) -> Optional[float]:
    """Days since the position was entered/adopted, or None if unknown."""
    if not plan:
        return None
    now = now or dt.datetime.now()
    for key in ("entered_at", "adopted_at", "created_at", "updated_at"):
        raw = plan.get(key)
        if not raw:
            continue
        try:
            ts = dt.datetime.fromisoformat(str(raw))
            return max(0.0, (now - ts).total_seconds() / 86400.0)
        except Exception:
            continue
    return None


def prioritize_actions(
    items: List[dict],
    *,
    max_trades: Optional[int] = None,
    min_hold_days: Optional[float] = None,
    now: Optional[dt.datetime] = None,
) -> tuple:
    """Decide which proposed actions actually surface this cycle (churn control).

    ``items`` = list of ``{"action": <Action|dict>, "holding": <Holding|dict>,
    "plan": <dict|None>}``. Returns ``(surfaced, deferred)`` lists of the same
    items; each deferred item gets ``["defer_reason"]`` set.

    Rules:
      * HOLD and non-order actions (ADOPT/SET_STOP) always surface (no churn/cost).
      * Mandatory risk exits (stop breach / crash) always surface, unbudgeted.
      * Discretionary orders (TRIM/ADD/EXIT-drop/target-trim) are gated by a
        minimum-hold period, then ranked and capped at ``max_trades``.
    """
    max_trades = _max_trades_per_cycle() if max_trades is None else max_trades
    min_hold_days = _min_hold_days() if min_hold_days is None else min_hold_days
    now = now or dt.datetime.now()

    def _kind(a):
        return a.get("kind") if isinstance(a, dict) else getattr(a, "kind", "")

    def _flags(a):
        return (a.get("risk_flags") if isinstance(a, dict) else getattr(a, "risk_flags", [])) or []

    def _conv(a):
        v = a.get("conviction") if isinstance(a, dict) else getattr(a, "conviction", 5)
        try:
            return int(v)
        except Exception:
            return 5

    surfaced: List[dict] = []
    deferred: List[dict] = []
    discretionary: List[tuple] = []  # (priority, item)

    for it in items:
        action = it.get("action")
        kind = _kind(action)
        flags = _flags(action)
        if kind == ACTION_HOLD:
            continue
        if kind not in ORDER_ACTIONS:           # ADOPT / SET_STOP — store-only, no churn
            surfaced.append(it)
            continue
        if _is_mandatory(kind, flags):          # stop breach / crash — never defer
            surfaced.append(it)
            continue
        # Concentration-cap trims are RISK reduction, not churn — they bypass the
        # minimum-hold period (a 30%-of-account position shouldn't wait days).
        risk_trim = kind == ACTION_TRIM and any(f.startswith("over_concentration") for f in flags)
        # Discretionary order — apply minimum hold period (except risk trims).
        age = _plan_age_days(it.get("plan"), now)
        if not risk_trim and age is not None and age < min_hold_days:
            it["defer_reason"] = f"min-hold {min_hold_days:.0f}d not met (held {age:.1f}d) — hold longer"
            deferred.append(it)
            continue
        holding = it.get("holding")
        prio = _action_priority(kind, flags, holding if not isinstance(holding, dict) else _HoldingView(holding), _conv(action))
        discretionary.append((prio, it))

    # Rank discretionary by priority, surface up to the per-cycle budget.
    discretionary.sort(key=lambda x: x[0], reverse=True)
    budget = max(0, max_trades)
    for i, (_, it) in enumerate(discretionary):
        if i < budget:
            surfaced.append(it)
        else:
            it["defer_reason"] = f"trade budget {budget}/cycle reached — deferred to a later cycle"
            deferred.append(it)
    return surfaced, deferred


class _HoldingView:
    """Tiny attr-accessor so _action_priority works on dict holdings too."""
    def __init__(self, d: dict):
        self._d = d or {}

    def __getattr__(self, k):
        return self._d.get(k, 0.0)


# ── Fast stop guard (no LLM, called frequently) ─────────────────────────────────
def check_stops(
    holdings: List[Holding],
    store: Dict[str, dict],
    quotes: Optional[Dict[str, float]] = None,
) -> List[StopBreach]:
    """Detect managed stop/target breaches against live quotes (or last price).

    ``quotes`` maps ticker→price (prefer a trusted live quote). Falls back to
    ``holding.last``. Only positions with an active managed plan are checked.
    """
    quotes = quotes or {}
    store = store or {}
    breaches: List[StopBreach] = []

    for h in holdings:
        plan = store.get(h.ticker)
        if not plan or str(plan.get("status")) not in _ACTIVE_STATUSES:
            continue
        price = _parse_num(quotes.get(h.ticker)) or h.last
        if price <= 0:
            continue
        stop = _parse_num(plan.get("stop"))
        target = _parse_num(plan.get("target"))
        if stop > 0 and price <= stop:
            breaches.append(StopBreach(h.ticker, "stop_hit", price, stop, h.unrealized_pct))
        elif target > 0 and price >= target:
            breaches.append(StopBreach(h.ticker, "target_hit", price, target, h.unrealized_pct))

    return breaches


# ── Store persistence (per-user JSON, atomic) ───────────────────────────────────
def _store_path(email: str, base_dir: Path) -> Path:
    safe = re.sub(r"[^a-z0-9_.@-]", "_", (email or "default").lower())
    return base_dir / f"holdings_brain_{safe}.json"


def load_store(email: str, base_dir: Path | str = "tmp") -> Dict[str, dict]:
    """Load the per-user managed-plan store: ``{ticker: plan_dict}``."""
    path = _store_path(email, Path(base_dir))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("positions", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_store(email: str, store: Dict[str, dict], base_dir: Path | str = "tmp") -> None:
    """Atomically persist the per-user managed-plan store."""
    path = _store_path(email, Path(base_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": dt.datetime.now().isoformat(timespec="seconds"), "positions": store}
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_hb_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def adopt_plan(action: Action, holding: Holding, theme: str = "unclassified") -> dict:
    """Build a managed-plan record from an ADOPT/assessment action."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    return {
        "ticker": holding.ticker,
        "status": "managed",
        "adopted_at": now,
        "source_broker": holding.broker,
        "theme": theme,
        "thesis": action.reason,
        "conviction": action.conviction,
        "stop": action.stop,
        "target": action.target,
        "trail_high": holding.last,
        "last_assessment_ts": now,
    }
