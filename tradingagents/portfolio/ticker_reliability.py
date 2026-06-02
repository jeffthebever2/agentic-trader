"""Per-ticker rolling reliability scoring for TradingAgents.

A ticker's reliability score measures how well the model's predictions have
performed on *that specific ticker* over recent trades, blended toward a
neutral prior when sample is small.

Formula:
    raw_wr = wins / total_closed_trades (last N)
    blend  = min(1.0, total / blend_at_n)   # how much weight on observed vs prior
    score  = 0.5 * (1 - blend) + raw_wr * blend

score=1.0 → always wins recently
score=0.5 → no data (neutral prior)
score=0.0 → always loses recently

Used in CandidateRanker as optional multiplier on composite score:
    final = composite * reliability_score  (when > 0.5)
    final = composite * 0.5               (when reliability_score < 0.4 → strong penalty)

Usage:
    from tradingagents.portfolio.ticker_reliability import TickerReliabilityTracker
    tracker = TickerReliabilityTracker()
    score = tracker.get_score("AAPL", trades)
    all_scores = tracker.get_all_scores(trades)
"""

from __future__ import annotations

from typing import Any, Dict, List


class TickerReliabilityTracker:
    """Compute per-ticker rolling reliability from a trades list.

    Parameters
    ----------
    window : int
        Number of most recent closed trades per ticker to use. Default 20.
    blend_at_n : int
        Sample size at which the observed win rate is weighted 100%.
        Below this, blended toward 0.5 prior. Default 10.
    penalty_threshold : float
        Score below this is a "penalized" ticker. Default 0.40.
    reward_threshold : float
        Score above this gets a size boost (returned as >1.0 multiplier). Default 0.65.
    """

    def __init__(
        self,
        window: int = 20,
        blend_at_n: int = 10,
        penalty_threshold: float = 0.40,
        reward_threshold: float = 0.65,
        data_path: str | None = None,
    ):
        self.window = window
        self.blend_at_n = blend_at_n
        self.penalty_threshold = penalty_threshold
        self.reward_threshold = reward_threshold

    def get_score(self, ticker: str, trades: List[Dict[str, Any]]) -> float:
        """Return reliability score for one ticker.

        Parameters
        ----------
        ticker : str
        trades : list of trade dicts. Must have 'ticker' and 'pnl' keys.
                 'pnl' > 0 = win, 'pnl' <= 0 = loss.

        Returns
        -------
        float in [0.0, 1.0]. 0.5 if no data.
        """
        def _pnl_value(t: dict):
            """TR-3: accept pnl / pnl_pct / return_pct / actual_return aliases."""
            for k in ("pnl", "pnl_pct", "return_pct", "actual_return"):
                if k in t and t[k] is not None:
                    return float(t[k])
            return None

        ticker_trades = [
            t for t in trades
            if t.get("ticker", "").upper() == ticker.upper()
            and _pnl_value(t) is not None
        ][-self.window:]

        n = len(ticker_trades)
        if n == 0:
            return 0.5

        raw_wr = sum(1 for t in ticker_trades if (_pnl_value(t) or 0) > 0) / n
        blend = min(1.0, n / self.blend_at_n)
        score = 0.5 * (1.0 - blend) + raw_wr * blend
        return round(score, 4)

    def get_all_scores(self, trades: List[Dict[str, Any]]) -> Dict[str, float]:
        """Return {ticker: score} for all tickers that have appeared in trades."""
        tickers = {t.get("ticker", "").upper() for t in trades if t.get("ticker")}
        return {t: self.get_score(t, trades) for t in sorted(tickers)}

    def size_multiplier(self, score: float) -> float:
        """Convert reliability score to a position size multiplier.

        Returns:
          ≥ reward_threshold → 1.10 (mild boost for proven tickers)
          0.50               → 1.00 (neutral)
          penalty_threshold  → 0.60 (significant penalty)
          0.0                → 0.50 (maximum penalty; floor)

        Linear interpolation between anchor points.
        """
        if score >= self.reward_threshold:
            # Linear from reward_threshold→1.0 to 1.10→1.10
            t = (score - self.reward_threshold) / max(1.0 - self.reward_threshold, 0.001)
            return round(1.00 + t * 0.10, 4)  # [1.00, 1.10]
        elif score >= 0.5:
            # Linear from 0.5→1.0 to reward_threshold→1.0 (flat)
            return 1.00
        elif score >= self.penalty_threshold:
            # Linear from penalty_threshold→0.60 to 0.5→1.0
            t = (score - self.penalty_threshold) / max(0.5 - self.penalty_threshold, 0.001)
            return round(0.60 + t * 0.40, 4)  # [0.60, 1.00]
        else:
            # Below penalty_threshold: linear from 0.0→0.50 to penalty_threshold→0.60
            t = score / max(self.penalty_threshold, 0.001)
            return round(0.50 + t * 0.10, 4)  # [0.50, 0.60]
