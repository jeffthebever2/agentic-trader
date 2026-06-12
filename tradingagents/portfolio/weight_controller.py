"""Weight Controller: post-HRP constraints — PC-2.

Enforces max_sector, max_single_name, and max_turnover constraints on
a weight vector produced by HRPOptimizer (or any other optimizer).

Usage:
    from tradingagents.portfolio.weight_controller import WeightController

    controller = WeightController(max_single=0.20, max_sector=0.30, max_turnover=0.50)
    weights = controller.enforce(
        weights={"AAPL": 0.25, "MSFT": 0.25, "NVDA": 0.50},
        prev_weights={"AAPL": 0.33, "MSFT": 0.33, "NVDA": 0.33},
        sector_map={"AAPL": "tech", "MSFT": "tech", "NVDA": "tech"},
    )
    # Returns re-normalised weights with constraints enforced.
"""
from __future__ import annotations

from typing import Dict, Optional


class WeightController:
    """Enforce portfolio-level weight constraints after optimization.

    Parameters
    ----------
    max_single : float
        Maximum weight for any single asset. Default 0.20 (20%).
    max_sector : float
        Maximum combined weight for any single sector. Default 0.30 (30%).
    max_turnover : float
        Maximum L1 turnover vs prev_weights. 0.50 = can turn over 50% of book.
        Applied as a soft constraint: if violated, scale down changes.
    min_weight : float
        Assets below this weight after truncation are set to 0.
    """

    def __init__(
        self,
        max_single: float = 0.20,
        max_sector: float = 0.30,
        max_turnover: float = 0.50,
        min_weight: float = 0.005,
    ):
        self.max_single = max_single
        self.max_sector = max_sector
        self.max_turnover = max_turnover
        self.min_weight = min_weight

    def enforce(
        self,
        weights: Dict[str, float],
        prev_weights: Optional[Dict[str, float]] = None,
        sector_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        """Apply constraints and re-normalise.

        Parameters
        ----------
        weights : dict[ticker -> float]
            Raw optimised weights (need not sum to 1 — will be normalised).
        prev_weights : dict[ticker -> float] or None
            Previous period weights for turnover constraint. None = no constraint.
        sector_map : dict[ticker -> sector] or None
            Sector assignments for sector cap. None = no sector constraint.

        Returns
        -------
        dict[str, float]
            Constrained and re-normalised weights.
        """
        if not weights:
            return {}

        tickers = list(weights.keys())
        w = {t: max(0.0, float(v)) for t, v in weights.items()}

        # Normalize
        w = self._normalize(w)

        # ── Single-name cap ────────────────────────────────────────────────
        w = self._apply_single_cap(w)
        w = self._normalize(w)

        # ── Sector cap ────────────────────────────────────────────────────
        if sector_map is not None:
            w = self._apply_sector_cap(w, sector_map)
            w = self._normalize(w)

        # ── Turnover constraint ───────────────────────────────────────────
        if prev_weights is not None:
            w = self._apply_turnover(w, prev_weights)
            w = self._normalize(w)

        # ── Min-weight floor (zero out tiny positions) ────────────────────
        w = {t: (v if v >= self.min_weight else 0.0) for t, v in w.items()}
        w = self._normalize(w)

        return w

    def _normalize(self, w: Dict[str, float]) -> Dict[str, float]:
        total = sum(w.values())
        if total <= 0.0:
            n = max(len(w), 1)
            return {t: 1.0 / n for t in w}
        return {t: v / total for t, v in w.items()}

    def _apply_single_cap(self, w: Dict[str, float]) -> Dict[str, float]:
        """Iteratively clip and renormalize until all weights satisfy the cap."""
        for _ in range(50):
            w = self._normalize(w)
            capped = {t: min(v, self.max_single) for t, v in w.items()}
            if all(abs(capped[t] - w[t]) < 1e-8 for t in w):
                return capped
            w = capped
        return w

    def _apply_sector_cap(
        self, w: Dict[str, float], sector_map: Dict[str, str]
    ) -> Dict[str, float]:
        """Iteratively scale and renormalize until all sector totals satisfy the cap."""
        for _ in range(50):
            w = self._normalize(w)
            # Compute sector totals
            sector_totals: Dict[str, float] = {}
            for t, v in w.items():
                sec = sector_map.get(t, "unknown")
                sector_totals[sec] = sector_totals.get(sec, 0.0) + v

            result = dict(w)
            changed = False
            for sec, total in sector_totals.items():
                if sec == "unknown":
                    continue
                if total > self.max_sector + 1e-9:
                    scale = self.max_sector / total
                    for t in result:
                        if sector_map.get(t, "unknown") == sec:
                            result[t] = result[t] * scale
                    changed = True
            if not changed:
                return result
            w = result
        return w

    def _apply_turnover(
        self, w: Dict[str, float], prev: Dict[str, float]
    ) -> Dict[str, float]:
        # L1 turnover = sum |new - old| / 2
        all_tickers = set(w) | set(prev)
        l1 = sum(abs(w.get(t, 0.0) - prev.get(t, 0.0)) for t in all_tickers) / 2.0
        if l1 <= self.max_turnover:
            return w

        # Scale changes down proportionally
        alpha = self.max_turnover / l1
        result = {}
        for t in all_tickers:
            old = prev.get(t, 0.0)
            new = w.get(t, 0.0)
            result[t] = old + alpha * (new - old)
        return result
