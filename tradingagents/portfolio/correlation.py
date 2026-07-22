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


# ── Effective-bets + weighted correlation LOAD (thematic v2 risk layer) ───────
# `max_correlation` above is single-worst-pairwise: it flags the one 0.8 pair but
# is blind to "death by a thousand cuts" — a candidate ~0.6 correlated with EVERY
# holding is far more concentrated than that lone pair, yet reports the same 0.6
# max. These helpers add (a) N_eff, the effective number of independent bets in a
# weighted book, and (b) a candidate's weight-weighted correlation LOAD against the
# WHOLE book. Both are pure/network-free and ADVISORY: they only ever SHRINK size
# (via position_sizer.correlation_factor) or annotate — never hard-block. The hard
# concentration floor lives in the theme-macro diversification layer.


def _num(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if _math.isfinite(v) else default


def effective_bets(weights: Sequence[float],
                   corr_matrix: Sequence[Sequence[float]]) -> Optional[float]:
    """Effective number of independent bets: N_eff = (Σw)² / (Σᵢ Σⱼ wᵢ wⱼ ρᵢⱼ).

    Reads as "how many truly independent positions you really hold": N_eff == N for
    a mutually-uncorrelated book and → 1 as everything co-moves. Weights need not be
    normalized (the (Σw)² numerator is scale-free). The diagonal is forced to 1.0 and
    off-diagonal ρ clamped to [-1, 1]; the result is clamped to [1, N] and rounded so
    it stays interpretable as a count of names. Returns None on a mis-shaped matrix,
    a non-positive weight sum, or a non-positive quadratic form (degenerate input) —
    the caller then treats N_eff as unknown (neutral / no advisory)."""
    w = [_num(x) for x in (weights or [])]
    n = len(w)
    if n == 0:
        return None
    if n == 1:
        return 1.0
    m = corr_matrix or []
    if len(m) != n:
        return None
    sw = sum(w)
    if sw <= 0:
        return None
    denom = 0.0
    for i in range(n):
        row = m[i] if i < len(m) else None
        if row is None or len(row) != n:
            return None
        for j in range(n):
            rij = 1.0 if i == j else max(-1.0, min(1.0, _num(row[j], 0.0)))
            denom += w[i] * w[j] * rij
    if denom <= 0:
        return None
    neff = (sw * sw) / denom
    return round(max(1.0, min(float(n), neff)), 3)


def correlation_matrix(closes_by_ticker: Mapping[str, Sequence[float]],
                       *, lookback: int = 60) -> tuple[list[str], list[list[float]]]:
    """Symmetric pairwise return-correlation matrix for a set of tickers.

    Returns ``(tickers, matrix)`` aligned by index: diagonal 1.0, and any pair whose
    correlation is uncomputable filled with 0.0 (treated as independent — conservative
    for the N_eff denominator, i.e. it will not *understate* diversification). Feed the
    matrix, with weights ordered to match ``tickers``, straight into ``effective_bets``."""
    tickers = [t for t in (closes_by_ticker or {})]
    rets = {t: pct_returns(closes_by_ticker[t])[-lookback:] for t in tickers}
    n = len(tickers)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
        for j in range(i + 1, n):
            r = pearson(rets[tickers[i]], rets[tickers[j]])
            v = 0.0 if r is None else r
            mat[i][j] = v
            mat[j][i] = v
    return tickers, mat


def correlation_load(candidate_closes: Sequence[float],
                     existing_closes: Mapping[str, Sequence[float]],
                     weights: Optional[Mapping[str, float]] = None,
                     *, lookback: int = 60, link_threshold: float = 0.65) -> dict:
    """Weight-weighted correlation LOAD of a candidate against the WHOLE book.

    Replaces single-worst-pairwise with a weighted mean of positive correlations::

        load = Σᵢ wᵢ·max(0, ρ_ci) / Σᵢ wᵢ    over holdings with a computable ρ

    so aggregate co-movement (many moderate correlations add up) drives the number,
    not one outlier pair. Negative ρ is floored at 0 — a claimed hedge never *inflates*
    size. Weights come from the caller (e.g. each holding's % of account); missing / ≤0
    weights degrade to equal-weight. ``link_threshold`` (SIZER_CLUSTER_LINK_THRESHOLD)
    counts how many holdings the candidate is "linked" to (ρ ≥ threshold) for the
    advisory. Returns::

        {load, max_corr, with_ticker, corr_vector, linked, n_pairs}

    where ``corr_vector`` maps ticker→ρ (the raw per-holding correlations the wiring
    layer reuses / surfaces), ``max_corr``/``with_ticker`` keep the legacy single-worst
    values for backward-compat + notes. Fail-open: ``load``/``max_corr`` are None when
    nothing is computable (sizer then holds correlation_factor neutral at 1.0)."""
    cand = pct_returns(candidate_closes)[-lookback:]
    corr_vector: dict[str, float] = {}
    best: Optional[float] = None
    best_tk: Optional[str] = None
    for tk, closes in (existing_closes or {}).items():
        r = pearson(cand, pct_returns(closes)[-lookback:])
        if r is None:
            continue
        corr_vector[tk] = round(r, 3)
        if best is None or r > best:
            best, best_tk = r, tk
    if not corr_vector:
        return {"load": None, "max_corr": None, "with_ticker": None,
                "corr_vector": {}, "linked": 0, "n_pairs": 0}
    wmap = weights or {}
    num = 0.0
    den = 0.0
    linked = 0
    for tk, r in corr_vector.items():
        w = _num(wmap.get(tk), 0.0)
        if w <= 0:
            w = 1.0
        num += w * max(0.0, r)
        den += w
        if r >= link_threshold:
            linked += 1
    load = (num / den) if den > 0 else None
    return {
        "load": (round(load, 3) if load is not None else None),
        "max_corr": (round(best, 3) if best is not None else None),
        "with_ticker": best_tk,
        "corr_vector": corr_vector,
        "linked": linked,
        "n_pairs": len(corr_vector),
    }
