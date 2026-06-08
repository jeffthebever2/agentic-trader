"""15-portfolio registry — the competition.

Each portfolio is a distinct hypothesis about what maximizes risk-adjusted returns.
All run in parallel on the same daily scan; the comparison dashboard shows which wins.

Groups
------
signal   — different signal sources (rule-based, ML variants, combined)
risk     — same signals, different stop/target/sizing
hold     — same signals, different max hold period
filter   — same signals, different entry gates
"""
from __future__ import annotations

from tradingagents.portfolios.config import PortfolioConfig

PORTFOLIO_REGISTRY: list[PortfolioConfig] = [

    # ── GROUP: SIGNAL SOURCE ──────────────────────────────────────────────────
    # Test: which candidate generation method produces the best alpha?

    PortfolioConfig(
        name="algo_standard",
        label="Algorithm",
        description="Pure rule-based breakout: confirmed-pullback with default stops/sizing.",
        source_strategy="algorithm",
        color="#2196F3",
        group="signal",
        emoji="📐",
    ),

    PortfolioConfig(
        name="combined_ml",
        label="Algo + ML",
        description="Breakout candidates that also pass the ML large-loss gate (best of both).",
        source_strategy="combined",
        color="#4CAF50",
        group="signal",
        emoji="🤖",
    ),

    PortfolioConfig(
        name="ml_new_model",
        label="ML New Model",
        description="Candidates sourced from the challenger ML model (ml_new bundle).",
        source_strategy="ml_new",
        color="#009688",
        group="signal",
        emoji="🧠",
    ),

    PortfolioConfig(
        name="ml_old_model",
        label="ML Legacy Model",
        description="Candidates sourced from the legacy ML model for drift comparison.",
        source_strategy="machine_learning",
        color="#78909C",
        group="signal",
        emoji="📦",
    ),

    PortfolioConfig(
        name="long_hold_standard",
        label="Long Hold",
        description="Buy breakouts and hold up to 20 calendar days — capture multi-week trends.",
        source_strategy="long_hold",
        long_hold_days=20,
        max_hold_days=22,
        color="#FF9800",
        group="signal",
        emoji="⏳",
    ),

    # ── GROUP: RISK MANAGEMENT ────────────────────────────────────────────────
    # Test: which stop/target/sizing combination maximizes EV?

    PortfolioConfig(
        name="algo_conservative",
        label="Conservative Risk",
        description="Tighter stops (0.8×ATR), smaller targets (1.0×ATR), half position size.",
        source_strategy="algorithm",
        stop_mult=0.8,
        target_mult=1.0,
        risk_per_trade_pct=0.5,
        max_positions=5,
        color="#F44336",
        group="risk",
        emoji="🛡️",
    ),

    PortfolioConfig(
        name="algo_aggressive",
        label="Aggressive Risk",
        description="Wide stops (1.5×ATR), large targets (2.0×ATR), double position size.",
        source_strategy="algorithm",
        stop_mult=1.5,
        target_mult=2.0,
        risk_per_trade_pct=2.0,
        max_positions=6,
        color="#E91E63",
        group="risk",
        emoji="🚀",
    ),

    PortfolioConfig(
        name="algo_concentrated",
        label="Concentrated",
        description="Large positions (3% risk/trade) in only 3 top-ranked setups at a time.",
        source_strategy="algorithm",
        risk_per_trade_pct=3.0,
        max_positions=3,
        color="#9C27B0",
        group="risk",
        emoji="💎",
    ),

    PortfolioConfig(
        name="algo_wide_stops",
        label="Wide Stops",
        description="Very wide stops (2×ATR) and big targets (3×ATR) to capture strong moves.",
        source_strategy="algorithm",
        stop_mult=2.0,
        target_mult=3.0,
        risk_per_trade_pct=1.0,
        partial_profit_pct=0.5,
        color="#FF5722",
        group="risk",
        emoji="🎯",
    ),

    # ── GROUP: HOLD PERIOD ────────────────────────────────────────────────────
    # Test: does holding longer or shorter improve returns?

    PortfolioConfig(
        name="algo_quick_exit",
        label="Quick Exit (3d)",
        description="Exit within 3 calendar days — capture the initial momentum burst.",
        source_strategy="algorithm",
        max_hold_days=3,
        partial_profit_pct=0.0,  # no partial on 3-day hold — just exit clean
        color="#00BCD4",
        group="hold",
        emoji="⚡",
    ),

    PortfolioConfig(
        name="algo_medium_hold",
        label="Medium Hold (7d)",
        description="Hold up to 7 calendar days — one full trading week.",
        source_strategy="algorithm",
        max_hold_days=7,
        color="#03A9F4",
        group="hold",
        emoji="📅",
    ),

    PortfolioConfig(
        name="algo_swing",
        label="Swing (25d)",
        description="Swing-trade: hold up to 25 calendar days to capture intermediate trends.",
        source_strategy="algorithm",
        max_hold_days=25,
        trailing_stop_atr_mult=1.0,  # wider trail to let winners run
        color="#1565C0",
        group="hold",
        emoji="🌊",
    ),

    # ── GROUP: ENTRY FILTERS ──────────────────────────────────────────────────
    # Test: does stricter filtering improve signal quality?

    PortfolioConfig(
        name="ml_strict_filter",
        label="ML Strict (≥0.57)",
        description="Combined signals filtered to ML win-prob ≥ 0.57 (top-conviction only).",
        source_strategy="combined",
        ml_probability_threshold=0.57,
        color="#8BC34A",
        group="filter",
        emoji="🔬",
    ),

    PortfolioConfig(
        name="ml_moderate_filter",
        label="ML Moderate (≥0.52)",
        description="Combined signals filtered to ML win-prob ≥ 0.52 (above-average confidence).",
        source_strategy="combined",
        ml_probability_threshold=0.52,
        color="#CDDC39",
        group="filter",
        emoji="🎚️",
    ),

    PortfolioConfig(
        name="high_rr_only",
        label="High R:R Only (≥1.5)",
        description="Algorithm candidates where live risk:reward ≥ 1.5 at entry — skip marginal setups.",
        source_strategy="algorithm",
        min_risk_reward=1.5,
        color="#FFC107",
        group="filter",
        emoji="⚖️",
    ),
]

# Name → config lookup
_REGISTRY_MAP: dict[str, PortfolioConfig] = {p.name: p for p in PORTFOLIO_REGISTRY}


def get_portfolio(name: str) -> PortfolioConfig:
    """Return PortfolioConfig by name. Raises KeyError if not found."""
    return _REGISTRY_MAP[name]


def list_portfolios() -> list[str]:
    """Return all registered portfolio names."""
    return [p.name for p in PORTFOLIO_REGISTRY]
