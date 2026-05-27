"""SafeTradeGuard — pre-scan no-trade mode checker.

Evaluated once per scan cycle BEFORE the candidate loop. Returns
(allow_trade, reason_list) where allow_trade=False halts all new entries.

Checks (in order of severity):
  1. Crisis VIX:     VIX > 35 → halt all new longs
  2. Hostile regime: bear SPY + VIX elevated (>25) → halt
  3. Portfolio drawdown: account_drawdown < -max_dd_pct → halt
  4. Model drift:    |predicted_win_rate - actual_win_rate| > drift_threshold
                     over recent N trades
  5. Win rate collapse: rolling N-trade win rate < floor (e.g. 30%)
  6. Model staleness: model bundle > max_model_age_days old → warn

Modes:
  HALT     — no new entries this cycle (all 6 checks above)
  CAUTION  — still trading but guard emits warnings (staleness only)

Usage in paper_trade_today.py:
    from tradingagents.portfolio.safe_trade_guard import SafeTradeGuard
    guard = SafeTradeGuard()
    allow, reasons = guard.check(
        vix_level=vix_level,
        spy_regime=spy_regime,
        account_drawdown=account_drawdown,
        recent_trades=account.trades[-20:],
        model_created_at=model_meta.get("created_at"),
    )
    if not allow:
        for r in reasons:
            account.log_event({"type": "NO_TRADE_MODE", "reason": r})
        return {"bought": 0, "sold": sold, "skipped": len(candidates)}
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple


class SafeTradeGuard:
    """Evaluate pre-scan conditions and return allow/halt verdict.

    Parameters
    ----------
    crisis_vix : float
        VIX above this → halt all new entries. Default 35.
    elevated_vix : float
        VIX above this (combined with bear) → halt. Default 25.
    max_dd_pct : float
        Account drawdown below this % → halt (e.g. -0.12 = -12%). Default -0.12.
    drift_threshold : float
        |predicted_wr - actual_wr| above this → halt. Default 0.20.
    drift_min_trades : int
        Minimum recent trades needed before drift check fires. Default 15.
    wr_floor : float
        Rolling win rate below this → halt (model failing live). Default 0.30.
    wr_floor_min_trades : int
        Minimum trades needed before WR floor check fires. Default 10.
    max_model_age_days : int
        Model older than this → CAUTION (warn but allow). Default 45.
    """

    def __init__(
        self,
        crisis_vix: float = 35.0,
        elevated_vix: float = 25.0,
        max_dd_pct: float = -0.12,
        drift_threshold: float = 0.20,
        drift_min_trades: int = 15,
        wr_floor: float = 0.30,
        wr_floor_min_trades: int = 10,
        max_model_age_days: int = 45,
    ):
        self.crisis_vix = crisis_vix
        self.elevated_vix = elevated_vix
        self.max_dd_pct = max_dd_pct
        self.drift_threshold = drift_threshold
        self.drift_min_trades = drift_min_trades
        self.wr_floor = wr_floor
        self.wr_floor_min_trades = wr_floor_min_trades
        self.max_model_age_days = max_model_age_days

    def check(
        self,
        vix_level: Optional[float] = None,
        spy_regime: str = "unknown",
        account_drawdown: float = 0.0,
        recent_trades: Optional[List[Dict[str, Any]]] = None,
        model_created_at: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Evaluate all guard conditions.

        Returns
        -------
        allow_trade : bool
            True = proceed with candidate scan. False = halt new entries.
        reasons : list of str
            List of triggered condition descriptions. Empty if allow=True
            and no warnings. May contain WARN_ prefixed strings for cautions.
        """
        halt_reasons: List[str] = []
        warn_reasons: List[str] = []

        # ── 1. Crisis VIX — hardest stop ─────────────────────────────────
        if vix_level is not None and vix_level >= self.crisis_vix:
            halt_reasons.append(
                f"crisis_vix: VIX={vix_level:.1f} >= {self.crisis_vix} "
                f"(halt all new longs; wait for rebound confirmation)"
            )

        # ── 2. Hostile regime (bear + elevated VIX) ───────────────────────
        _r = (spy_regime or "unknown").lower()
        is_bear = _r in ("bear", "sell", "downtrend")
        if (
            not halt_reasons  # only if not already halted by crisis
            and is_bear
            and vix_level is not None
            and vix_level >= self.elevated_vix
        ):
            halt_reasons.append(
                f"hostile_regime: spy={spy_regime}, VIX={vix_level:.1f} >= {self.elevated_vix} "
                f"(bear market + elevated volatility; pullback strategy loses edge)"
            )

        # ── 3. Portfolio drawdown ─────────────────────────────────────────
        if account_drawdown < self.max_dd_pct:
            halt_reasons.append(
                f"portfolio_drawdown: drawdown={account_drawdown:.2%} < floor {self.max_dd_pct:.2%} "
                f"(account underwater; protect remaining capital)"
            )

        # ── 4. Model drift ────────────────────────────────────────────────
        if recent_trades:
            scored_trades = [
                t for t in recent_trades
                if t.get("ml_probability") is not None and "pnl" in t
            ]
            if len(scored_trades) >= self.drift_min_trades:
                pred_wr = sum(t["ml_probability"] for t in scored_trades) / len(scored_trades)
                actual_wr = sum(1 for t in scored_trades if t["pnl"] > 0) / len(scored_trades)
                drift = abs(pred_wr - actual_wr)
                if drift > self.drift_threshold:
                    halt_reasons.append(
                        f"model_drift: |predicted_wr={pred_wr:.3f} - actual_wr={actual_wr:.3f}| "
                        f"= {drift:.3f} > {self.drift_threshold} "
                        f"(model predictions unreliable on recent live trades)"
                    )

        # ── 5. Win rate collapse ──────────────────────────────────────────
        if recent_trades:
            closed = [t for t in recent_trades if "pnl" in t]
            if len(closed) >= self.wr_floor_min_trades:
                wr = sum(1 for t in closed if t["pnl"] > 0) / len(closed)
                if wr < self.wr_floor:
                    halt_reasons.append(
                        f"wr_collapse: rolling_wr={wr:.3f} < floor {self.wr_floor} "
                        f"over last {len(closed)} trades "
                        f"(strategy not working in current conditions)"
                    )

        # ── 6. Model staleness (WARN only, not halt) ──────────────────────
        if model_created_at:
            try:
                created = dt.datetime.fromisoformat(model_created_at[:19])
                age_days = (dt.datetime.now() - created).days
                if age_days > self.max_model_age_days:
                    warn_reasons.append(
                        f"WARN_model_stale: model is {age_days}d old "
                        f"(> {self.max_model_age_days}d; retrain recommended)"
                    )
            except Exception:
                pass

        all_reasons = halt_reasons + warn_reasons
        allow = len(halt_reasons) == 0

        return allow, all_reasons

    def high_vol_adjustments(
        self,
        vix_level: Optional[float],
        min_prob_threshold: float,
        combined_size_factor: float,
    ) -> Tuple[float, float, Optional[int]]:
        """When VIX is elevated (but not crisis), return tightened parameters.

        Returns
        -------
        (effective_min_prob, effective_size_factor, max_hold_override)
        max_hold_override: if not None, override hold-period limit to this many days.
        """
        if vix_level is None:
            return min_prob_threshold, combined_size_factor, None

        if vix_level >= self.elevated_vix and vix_level < self.crisis_vix:
            # High-vol mode: stricter gate + half size + shorter hold
            return (
                min(0.85, min_prob_threshold + 0.08),  # +8% probability gate
                combined_size_factor * 0.50,            # 50% size
                2,                                       # 2-day max hold
            )

        return min_prob_threshold, combined_size_factor, None
