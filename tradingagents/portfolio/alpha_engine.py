"""Unified Alpha Engine for TradingAgents.

Combines all candidate quality signals into one alpha_score and a tier label
(A+/A/B/C/NO_TRADE) that drives position sizing and entry decisions.

Formula (Cycle 38 — win_prob removed, WF HC WR=39.5% anti-predictive on 679 OOS rows)
-------
              regime_score × breakout_boost
alpha_score = ────────────────────────────── × ticker_rel × feedback_mult
              1 + ll_penalty + vol_penalty + corr_penalty + liq_penalty

win_prob removed: current model WF HC WR=39.5% < 54.2% base at threshold=0.6 (679 OOS rows).
Restore when: WF ROC > 0.55 AND WF HC WR > base WR (new geometry model needed).
ll_penalty: large_loss model ROC=0.73 (strong, retained in denominator).

Tier thresholds (Cycle 38 — recalibrated for reg_score-only alpha range ~0.28–0.79):
  win_prob removed (anti-predictive). New formula: alpha = reg_score × breakout_boost / denominator.
  A+   : alpha ≥ 0.72 AND regime_score ≥ 0.85 (strong bull + low ll risk)
  A    : alpha ≥ 0.55 (bull regime or moderate with low ll)
  B    : alpha ≥ 0.38 (sideways or weak regime)
  C    : otherwise (reject entry)
  NO_TRADE: regime.no_trade or crash_risk_score > 0.70

PaperFeedbackTracker
--------------------
Tracks predicted probability vs actual paper-trade outcomes to detect model
drift and scale down aggression before a retrain is needed.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Tier thresholds ─────────────────────────────────────────────────────────
# Cycle 38: win_prob removed from numerator (anti-predictive, WF HC WR=39.5% vs 54.2% base).
# New formula: alpha = reg_score × breakout_boost / denominator
# New alpha range (ll_penalty_scale=1.5, no vol penalty):
#   bull (reg=0.85), ll=0.05 → 0.85/1.075 = 0.791
#   bull (reg=0.85), ll=0.15 → 0.85/1.225 = 0.694
#   moderate (reg=0.75), ll=0.05 → 0.75/1.075 = 0.698
#   sideways (reg=0.60), ll=0.15 → 0.60/1.225 = 0.490
#   bear (reg=0.30), ll=0.05 → 0.30/1.075 = 0.279
# A+ = strong bull + low ll risk. A = bull or moderate. B = sideways. C = bear/reject.
TIER_THRESHOLDS: Dict[str, Dict[str, float]] = {
    # A+: requires strong-bull regime (≥0.85) + low ll risk → alpha naturally >= 0.72
    # A: bull regime with any ll risk, or moderate regime with low ll → alpha >= 0.55
    # B: sideways or weak regime → alpha >= 0.38
    "A+": {"alpha": 0.72, "win_prob": 0.0, "regime_score": 0.85, "breakout_score": 0.0},
    "A":  {"alpha": 0.55, "win_prob": 0.0},
    "B":  {"alpha": 0.38, "win_prob": 0.0},
}

TIER_SIZE_MULT: Dict[str, float] = {
    # A+ = regime-quality + ll-safe signals. 1.25× maintained until paper trade evidence.
    "A+": 1.25,
    "A":  1.00,
    "B":  0.50,
    "C":  0.00,
    "NO_TRADE": 0.00,
}


# ── AlphaResult ───────────────────────────────────────────────────────────────

@dataclass
class AlphaResult:
    """Full output of AlphaEngine.evaluate() for one candidate."""
    ticker: str
    alpha_score: float
    tier: str                   # "A+", "A", "B", "C", "NO_TRADE"
    size_mult: float            # [0.0, 1.5] — multiply against base position size
    rejected: bool
    rejection_reason: str       # "" if not rejected
    # Component inputs (for audit)
    win_prob: float
    expected_return: float
    er_boost: float
    tbs_prob: float
    timeout_prob: float
    large_loss_prob: float
    atr_pct: float
    breakout_score: float
    breakout_boost: float
    regime_score: float
    ticker_reliability: float
    feedback_mult: float
    # Penalty breakdown
    ll_penalty: float
    vol_penalty: float
    timeout_penalty: float
    corr_penalty: float
    liq_penalty: float
    # Internals
    numerator: float
    denominator: float
    audit: Dict[str, float] = field(default_factory=dict)

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "alpha_score": round(self.alpha_score, 5),
            "tier": self.tier,
            "size_mult": round(self.size_mult, 4),
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "win_prob": round(self.win_prob, 4),
            "expected_return": round(self.expected_return, 4),
            "er_boost": round(self.er_boost, 4),
            "tbs_prob": round(self.tbs_prob, 4),
            "timeout_prob": round(self.timeout_prob, 4),
            "large_loss_prob": round(self.large_loss_prob, 4),
            "atr_pct": round(self.atr_pct, 4),
            "breakout_score": round(self.breakout_score, 2),
            "breakout_boost": round(self.breakout_boost, 4),
            "regime_score": round(self.regime_score, 4),
            "ticker_reliability": round(self.ticker_reliability, 4),
            "feedback_mult": round(self.feedback_mult, 4),
            "ll_penalty": round(self.ll_penalty, 4),
            "vol_penalty": round(self.vol_penalty, 4),
            "timeout_penalty": round(self.timeout_penalty, 4),
            "corr_penalty": round(self.corr_penalty, 4),
            "liq_penalty": round(self.liq_penalty, 4),
            "numerator": round(self.numerator, 5),
            "denominator": round(self.denominator, 5),
        }


# ── AlphaEngine ───────────────────────────────────────────────────────────────

class AlphaEngine:
    """Evaluate candidates to produce alpha_score, tier, and size multiplier.

    Parameters
    ----------
    ll_hard_cap : float
        Reject candidates where large_loss_probability > this. Default 0.50.
    min_win_prob : float
        Reject candidates where win_probability < this. Default 0.55.
    min_risk_reward : float
        Reject candidates where risk_reward < this. Default 1.2 (screener R:R=1.71).
    vol_penalty_threshold : float
        ATR% above this incurs a volatility penalty. Default 0.03 (3%).
    vol_penalty_scale : float
        Penalty strength per unit excess ATR. Default 1.0.
    ll_penalty_scale : float
        Denominator weight on large_loss_probability. Default 1.5.
    timeout_penalty_scale : float
        Denominator weight on timeout_probability. Default 0.3.
    corr_penalty : float
        Denominator addition when candidate is highly correlated to portfolio. Default 0.15.
    liq_penalty : float
        Denominator addition when ADV is below min_adv_dollars. Default 0.20.
    breakout_max_boost : float
        Max breakout multiplier (at score=100). Default 0.5 → boost in [1.0, 1.5].
    er_clip_max : float
        Max expected return before clipping. Default 3.0.
    """

    def __init__(
        self,
        ll_hard_cap: float = 0.50,
        min_win_prob: float = 0.0,   # disabled: ROC=0.4684 < 0.5 (anti-predictive). Re-enable after Cycle 17.
        min_risk_reward: float = 1.15,  # Cycle 44: screener now R:R=1.2/1.0=1.20; floor 1.15 so cent-rounded signals pass
        vol_penalty_threshold: float = 0.04,  # raised from 0.03: ATR 3-4% consistently better (5/6 years), penalty was unfair
        vol_penalty_scale: float = 1.0,
        ll_penalty_scale: float = 1.5,
        timeout_penalty_scale: float = 0.3,
        corr_penalty: float = 0.15,
        liq_penalty: float = 0.20,
        breakout_max_boost: float = 0.5,
        er_clip_max: float = 3.0,
    ):
        self.ll_hard_cap = ll_hard_cap
        self.min_win_prob = min_win_prob
        self.min_risk_reward = min_risk_reward
        self.vol_penalty_threshold = vol_penalty_threshold
        self.vol_penalty_scale = vol_penalty_scale
        self.ll_penalty_scale = ll_penalty_scale
        self.timeout_penalty_scale = timeout_penalty_scale
        self.corr_penalty_val = corr_penalty
        self.liq_penalty_val = liq_penalty
        self.breakout_max_boost = breakout_max_boost
        self.er_clip_max = er_clip_max

    def _get(self, obj: Any, attr: str, default: float) -> float:
        val = getattr(obj, attr, None)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def evaluate(
        self,
        candidate: Any,
        regime_state: Optional[Any] = None,
        ticker_reliability: float = 0.5,
        feedback_mult: float = 1.0,
        breakout_score: float = 0.0,
        is_correlated: bool = False,
        adv: Optional[float] = None,
        min_adv_dollars: float = 0.0,
    ) -> AlphaResult:
        """Compute alpha_score and tier for one candidate.

        Parameters
        ----------
        candidate : Candidate
            Candidate object with ml_probability, expected_return, etc.
        regime_state : MarketRegimeState, optional
            Full regime state. Uses .regime_score and .no_trade.
        ticker_reliability : float
            Per-ticker rolling reliability [0, 1]. 0.5 = neutral.
        feedback_mult : float
            Aggression scalar from PaperFeedbackTracker [0.5, 1.0].
        breakout_score : float
            0-100 score from BreakoutScanner. 0 = no breakout data.
        is_correlated : bool
            True if candidate is highly correlated to current portfolio.
        adv : float, optional
            20-day average dollar volume. None = unknown.
        min_adv_dollars : float
            Minimum acceptable ADV. 0 = no liquidity gate.
        """
        ticker = getattr(candidate, "ticker", "?")

        # ── Extract inputs ──────────────────────────────────────────────────
        win_prob     = self._get(candidate, "ml_probability", 0.50)
        expected_ret = self._get(candidate, "expected_return", 0.00)
        large_loss   = self._get(candidate, "large_loss_probability", 0.20)
        tbs          = self._get(candidate, "target_before_stop_probability", win_prob)
        timeout      = self._get(candidate, "timeout_probability", 0.30)
        atr          = self._get(candidate, "atr", 0.00)
        entry_price  = self._get(candidate, "entry", 0.00)
        stop_price   = self._get(candidate, "stop", 0.00)
        target_price = self._get(candidate, "target", 0.00)

        # Clip inputs to safe ranges
        win_prob   = max(0.0, min(1.0, win_prob))
        large_loss = max(0.0, min(1.0, large_loss))
        tbs        = max(0.0, min(1.0, tbs))
        timeout    = max(0.0, min(1.0, timeout))
        breakout_score = max(0.0, min(100.0, breakout_score))
        feedback_mult  = max(0.5, min(1.0, feedback_mult))

        # ATR as fraction of price
        atr_pct = (atr / entry_price) if entry_price > 0 else 0.0

        # Risk/reward ratio
        if entry_price > 0 and stop_price > 0 and target_price > 0:
            risk_dist   = entry_price - stop_price
            reward_dist = target_price - entry_price
            risk_reward = reward_dist / max(risk_dist, 1e-9) if risk_dist > 0 else 0.0
        else:
            risk_reward = 0.0

        # ── Regime score ────────────────────────────────────────────────────
        if regime_state is not None:
            reg_score      = float(getattr(regime_state, "regime_score", 0.80))
            crash_risk     = float(getattr(regime_state, "crash_risk_score", 0.00))
            no_trade_flag  = bool(getattr(regime_state, "no_trade", False))
            regime_label   = str(getattr(regime_state, "regime", "unknown")).lower()
        else:
            reg_score     = 0.80
            crash_risk    = 0.00
            no_trade_flag = False
            regime_label  = "unknown"

        # ── Hard gate: NO_TRADE regime ──────────────────────────────────────
        if no_trade_flag or crash_risk > 0.70:
            reason = (
                f"no_trade=True (regime={regime_label})" if no_trade_flag
                else f"crash_risk_score={crash_risk:.3f} > 0.70"
            )
            return self._rejected("NO_TRADE", ticker, reason, win_prob, expected_ret,
                                  large_loss, tbs, timeout, atr_pct, breakout_score,
                                  reg_score, ticker_reliability, feedback_mult)

        # ── Hard gate: large-loss cap ───────────────────────────────────────
        if large_loss > self.ll_hard_cap:
            return self._rejected(
                "C", ticker,
                f"large_loss={large_loss:.3f} > cap {self.ll_hard_cap}",
                win_prob, expected_ret, large_loss, tbs, timeout, atr_pct,
                breakout_score, reg_score, ticker_reliability, feedback_mult,
            )

        # ── Hard gate: minimum win probability ─────────────────────────────
        if win_prob < self.min_win_prob:
            return self._rejected(
                "C", ticker,
                f"win_prob={win_prob:.3f} < floor {self.min_win_prob}",
                win_prob, expected_ret, large_loss, tbs, timeout, atr_pct,
                breakout_score, reg_score, ticker_reliability, feedback_mult,
            )

        # ── Hard gate: minimum risk/reward ──────────────────────────────────
        # Cycle 44: enforce min R:R for any PRICED candidate, including the malformed
        # rr<=0 case (stop above entry). Previously `risk_reward > 0` let zero-R:R
        # candidates bypass the gate. Unpriced candidates (entry/stop/target=0) skip it.
        if (
            self.min_risk_reward > 0
            and entry_price > 0 and stop_price > 0 and target_price > 0
            and risk_reward < self.min_risk_reward
        ):
            return self._rejected(
                "C", ticker,
                f"risk_reward={risk_reward:.2f} < min {self.min_risk_reward}",
                win_prob, expected_ret, large_loss, tbs, timeout, atr_pct,
                breakout_score, reg_score, ticker_reliability, feedback_mult,
            )

        # ── Compute factors ─────────────────────────────────────────────────

        # Breakout boost: score=0 → 1.0×, score=100 → (1 + breakout_max_boost)×
        breakout_boost = 1.0 + (breakout_score / 100.0) * self.breakout_max_boost

        # Penalty terms (denominator additions)
        ll_penalty = large_loss * self.ll_penalty_scale

        excess_atr  = max(0.0, atr_pct - self.vol_penalty_threshold)
        vol_penalty = (
            excess_atr / max(self.vol_penalty_threshold, 0.001) * self.vol_penalty_scale
        )

        # timeout_penalty neutralized: timeout model ROC=0.4023 (anti-predictive, penalizes wrong signals)
        corr_penalty    = self.corr_penalty_val if is_correlated else 0.0
        liq_penalty     = (
            self.liq_penalty_val
            if (min_adv_dollars > 0 and adv is not None and adv < min_adv_dollars)
            else 0.0
        )

        # Numerator: Cycle 38 — win_prob removed (WF HC WR=39.5% anti-predictive on 679 OOS rows).
        # Restore when WF ROC > 0.55 AND WF HC WR > base WR after new-geometry retrain.
        numerator = reg_score * breakout_boost

        # Denominator (timeout_penalty removed — model ROC=0.4023, anti-predictive)
        denominator = max(
            1.0 + ll_penalty + vol_penalty + corr_penalty + liq_penalty,
            0.01,
        )

        raw_alpha = numerator / denominator

        # Ticker reliability multiplier [0.5, 1.1]
        # AE-A3: rel_clipped was computed but not used in math, then exported as audit value
        # — telemetry reported a different reliability than what scored the trade. Removed.
        if ticker_reliability >= 0.65:
            rel_mult = 1.0 + (ticker_reliability - 0.65) / 0.35 * 0.10   # [1.00, 1.10]
        elif ticker_reliability >= 0.40:
            rel_mult = 0.60 + (ticker_reliability - 0.40) / 0.25 * 0.40  # [0.60, 1.00]
        else:
            rel_mult = 0.50  # max penalty
        rel_mult = max(0.50, min(1.10, rel_mult))

        alpha_score = raw_alpha * rel_mult * feedback_mult

        # ── Assign tier ─────────────────────────────────────────────────────
        tier = self._assign_tier(alpha_score, win_prob, reg_score, breakout_score)
        size_mult = TIER_SIZE_MULT.get(tier, 0.0)

        return AlphaResult(
            ticker=ticker,
            alpha_score=round(alpha_score, 5),
            tier=tier,
            size_mult=size_mult,
            rejected=(tier in ("C", "NO_TRADE")),
            rejection_reason="" if tier not in ("C", "NO_TRADE") else f"tier={tier}",
            win_prob=win_prob,
            expected_return=expected_ret,
            er_boost=1.0,         # neutralized: ER model R²=0.012 (Cycle 25)
            tbs_prob=tbs,
            timeout_prob=timeout,
            large_loss_prob=large_loss,
            atr_pct=atr_pct,
            breakout_score=breakout_score,
            breakout_boost=breakout_boost,
            regime_score=reg_score,
            ticker_reliability=ticker_reliability,  # AE-A3: export real value used in math
            feedback_mult=feedback_mult,
            ll_penalty=ll_penalty,
            vol_penalty=vol_penalty,
            timeout_penalty=0.0,  # neutralized: timeout model ROC=0.4023 (Cycle 25)
            corr_penalty=corr_penalty,
            liq_penalty=liq_penalty,
            numerator=numerator,
            denominator=denominator,
            audit={
                "win_prob": win_prob, "er_boost": 1.0, "tbs": tbs,
                "reg_score": reg_score, "breakout_boost": breakout_boost,
                "numerator": numerator, "ll_penalty": ll_penalty,
                "vol_penalty": vol_penalty, "timeout_penalty": 0.0,
                "corr_penalty": corr_penalty, "liq_penalty": liq_penalty,
                "denominator": denominator, "raw_alpha": raw_alpha,
                "rel_mult": rel_mult, "feedback_mult": feedback_mult,
                "alpha_score": alpha_score,
            },
        )

    def _assign_tier(
        self,
        alpha_score: float,
        win_prob: float,
        regime_score: float,
        breakout_score: float,
    ) -> str:
        t = TIER_THRESHOLDS
        if (
            alpha_score >= t["A+"]["alpha"]
            and win_prob >= t["A+"]["win_prob"]
            and regime_score >= t["A+"]["regime_score"]
            and breakout_score >= t["A+"]["breakout_score"]
        ):
            return "A+"
        if alpha_score >= t["A"]["alpha"] and win_prob >= t["A"]["win_prob"]:
            return "A"
        if alpha_score >= t["B"]["alpha"] and win_prob >= t["B"]["win_prob"]:
            return "B"
        return "C"

    def _rejected(
        self, tier: str, ticker: str, reason: str,
        win_prob: float, expected_ret: float, large_loss: float,
        tbs: float, timeout: float, atr_pct: float,
        breakout_score: float, reg_score: float,
        ticker_reliability: float, feedback_mult: float,
    ) -> AlphaResult:
        return AlphaResult(
            ticker=ticker,
            alpha_score=0.0,
            tier=tier,
            size_mult=0.0,
            rejected=True,
            rejection_reason=reason,
            win_prob=win_prob,
            expected_return=expected_ret,
            er_boost=0.0,
            tbs_prob=tbs,
            timeout_prob=timeout,
            large_loss_prob=large_loss,
            atr_pct=atr_pct,
            breakout_score=breakout_score,
            breakout_boost=1.0,
            regime_score=reg_score,
            ticker_reliability=ticker_reliability,
            feedback_mult=feedback_mult,
            ll_penalty=0.0,
            vol_penalty=0.0,
            timeout_penalty=0.0,
            corr_penalty=0.0,
            liq_penalty=0.0,
            numerator=0.0,
            denominator=1.0,
        )

    def rank(
        self,
        candidates: List[Any],
        regime_state: Optional[Any] = None,
        trades: Optional[List[Dict]] = None,
        feedback_mult: float = 1.0,
        breakout_scores: Optional[Dict[str, float]] = None,
        correlated_tickers: Optional[set] = None,
        adv_map: Optional[Dict[str, float]] = None,
        min_adv_dollars: float = 0.0,
    ) -> List[AlphaResult]:
        """Score and sort all candidates. Rejected (C/NO_TRADE) sort last.

        Parameters
        ----------
        trades : list of trade dicts, optional
            Used for TickerReliabilityTracker.
        breakout_scores : dict {ticker: score}, optional
            Pre-computed breakout scores. Missing tickers default to 0.
        correlated_tickers : set, optional
            Tickers flagged as highly correlated to current portfolio.
        adv_map : dict {ticker: dollar_adv}, optional
            Pre-computed 20d ADV in dollars per ticker.
        """
        from tradingagents.portfolio.ticker_reliability import TickerReliabilityTracker
        reliability_scores: Dict[str, float] = {}
        if trades:
            _tracker = TickerReliabilityTracker()
            for c in candidates:
                t = getattr(c, "ticker", "?")
                if t not in reliability_scores:
                    reliability_scores[t] = _tracker.get_score(t, trades)

        results = []
        for c in candidates:
            t = getattr(c, "ticker", "?")
            results.append(self.evaluate(
                candidate=c,
                regime_state=regime_state,
                ticker_reliability=reliability_scores.get(t, 0.5),
                feedback_mult=feedback_mult,
                breakout_score=(breakout_scores or {}).get(t, 0.0),
                is_correlated=(t in (correlated_tickers or set())),
                adv=(adv_map or {}).get(t),
                min_adv_dollars=min_adv_dollars,
            ))

        # Non-rejected first (by alpha DESC), then rejected last
        results.sort(key=lambda r: (not r.rejected, r.alpha_score), reverse=True)
        return results


# ── PaperFeedbackTracker ──────────────────────────────────────────────────────

class PaperFeedbackTracker:
    """Track predicted probability vs actual outcomes to detect model drift.

    Storage: JSON file at `state_path` (default: feedback_tracker.json next to
    the paper account state file).

    Parameters
    ----------
    window : int
        Rolling window of trades to consider. Default 30.
    drift_halt_threshold : float
        |mean_predicted - actual_wr| above this → reduce aggression. Default 0.15.
    retrain_window : int
        If drift has persisted for this many consecutive trades → retrain recommended. Default 20.
    """

    def __init__(
        self,
        state_path: str = "",
        window: int = 30,
        drift_halt_threshold: float = 0.15,
        retrain_window: int = 20,
    ):
        self.state_path = state_path
        self.window = window
        self.drift_halt_threshold = drift_halt_threshold
        self.retrain_window = retrain_window
        self._records: List[Dict] = []
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    self._records = json.load(f)
            except Exception:
                self._records = []

    def record(
        self, ticker: str, predicted_prob: float, won: bool, timestamp: str = ""
    ) -> None:
        """Record one closed trade outcome."""
        self._records.append({
            "ticker": ticker,
            "pred": float(predicted_prob),
            "won": int(won),
            "t": timestamp,
        })
        # Keep only last 3× window to limit file size
        if len(self._records) > self.window * 3:
            self._records = self._records[-(self.window * 3):]
        self._save()

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w") as f:
                json.dump(self._records, f)
        except Exception:
            pass

    def recent(self, n: Optional[int] = None) -> List[Dict]:
        n = n or self.window
        return self._records[-n:]

    def drift_score(self, n: Optional[int] = None) -> Optional[float]:
        """Mean predicted_prob − actual_win_rate over last n trades.

        Positive = model is overconfident (predicting wins that don't happen).
        Returns None if fewer than 5 trades available.
        """
        recs = self.recent(n)
        if len(recs) < 5:
            return None
        mean_pred = sum(r["pred"] for r in recs) / len(recs)
        actual_wr = sum(r["won"] for r in recs) / len(recs)
        return round(mean_pred - actual_wr, 4)

    def aggression_mult(self) -> float:
        """Scalar [0.5, 1.0] applied to alpha_score.

        - drift < threshold       → 1.0 (normal)
        - drift in [thr, 2×thr]  → linear decay from 1.0 → 0.75
        - drift > 2×threshold    → 0.50 (minimal)
        """
        d = self.drift_score()
        if d is None or abs(d) < self.drift_halt_threshold:
            return 1.0
        excess = abs(d) - self.drift_halt_threshold
        max_excess = self.drift_halt_threshold  # 2× threshold = max penalty
        t = min(1.0, excess / max(max_excess, 0.001))
        return round(1.0 - t * 0.50, 4)  # [1.0 → 0.50]

    def retrain_recommended(self, n: Optional[int] = None) -> bool:
        """True when drift has persisted for retrain_window consecutive trades."""
        recs = self.recent(n or self.retrain_window)
        if len(recs) < self.retrain_window:
            return False
        # Check if all sub-windows of size 5 within this window show drift
        drifted_count = 0
        for i in range(len(recs) - 4):
            chunk = recs[i:i + 5]
            mp = sum(r["pred"] for r in chunk) / 5
            aw = sum(r["won"] for r in chunk) / 5
            if abs(mp - aw) > self.drift_halt_threshold:
                drifted_count += 1
        required = max(1, (len(recs) - 4) // 2)  # majority of sub-windows drifted
        return drifted_count >= required

    def summary(self) -> Dict[str, Any]:
        """Dict summary for logging."""
        d = self.drift_score()
        return {
            "n_records": len(self._records),
            "drift_score": d,
            "aggression_mult": self.aggression_mult(),
            "retrain_recommended": self.retrain_recommended(),
        }
