"""Unified Short-Hold Portfolio Brain for TradingAgents.

Central decision layer that:
  1. Accepts candidates from ALL strategy sources (rule-based, ML, breakout,
     regime, paper-feedback) as a unified pool
  2. Deduplicates: one trade per ticker — multiple source signals → merged
  3. Scores each with a unified alpha_score formula
  4. Assigns tier (A+/A/B/C/NO_TRADE)
  5. Allocates position sizes (risk-dollars based, not equal-shares)
  6. Enforces short-hold constraints: max_hold_days=10, min_rr=1.15
  7. Writes full audit trail for every accept and reject decision

This module does NOT delete or modify any existing strategy logic.
It runs as a parallel layer in paper_trade_unified.py.

Short-hold mode:
  - Target horizon 1–10 trading days (configurable)
  - Reject candidates with horizon_days > max_hold_days
  - Prefer setups with high tbs_prob (target-before-stop)
  - Trailing stop activates at breakeven
  - Hard max-hold exit enforced in paper runner

Usage::

    from tradingagents.portfolio.unified_brain import UnifiedBrain, UnifiedCandidate, SHORT_HOLD_CONFIG

    brain = UnifiedBrain(config=SHORT_HOLD_CONFIG)
    unified = brain.process(
        candidates_by_strategy=candidates_by_strategy,
        account=account,
        account_value=10000.0,
        prices=prices,
        regime_state=regime_state,
        spy_regime="bull",
        vix_level=18.5,
        reliability_tracker=reliability_tracker,
        feedback_tracker=feedback_tracker,
    )
    for uc in unified.accepted:
        print(uc.ticker, uc.tier, uc.alpha_score, uc.shares)
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── Short-hold mode defaults ──────────────────────────────────────────────────

SHORT_HOLD_CONFIG: Dict[str, Any] = {
    # Hold duration
    "max_hold_days": 10,            # hard exit after N trading days
    "min_hold_days": 1,
    "horizon_target_days": 3,       # preferred exit window for scoring
    # Entry gates
    "min_confidence": 0.0,          # disabled: win_prob ROC=0.4684 < 0.5 (anti-predictive). Re-enable after Cycle 17.
    "min_breakout_score": 55.0,     # min breakout_score (0=no gate)
    "ll_hard_cap": 0.50,            # reject if large_loss_prob > this (0.35-0.50 bucket E=+0.29%, safe to include)
    "min_rr": 1.15,                 # min reward:risk ratio. Cycle 44: screener now R:R = 1.2/1.0 = 1.20 (stop raised 0.7→1.0 for label coherence). Floor set 1.15 so cent-rounded 1.20 signals pass reliably (strict < at 1.20 would reject ~half on rounding noise).
    # Tier thresholds — Cycle 38: win_prob removed (anti-predictive). Alpha range ~0.28–0.79.
    "tier_aplus": {"alpha": 0.72, "win_prob": 0.0, "regime_score": 0.85, "breakout_score": 0.0},
    "tier_a":     {"alpha": 0.55, "win_prob": 0.0},
    "tier_b":     {"alpha": 0.38, "win_prob": 0.0},
    # Sizing
    "risk_pct_per_trade": 1.0,      # % of account to risk per trade (ATR-based)
    "position_cap_pct": 20.0,       # max % of account per position
    "position_cap_min_pct": 5.0,    # min position size (for A+ only)
    "max_heat_pct": 75.0,           # max % of account deployed total
    "max_open_positions": 5,
    "max_sector_positions": 2,
    "adv_cap_pct": 0.01,            # max 1% of ADV
    # Tier size multipliers
    "tier_mult_aplus": 1.25, # Cycle 27: partially restored (no-Thu model ROC=0.5253, not full 1.5× until WF>0.55)
    "tier_mult_a": 1.0,
    "tier_mult_b": 0.5,  # Cycle 44: unified with live AlphaEngine TIER_SIZE_MULT B=0.50
    # Regime size factors
    "bear_size_factor": 0.5,
    "neutral_size_factor": 0.75,
    "crisis_size_factor": 0.0,
    # Volatility (VIX) size factors / regime filters
    "vix_low_vol_threshold": 15.0,       # VIX < this → low_vol regime, skip all trades
    "skip_vix_low_vol": True,             # confirmed: low_vol expectancy = -0.248%/trade
    "vix_elevated_threshold": 25.0,
    "vix_crisis_threshold": 35.0,
    "vix_elevated_size_factor": 0.75,
    "vix_crisis_size_factor": 0.5,
    # Exit
    "breakeven_trigger_atr": 1.0,   # move stop to entry after 1 ATR gain
    "trail_atr_mult": 0.5,          # trail at peak - 0.5*ATR
    "partial_profit_fraction": 0.5,   # sell 50% at partial trigger
    "partial_profit_trigger": 0.833,  # Cycle 38: synced with breakeven (stop_mult/target_mult=1.0/1.2)
    # Scoring penalty scales
    "ll_penalty_scale": 1.5,
    "vol_penalty_atr_threshold": 0.04,  # Cycle 42: raised from 0.03 (ATR 3-4% better outcomes)
    "vol_penalty_scale": 1.0,
    "timeout_penalty_scale": 0.3,
    "corr_penalty_scale": 0.25,
    "liq_penalty_scale": 0.20,
    "breakout_max_boost": 0.50,  # Cycle 44: unified with live AlphaEngine (was 0.30)
    "er_clip_max": 3.0,          # unused — ER neutralized (R²=0.012, Cycle 25)
    # Audit
    "write_audit_trail": True,
    "audit_filename_prefix": "unified_brain_audit",
}


# ── Regime score lookup ───────────────────────────────────────────────────────

REGIME_SCORE: Dict[str, float] = {
    "bull": 1.00, "uptrend": 1.00, "buy": 1.00,
    "neutral": 0.75, "sideways": 0.75, "mixed": 0.75,
    "bear": 0.50, "downtrend": 0.50, "sell": 0.40,
    "high_vol_bull": 0.65,
    "high_vol_bear": 0.30,
    "crash_risk": 0.05,
    "crash_rebound": 0.60,
    "unknown": 0.80,
}


# ── UnifiedCandidate ──────────────────────────────────────────────────────────

@dataclass
class UnifiedCandidate:
    """A single trade candidate after merging all signal sources.

    One UnifiedCandidate per ticker. strategy_sources lists every system
    that contributed a signal for this ticker.
    """
    # Identity
    ticker: str
    strategy_sources: List[str]         # all strategies that contributed
    primary_source: str                  # highest-scoring source

    # Entry/exit plan
    direction: str                       # "long" (only long supported)
    entry: float
    stop: float
    take_profit: float
    horizon_days: int                    # target hold days
    atr: float

    # ML signals
    confidence: float                    # win_probability
    expected_return: float
    large_loss_probability: float
    target_before_stop_probability: float
    timeout_probability: float

    # Quality signals
    breakout_score: float                # [0–100]
    regime_score: float                  # [0–1]
    ticker_reliability: float            # [0–1]
    liquidity_score: float               # [0–1]
    volatility_score: float              # [0–1] higher = less volatile (better)

    # Composite
    alpha_score: float = 0.0
    tier: str = "C"                      # A+ / A / B / C / NO_TRADE

    # Audit
    reason: str = ""
    rejection_reason: str = ""
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    # Sizing (filled by allocator)
    shares: int = 0
    risk_dollars: float = 0.0
    reward_risk: float = 0.0
    size_factor: float = 1.0            # combined regime * vix * tier factor

    # Exit plan (filled by allocator)
    breakeven_trigger: float = 0.0
    trail_atr_mult: float = 0.5
    partial_target: float = 0.0
    partial_fraction: float = 0.5
    max_hold_days: int = 10

    # Metadata
    signal_date: str = ""
    scored_at: str = ""

    @property
    def risk_reward(self) -> float:
        if self.entry <= 0 or self.stop <= 0 or self.take_profit <= 0:
            return 0.0
        risk = self.entry - self.stop
        reward = self.take_profit - self.entry
        return reward / risk if risk > 0 else 0.0

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "strategy_sources": self.strategy_sources,
            "primary_source": self.primary_source,
            "direction": self.direction,
            "entry": round(self.entry, 4),
            "stop": round(self.stop, 4),
            "take_profit": round(self.take_profit, 4),
            "horizon_days": self.horizon_days,
            "atr": round(self.atr, 4),
            "confidence": round(self.confidence, 4),
            "expected_return": round(self.expected_return, 4),
            "large_loss_probability": round(self.large_loss_probability, 4),
            "target_before_stop_probability": round(self.target_before_stop_probability, 4),
            "timeout_probability": round(self.timeout_probability, 4),
            "breakout_score": round(self.breakout_score, 2),
            "regime_score": round(self.regime_score, 4),
            "ticker_reliability": round(self.ticker_reliability, 4),
            "liquidity_score": round(self.liquidity_score, 4),
            "volatility_score": round(self.volatility_score, 4),
            "alpha_score": round(self.alpha_score, 5),
            "tier": self.tier,
            "shares": self.shares,
            "risk_dollars": round(self.risk_dollars, 2),
            "reward_risk": round(self.risk_reward, 3),
            "size_factor": round(self.size_factor, 4),
            "reason": self.reason,
            "rejection_reason": self.rejection_reason,
            "score_breakdown": {k: round(v, 5) for k, v in self.score_breakdown.items()},
            "signal_date": self.signal_date,
            "scored_at": self.scored_at,
        }


@dataclass
class BrainResult:
    """Output of UnifiedBrain.process()."""
    accepted: List[UnifiedCandidate]        # A+/A (and B at tiny size)
    watchlist: List[UnifiedCandidate]       # B tier, not sized
    rejected: List[UnifiedCandidate]        # C/NO_TRADE with rejection reasons
    total_input: int = 0
    total_unique_tickers: int = 0
    regime: str = "unknown"
    regime_score: float = 0.80
    vix_level: Optional[float] = None
    run_at: str = ""

    @property
    def all_decisions(self) -> List[UnifiedCandidate]:
        return self.accepted + self.watchlist + self.rejected

    def summary_str(self) -> str:
        tiers = {}
        for uc in self.accepted:
            tiers[uc.tier] = tiers.get(uc.tier, 0) + 1
        tier_str = " ".join(f"{t}:{n}" for t, n in sorted(tiers.items()))
        return (
            f"Unified brain: {len(self.accepted)} accepted ({tier_str}) "
            f"/ {len(self.watchlist)} watchlist "
            f"/ {len(self.rejected)} rejected "
            f"| regime={self.regime} vix={self.vix_level}"
        )


# ── UnifiedBrain ─────────────────────────────────────────────────────────────

class UnifiedBrain:
    """Central portfolio decision layer for short-hold trading.

    Parameters
    ----------
    config : dict
        Override any key from SHORT_HOLD_CONFIG.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg: Dict[str, Any] = dict(SHORT_HOLD_CONFIG)
        if config:
            self.cfg.update(config)

    # ── Merge candidates from multiple strategies ────────────────────────────

    def merge_candidates(
        self,
        candidates_by_strategy: Dict[str, List[Any]],
        exclude_strategies: Optional[List[str]] = None,
    ) -> Dict[str, List[Any]]:
        """Group all candidates by ticker. Candidates from multiple strategies
        for the same ticker are grouped together for deduplication.

        Parameters
        ----------
        candidates_by_strategy : dict
            Keys are strategy names; values are lists of Candidate-like objects
            with .ticker attribute.
        exclude_strategies : list, optional
            Strategies to skip entirely (e.g. ["long_hold"]).

        Returns
        -------
        dict[ticker → list[candidates from all strategies]]
        """
        excluded = set(exclude_strategies or ["long_hold"])
        by_ticker: Dict[str, List[Tuple[str, Any]]] = {}
        for strategy, candidates in candidates_by_strategy.items():
            if strategy in excluded:
                continue
            for c in candidates:
                ticker = getattr(c, "ticker", None)
                if ticker:
                    by_ticker.setdefault(ticker, []).append((strategy, c))
        return by_ticker

    def _best_float(self, candidates_with_sources: List[Tuple[str, Any]], attr: str,
                     default: float, agg: str = "max") -> float:
        vals = []
        for _, c in candidates_with_sources:
            v = getattr(c, attr, None)
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        if not vals:
            return default
        if agg == "max":
            return max(vals)
        if agg == "min":
            return min(vals)
        return sum(vals) / len(vals)

    def _dedup_ticker(
        self,
        ticker: str,
        candidates_with_sources: List[Tuple[str, Any]],
        regime_state: Optional[Any],
        spy_regime: str,
        reliability_tracker: Optional[Any],
        feedback_tracker: Optional[Any],
        config: Dict[str, Any],
    ) -> UnifiedCandidate:
        """Merge multiple strategy candidates for same ticker into one UnifiedCandidate."""
        sources = [s for s, _ in candidates_with_sources]
        all_c = [c for _, c in candidates_with_sources]

        # Take best ML signals
        confidence   = self._best_float(candidates_with_sources, "ml_probability", 0.50, "max")
        expected_ret = self._best_float(candidates_with_sources, "expected_return", 0.00, "max")
        ll_prob      = self._best_float(candidates_with_sources, "large_loss_probability", 0.20, "min")
        tbs_prob     = self._best_float(candidates_with_sources, "target_before_stop_probability", confidence, "max")
        timeout_prob = self._best_float(candidates_with_sources, "timeout_probability", 0.30, "min")
        breakout_sc  = self._best_float(candidates_with_sources, "breakout_score", 0.0, "max")

        # Use entry/stop/target/atr from candidate with highest breakout_score
        # (or highest score if no breakout_score)
        best_c = max(all_c,
            key=lambda c: (
                float(getattr(c, "breakout_score", 0) or 0) * 100
                + float(getattr(c, "ml_probability", 0) or 0) * 10
                + float(getattr(c, "score", 0) or 0)
            )
        )
        entry  = float(getattr(best_c, "entry", 0.0) or 0.0)
        stop   = float(getattr(best_c, "stop", 0.0) or 0.0)
        target = float(getattr(best_c, "target", 0.0) or 0.0)
        atr    = float(getattr(best_c, "atr", 0.0) or 0.0)
        signal_date = str(getattr(best_c, "signal_date", ""))

        # Primary source = strategy with highest alpha_score if available,
        # else highest score
        primary = sources[0]
        best_alpha = -1.0
        for s, c in candidates_with_sources:
            a = float(getattr(c, "alpha_score", 0.0) or 0.0)
            sc = float(getattr(c, "score", 0.0) or 0.0)
            if a + sc > best_alpha:
                best_alpha = a + sc
                primary = s

        # Regime score
        if regime_state is not None:
            reg_score = float(getattr(regime_state, "regime_score", 0.80))
            regime_key = str(getattr(regime_state, "regime", spy_regime)).lower()
        else:
            regime_key = (spy_regime or "unknown").lower()
            reg_score = REGIME_SCORE.get(regime_key, REGIME_SCORE["unknown"])

        # Ticker reliability: default to 0.65 (neutral, rel_mult=1.0) when no tracker.
        # Default of 0.5 gives rel_mult=0.76, penalizing ALL signals by 24% when
        # paper_trade_today.py (which doesn't pass reliability_tracker) is used.
        rel = 0.65
        if reliability_tracker is not None:
            try:
                rel = float(reliability_tracker.get_score(ticker))
            except Exception:
                rel = 0.5

        # Liquidity score: ADV-based (use 0.5 as neutral if not available)
        adv = self._best_float(candidates_with_sources, "adv", None, "max")
        if adv is not None and adv > 0 and entry > 0:
            adv_dollar = adv * entry
            # Score: 1.0 at ≥ $50M ADV, 0.0 at < $500K
            liq_score = min(1.0, math.log10(max(adv_dollar, 1)) / math.log10(50_000_000))
        else:
            liq_score = 0.5  # neutral

        # Volatility score: invert ATR% (lower ATR% = better for short hold)
        atr_pct = atr / entry if entry > 0 else 0.0
        vol_score = max(0.0, 1.0 - atr_pct / 0.06)  # 0% atr → 1.0; ≥ 6% atr → 0.0

        # Feedback multiplier
        feedback_mult = 1.0
        if feedback_tracker is not None:
            try:
                feedback_mult = float(feedback_tracker.get_mult(ticker))
            except Exception:
                feedback_mult = 1.0

        # Compute horizon_days from config max
        horizon_days = min(config.get("horizon_target_days", 3), config.get("max_hold_days", 10))

        return UnifiedCandidate(
            ticker=ticker,
            strategy_sources=sorted(set(sources)),
            primary_source=primary,
            direction="long",
            entry=entry,
            stop=stop,
            take_profit=target,
            horizon_days=horizon_days,
            atr=atr,
            confidence=confidence,
            expected_return=expected_ret,
            large_loss_probability=ll_prob,
            target_before_stop_probability=tbs_prob,
            timeout_probability=timeout_prob,
            breakout_score=breakout_sc,
            regime_score=reg_score,
            ticker_reliability=rel,
            liquidity_score=liq_score,
            volatility_score=vol_score,
            signal_date=signal_date,
            scored_at=dt.datetime.now().isoformat(),
            trail_atr_mult=float(config.get("trail_atr_mult", 0.5)),
            partial_fraction=float(config.get("partial_profit_fraction", 0.5)),
            max_hold_days=int(config.get("max_hold_days", 10)),
            # feedback_mult stored in score_breakdown
            score_breakdown={"feedback_mult": round(feedback_mult, 4)},
        )

    # ── Score one UnifiedCandidate ───────────────────────────────────────────

    def score_one(
        self,
        uc: UnifiedCandidate,
        no_trade: bool = False,
    ) -> UnifiedCandidate:
        """Compute alpha_score and tier. Modifies uc in place, returns uc."""
        cfg = self.cfg
        feedback_mult = uc.score_breakdown.get("feedback_mult", 1.0)

        # ── Hard reject gates ──────────────────────────────────────────────
        if no_trade:
            uc.alpha_score = 0.0
            uc.tier = "NO_TRADE"
            uc.rejection_reason = "regime.no_trade=True or crisis"
            return uc

        if uc.entry <= 0 or uc.stop <= 0 or uc.take_profit <= 0:
            uc.alpha_score = 0.0
            uc.tier = "C"
            uc.rejection_reason = "missing_entry_stop_target"
            return uc

        rr = uc.risk_reward
        if rr < float(cfg.get("min_rr", 1.15)):  # UB-A8: fallback was 1.5, config is 1.15
            uc.alpha_score = 0.0
            uc.tier = "C"
            uc.rejection_reason = f"rr={rr:.2f} < min_rr={cfg['min_rr']}"
            return uc

        if uc.confidence < float(cfg.get("min_confidence", 0.0)):
            uc.alpha_score = 0.0
            uc.tier = "C"
            uc.rejection_reason = f"confidence={uc.confidence:.3f} < min={cfg['min_confidence']}"
            return uc

        if uc.large_loss_probability > float(cfg.get("ll_hard_cap", 0.50)):
            uc.alpha_score = 0.0
            uc.tier = "C"
            uc.rejection_reason = f"large_loss_prob={uc.large_loss_probability:.3f} > cap={cfg['ll_hard_cap']}"
            return uc

        # Reject if horizon too long (soft — only rejects long-hold type setups)
        # We check via timeout_probability as proxy for "will it resolve quickly"
        # Hard horizon gate is enforced by the runner, not the scorer

        # ── Score components ───────────────────────────────────────────────
        # Cycle 38: win_prob removed (WF HC WR=39.5% anti-predictive on 679 OOS rows).
        # Restore when WF ROC > 0.55 AND WF HC WR > base WR after new-geometry retrain.

        # Breakout boost: [1.0, 1+bmax] based on score [0, 100]
        # AE-A6: clip to [0,100] before /100 — engine does this, brain didn't; malformed score>100 blows boost cap
        bmax = float(cfg.get("breakout_max_boost", 0.50))  # UB-A9: fallback was 0.30, config is 0.50
        breakout_boost = 1.0 + (min(max(uc.breakout_score, 0.0), 100.0) / 100.0) * bmax

        # Numerator: regime × breakout (win_prob removed — anti-predictive)
        numerator = uc.regime_score * breakout_boost

        # Penalties (timeout_pen removed: timeout model ROC=0.4023, anti-predictive)
        ll_pen  = float(cfg.get("ll_penalty_scale", 1.5)) * uc.large_loss_probability
        atr_pct = uc.atr / uc.entry if uc.entry > 0 else 0.0
        vol_threshold = float(cfg.get("vol_penalty_atr_threshold", 0.04))
        # Cycle 44: normalize excess ATR by threshold to match live AlphaEngine
        # (was raw excess → ~25× weaker vol penalty than the engine).
        vol_pen = (
            max(0.0, atr_pct - vol_threshold) / max(vol_threshold, 0.001)
        ) * float(cfg.get("vol_penalty_scale", 1.0))
        liq_pen = max(0.0, 1.0 - uc.liquidity_score) * float(cfg.get("liq_penalty_scale", 0.20))
        # Correlation penalty: placeholder (computed by allocator if positions available)
        corr_pen = 0.0

        denominator = 1.0 + ll_pen + vol_pen + liq_pen + corr_pen
        raw_alpha   = numerator / denominator

        # Ticker reliability multiplier [0.6, 1.1]
        rel = uc.ticker_reliability
        if rel >= 0.65:
            rel_mult = 1.0 + (rel - 0.65) / 0.35 * 0.10
        elif rel >= 0.40:
            rel_mult = 0.60 + (rel - 0.40) / 0.25 * 0.40
        else:
            rel_mult = 0.50  # Cycle 44: unified with live AlphaEngine floor (was 0.60)
        rel_mult = max(0.50, min(1.10, rel_mult))  # clamp to design range

        alpha_score = raw_alpha * rel_mult * feedback_mult

        # ── Assign tier ────────────────────────────────────────────────────
        t = cfg
        aplus = t.get("tier_aplus", {})
        a_tier = t.get("tier_a", {})
        b_tier = t.get("tier_b", {})

        # UB-A10: win_prob clauses removed — all thresholds are 0.0 (disabled).
        # The dead clauses were a revert-to-pre-Cycle-44 landmine under partial-config override.
        if (
            alpha_score >= float(aplus.get("alpha", 0.72))
            and uc.regime_score >= float(aplus.get("regime_score", 0.85))
            and uc.breakout_score >= float(aplus.get("breakout_score", 0.0))
        ):
            tier = "A+"
        elif alpha_score >= float(a_tier.get("alpha", 0.55)):
            tier = "A"
        elif alpha_score >= float(b_tier.get("alpha", 0.38)):
            tier = "B"
        else:
            tier = "C"

        uc.alpha_score = round(alpha_score, 5)
        uc.tier = tier

        breakdown = {
            "confidence": round(uc.confidence, 4),
            "er_boost": 1.0,       # neutralized Cycle 25
            "tbs_prob": round(uc.target_before_stop_probability, 4),
            "regime_score": round(uc.regime_score, 4),
            "breakout_boost": round(breakout_boost, 4),
            "numerator": round(numerator, 5),
            "ll_penalty": round(ll_pen, 4),
            "vol_penalty": round(vol_pen, 4),
            "timeout_penalty": 0.0,  # neutralized Cycle 25
            "liq_penalty": round(liq_pen, 4),
            "denominator": round(denominator, 5),
            "raw_alpha": round(raw_alpha, 5),
            "rel_mult": round(rel_mult, 4),
            "feedback_mult": round(feedback_mult, 4),
            "alpha_score": round(alpha_score, 5),
            "rr": round(rr, 3),
        }
        uc.score_breakdown.update(breakdown)

        if tier == "C":
            uc.rejection_reason = (
                f"tier=C: alpha={alpha_score:.3f} conf={uc.confidence:.3f}"
            )
        else:
            uc.reason = (
                f"tier={tier}: alpha={alpha_score:.3f} "
                f"conf={uc.confidence:.3f} "
                f"breakout={uc.breakout_score:.0f} "
                f"rr={rr:.2f}"
            )

        return uc

    # ── Portfolio allocator ──────────────────────────────────────────────────

    def allocate(
        self,
        candidates: List[UnifiedCandidate],
        account_value: float,
        settled_cash: float,
        current_heat_pct: float,          # fraction of account already deployed
        current_positions: Dict[str, Any], # ticker → Position
        vix_level: Optional[float],
        regime_state: Optional[Any],
        spy_regime: str,
    ) -> List[UnifiedCandidate]:
        """Assign shares and risk_dollars to each accepted candidate.

        Modifies candidates in place. Returns only those with shares > 0.
        """
        cfg = self.cfg
        max_heat    = float(cfg.get("max_heat_pct", 75.0)) / 100.0
        cap_pct     = float(cfg.get("position_cap_pct", 20.0)) / 100.0
        risk_pct    = float(cfg.get("risk_pct_per_trade", 1.0)) / 100.0
        adv_cap     = float(cfg.get("adv_cap_pct", 0.01))
        max_pos     = int(cfg.get("max_open_positions", 5))
        max_sector  = int(cfg.get("max_sector_positions", 2))

        # Regime size factor
        if regime_state is not None:
            reg_factor = float(getattr(regime_state, "size_factor", 1.0))
        else:
            r = (spy_regime or "unknown").lower()
            bear_f    = float(cfg.get("bear_size_factor", 0.5))
            neutral_f = float(cfg.get("neutral_size_factor", 0.75))
            if r in ("bear", "sell", "downtrend", "high_vol_bear", "crash_risk"):
                reg_factor = bear_f
            elif r in ("neutral", "sideways", "mixed", "high_vol_bull"):
                reg_factor = neutral_f
            else:
                reg_factor = 1.0

        # VIX size factor
        vix_factor = 1.0
        if vix_level is not None:
            if vix_level >= float(cfg.get("vix_crisis_threshold", 35.0)):
                vix_factor = float(cfg.get("vix_crisis_size_factor", 0.5))
            elif vix_level >= float(cfg.get("vix_elevated_threshold", 25.0)):
                vix_factor = float(cfg.get("vix_elevated_size_factor", 0.75))

        # Sector counts in current portfolio
        sector_counts: Dict[str, int] = {}
        for pos in current_positions.values():
            sec = getattr(pos, "sector", "unknown")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        n_open = len(current_positions)
        deployed = current_heat_pct  # fraction

        sized: List[UnifiedCandidate] = []

        # Sort by alpha_score descending (best first gets capital)
        for uc in sorted(candidates, key=lambda x: x.alpha_score, reverse=True):
            if uc.entry <= 0 or uc.stop <= 0:
                uc.rejection_reason = "missing_entry_or_stop"
                continue
            if n_open >= max_pos:
                uc.rejection_reason = f"max_positions_reached ({max_pos})"
                continue
            if deployed >= max_heat:
                uc.rejection_reason = f"max_heat_reached ({max_heat:.0%})"
                continue

            # Skip already-held tickers
            if uc.ticker in current_positions:
                uc.rejection_reason = "already_in_portfolio"
                continue

            # Sector cap. Cycle 44: read real sector when available and only enforce
            # for KNOWN sectors. Previously sec was hardcoded "unknown", so every fill
            # incremented sector_counts["unknown"] and silently capped the whole book at
            # max_sector positions (defeating max_open_positions).
            sec = getattr(uc, "sector", None) or "unknown"
            if sec != "unknown" and sector_counts.get(sec, 0) >= max_sector:
                uc.rejection_reason = f"sector_cap_reached ({sec}: {max_sector})"
                continue

            # Tier multiplier
            tier_mults = {
                "A+": float(cfg.get("tier_mult_aplus", 1.5)),
                "A":  float(cfg.get("tier_mult_a", 1.0)),
                "B":  float(cfg.get("tier_mult_b", 0.5)),
            }
            tier_mult = tier_mults.get(uc.tier, 0.0)
            if tier_mult <= 0:
                uc.rejection_reason = "tier_C_no_size"
                continue

            # Cycle 44 B-16: smooth heat taper. As the book fills toward max_heat,
            # shrink each successive entry (sqrt taper) instead of hitting a hard wall,
            # improving geometric growth by avoiding lumpy all-at-once exposure.
            heat_taper = math.sqrt(max(0.0, 1.0 - (deployed / max_heat))) if max_heat > 0 else 1.0
            combined_factor = reg_factor * vix_factor * tier_mult * heat_taper
            uc.size_factor = round(combined_factor, 4)

            # ATR-based risk sizing
            stop_dist = uc.entry - uc.stop
            if stop_dist <= 0:
                uc.rejection_reason = "stop_above_entry"
                continue

            risk_dollars = account_value * risk_pct * combined_factor
            raw_shares   = int(math.floor(risk_dollars / stop_dist))

            # Hard position cap scaled by tier_mult so B-tier capped lower than A/A+
            tier_cap_pct = cap_pct * min(1.0, tier_mult)
            cap_shares   = int(math.floor(account_value * tier_cap_pct / uc.entry))
            # Cash ceiling
            cash_shares  = int(math.floor(max(0.0, settled_cash) / uc.entry))
            # ADV cap (use 0.5 neutral liquidity → large stock approximation)
            # If adv not known, skip ADV cap
            adv_shares   = 999_999  # no ADV cap unless we have the data

            final_shares = max(0, min(raw_shares, cap_shares, cash_shares, adv_shares))

            if final_shares <= 0:
                uc.rejection_reason = "no_shares_after_sizing (cash or cap)"
                continue

            actual_risk  = stop_dist * final_shares
            reward_dist  = uc.take_profit - uc.entry
            rr           = reward_dist / stop_dist if stop_dist > 0 else 0.0

            uc.shares       = final_shares
            uc.risk_dollars = round(actual_risk, 2)

            # Exit plan
            uc.breakeven_trigger = round(
                uc.entry + float(cfg.get("breakeven_trigger_atr", 1.0)) * uc.atr, 4
            ) if uc.atr > 0 else uc.entry + stop_dist
            uc.partial_target = round(
                uc.entry + float(cfg.get("partial_profit_trigger", 0.833)) * reward_dist, 4
            )

            # Update heat tracker
            position_pct = (uc.entry * final_shares) / account_value
            deployed += position_pct
            n_open += 1
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

            # Settle cash reduction (approximate — runner handles precise)
            settled_cash = max(0.0, settled_cash - uc.entry * final_shares)

            sized.append(uc)

        return sized

    # ── Main entry point ─────────────────────────────────────────────────────

    def process(
        self,
        candidates_by_strategy: Dict[str, List[Any]],
        account: Optional[Any] = None,
        account_value: float = 10_000.0,
        prices: Optional[Dict[str, float]] = None,
        regime_state: Optional[Any] = None,
        spy_regime: str = "unknown",
        vix_level: Optional[float] = None,
        reliability_tracker: Optional[Any] = None,
        feedback_tracker: Optional[Any] = None,
        output_dir: Optional[Path] = None,
    ) -> BrainResult:
        """Full pipeline: merge → score → tier → allocate → audit.

        Parameters
        ----------
        candidates_by_strategy : dict
            {strategy_name: [Candidate, ...]} from build_candidates().
        account : PaperAccount, optional
            Used to read settled_cash, current positions, heat.
        account_value : float
            Current total portfolio value.
        prices : dict, optional
            {ticker: current_price} for heat calculation.
        regime_state : MarketRegimeState, optional
        spy_regime : str
            Fallback regime label.
        vix_level : float, optional
        reliability_tracker : TickerReliabilityTracker, optional
        feedback_tracker : PaperFeedbackTracker, optional
        output_dir : Path, optional
            If provided, write audit JSONL here.

        Returns
        -------
        BrainResult
        """
        cfg = self.cfg
        now_str = dt.datetime.now().isoformat()

        # Check no-trade regime
        no_trade_regime = False
        if regime_state is not None:
            no_trade_regime = bool(getattr(regime_state, "no_trade", False))
            crash_risk = float(getattr(regime_state, "crash_risk_score", 0.0))
            if crash_risk > 0.70:
                no_trade_regime = True

        # Count total input candidates
        total_input = sum(len(v) for v in candidates_by_strategy.values())

        # Step 1: Merge by ticker (exclude long_hold and pure_ai by default)
        exclude = ["long_hold", "pure_ai"]
        by_ticker = self.merge_candidates(candidates_by_strategy, exclude_strategies=exclude)
        total_unique = len(by_ticker)

        # Step 2: Build UnifiedCandidate per ticker
        unified_candidates: List[UnifiedCandidate] = []
        for ticker, cands_with_sources in by_ticker.items():
            uc = self._dedup_ticker(
                ticker=ticker,
                candidates_with_sources=cands_with_sources,
                regime_state=regime_state,
                spy_regime=spy_regime,
                reliability_tracker=reliability_tracker,
                feedback_tracker=feedback_tracker,
                config=cfg,
            )
            unified_candidates.append(uc)

        # Step 3: Score each
        vix_no_trade = (
            cfg.get("skip_vix_low_vol", True)
            and vix_level is not None
            and vix_level < float(cfg.get("vix_low_vol_threshold", 15.0))
        )
        for uc in unified_candidates:
            self.score_one(uc, no_trade=no_trade_regime or vix_no_trade)

        # Step 4: Separate by tier
        tradeable   = [uc for uc in unified_candidates if uc.tier in ("A+", "A")]
        watchlist_c = [uc for uc in unified_candidates if uc.tier == "B"]
        rejected_c  = [uc for uc in unified_candidates if uc.tier in ("C", "NO_TRADE")]

        # Step 5: Allocate (only A+ and A get sized; B watchlist only if B_trade enabled)
        settled_cash = 0.0
        current_positions: Dict[str, Any] = {}
        current_heat_pct = 0.0

        if account is not None:
            settled_cash = float(getattr(account, "settled_cash", account_value))
            current_positions = dict(getattr(account, "positions", {}))
            if prices and account_value > 0:
                deployed = sum(
                    pos.shares * prices.get(pos.ticker, getattr(pos, "entry_price", 0.0))
                    for pos in current_positions.values()
                )
                current_heat_pct = deployed / account_value
        else:
            settled_cash = account_value

        regime_label = (
            str(getattr(regime_state, "regime", spy_regime)) if regime_state else spy_regime
        )
        regime_score_val = float(
            getattr(regime_state, "regime_score", REGIME_SCORE.get(spy_regime, 0.80))
            if regime_state else REGIME_SCORE.get(spy_regime, 0.80)
        )

        accepted_sized = self.allocate(
            candidates=tradeable,
            account_value=account_value,
            settled_cash=settled_cash,
            current_heat_pct=current_heat_pct,
            current_positions=current_positions,
            vix_level=vix_level,
            regime_state=regime_state,
            spy_regime=spy_regime,
        )

        # Move unsized A/A+ to watchlist
        sized_tickers = {uc.ticker for uc in accepted_sized}
        for uc in tradeable:
            if uc.ticker not in sized_tickers:
                if not uc.rejection_reason:
                    uc.rejection_reason = "not_sized (heat/cash/cap)"
                watchlist_c.append(uc)

        # Sort accepted by alpha_score
        accepted_sized.sort(key=lambda x: x.alpha_score, reverse=True)
        watchlist_c.sort(key=lambda x: x.alpha_score, reverse=True)
        rejected_c.sort(key=lambda x: x.alpha_score, reverse=True)

        result = BrainResult(
            accepted=accepted_sized,
            watchlist=watchlist_c,
            rejected=rejected_c,
            total_input=total_input,
            total_unique_tickers=total_unique,
            regime=regime_label,
            regime_score=regime_score_val,
            vix_level=vix_level,
            run_at=now_str,
        )

        # Step 6: Audit trail
        if cfg.get("write_audit_trail", True) and output_dir is not None:
            self._write_audit(result, output_dir)

        return result

    def _write_audit(self, result: BrainResult, output_dir: Path) -> None:
        """Append all decisions to JSONL audit file."""
        today = dt.date.today().isoformat()
        prefix = self.cfg.get("audit_filename_prefix", "unified_brain_audit")
        audit_path = Path(output_dir) / f"{prefix}_{today}.jsonl"
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            with open(audit_path, "a", encoding="utf-8") as f:
                meta = {
                    "ts": result.run_at,
                    "regime": result.regime,
                    "regime_score": result.regime_score,
                    "vix_level": result.vix_level,
                    "total_input": result.total_input,
                    "total_unique": result.total_unique_tickers,
                    "n_accepted": len(result.accepted),
                    "n_watchlist": len(result.watchlist),
                    "n_rejected": len(result.rejected),
                }
                f.write(json.dumps({"_meta": meta}) + "\n")
                for uc in result.all_decisions:
                    row = uc.to_audit_dict()
                    row["decision"] = (
                        "ACCEPT" if uc.ticker in {x.ticker for x in result.accepted}
                        else "WATCHLIST" if uc.ticker in {x.ticker for x in result.watchlist}
                        else "REJECT"
                    )
                    f.write(json.dumps(row) + "\n")
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("unified_brain").warning("Audit write failed: %s", exc)
