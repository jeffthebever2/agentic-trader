"""Portfolio-aware position sizer — replaces fixed-dollar / size-in-isolation.

Pure, sync, network-free (same contract as ``portfolio_policy`` / ``holdings_brain``):
given total account value, a candidate's quality + risk metrics, and the CURRENT
book, return a dollar size chosen for the position's *marginal effect on the whole
portfolio* — bigger for high-conviction, high reward-to-risk, low-volatility,
*diversifying* ideas; smaller for low-conviction, volatile, or concentration-adding
ones — then hard-clamped to per-position / per-sector / portfolio-heat / cash limits.

Design notes
------------
* Multiplicative scaler off a base % of account value. Each factor is centred on
  1.0 and clamped, so no single input can blow up the size, and a MISSING input
  degrades that factor to neutral (never crashes money sizing — fail safe).
* The per-position ceiling is the compliance cap (``MAX_POSITION_PCT_OF_ACCOUNT``)
  and is never exceeded regardless of factors.
* The wiring layer (web/api) fetches volatility / sector / correlation and injects
  them; this module does no I/O.
"""
from __future__ import annotations

import math
import os
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

from tradingagents.portfolio.portfolio_policy import conviction_scale

try:
    from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT
except Exception:  # pragma: no cover - compliance always importable in-app
    MAX_POSITION_PCT_OF_ACCOUNT = 10.0


def _finite(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


# ── Inputs ────────────────────────────────────────────────────────────────────
@dataclass
class BookPosition:
    """One position already in the portfolio (real holding or open thematic)."""
    ticker: str
    weight_pct: float = 0.0          # current % of total account value
    sector: Optional[str] = None


@dataclass
class SizingCandidate:
    """The new idea being sized, with whatever risk metrics are available."""
    ticker: str
    conviction: int = 5              # 1..10 (analyst/LLM call)
    score: float = 50.0              # 0..100 composite signal score
    expected_return_pct: float = 0.0 # target_pct (remaining upside proxy)
    stop_pct: float = 0.0            # planned stop distance %
    volatility_pct: Optional[float] = None  # annualized realized vol %, None=unknown
    sector: Optional[str] = None
    max_corr: Optional[float] = None        # max |corr| to existing book, None=unknown
    adv_dollars: Optional[float] = None     # avg daily DOLLAR volume; None=unknown
    avg_volume: Optional[float] = None      # avg daily SHARE volume (alt to adv_dollars)
    price: Optional[float] = None           # used with avg_volume to derive adv_dollars


@dataclass
class SizerConfig:
    base_position_pct: float = 4.0          # anchor: % of account before factors
    max_position_pct: float = float(MAX_POSITION_PCT_OF_ACCOUNT)  # HARD compliance cap
    max_sector_pct: float = 30.0            # max % of account in any one sector
    max_portfolio_heat_pct: float = 80.0    # max % of account deployed at once
    target_vol_pct: float = 40.0            # reference vol; calmer names size up
    vol_factor_floor: float = 0.5
    vol_factor_cap: float = 1.5
    corr_threshold: float = 0.70            # penalize correlation above this
    corr_penalty_max: float = 0.50          # up to -50% size as corr → 1.0
    max_adv_participation_pct: float = 1.0  # never size above this % of avg daily $ volume
    quality_floor: float = 0.4
    quality_cap: float = 2.0
    min_dollar: float = 25.0

    @classmethod
    def from_env(cls, hil: Optional[dict] = None) -> "SizerConfig":
        hil = hil or {}

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, "") or default)
            except (TypeError, ValueError):
                return default

        # hil overrides for the few knobs the user already tunes per-account
        base_pct = _finite(hil.get("base_position_pct"), _f("SIZER_BASE_POSITION_PCT", 4.0))
        heat = _finite(hil.get("max_portfolio_heat"), _f("SIZER_MAX_HEAT_PCT", 80.0))
        # per-position cap is the compliance ceiling — env may LOWER it, never raise.
        cap = min(float(MAX_POSITION_PCT_OF_ACCOUNT), _f("SIZER_MAX_POSITION_PCT", float(MAX_POSITION_PCT_OF_ACCOUNT)))
        return cls(
            base_position_pct=max(0.1, base_pct),
            max_position_pct=max(0.5, cap),
            max_sector_pct=max(cap, _f("SIZER_MAX_SECTOR_PCT", 30.0)),
            max_portfolio_heat_pct=max(10.0, heat),
            target_vol_pct=max(5.0, _f("SIZER_TARGET_VOL_PCT", 40.0)),
            corr_threshold=min(0.99, max(0.1, _f("SIZER_CORR_THRESHOLD", 0.70))),
            corr_penalty_max=min(0.9, max(0.0, _f("SIZER_CORR_PENALTY_MAX", 0.50))),
            max_adv_participation_pct=max(0.0, _f("SIZER_MAX_ADV_PARTICIPATION_PCT", 1.0)),
            min_dollar=max(0.0, _finite(hil.get("min_dollar"), _f("SIZER_MIN_DOLLAR", 25.0))),
        )


# ── Output ──────────────────────────────────────────────────────────────────────
@dataclass
class SizingResult:
    dollars: float
    weight_pct: float
    factors: dict = field(default_factory=dict)
    binding_constraint: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "dollars": round(self.dollars, 2),
            "weight_pct": round(self.weight_pct, 3),
            "factors": {k: round(v, 4) for k, v in self.factors.items()},
            "binding_constraint": self.binding_constraint,
            "notes": self.notes,
        }


# ── Factors (each centred on 1.0, clamped, neutral when data missing) ────────────
def quality_factor(score: float, expected_return_pct: float, stop_pct: float, cfg: SizerConfig) -> float:
    """Signal quality: composite score plus a reward-to-risk nudge.

    Score 50 → 0.6×, 70 → ~1.12×, 85 → 1.5× (same curve as the legacy adaptive
    sizer). Reward:risk = target/stop centres at 2.0 (×1.0); rich R:R adds up to
    +20%, thin R:R cuts to -15%."""
    s = _finite(score, 50.0)
    f = 0.6 + (s - 50.0) / 50.0 * 1.3
    er, sp = _finite(expected_return_pct), _finite(stop_pct)
    if sp > 0 and er > 0:
        rr = er / sp
        f *= max(0.85, min(1.20, 1.0 + (rr - 2.0) * 0.10))
    return max(cfg.quality_floor, min(cfg.quality_cap, f))


def inverse_vol_factor(volatility_pct: Optional[float], cfg: SizerConfig) -> float:
    """Volatility targeting: size ∝ target_vol / realized_vol, clamped.

    A calmer-than-target name sizes up (toward the cap), a wilder one sizes down —
    so equal *risk* contribution, not equal dollars. Unknown vol → 1.0 (neutral)."""
    if volatility_pct is None:
        return 1.0
    v = _finite(volatility_pct, 0.0)
    if v <= 0:
        return 1.0
    return max(cfg.vol_factor_floor, min(cfg.vol_factor_cap, cfg.target_vol_pct / v))


def correlation_factor(max_corr: Optional[float], cfg: SizerConfig) -> float:
    """Diversification: shrink a candidate highly correlated to the existing book.

    Below ``corr_threshold`` → 1.0; scales linearly to (1 - corr_penalty_max) at
    correlation 1.0. Unknown correlation → 1.0 (neutral)."""
    if max_corr is None:
        return 1.0
    c = max(0.0, min(1.0, _finite(max_corr, 0.0)))
    if c <= cfg.corr_threshold:
        return 1.0
    span = max(1e-9, 1.0 - cfg.corr_threshold)
    pen = cfg.corr_penalty_max * (c - cfg.corr_threshold) / span
    return max(1.0 - cfg.corr_penalty_max, 1.0 - pen)


def candidate_adv_dollars(candidate: SizingCandidate) -> Optional[float]:
    """Average daily DOLLAR volume for the candidate: explicit ``adv_dollars`` if
    given, else ``avg_volume × price``. None when neither is available — the
    liquidity factor is then neutral and never clamps."""
    adv = _finite(candidate.adv_dollars, 0.0)
    if adv > 0:
        return adv
    vol, px = _finite(candidate.avg_volume, 0.0), _finite(candidate.price, 0.0)
    if vol > 0 and px > 0:
        return vol * px
    return None


# ── The sizer ─────────────────────────────────────────────────────────────────
def size_position(
    account_value: float,
    candidate: SizingCandidate,
    existing: Optional[List[BookPosition]] = None,
    *,
    cash_available: Optional[float] = None,
    cfg: Optional[SizerConfig] = None,
) -> SizingResult:
    """Dollar size for ``candidate`` given the whole portfolio. See module docstring."""
    cfg = cfg or SizerConfig()
    existing = existing or []
    av = _finite(account_value, 0.0)
    if av <= 0:
        return SizingResult(0.0, 0.0, {}, "no_account_value", "account value unavailable")

    f_conv = conviction_scale(candidate.conviction)
    f_q = quality_factor(candidate.score, candidate.expected_return_pct, candidate.stop_pct, cfg)
    f_vol = inverse_vol_factor(candidate.volatility_pct, cfg)
    f_corr = correlation_factor(candidate.max_corr, cfg)

    base = av * cfg.base_position_pct / 100.0
    raw = base * f_conv * f_q * f_vol * f_corr

    # Liquidity / ADV throttle: never take more than max_adv_participation_pct of
    # average daily dollar volume. A hard cap (not a multiplier) so it can be the
    # binding constraint; reported as a factor = how much it throttles the target.
    adv = candidate_adv_dollars(candidate)
    if adv and adv > 0 and cfg.max_adv_participation_pct > 0:
        liq_cap = adv * cfg.max_adv_participation_pct / 100.0
        f_liq = min(1.0, liq_cap / raw) if raw > 0 else 1.0
    else:
        liq_cap = math.inf          # missing ADV → neutral, no clamp
        f_liq = 1.0

    factors = {
        "conviction": f_conv, "quality": f_q, "inverse_vol": f_vol,
        "correlation": f_corr, "liquidity": f_liq, "base_dollar": base, "raw_dollar": raw,
    }

    # ── Hard constraints (portfolio-level). Pick the smallest binding limit. ──
    pos_cap = av * cfg.max_position_pct / 100.0
    deployed = sum(av * _finite(p.weight_pct) / 100.0 for p in existing)
    heat_room = av * cfg.max_portfolio_heat_pct / 100.0 - deployed

    sec = (candidate.sector or "").strip().lower()
    if sec:
        sector_dollars = sum(
            av * _finite(p.weight_pct) / 100.0
            for p in existing if (p.sector or "").strip().lower() == sec
        )
        sector_room = av * cfg.max_sector_pct / 100.0 - sector_dollars
    else:
        sector_room = math.inf  # unknown sector → no sector clamp

    cash_room = _finite(cash_available, math.inf) if cash_available is not None else math.inf

    limits = [
        ("target_size", raw),
        ("per_position_cap", pos_cap),
        ("sector_cap", sector_room),
        ("portfolio_heat", heat_room),
        ("liquidity_adv", liq_cap),
        ("cash", cash_room),
    ]
    binding, size = min(limits, key=lambda kv: kv[1])
    size = max(0.0, size)

    if size < cfg.min_dollar:
        why = binding if binding != "target_size" else "below_min_dollar"
        note = (
            f"{candidate.ticker}: sized ${size:,.0f} < min ${cfg.min_dollar:,.0f} "
            f"(limited by {why}) — skip."
        )
        return SizingResult(0.0, 0.0, factors, why, note)

    note = (
        f"{candidate.ticker}: ${size:,.0f} ({size / av * 100:.1f}% of ${av:,.0f}) — "
        f"conv×{f_conv:.2f} quality×{f_q:.2f} vol×{f_vol:.2f} corr×{f_corr:.2f} liq×{f_liq:.2f}; "
        f"bound by {binding}."
    )
    return SizingResult(round(size, 2), round(size / av * 100.0, 3), factors, binding, note)


# ── Helpers for the wiring layer (still pure — closes injected) ─────────────────
def realized_vol_pct(closes, periods_per_year: int = 252) -> Optional[float]:
    """Annualized realized volatility % from a close series, or None if too short."""
    from tradingagents.portfolio.correlation import pct_returns
    rets = pct_returns(closes)
    if len(rets) < 5:
        return None
    try:
        sd = statistics.pstdev(rets)
    except statistics.StatisticsError:
        return None
    return sd * math.sqrt(periods_per_year) * 100.0
