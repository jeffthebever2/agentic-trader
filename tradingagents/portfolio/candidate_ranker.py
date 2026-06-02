"""Candidate ranking for TradingAgents paper/live trading.

Candidate ranking for capital allocation ordering.

Cycle 25/26: ML-neutralized formula. All models except large_loss are anti-predictive or
useless on 2026 test set (win_prob ROC=0.4684, tbs ROC=0.4739, er R²=0.012, timeout ROC=0.4023).
Removed from formula. large_loss (ROC=0.7116) retained in denominator penalty.

Formula (Cycle 25/26):
  numerator   = regime_score                              (rule-based, not ML)
  denominator = 1.0 + ll_penalty + vol_penalty           (timeout_penalty removed — anti-predictive)
  composite   = numerator / denominator × ticker_rel_mult

All inputs are clipped to prevent outliers from dominating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING


# ── Regime score lookup ───────────────────────────────────────────────────────
# Maps SPY regime string → [0.0, 1.0] quality multiplier used in ranking.
# Does NOT affect position sizing directly (that uses regime_size_factor).
# Kept for backward compat — MarketRegimeState.regime_score is preferred when available.
REGIME_SCORE: Dict[str, float] = {
    # Strong trend
    "bull":           1.00,
    "uptrend":        1.00,
    "buy":            1.00,
    # Weakening / consolidation
    "neutral":        0.75,
    "sideways":       0.75,
    "mixed":          0.75,
    # Bearish trend
    "bear":           0.50,
    "downtrend":      0.50,
    "sell":           0.40,
    # High-volatility regimes (from build_combined_regime)
    "high_vol_bull":  0.65,  # bull market but vol elevated — reduce but don't stop
    "high_vol_bear":  0.30,  # bear + elevated vol — very cautious
    "crash_risk":     0.05,  # bear + VIX>35 — near-zero new entries
    "crash_rebound":  0.60,  # recovering from crash — cautious opportunistic
    # Default
    "unknown":        0.80,  # unknown → slightly below bull, not worst-case
}


@dataclass
class RankedCandidate:
    """Output of CandidateRanker: a candidate with its computed scores."""
    candidate: Any              # The original Candidate object
    composite_score: float      # Primary ranking score (higher = better)
    win_prob: float
    expected_return: float
    large_loss_prob: float
    tbs_prob: float             # target_before_stop_probability
    timeout_prob: float
    atr_pct: float              # ATR as fraction of price
    regime_score: float
    er_boost: float             # expected_return multiplier applied
    ll_penalty: float           # denominator contribution from large-loss
    vol_penalty: float          # denominator contribution from volatility
    timeout_penalty: float      # denominator contribution from timeout
    rejected: bool = False
    rejection_reason: str = ""
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    def to_audit_dict(self) -> Dict:
        return {
            "ticker": getattr(self.candidate, "ticker", "?"),
            "composite_score": round(self.composite_score, 5),
            "win_prob": round(self.win_prob, 4),
            "expected_return": round(self.expected_return, 4),
            "large_loss_prob": round(self.large_loss_prob, 4),
            "tbs_prob": round(self.tbs_prob, 4),
            "timeout_prob": round(self.timeout_prob, 4),
            "atr_pct": round(self.atr_pct, 4),
            "regime_score": round(self.regime_score, 4),
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "score_breakdown": {k: round(v, 5) for k, v in self.score_breakdown.items()},
        }


class CandidateRanker:
    """Ranks a list of candidates by composite ML + regime + volatility score.

    Parameters
    ----------
    ll_hard_cap : float
        Reject candidates where large_loss_probability > this value regardless
        of win_probability. Default 0.50.
    min_win_prob : float
        Reject candidates where win_probability < this value. Default 0.0
        (DISABLED — win_prob ROC=0.4684 anti-predictive; do not gate on it).
    vol_penalty_atr_threshold : float
        ATR% above this triggers a volatility penalty. Default 0.04 (4%) — CR-A2: unified with AlphaEngine.
    vol_penalty_scale : float
        Penalty strength per unit of excess ATR%. Default 1.0.
    ll_penalty_scale : float
        Denominator contribution from large_loss_probability. Default 1.5.
    timeout_penalty_scale : float
        Denominator contribution from timeout_probability. Default 0.3.
    er_clip_max : float
        Maximum expected return boost (raw er clipped before scaling). Default 3.0.
    """

    def __init__(
        self,
        ll_hard_cap: float = 0.50,
        min_win_prob: float = 0.0,   # disabled: ROC=0.4684 < 0.5. Re-enable after Cycle 17 retrain.
        vol_penalty_atr_threshold: float = 0.04,  # CR-A2: unified with AlphaEngine (was 0.03)
        vol_penalty_scale: float = 1.0,
        ll_penalty_scale: float = 1.5,
        timeout_penalty_scale: float = 0.3,
        er_clip_max: float = 3.0,
    ):
        self.ll_hard_cap = ll_hard_cap
        self.min_win_prob = min_win_prob
        self.vol_penalty_atr_threshold = vol_penalty_atr_threshold
        self.vol_penalty_scale = vol_penalty_scale
        self.ll_penalty_scale = ll_penalty_scale
        self.timeout_penalty_scale = timeout_penalty_scale
        self.er_clip_max = er_clip_max

    def _get_float(self, candidate: Any, attr: str, default: float) -> float:
        val = getattr(candidate, attr, None)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def score_one(
        self,
        candidate: Any,
        spy_regime: str = "unknown",
        ticker_reliability: float = 0.5,
        regime_state: Optional[Any] = None,   # Optional[MarketRegimeState]
    ) -> RankedCandidate:
        """Compute composite score for a single candidate.

        Parameters
        ----------
        spy_regime : str
            Fallback regime label string. Used when regime_state is None.
        ticker_reliability : float
            Per-ticker rolling reliability [0, 1]. 0.5 = neutral (no data).
            Use TickerReliabilityTracker.get_score() to compute.
            Reliability < 0.4 applies a significant penalty to composite score.
        regime_state : MarketRegimeState, optional
            Full probabilistic regime state from MarketRegimeEngine.
            When provided, overrides spy_regime string lookup and uses the
            continuous regime_score, crash_risk_score, and no_trade flag.
        """
        win_prob      = self._get_float(candidate, "ml_probability", 0.50)
        expected_ret  = self._get_float(candidate, "expected_return", 0.00)
        large_loss    = self._get_float(candidate, "large_loss_probability", 0.20)
        tbs           = self._get_float(candidate, "target_before_stop_probability", win_prob)
        timeout       = self._get_float(candidate, "timeout_probability", 0.30)
        atr           = self._get_float(candidate, "atr", 0.00)
        entry_price   = self._get_float(candidate, "entry", 0.00)

        # ── Clip inputs to safe ranges ──────────────────────────────────────
        win_prob   = max(0.0, min(1.0, win_prob))
        large_loss = max(0.0, min(1.0, large_loss))
        tbs        = max(0.0, min(1.0, tbs))
        timeout    = max(0.0, min(1.0, timeout))

        # ATR as fraction of price
        atr_pct = (atr / entry_price) if entry_price > 0 else 0.0

        # ── Regime score ────────────────────────────────────────────────────
        if regime_state is not None:
            # Use rich probabilistic score from MarketRegimeEngine
            reg_score  = float(getattr(regime_state, "regime_score", 0.80))
            regime_key = str(getattr(regime_state, "regime", "unknown")).lower()
            # Hard no-trade: crash_risk or engine-level no_trade
            if getattr(regime_state, "no_trade", False):
                return RankedCandidate(
                    candidate=candidate,
                    composite_score=0.0,
                    win_prob=win_prob, expected_return=expected_ret,
                    large_loss_prob=large_loss, tbs_prob=tbs, timeout_prob=timeout,
                    atr_pct=atr_pct, regime_score=reg_score,
                    er_boost=0.0, ll_penalty=0.0, vol_penalty=0.0, timeout_penalty=0.0,
                    rejected=True,
                    rejection_reason=f"no_trade=True (regime={regime_key})",
                    score_breakdown={},
                )
        else:
            # Fallback: legacy string lookup
            regime_key = (spy_regime or "unknown").lower().strip()
            reg_score = REGIME_SCORE.get(regime_key, REGIME_SCORE["unknown"])

        # ── Rejection checks ────────────────────────────────────────────────
        if large_loss > self.ll_hard_cap:
            return RankedCandidate(
                candidate=candidate,
                composite_score=0.0,
                win_prob=win_prob, expected_return=expected_ret,
                large_loss_prob=large_loss, tbs_prob=tbs, timeout_prob=timeout,
                atr_pct=atr_pct, regime_score=reg_score,
                er_boost=0.0, ll_penalty=0.0, vol_penalty=0.0, timeout_penalty=0.0,
                rejected=True,
                rejection_reason=f"large_loss_probability={large_loss:.3f} > cap {self.ll_hard_cap}",
                score_breakdown={},
            )

        if win_prob < self.min_win_prob:
            return RankedCandidate(
                candidate=candidate,
                composite_score=0.0,
                win_prob=win_prob, expected_return=expected_ret,
                large_loss_prob=large_loss, tbs_prob=tbs, timeout_prob=timeout,
                atr_pct=atr_pct, regime_score=reg_score,
                er_boost=0.0, ll_penalty=0.0, vol_penalty=0.0, timeout_penalty=0.0,
                rejected=True,
                rejection_reason=f"win_probability={win_prob:.3f} < floor {self.min_win_prob}",
                score_breakdown={},
            )

        # ── Penalty terms (denominator) ──────────────────────────────────────
        # ll_penalty: large_loss model ROC=0.7116 — GOOD, keep
        ll_penalty = large_loss * self.ll_penalty_scale

        # Volatility penalty: excess ATR% above threshold (rule-based, keep)
        excess_atr = max(0.0, atr_pct - self.vol_penalty_atr_threshold)
        vol_penalty = excess_atr / max(self.vol_penalty_atr_threshold, 0.001) * self.vol_penalty_scale
        vol_penalty = min(vol_penalty, 3.0)  # Cycle 44: clip (was unbounded; honors "all inputs clipped" invariant)

        # timeout_penalty REMOVED: timeout model ROC=0.4023 (anti-predictive, Cycle 25/26)
        # er_boost REMOVED: ER model R²=0.012 (noise, Cycle 25/26)

        # ── Numerator: Cycle 38 — win_prob removed ───────────────────────────
        # win_prob removed: WF HC WR=39.5% < 54.2% base at threshold=0.6 (679 OOS rows).
        # Restore when WF ROC > 0.55 AND WF HC WR > base WR after new-geometry retrain.
        numerator = reg_score
        er_boost = 1.0   # kept for audit/logging only
        timeout_penalty = 0.0  # kept for audit/logging only

        # ── Denominator ─────────────────────────────────────────────────────
        denominator = 1.0 + ll_penalty + vol_penalty
        denominator = max(denominator, 0.01)  # never divide by zero

        composite = numerator / denominator

        # ── Ticker reliability multiplier ────────────────────────────────────
        # Clip to [0.5, 1.0] so a newly-seen ticker stays neutral, not penalized
        # below 0.5 which would unfairly block candidates we have no data on.
        # Only apply the penalty when we have enough data (reliability < 0.4 means
        # we've seen it lose consistently).
        # Boost for proven tickers (>0.65), floor for unreliable (<0.5→0.5×)
        if ticker_reliability >= 0.65:
            rel_mult = 1.0 + (ticker_reliability - 0.65) / 0.35 * 0.10  # CR-A2: slope 0.10 unified with AlphaEngine (was 0.15)
        elif ticker_reliability >= 0.40:
            rel_mult = 0.60 + (ticker_reliability - 0.40) / 0.25 * 0.40  # [0.60, 1.0]
        else:
            rel_mult = 0.50  # max penalty for unreliable ticker
        composite = composite * rel_mult

        breakdown = {
            "win_prob": win_prob,
            "er_boost": er_boost,
            "tbs": tbs,
            "reg_score": reg_score,
            "numerator": numerator,
            "ll_penalty": ll_penalty,
            "vol_penalty": vol_penalty,
            "timeout_penalty": timeout_penalty,
            "denominator": denominator,
            "composite": composite,
        }

        return RankedCandidate(
            candidate=candidate,
            composite_score=composite,
            win_prob=win_prob,
            expected_return=expected_ret,
            large_loss_prob=large_loss,
            tbs_prob=tbs,
            timeout_prob=timeout,
            atr_pct=atr_pct,
            regime_score=reg_score,
            er_boost=er_boost,
            ll_penalty=ll_penalty,
            vol_penalty=vol_penalty,
            timeout_penalty=timeout_penalty,
            rejected=False,
            rejection_reason="",
            score_breakdown=breakdown,
        )

    def rank(
        self,
        candidates: List[Any],
        spy_regime: str = "unknown",
        trades: Optional[List[Dict]] = None,
        regime_state: Optional[Any] = None,   # Optional[MarketRegimeState]
    ) -> List[RankedCandidate]:
        """Score and sort all candidates. Rejected candidates sort last.

        Parameters
        ----------
        trades : list of trade dicts, optional
            If provided, per-ticker reliability is computed from closed trades
            using TickerReliabilityTracker. Pass account.trades to enable.
        regime_state : MarketRegimeState, optional
            Full regime state from MarketRegimeEngine. When provided, overrides
            spy_regime string and enables no_trade enforcement + probabilistic scoring.
        """
        reliability_scores: Dict[str, float] = {}
        if trades:
            from tradingagents.portfolio.ticker_reliability import TickerReliabilityTracker
            _tracker = TickerReliabilityTracker()
            for c in candidates:
                t = getattr(c, "ticker", "?")
                if t not in reliability_scores:
                    reliability_scores[t] = _tracker.get_score(t, trades)

        scored = [
            self.score_one(
                c,
                spy_regime,
                ticker_reliability=reliability_scores.get(getattr(c, "ticker", "?"), 0.5),
                regime_state=regime_state,
            )
            for c in candidates
        ]
        # Sort: non-rejected by composite_score DESC, rejected last
        scored.sort(key=lambda x: (not x.rejected, x.composite_score), reverse=True)
        return scored

    def allocation_weights(
        self,
        ranked: List[RankedCandidate],
        min_weight: float = 0.5,
        max_weight: float = 2.0,
    ) -> Dict[str, float]:
        """Compute relative allocation weights for non-rejected candidates.

        Returns a dict {ticker: weight} where weight ∈ [min_weight, max_weight].
        These weights are multiplied against the base position size in the sizer
        so that rank-1 gets more capital than rank-5.

        Normalization: weights are scaled so the best candidate gets max_weight
        and the worst non-rejected candidate gets min_weight. Linear interpolation.
        """
        valid = [r for r in ranked if not r.rejected]
        if not valid:
            return {}
        if len(valid) == 1:
            return {getattr(valid[0].candidate, "ticker", "?"): max_weight}

        scores = [r.composite_score for r in valid]
        min_s = min(scores)
        max_s = max(scores)
        score_range = max(max_s - min_s, 1e-9)

        weights = {}
        for r in valid:
            t = (r.composite_score - min_s) / score_range  # 0.0 = worst, 1.0 = best
            w = min_weight + t * (max_weight - min_weight)
            ticker = getattr(r.candidate, "ticker", "?")
            weights[ticker] = round(w, 4)
        return weights
