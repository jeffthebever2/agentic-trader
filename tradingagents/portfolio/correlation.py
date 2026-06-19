"""Portfolio correlation checks to reduce hidden concentration risk."""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd
import yfinance as yf


class CorrelationAnalyzer:
    def __init__(self, threshold: float = 0.70, max_high_corr: int = 2):
        self.threshold = threshold
        self.max_high_corr = max_high_corr

    def get_correlation_matrix(self, tickers: List[str], period: str = "1y") -> pd.DataFrame:
        data = yf.download(tickers, period=period, progress=False, auto_adjust=False)
        if isinstance(data.columns, pd.MultiIndex):
            if "Adj Close" in data.columns.get_level_values(0):
                prices = data["Adj Close"]
            else:
                prices = data["Close"]
        else:
            prices = data[["Adj Close"]] if "Adj Close" in data else data[["Close"]]
            prices.columns = tickers[:1]
        return prices.pct_change(fill_method=None).dropna(how="all").corr()

    def check_concentration_risk(self, portfolio, new_ticker: str) -> Tuple[bool, str]:
        current_tickers = list(portfolio.positions.keys())
        if len(current_tickers) < 2:
            return True, "Not enough positions for correlation risk"

        new_ticker = new_ticker.upper()
        all_tickers = current_tickers + [new_ticker]
        try:
            corr_matrix = self.get_correlation_matrix(all_tickers)
            if new_ticker not in corr_matrix:
                return True, "Correlation data unavailable"
            new_corr = corr_matrix[new_ticker].drop(labels=[new_ticker], errors="ignore")
            high_corr = new_corr[new_corr > self.threshold]
            # Cycle 44 SR-10: block when the new name would be the (max_high_corr)-th
            # correlated position, not the (max_high_corr+1)-th. `>` previously allowed
            # a 3-way correlated cluster when max_high_corr=2.
            if len(high_corr) >= self.max_high_corr:
                names = ", ".join(high_corr.index.astype(str))
                return False, (
                    f"{new_ticker} is highly correlated with {len(high_corr)} "
                    f"positions ({names})"
                )
            return True, "OK to buy"
        except Exception as exc:
            return True, f"Correlation check unavailable: {exc}"


# ── Pure, network-free correlation helpers (for the thematic risk layer) ──────
# The class above couples to yfinance/pandas. These functions take injected price
# series so the correlation concentration guard is deterministic and unit-testable
# offline; the caller supplies closes (e.g. from the existing price cache).
import math as _math
from typing import Mapping, Optional, Sequence


def pct_returns(closes: Sequence[float]) -> list[float]:
    """Daily simple returns from a close series; skips non-finite/non-positive."""
    out: list[float] = []
    prev: Optional[float] = None
    for x in closes or []:
        try:
            v = float(x)
        except (TypeError, ValueError):
            prev = None
            continue
        if not _math.isfinite(v) or v <= 0:
            prev = None
            continue
        if prev is not None:
            out.append((v - prev) / prev)
        prev = v
    return out


def pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Pearson correlation over the overlapping tail; None if too short / no variance."""
    a = [float(x) for x in (a or []) if isinstance(x, (int, float)) and _math.isfinite(float(x))]
    b = [float(x) for x in (b or []) if isinstance(x, (int, float)) and _math.isfinite(float(x))]
    n = min(len(a), len(b))
    if n < 5:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return max(-1.0, min(1.0, cov / _math.sqrt(va * vb)))


def max_correlation(candidate_closes: Sequence[float],
                    existing_closes: Mapping[str, Sequence[float]],
                    *, lookback: int = 60) -> dict:
    """Highest return-correlation of the candidate with any existing holding →
    {max_corr, with_ticker}."""
    cand = pct_returns(candidate_closes)[-lookback:]
    best: Optional[float] = None
    best_tk: Optional[str] = None
    for tk, closes in (existing_closes or {}).items():
        r = pearson(cand, pct_returns(closes)[-lookback:])
        if r is None:
            continue
        if best is None or r > best:
            best, best_tk = r, tk
    return {"max_corr": (round(best, 3) if best is not None else None), "with_ticker": best_tk}


def correlation_ok(candidate_closes: Sequence[float],
                   existing_closes: Mapping[str, Sequence[float]],
                   *, max_corr: float = 0.85) -> bool:
    """True if the candidate is NOT excessively correlated with the existing book.
    Fail-open on missing/uncomputable data (advisory concentration guard)."""
    if not existing_closes:
        return True
    mc = max_correlation(candidate_closes, existing_closes)["max_corr"]
    return True if mc is None else mc < max_corr
