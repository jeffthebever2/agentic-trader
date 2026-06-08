"""PortfolioConfig — parametric definition for a single paper-trading portfolio."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PortfolioConfig:
    """All tunable parameters for one portfolio account.

    A portfolio is a named combination of:
      - source_strategy: which candidate bucket to pull from (maps to build_candidates keys)
      - position-management overrides: stop/target ATR multiples, sizing, hold period
      - entry-gate overrides: ML threshold, min R:R
      - display metadata: label, description, color, group

    None values mean "use the global args default" — so portfolios only need to specify
    params that differ from the baseline.
    """
    name: str
    label: str
    description: str

    # Which build_candidates() bucket feeds this portfolio
    source_strategy: str = "algorithm"

    # ── Position management ───────────────────────────────────────────────────
    stop_mult: Optional[float] = None           # ATR × stop_mult = stop distance
    target_mult: Optional[float] = None         # ATR × target_mult = target distance
    max_hold_days: Optional[int] = None         # Time-stop (calendar days)
    risk_per_trade_pct: Optional[float] = None  # % of account to risk per trade
    max_positions: Optional[int] = None         # Max simultaneous open positions
    partial_profit_pct: Optional[float] = None  # Fraction of way to target → partial exit
    partial_profit_fraction: Optional[float] = None  # Share of position to sell on partial
    trailing_stop_atr_mult: Optional[float] = None   # Trail stop after breakeven

    # ── Entry gates ───────────────────────────────────────────────────────────
    ml_probability_threshold: Optional[float] = None  # Min ML win-prob (0 = disabled)
    min_risk_reward: Optional[float] = None            # Min live R:R at entry

    # ── Long-hold specific ────────────────────────────────────────────────────
    long_hold_days: Optional[int] = None  # Calendar days for long-hold exit

    # ── Display metadata ──────────────────────────────────────────────────────
    color: str = "#4B9CD3"
    group: str = "signal"       # signal | risk | hold | filter
    emoji: str = "📊"

    def as_param_dict(self) -> Dict[str, Any]:
        """Return non-None overrides + source_strategy as a plain dict for get_param() storage."""
        mapping = {
            "source_strategy": self.source_strategy,  # always present for candidate routing
            "stop_mult": self.stop_mult,
            "target_mult": self.target_mult,
            "max_hold_days": self.max_hold_days,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_positions": self.max_positions,
            "partial_profit_pct": self.partial_profit_pct,
            "partial_profit_fraction": self.partial_profit_fraction,
            "trailing_stop_atr_mult": self.trailing_stop_atr_mult,
            "ml_probability_threshold": self.ml_probability_threshold,
            "min_risk_reward": self.min_risk_reward,
            "long_hold_days": self.long_hold_days,
            "portfolio_label": self.label,
            "portfolio_group": self.group,
        }
        return {k: v for k, v in mapping.items() if v is not None}

    def filter_candidates(self, candidates: list) -> list:
        """Apply per-portfolio secondary filter to the candidate list.

        Called after build_candidates() produces the source_strategy bucket.
        Currently applies ML probability gate when ml_probability_threshold is set.
        """
        if not self.ml_probability_threshold:
            return candidates
        threshold = self.ml_probability_threshold
        return [
            c for c in candidates
            if (getattr(c, "ml_probability", None) or 0.0) >= threshold
        ]
