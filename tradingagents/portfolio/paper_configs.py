"""15 paper-only portfolio configurations.

Single source of truth for paper trading experiments.
Each portfolio starts with $10,000, is completely isolated, and competes by all-time ROR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PaperPortfolioConfig:
    """Configuration for one paper-only portfolio."""

    portfolio_id: str
    """Unique identifier (snake_case, used in file paths)."""

    name: str
    """Display name (e.g., 'Neural Falcon')."""

    source_strategy: str
    """Which candidate source to pull from: algorithm, machine_learning, ml_new,
    combined, long_hold, pure_ai, or unified_brain."""

    stop_mult: float
    """ATR multiplier for stop loss distance."""

    target_mult: float
    """ATR multiplier for profit target distance."""

    max_hold_days: int
    """Calendar days before forced exit (time stop)."""

    risk_per_trade_pct: float
    """Percentage of account to risk per trade (0.5 = 0.5%)."""

    ml_probability_threshold: Optional[float] = None
    """Minimum ML win probability to enter (0.50–0.70 range). None = no ML gate."""

    initial_cash: float = 10000.0
    """Starting account size."""

    paper_only: bool = True
    """Must be True. Enforced at runtime."""

    trailing_stop_atr_mult: Optional[float] = None
    """ATR multiplier for trailing stop (activates after breakeven)."""

    partial_profit_pct: Optional[float] = None
    """Take partial profit at this % of way to target (0.0–1.0)."""

    partial_profit_fraction: Optional[float] = None
    """Fraction of position to exit on partial."""

    max_positions: Optional[int] = None
    """Max concurrent open positions (None = no limit)."""

    # ── Multi-tool combination (portfolios 16–30) ─────────────────────────────
    source_strategies: Optional[list[str]] = None
    """When set, this portfolio combines signals from MULTIPLE source buckets
    instead of `source_strategy`. e.g. ['algorithm','machine_learning']."""

    combine_mode: str = "single"
    """How to combine `source_strategies`:
      single       — use `source_strategy` (default, portfolios 1–15)
      union        — any tool flags the ticker (dedup, best score wins)
      consensus_2  — ≥2 of the listed tools agree on the ticker
      consensus_3  — ≥3 of the listed tools agree
      intersection — ALL listed tools agree (strongest confirmation)"""

    trade_skip_days: bool = False
    """When True this portfolio ENTERS on Monday/Thursday signal-bar scans; when
    False (default) it sits those days out — matching the backtested calendar
    edge the screen applies (Thu n=3974 strong, Mon n=351 weak). Exits ALWAYS
    run regardless. The field is split ~half across the registry so all-time ROR
    can settle empirically whether skipping those signal bars actually helps."""


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO REGISTRY — 1–15 single-source, 16–30 multi-tool consensus/blend
# ─────────────────────────────────────────────────────────────────────────────

PAPER_PORTFOLIOS: list[PaperPortfolioConfig] = [
    # 1. Breakout Phoenix — baseline rule-based
    PaperPortfolioConfig(
        portfolio_id="breakout_phoenix",
        name="Rule Breakout 7d",
        source_strategy="algorithm",
        stop_mult=1.4,
        target_mult=2.8,
        max_hold_days=7,
        risk_per_trade_pct=1.00,
    ),

    # 2. Neural Falcon — conservative old ML
    PaperPortfolioConfig(
        portfolio_id="neural_falcon",
        name="ML Conservative",
        trailing_stop_atr_mult=1.5,
        source_strategy="machine_learning",
        stop_mult=1.3,
        target_mult=2.6,
        max_hold_days=8,
        risk_per_trade_pct=0.90,
        ml_probability_threshold=0.60,
    ),

    # 3. Quantum Lynx — balanced new ML
    PaperPortfolioConfig(
        portfolio_id="quantum_lynx",
        name="ML-New Balanced",
        trailing_stop_atr_mult=1.5,
        source_strategy="ml_new",
        stop_mult=1.2,
        target_mult=2.4,
        max_hold_days=6,
        risk_per_trade_pct=1.00,
        ml_probability_threshold=0.58,
    ),

    # 4. Fusion Hydra — algo + ML combined
    PaperPortfolioConfig(
        portfolio_id="fusion_hydra",
        name="Algo + ML",
        trailing_stop_atr_mult=1.5,
        source_strategy="combined",
        stop_mult=1.5,
        target_mult=3.0,
        max_hold_days=10,
        risk_per_trade_pct=1.10,
        ml_probability_threshold=0.55,
    ),

    # 5. Oracle Raven — pure AI (guardrailed)
    PaperPortfolioConfig(
        portfolio_id="oracle_raven",
        name="AI Fast Exit",
        source_strategy="pure_ai",
        stop_mult=1.6,
        target_mult=3.2,
        max_hold_days=5,
        risk_per_trade_pct=0.75,
        ml_probability_threshold=0.57,
    ),

    # 6. Titan Turtle — slow long-hold
    PaperPortfolioConfig(
        portfolio_id="titan_turtle",
        name="Long Hold 30d",
        trailing_stop_atr_mult=2.0,
        source_strategy="long_hold",
        stop_mult=2.2,
        target_mult=4.0,
        max_hold_days=30,
        risk_per_trade_pct=0.80,
        ml_probability_threshold=0.52,
    ),

    # 7. Brainstorm Atlas — UnifiedBrain primary
    PaperPortfolioConfig(
        portfolio_id="brainstorm_atlas",
        name="UnifiedBrain",
        trailing_stop_atr_mult=1.5,
        source_strategy="unified_brain",
        stop_mult=1.5,
        target_mult=3.5,
        max_hold_days=12,
        risk_per_trade_pct=1.00,
        ml_probability_threshold=0.56,
    ),

    # 8. Scalpel Cheetah — fast short-hold ML
    PaperPortfolioConfig(
        portfolio_id="scalpel_cheetah",
        name="ML Scalp 3d",
        source_strategy="ml_new",
        stop_mult=1.0,
        target_mult=1.9,
        max_hold_days=3,
        risk_per_trade_pct=0.70,
        ml_probability_threshold=0.62,
    ),

    # 9. Deep Space Owl — high-confidence combined
    PaperPortfolioConfig(
        portfolio_id="deep_space_owl",
        name="Algo+ML High-Conf",
        trailing_stop_atr_mult=1.5,
        source_strategy="combined",
        stop_mult=1.4,
        target_mult=3.4,
        max_hold_days=9,
        risk_per_trade_pct=0.85,
        ml_probability_threshold=0.66,
    ),

    # 10. Volcano Mantis — aggressive breakout
    PaperPortfolioConfig(
        portfolio_id="volcano_mantis",
        name="Breakout Aggressive",
        source_strategy="algorithm",
        stop_mult=1.1,
        target_mult=2.5,
        max_hold_days=4,
        risk_per_trade_pct=1.25,
    ),

    # 11. Iron Koala — defensive old ML
    PaperPortfolioConfig(
        portfolio_id="iron_koala",
        name="ML Defensive",
        trailing_stop_atr_mult=1.8,
        source_strategy="machine_learning",
        stop_mult=1.8,
        target_mult=2.7,
        max_hold_days=10,
        risk_per_trade_pct=0.50,
        ml_probability_threshold=0.64,
    ),

    # 12. Sapphire Dragon — AI guarded
    PaperPortfolioConfig(
        portfolio_id="sapphire_dragon",
        name="AI Guarded",
        trailing_stop_atr_mult=1.5,
        source_strategy="pure_ai",
        stop_mult=1.3,
        target_mult=3.1,
        max_hold_days=7,
        risk_per_trade_pct=0.65,
        ml_probability_threshold=0.61,
    ),

    # 13. Moonshot Gecko — high-risk ML growth
    PaperPortfolioConfig(
        portfolio_id="moonshot_gecko",
        name="ML-New High-Risk",
        trailing_stop_atr_mult=1.5,
        source_strategy="ml_new",
        stop_mult=1.2,
        target_mult=3.8,
        max_hold_days=8,
        risk_per_trade_pct=1.35,
        ml_probability_threshold=0.54,
    ),

    # 14. Steady Comet — low-risk long-hold
    PaperPortfolioConfig(
        portfolio_id="steady_comet",
        name="Long Hold 45d Low-Risk",
        trailing_stop_atr_mult=2.5,
        source_strategy="long_hold",
        stop_mult=2.5,
        target_mult=3.5,
        max_hold_days=45,
        risk_per_trade_pct=0.45,
        ml_probability_threshold=0.55,
    ),

    # 15. Apex Chimera — best-of-tools blend
    PaperPortfolioConfig(
        portfolio_id="apex_chimera",
        name="Algo+ML Blend",
        trailing_stop_atr_mult=1.5,
        source_strategy="combined",
        stop_mult=1.4,
        target_mult=3.0,
        max_hold_days=10,
        risk_per_trade_pct=1.00,
        ml_probability_threshold=0.59,
    ),

    # ── 16–30: MULTI-TOOL — combine signals across tools; agreement is the gate ──
    # 16. Rule breakout + ML must both flag it.
    PaperPortfolioConfig(
        portfolio_id="consensus_breakout_ml", name="Breakout+ML Consensus", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.0, max_hold_days=8, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.5,
    ),
    # 17. Both ML models (old + new) agree.
    PaperPortfolioConfig(
        portfolio_id="dual_ml_consensus", name="Dual-ML Consensus", source_strategy="multi",
        source_strategies=["machine_learning", "ml_new"], combine_mode="consensus_2",
        stop_mult=1.3, target_mult=2.8, max_hold_days=7, risk_per_trade_pct=0.90, trailing_stop_atr_mult=1.5,
    ),
    # 18. Any 2 of {rule, ML-old, ML-new}.
    PaperPortfolioConfig(
        portfolio_id="triple_threat", name="Triple Threat 2of3", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning", "ml_new"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.2, max_hold_days=9, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.6,
    ),
    # 19. 3 of 4 tools agree — strong confirmation.
    PaperPortfolioConfig(
        portfolio_id="full_agreement", name="Full Agreement 3of4", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning", "ml_new", "combined"], combine_mode="consensus_3",
        stop_mult=1.5, target_mult=3.5, max_hold_days=10, risk_per_trade_pct=1.10, trailing_stop_atr_mult=1.5,
    ),
    # 20. Rule setup + UnifiedBrain both accept.
    PaperPortfolioConfig(
        portfolio_id="rule_plus_brain", name="Rule + Brain", source_strategy="multi",
        source_strategies=["algorithm", "unified_brain"], combine_mode="consensus_2",
        stop_mult=1.5, target_mult=3.4, max_hold_days=10, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.5,
    ),
    # 21. ML + UnifiedBrain agree.
    PaperPortfolioConfig(
        portfolio_id="ml_plus_brain", name="ML + Brain", source_strategy="multi",
        source_strategies=["machine_learning", "unified_brain"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.2, max_hold_days=9, risk_per_trade_pct=0.90, trailing_stop_atr_mult=1.5,
    ),
    # 22. AI stream + ML confirm.
    PaperPortfolioConfig(
        portfolio_id="ai_ml_confirm", name="AI + ML Confirm", source_strategy="multi",
        source_strategies=["pure_ai", "machine_learning"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.0, max_hold_days=6, risk_per_trade_pct=0.80, trailing_stop_atr_mult=1.4,
    ),
    # 23. AI + rule breakout.
    PaperPortfolioConfig(
        portfolio_id="ai_breakout", name="AI + Breakout", source_strategy="multi",
        source_strategies=["pure_ai", "algorithm"], combine_mode="consensus_2",
        stop_mult=1.3, target_mult=2.8, max_hold_days=6, risk_per_trade_pct=0.85, trailing_stop_atr_mult=1.4,
    ),
    # 24. Widest net — any of 4 tools; best-scored wins (union).
    PaperPortfolioConfig(
        portfolio_id="quad_blend", name="Quad Blend (union)", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning", "ml_new", "pure_ai"], combine_mode="union",
        stop_mult=1.4, target_mult=3.0, max_hold_days=8, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.5,
    ),
    # 25. UnifiedBrain + combined agree.
    PaperPortfolioConfig(
        portfolio_id="brain_combined", name="Brain + Combined", source_strategy="multi",
        source_strategies=["unified_brain", "combined"], combine_mode="consensus_2",
        stop_mult=1.5, target_mult=3.6, max_hold_days=11, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.6,
    ),
    # 26. Broad — ≥3 of all 6 tools agree.
    PaperPortfolioConfig(
        portfolio_id="everything_consensus", name="Everything 3+", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning", "ml_new", "combined", "pure_ai", "unified_brain"],
        combine_mode="consensus_3",
        stop_mult=1.5, target_mult=3.5, max_hold_days=10, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.6,
    ),
    # 27. Trend blend — rule + ML-new + long-hold, ride winners.
    PaperPortfolioConfig(
        portfolio_id="momentum_stack", name="Momentum Stack", source_strategy="multi",
        source_strategies=["algorithm", "ml_new", "long_hold"], combine_mode="union",
        stop_mult=1.6, target_mult=3.8, max_hold_days=20, risk_per_trade_pct=0.80, trailing_stop_atr_mult=2.0,
    ),
    # 28. AI + ML + Brain, any 2 agree.
    PaperPortfolioConfig(
        portfolio_id="ai_ml_brain", name="AI+ML+Brain", source_strategy="multi",
        source_strategies=["pure_ai", "machine_learning", "unified_brain"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.2, max_hold_days=8, risk_per_trade_pct=0.85, trailing_stop_atr_mult=1.5,
    ),
    # 29. High conviction — ML + combined + Brain, any 2.
    PaperPortfolioConfig(
        portfolio_id="conviction_blend", name="Conviction Blend", source_strategy="multi",
        source_strategies=["machine_learning", "combined", "unified_brain"], combine_mode="consensus_2",
        stop_mult=1.4, target_mult=3.4, max_hold_days=9, risk_per_trade_pct=1.00, trailing_stop_atr_mult=1.5,
    ),
    # 30. Strictest — rule AND ML AND combined must ALL flag it (intersection).
    PaperPortfolioConfig(
        portfolio_id="triple_intersect", name="Triple Intersect (all)", source_strategy="multi",
        source_strategies=["algorithm", "machine_learning", "combined"], combine_mode="intersection",
        stop_mult=1.5, target_mult=3.5, max_hold_days=10, risk_per_trade_pct=1.10, trailing_stop_atr_mult=1.5,
    ),

    # ── 31: THEMATIC — the social-momentum strategy competing head-to-head ────────
    # Draws its OWN pending picks (social/news scan → AI pick → v2 macro-cluster
    # diversified) from the thematic scanner, not the price screen. Each pick carries
    # its own stop/target (from the signal's stop_pct/target_pct), so stop_mult/
    # target_mult are fallbacks only. Let-winners-run exits (trailing) + long hold
    # match the thematic philosophy (ride momentum, targets 15-300%).
    PaperPortfolioConfig(
        portfolio_id="thematic_momentum",
        name="Thematic Momentum",
        source_strategy="thematic",
        stop_mult=1.5, target_mult=3.5, max_hold_days=25,
        risk_per_trade_pct=1.00,
        trailing_stop_atr_mult=2.0,
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# SKIP-DAY A/B — which portfolios TRADE on Mon/Thu signal bars vs sit them out.
# The Mon/Thu calendar sit-out is a backtested edge (Thu WR 50.4% vs 57.4%,
# n=3974; Mon WR 55.3% vs 66.9%, n=351). Rather than argue priors, split the
# field 15/15 and let all-time ROR decide whether it holds ON THIS BOOK.
# Balanced across strategy archetypes (algo / ML / ml_new / combined / pure_ai /
# long_hold / brain / multi-tool) so the cohort comparison isn't a pure
# single-vs-multi or algo-vs-ML confound. Exits still run for everyone daily.
_TRADE_SKIP_DAYS_IDS: frozenset[str] = frozenset({
    # single-tool (8): one+ from each archetype
    "quantum_lynx", "fusion_hydra", "titan_turtle", "scalpel_cheetah",
    "volcano_mantis", "iron_koala", "sapphire_dragon", "apex_chimera",
    # multi-tool (7)
    "consensus_breakout_ml", "triple_threat", "rule_plus_brain",
    "ai_ml_confirm", "quad_blend", "everything_consensus", "ai_ml_brain",
    # thematic (its social-momentum picks are not breakout-signal-bar-gated → trade any day)
    "thematic_momentum",
})
for _p in PAPER_PORTFOLIOS:
    if _p.portfolio_id in _TRADE_SKIP_DAYS_IDS:
        _p.trade_skip_days = True


# Name → config lookup
_PORTFOLIO_MAP: dict[str, PaperPortfolioConfig] = {p.portfolio_id: p for p in PAPER_PORTFOLIOS}


def get_portfolio(portfolio_id: str) -> PaperPortfolioConfig:
    """Return PaperPortfolioConfig by ID. Raises KeyError if not found."""
    return _PORTFOLIO_MAP[portfolio_id]


def list_portfolios() -> list[str]:
    """Return all portfolio IDs."""
    return [p.portfolio_id for p in PAPER_PORTFOLIOS]


def all_portfolios() -> list[PaperPortfolioConfig]:
    """Return all portfolio configs."""
    return PAPER_PORTFOLIOS
