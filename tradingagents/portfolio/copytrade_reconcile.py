"""Pure copy-trade reconciliation engine.

Given a *followed* paper portfolio's current positions and the set of tickers we
already copy-own on the real broker, compute the BUY / SELL actions that move the
real account toward the paper portfolio — **mirroring weights, not dollars**.

This module is deliberately pure, synchronous and network-free: it is the safety
floor. All Playwright / broker / quote wiring lives in ``web/copytrade.py``.

Design decisions (v1, intentionally conservative to avoid churn + double-buys):
  * We mirror *membership + weight*, not every intraday trade event. Replaying
    event logs is fragile (ordering, partial fills, re-scans); reconciling the
    live position set is idempotent and self-healing.
  * BUY a name the portfolio holds that we do **not** copy-own AND do **not**
    already hold outside copy-trade (never stack onto a user's existing bag).
  * SELL a name we copy-own that the portfolio has fully exited.
  * Weight = paper position market value / paper book equity — a **unitless
    ratio**. Applying it to the real account (via ``pct_of_account``) can never
    mix a real-dollar figure into a paper-dollar book (the scale-mix trap).
  * Drift-rebalance of already-held names is deliberately NOT done in v1 — it
    creates turnover/wash-sale noise for marginal tracking gain. ``rebalance``
    hook is left for a future opt-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CopyAction:
    """One proposed real-broker action derived from the followed portfolio."""

    action: str            # "buy" | "sell"
    ticker: str
    target_weight: float   # 0..1 fraction of account (buy only; 0.0 for sell)
    reason: str
    paper_shares: float = 0.0
    paper_weight_raw: float = 0.0  # pre-clamp weight, for display/telemetry
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "ticker": self.ticker,
            "target_weight": round(self.target_weight, 4),
            "target_pct": round(self.target_weight * 100, 2),
            "paper_weight_raw": round(self.paper_weight_raw, 4),
            "paper_shares": self.paper_shares,
            "reason": self.reason,
            **({"meta": self.meta} if self.meta else {}),
        }


def _pos_value(p: dict) -> float:
    """Market value of a paper position dict, current_price preferred."""
    try:
        shares = float(p.get("shares") or 0)
    except (TypeError, ValueError):
        return 0.0
    price = p.get("current_price") or p.get("entry_price") or 0
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0.0
    return max(0.0, shares) * max(0.0, price)


def _norm(t: str | None) -> str:
    return str(t or "").strip().upper()


def compute_copy_actions(
    portfolio_positions: list[dict],
    portfolio_equity: float,
    owned: dict,
    external_holdings: set,
    *,
    min_weight: float = 0.01,
    max_weight: float = 0.10,
) -> list[CopyAction]:
    """Reconcile owned copy positions toward the followed portfolio.

    Args:
        portfolio_positions: [{ticker, shares, current_price|entry_price, stop, target}, ...]
        portfolio_equity: total book equity (cash + positions MV) for weight math.
            Must be > 0 or every weight is undefined and we fail closed (no buys).
        owned: {TICKER: {...}} tickers we currently copy-own on the real broker.
        external_holdings: {TICKER, ...} real holdings held OUTSIDE copy-trade —
            never bought into (no stacking) and never sold (not ours to touch).
        min_weight: ignore dust positions below this fraction (default 1%).
        max_weight: clamp target weight to the compliance cap (default 10%).

    Returns:
        List of CopyAction — sells first (free cash), then buys by descending weight.
    """
    owned_norm = {_norm(k) for k in owned}
    external_norm = {_norm(k) for k in external_holdings}

    # Map followed portfolio → {ticker: position}
    port: dict[str, dict] = {}
    for p in portfolio_positions or []:
        t = _norm(p.get("ticker"))
        if t:
            port[t] = p
    port_tickers = set(port)

    buys: list[CopyAction] = []
    sells: list[CopyAction] = []

    # ── SELLS: copy-owned names the portfolio has fully exited ────────────────
    for t in sorted(owned_norm - port_tickers):
        if t in external_norm:
            # Defensive: a name we recorded as copy-owned now also appears as an
            # external holding — do not auto-sell ambiguous ownership.
            continue
        sells.append(CopyAction(
            action="sell",
            ticker=t,
            target_weight=0.0,
            reason="followed portfolio exited this position",
            meta={"copy_owned": True},
        ))

    # ── BUYS: portfolio names we neither copy-own nor already hold ────────────
    equity_ok = isinstance(portfolio_equity, (int, float)) and portfolio_equity > 0
    for t in port_tickers - owned_norm:
        if t in external_norm:
            # Already in the real account outside copy-trade — never stack.
            continue
        if not equity_ok:
            # Fail closed: without a valid denominator we cannot size safely.
            continue
        p = port[t]
        raw = _pos_value(p) / float(portfolio_equity)
        if raw < min_weight:
            continue
        weight = min(raw, max_weight)
        try:
            shares = float(p.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        buys.append(CopyAction(
            action="buy",
            ticker=t,
            target_weight=weight,
            paper_shares=shares,
            paper_weight_raw=raw,
            reason=f"followed portfolio holds {raw * 100:.1f}% weight",
            meta={
                "stop": p.get("stop"),
                "target": p.get("target"),
                "entry_price": p.get("entry_price"),
            },
        ))

    buys.sort(key=lambda a: a.target_weight, reverse=True)
    return sells + buys
