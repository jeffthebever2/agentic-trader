"""Dynamic exit level calculator for TradingAgents paper/live trading.

Computes stop-loss and take-profit levels from ATR, ML confidence, expected
return, and R:R constraints. All logic is deterministic and auditable.

Usage:
    from tradingagents.portfolio.exit_manager import ExitManager

    em = ExitManager(min_risk_reward=1.5, stop_atr_mult=1.0, target_atr_mult=0.75)
    result = em.calculate(
        entry_price=50.00,
        atr=1.50,
        ml_probability=0.68,
        expected_return=0.032,
        direction="long",
    )
    print(result.stop, result.target, result.risk_reward)

Exit level hierarchy (most specific wins):
  1. ATR-based stop/target (primary)
  2. ML confidence extension (high prob → wider target)
  3. Expected return anchor (target ≥ entry * (1 + expected_return))
  4. Min R:R enforcement (target ≥ entry + risk_distance * min_rr)

Trailing stop (activated after price reaches breakeven_trigger):
  trail_stop = peak_price - atr * trail_atr_mult
  Never lower than previous stop.

Partial take-profit:
  Sell partial_fraction of position at entry + risk_distance * partial_rr
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExitLevels:
    """Computed entry/stop/target/trail levels for one trade."""
    entry_price: float
    stop_price: float
    target_price: float
    partial_target: float          # price to take partial profit
    partial_fraction: float        # fraction to sell at partial_target
    trail_activation_price: float  # price where trailing stop activates
    trail_atr_mult: float          # trailing stop = peak - atr * this
    atr: float
    risk_distance: float           # entry - stop (always positive)
    reward_distance: float         # target - entry
    risk_reward: float             # reward / risk
    direction: str                 # "long" only for now
    adjustments: Dict[str, str] = field(default_factory=dict)  # what changed and why

    def to_audit_dict(self) -> Dict:
        return {
            "entry": round(self.entry_price, 4),
            "stop": round(self.stop_price, 4),
            "target": round(self.target_price, 4),
            "partial_target": round(self.partial_target, 4),
            "partial_fraction": round(self.partial_fraction, 4),
            "trail_activation": round(self.trail_activation_price, 4),
            "trail_atr_mult": round(self.trail_atr_mult, 4),
            "atr": round(self.atr, 4),
            "risk_distance": round(self.risk_distance, 4),
            "reward_distance": round(self.reward_distance, 4),
            "risk_reward": round(self.risk_reward, 4),
            "direction": self.direction,
            "adjustments": self.adjustments,
        }


class ExitManager:
    """Compute stop, target, and trailing-stop levels for a trade candidate.

    Parameters
    ----------
    min_risk_reward : float
        Minimum acceptable R:R ratio. Target is raised if needed. Default 1.5.
    stop_atr_mult : float
        Stop distance = atr * this. Default 1.0.
    target_atr_mult : float
        Base target distance = atr * this. Default 0.75.
        (target raised by min_rr enforcement, confidence, or expected_return)
    confidence_extension_threshold : float
        ML probability above this extends target by confidence_extension_factor. Default 0.70.
    confidence_extension_factor : float
        Target multiplier when confidence is high. Default 1.3.
    er_anchor_weight : float
        Weight given to expected_return anchor vs ATR target. 0 = ignore ER. Default 1.0.
    trail_atr_mult : float
        Trailing stop = peak_price - atr * trail_atr_mult. Default 0.5.
    trail_activation_atr_mult : float
        Trailing stop activates after price moves trail_activation_atr_mult ATRs from entry.
        Default 1.0 (activates at breakeven + 1 ATR).
    partial_rr : float
        Take partial profit at entry + risk_distance * partial_rr. Default 1.0 (1R).
    partial_fraction : float
        Fraction of position to sell at partial target. Default 0.33.
    atr_fallback_pct : float
        If ATR is 0 or unavailable, use entry_price * this as ATR proxy. Default 0.02 (2%).
    """

    def __init__(
        self,
        min_risk_reward: float = 1.5,
        stop_atr_mult: float = 1.0,
        target_atr_mult: float = 0.75,
        confidence_extension_threshold: float = 0.70,
        confidence_extension_factor: float = 1.3,
        er_anchor_weight: float = 1.0,
        trail_atr_mult: float = 0.5,
        trail_activation_atr_mult: float = 1.0,
        partial_rr: float = 1.0,
        partial_fraction: float = 0.33,
        atr_fallback_pct: float = 0.02,
    ):
        self.min_risk_reward = min_risk_reward
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.confidence_extension_threshold = confidence_extension_threshold
        self.confidence_extension_factor = confidence_extension_factor
        self.er_anchor_weight = er_anchor_weight
        self.trail_atr_mult = trail_atr_mult
        self.trail_activation_atr_mult = trail_activation_atr_mult
        self.partial_rr = partial_rr
        self.partial_fraction = partial_fraction
        self.atr_fallback_pct = atr_fallback_pct

    def calculate(
        self,
        entry_price: float,
        atr: float,
        ml_probability: float = 0.55,
        expected_return: Optional[float] = None,
        direction: str = "long",
        existing_stop: Optional[float] = None,
        invalidation_level: Optional[float] = None,
    ) -> ExitLevels:
        """Compute exit levels for a new entry.

        Parameters
        ----------
        entry_price : float
            Planned entry price.
        atr : float
            Average True Range of the instrument.
        ml_probability : float
            Calibrated ML win probability [0, 1].
        expected_return : float, optional
            Model expected return (e.g. 0.032 = 3.2%). Used as target anchor.
        invalidation_level : float, optional
            Price level from BreakoutScanner where the setup is invalidated
            (e.g., prior support that must hold). Used as a floor for the stop:
            if invalidation_level > ATR stop, the tighter of the two wins.
        direction : str
            "long" only. "short" may be added in future.
        existing_stop : float, optional
            If a manual stop was provided (e.g. from technical pattern), use it
            instead of ATR-derived stop if it gives a tighter risk.

        Returns
        -------
        ExitLevels
        """
        adjustments: Dict[str, str] = {}

        # ── ATR fallback ────────────────────────────────────────────────────
        if atr <= 0:
            atr = entry_price * self.atr_fallback_pct
            adjustments["atr"] = f"fallback {self.atr_fallback_pct:.1%} of entry"

        # ── Stop price ──────────────────────────────────────────────────────
        atr_stop = entry_price - atr * self.stop_atr_mult
        stop_price = atr_stop

        if existing_stop is not None and existing_stop > 0:
            # Use tighter of ATR stop vs manual stop (never widen risk)
            if direction == "long":
                stop_price = max(atr_stop, existing_stop)  # higher = less risk
                if stop_price != atr_stop:
                    adjustments["stop"] = f"manual stop {existing_stop:.4f} tighter than ATR {atr_stop:.4f}"
            else:
                stop_price = min(atr_stop, existing_stop)

        # ── Breakout invalidation level (use if tighter than ATR stop) ─────
        if invalidation_level is not None and invalidation_level > 0:
            if direction == "long" and invalidation_level > stop_price:
                # Invalidation is above current stop → tighter → use it
                stop_price = invalidation_level
                adjustments["invalidation"] = (
                    f"breakout invalidation {invalidation_level:.4f} tighter than "
                    f"ATR stop {atr_stop:.4f}"
                )

        stop_price = max(stop_price, 0.01)  # never negative
        risk_distance = entry_price - stop_price
        if risk_distance <= 0:
            # Degenerate case: entry at or below stop; force 1% risk
            risk_distance = entry_price * 0.01
            stop_price = entry_price - risk_distance
            adjustments["stop_degenerate"] = "risk_distance<=0, forced to 1% of entry"

        # ── Base target (ATR-based) ─────────────────────────────────────────
        target_price = entry_price + atr * self.target_atr_mult

        # ── Confidence extension ────────────────────────────────────────────
        if ml_probability >= self.confidence_extension_threshold:
            extended = entry_price + atr * self.target_atr_mult * self.confidence_extension_factor
            if extended > target_price:
                target_price = extended
                adjustments["confidence"] = (
                    f"ml_prob={ml_probability:.3f} >= {self.confidence_extension_threshold}, "
                    f"target extended {self.confidence_extension_factor}×"
                )

        # ── Expected return anchor ──────────────────────────────────────────
        if expected_return is not None and self.er_anchor_weight > 0:
            er_clipped = max(-0.5, min(3.0, expected_return))
            if er_clipped > 0:
                er_target = entry_price * (1.0 + er_clipped * self.er_anchor_weight)
                if er_target > target_price:
                    target_price = er_target
                    adjustments["expected_return"] = (
                        f"er={expected_return:.4f} → target raised to {er_target:.4f}"
                    )

        # ── Min R:R enforcement ─────────────────────────────────────────────
        min_target = entry_price + risk_distance * self.min_risk_reward
        if min_target > target_price:
            target_price = min_target
            adjustments["min_rr"] = (
                f"min_rr={self.min_risk_reward} raised target to {min_target:.4f} "
                f"(risk_dist={risk_distance:.4f})"
            )

        reward_distance = target_price - entry_price
        risk_reward = reward_distance / max(risk_distance, 1e-9)

        # ── Trailing stop parameters ────────────────────────────────────────
        trail_activation = entry_price + atr * self.trail_activation_atr_mult

        # ── Partial take-profit ─────────────────────────────────────────────
        partial_target = entry_price + risk_distance * self.partial_rr

        return ExitLevels(
            entry_price=entry_price,
            stop_price=round(stop_price, 4),
            target_price=round(target_price, 4),
            partial_target=round(partial_target, 4),
            partial_fraction=self.partial_fraction,
            trail_activation_price=round(trail_activation, 4),
            trail_atr_mult=self.trail_atr_mult,
            atr=round(atr, 4),
            risk_distance=round(risk_distance, 4),
            reward_distance=round(reward_distance, 4),
            risk_reward=round(risk_reward, 4),
            direction=direction,
            adjustments=adjustments,
        )

    def update_trailing_stop(
        self,
        current_stop: float,
        peak_price: float,
        atr: float,
    ) -> float:
        """Compute updated trailing stop. Never lower than current_stop.

        Call this on each price update after trail_activation_price is hit.

        Parameters
        ----------
        current_stop : float
            Most recent stop level. New stop will not go below this.
        peak_price : float
            Highest price seen since entry (for long positions).
        atr : float
            Current ATR (may differ from entry ATR).

        Returns
        -------
        float
            New (or unchanged) stop price.
        """
        trail = peak_price - atr * self.trail_atr_mult
        return max(current_stop, trail)

    @staticmethod
    def from_candidate(candidate: Any, **kwargs) -> "ExitManager":
        """Convenience: build ExitManager with overrides from a candidate object.

        Candidate attributes checked (all optional):
          - min_risk_reward
          - stop_atr_mult
          - target_atr_mult
        Keyword args override candidate attrs.
        """
        em_kwargs: Dict[str, Any] = {}
        for attr in ("min_risk_reward", "stop_atr_mult", "target_atr_mult"):
            val = getattr(candidate, attr, None)
            if val is not None:
                em_kwargs[attr] = val
        em_kwargs.update(kwargs)
        return ExitManager(**em_kwargs)
