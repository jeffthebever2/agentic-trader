"""Short-Hold Exit Plan and Manager for TradingAgents Unified Brain.

Manages exit logic for positions opened via the UnifiedBrain short-hold pipeline.
Works alongside ExitManager but is tuned specifically for 1–10 day holds.

Key behaviours
--------------
- Stop-loss: ATR-based or manual override (entry - N × ATR)
- Take-profit: expected-return target (entry + ER × entry, capped by ATR RR)
- Minimum reward:risk gate at entry time
- Trailing stop activates after price gains `breakeven_trigger_atr` × ATR
- Optional partial take-profit: sell `partial_fraction` of position at
  `partial_trigger` fraction of the way to take_profit
- Hard max_hold_days exit regardless of price (prevents turning into bag holder)

Usage::

    from tradingagents.portfolio.short_hold_exits import ShortHoldExitPlan, ShortHoldExitManager

    plan = ShortHoldExitPlan.from_candidate(uc, config=SHORT_HOLD_CONFIG)
    manager = ShortHoldExitManager()
    signal = manager.check(plan, current_price, current_date, open_days)
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Exit signal enum
# ---------------------------------------------------------------------------

class ExitSignal(str, Enum):
    HOLD          = "HOLD"
    STOP_HIT      = "STOP_HIT"
    TARGET_HIT    = "TARGET_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    PARTIAL       = "PARTIAL"          # partial take-profit (position not fully closed)
    MAX_HOLD      = "MAX_HOLD"         # hard time-based exit
    MANUAL        = "MANUAL"           # override from dashboard/admin


# ---------------------------------------------------------------------------
# ShortHoldExitPlan
# ---------------------------------------------------------------------------

@dataclass
class ShortHoldExitPlan:
    """All exit parameters for one short-hold position.

    Created at order time; immutable fields set at entry, mutable fields
    (`trail_stop`, `partial_taken`, `peak_price`) updated as price moves.
    """
    # --- Identity -----------------------------------------------------------
    ticker:                str
    entry:                 float
    atr:                   float
    direction:             str = "long"   # only long supported

    # --- Hard levels (set at entry) -----------------------------------------
    stop:                  float = 0.0    # initial stop price
    take_profit:           float = 0.0    # initial take-profit price

    # --- Time limits --------------------------------------------------------
    max_hold_days:         int   = 10     # hard exit after N trading days
    min_hold_days:         int   = 1      # no exit before this many days

    # --- Trailing stop ------------------------------------------------------
    breakeven_trigger_atr: float = 1.0    # move stop to entry after 1 ATR gain
    trail_atr_mult:        float = 0.5    # trail at peak - mult × ATR
    trail_active:          bool  = False  # set True once breakeven trigger fires

    # --- Partial take-profit ------------------------------------------------
    partial_profit_fraction: float = 0.50  # sell 50% of position
    partial_profit_trigger:  float = 0.50  # trigger at 50% of way to take_profit
    partial_taken:           bool  = False # set True once partial fires

    # --- Risk / reward gate -------------------------------------------------
    min_rr:                float = 1.5

    # --- Live state (updated as price moves) --------------------------------
    trail_stop:            float = 0.0    # current trailing stop (0 = inactive)
    peak_price:            float = 0.0    # highest price seen since entry

    # --- Audit --------------------------------------------------------------
    created_at:            str   = ""
    source_alpha_score:    float = 0.0
    source_tier:           str   = ""

    def __post_init__(self) -> None:
        if self.peak_price == 0.0:
            self.peak_price = self.entry
        if self.trail_stop == 0.0:
            self.trail_stop = self.stop

    # ------------------------------------------------------------------ #
    @classmethod
    def from_candidate(
        cls,
        uc: Any,         # UnifiedCandidate; typed as Any to avoid circular import
        config: Optional[Dict[str, Any]] = None,
    ) -> "ShortHoldExitPlan":
        """Build ShortHoldExitPlan from a UnifiedCandidate and optional config."""
        cfg = config or {}
        entry       = float(uc.entry)
        stop        = float(uc.stop)
        take_profit = float(uc.take_profit)
        atr         = float(uc.atr) if uc.atr else _estimate_atr(entry)

        # Validate RR
        stop_dist = entry - stop
        tp_dist   = take_profit - entry
        min_rr    = float(cfg.get("min_rr", 1.5))
        if stop_dist > 0 and tp_dist / stop_dist < min_rr:
            # Stretch take_profit to meet min_rr
            take_profit = entry + stop_dist * min_rr

        return cls(
            ticker                  = uc.ticker,
            entry                   = entry,
            atr                     = atr,
            direction               = getattr(uc, "direction", "long"),
            stop                    = stop,
            take_profit             = take_profit,
            max_hold_days           = int(cfg.get("max_hold_days", 10)),
            min_hold_days           = int(cfg.get("min_hold_days", 1)),
            breakeven_trigger_atr   = float(cfg.get("breakeven_trigger_atr", 1.0)),
            trail_atr_mult          = float(cfg.get("trail_atr_mult", 0.5)),
            partial_profit_fraction = float(cfg.get("partial_profit_fraction", 0.50)),
            partial_profit_trigger  = float(cfg.get("partial_profit_trigger", 0.50)),
            min_rr                  = min_rr,
            trail_stop              = stop,
            peak_price              = entry,
            created_at              = dt.datetime.now().isoformat(timespec="seconds"),
            source_alpha_score      = float(getattr(uc, "alpha_score", 0.0)),
            source_tier             = str(getattr(uc, "tier", "")),
        )

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ShortHoldExitPlan":
        """Restore from persisted dict (e.g., account state JSON)."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for persistence."""
        return {
            "ticker":                  self.ticker,
            "entry":                   self.entry,
            "atr":                     self.atr,
            "direction":               self.direction,
            "stop":                    self.stop,
            "take_profit":             self.take_profit,
            "max_hold_days":           self.max_hold_days,
            "min_hold_days":           self.min_hold_days,
            "breakeven_trigger_atr":   self.breakeven_trigger_atr,
            "trail_atr_mult":          self.trail_atr_mult,
            "trail_active":            self.trail_active,
            "partial_profit_fraction": self.partial_profit_fraction,
            "partial_profit_trigger":  self.partial_profit_trigger,
            "partial_taken":           self.partial_taken,
            "min_rr":                  self.min_rr,
            "trail_stop":              self.trail_stop,
            "peak_price":              self.peak_price,
            "created_at":              self.created_at,
            "source_alpha_score":      self.source_alpha_score,
            "source_tier":             self.source_tier,
        }

    # ------------------------------------------------------------------ #
    @property
    def reward_risk(self) -> float:
        stop_dist = self.entry - self.stop
        if stop_dist <= 0:
            return 0.0
        return (self.take_profit - self.entry) / stop_dist

    @property
    def partial_trigger_price(self) -> float:
        """Price at which to take partial profit."""
        return self.entry + self.partial_profit_trigger * (self.take_profit - self.entry)

    @property
    def breakeven_trigger_price(self) -> float:
        """Price at which trailing stop activates (entry + N × ATR)."""
        return self.entry + self.breakeven_trigger_atr * self.atr

    def effective_stop(self) -> float:
        """Current effective stop (trailing if active, else initial)."""
        if self.trail_active and self.trail_stop > self.stop:
            return self.trail_stop
        return self.stop


# ---------------------------------------------------------------------------
# ExitCheckResult
# ---------------------------------------------------------------------------

@dataclass
class ExitCheckResult:
    """Output of ShortHoldExitManager.check()."""
    signal:          ExitSignal
    exit_price:      float        # suggested exit price (last price or level)
    close_fraction:  float        # 0.0 = no close, 0.5 = partial, 1.0 = full close
    reason:          str
    updated_plan:    ShortHoldExitPlan  # plan with mutable state updated


# ---------------------------------------------------------------------------
# ShortHoldExitManager
# ---------------------------------------------------------------------------

class ShortHoldExitManager:
    """Evaluates open positions and emits exit signals.

    Each call to `check()` is stateless from the caller's perspective;
    all mutable state lives in the `ShortHoldExitPlan` dataclass and is
    returned in `updated_plan`.

    Order of checks (first match wins):
      1. MAX_HOLD  — open_days >= max_hold_days
      2. STOP_HIT  — current_price <= effective_stop (or <= stop if trail inactive)
      3. TARGET_HIT — current_price >= take_profit
      4. PARTIAL   — not yet taken, current_price >= partial_trigger_price
      5. TRAILING_STOP — trail_active, current_price <= trail_stop (handled via STOP_HIT)
      6. BREAKEVEN ACTIVATION — update trail_stop silently if trigger hit
    """

    def check(
        self,
        plan: ShortHoldExitPlan,
        current_price: float,
        current_date: Optional[dt.date] = None,
        open_days: int = 0,
    ) -> ExitCheckResult:
        """Return exit signal and updated plan.

        Parameters
        ----------
        plan          : current plan (will NOT be mutated; updates returned in result)
        current_price : latest price for the ticker
        current_date  : today's date (unused for logic but kept for future scheduling)
        open_days     : number of trading days the position has been open
        """
        import copy
        p = copy.deepcopy(plan)  # work on copy; original stays clean

        # 1. Hard time-based exit
        if open_days >= p.max_hold_days:
            return ExitCheckResult(
                signal         = ExitSignal.MAX_HOLD,
                exit_price     = current_price,
                close_fraction = 1.0,
                reason         = f"max_hold_days={p.max_hold_days} reached (open_days={open_days})",
                updated_plan   = p,
            )

        # 2. Stop hit (handles both initial stop and active trail stop)
        effective_stop = p.effective_stop()
        if current_price <= effective_stop:
            if p.trail_active:
                signal = ExitSignal.TRAILING_STOP
                reason = f"trailing stop hit: price={current_price:.2f} <= trail_stop={p.trail_stop:.2f}"
            else:
                signal = ExitSignal.STOP_HIT
                reason = f"stop hit: price={current_price:.2f} <= stop={p.stop:.2f}"
            return ExitCheckResult(
                signal         = signal,
                exit_price     = current_price,
                close_fraction = 1.0,
                reason         = reason,
                updated_plan   = p,
            )

        # 3. Take-profit hit (full close)
        if current_price >= p.take_profit:
            return ExitCheckResult(
                signal         = ExitSignal.TARGET_HIT,
                exit_price     = p.take_profit,
                close_fraction = 1.0,
                reason         = f"take_profit hit: price={current_price:.2f} >= tp={p.take_profit:.2f}",
                updated_plan   = p,
            )

        # 4. Partial take-profit (fire once, before trail activation)
        if not p.partial_taken and open_days >= p.min_hold_days:
            if current_price >= p.partial_trigger_price:
                p.partial_taken = True
                return ExitCheckResult(
                    signal         = ExitSignal.PARTIAL,
                    exit_price     = current_price,
                    close_fraction = p.partial_profit_fraction,
                    reason         = (
                        f"partial profit: price={current_price:.2f} >= "
                        f"partial_trigger={p.partial_trigger_price:.2f} "
                        f"(selling {p.partial_profit_fraction*100:.0f}%)"
                    ),
                    updated_plan   = p,
                )

        # 5. Breakeven / trailing stop activation and update (silent, no exit)
        _update_trail(p, current_price)

        return ExitCheckResult(
            signal         = ExitSignal.HOLD,
            exit_price     = current_price,
            close_fraction = 0.0,
            reason         = (
                f"hold: price={current_price:.2f}, "
                f"stop={p.effective_stop():.2f}, "
                f"tp={p.take_profit:.2f}, "
                f"days={open_days}/{p.max_hold_days}"
            ),
            updated_plan   = p,
        )

    # ------------------------------------------------------------------ #
    def bulk_check(
        self,
        plans: List[ShortHoldExitPlan],
        prices: Dict[str, float],
        open_days_map: Dict[str, int],
        current_date: Optional[dt.date] = None,
    ) -> List[ExitCheckResult]:
        """Check all open positions. Returns one result per plan."""
        results = []
        for plan in plans:
            price = prices.get(plan.ticker, plan.entry)   # fallback to entry if no price
            days  = open_days_map.get(plan.ticker, 0)
            results.append(self.check(plan, price, current_date, days))
        return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _update_trail(plan: ShortHoldExitPlan, current_price: float) -> None:
    """Mutate plan in place: activate trail if trigger hit, then update trail_stop."""
    # Update peak
    if current_price > plan.peak_price:
        plan.peak_price = current_price

    # Activate trail once price exceeds breakeven trigger
    if not plan.trail_active and current_price >= plan.breakeven_trigger_price:
        plan.trail_active = True
        # Also move initial stop to at-least entry (lock in breakeven)
        plan.trail_stop = max(plan.trail_stop, plan.entry)

    # Update trailing stop upward (never downward)
    if plan.trail_active:
        new_trail = plan.peak_price - plan.trail_atr_mult * plan.atr
        if new_trail > plan.trail_stop:
            plan.trail_stop = new_trail


def _estimate_atr(entry: float, pct: float = 0.02) -> float:
    """Fallback ATR estimate when ATR is unavailable: 2% of entry price."""
    return entry * pct


# ---------------------------------------------------------------------------
# Convenience: build exit plan directly from raw params (for testing/admin)
# ---------------------------------------------------------------------------

def build_exit_plan(
    ticker:       str,
    entry:        float,
    stop:         float,
    take_profit:  float,
    atr:          float,
    config:       Optional[Dict[str, Any]] = None,
) -> ShortHoldExitPlan:
    """Build a ShortHoldExitPlan from raw params without a UnifiedCandidate."""
    cfg = config or {}
    return ShortHoldExitPlan(
        ticker                  = ticker,
        entry                   = entry,
        atr                     = atr,
        stop                    = stop,
        take_profit             = take_profit,
        max_hold_days           = int(cfg.get("max_hold_days", 10)),
        min_hold_days           = int(cfg.get("min_hold_days", 1)),
        breakeven_trigger_atr   = float(cfg.get("breakeven_trigger_atr", 1.0)),
        trail_atr_mult          = float(cfg.get("trail_atr_mult", 0.5)),
        partial_profit_fraction = float(cfg.get("partial_profit_fraction", 0.50)),
        partial_profit_trigger  = float(cfg.get("partial_profit_trigger", 0.50)),
        min_rr                  = float(cfg.get("min_rr", 1.5)),
        trail_stop              = stop,
        peak_price              = entry,
        created_at              = dt.datetime.now().isoformat(timespec="seconds"),
    )
