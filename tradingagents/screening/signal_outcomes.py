"""Signal outcome tracking + adaptive source weights — pure, deterministic.

The thematic scanner scores tickers from ~19 sources with STATIC weights: a
mention from trusted_twitter is always worth what the hardcoded merge table says,
whether or not trusted_twitter's picks have actually gone anywhere. Nothing in
the pipeline ever asks "did the signals this source surfaced make money?" — the
system has no memory of its own accuracy.

This module closes that loop without needing trades to happen:

1. **Record** — every scan appends its top-ranked tickers with a price snapshot
   and the per-source score breakdown (``thematic_signal_outcomes.jsonl``).
2. **Evaluate** — later scans fill in forward returns (1d / 5d) for the recorded
   rows once enough time has elapsed and a fresh price is available.
3. **Weight** — per-source hit-rates are computed by attributing each evaluated
   row to its sources proportionally to their score share, with Laplace-style
   smoothing toward the global baseline so a source with 3 lucky picks doesn't
   get cranked to the ceiling. Real closed trades (which carry pnl) fold in at
   a higher observation weight. Output: clamped multipliers (default 0.6–1.4)
   the merge step applies to each source's contribution.

Everything here is pure — file IO is confined to ``load_rows``/``save_rows``/
``load_weights``/``save_weights`` helpers so the core logic is unit-testable
with plain lists and dicts.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ── Config (env-overridable, mirrors the BUZZ_* / SIZER_* pattern) ───────────
@dataclass(frozen=True)
class OutcomeConfig:
    # Forward-return horizons: label → minimum elapsed hours before it can be
    # filled. 1d fills after ~a trading day; 5d after a calendar week's trading.
    h1_hours: float = 20.0
    h5_hours: float = 120.0

    top_n: int = 25              # rank cutoff for recording a scan's tickers
    max_rows: int = 2000         # retention cap for the outcomes file
    max_age_days: float = 90.0   # drop rows older than this

    # Weighting: smoothed hit-rate vs global baseline, scaled by k, clamped.
    alpha: float = 8.0           # pseudo-observations pulled toward the baseline
    k: float = 1.5               # sensitivity of multiplier to hit-rate edge
    k_ret: float = 3.0           # small tilt from smoothed avg forward return
    lo: float = 0.6
    hi: float = 1.4
    trade_weight: float = 3.0    # a real closed trade counts this much vs 1 scan row
    min_weight_obs: float = 6.0  # below this effective n, stay at 1.0 (not enough data)

    @staticmethod
    def from_env() -> "OutcomeConfig":
        g = os.getenv
        def f(name: str, d: float) -> float:
            try:
                return float(g(name, str(d)))
            except (TypeError, ValueError):
                return d
        return OutcomeConfig(
            h1_hours=f("OUTCOME_H1_HOURS", 20.0),
            h5_hours=f("OUTCOME_H5_HOURS", 120.0),
            top_n=int(f("OUTCOME_TOP_N", 25)),
            max_rows=int(f("OUTCOME_MAX_ROWS", 2000)),
            max_age_days=f("OUTCOME_MAX_AGE_DAYS", 90.0),
            alpha=f("OUTCOME_ALPHA", 8.0),
            k=f("OUTCOME_K", 1.5),
            k_ret=f("OUTCOME_K_RET", 3.0),
            lo=f("OUTCOME_WEIGHT_LO", 0.6),
            hi=f("OUTCOME_WEIGHT_HI", 1.4),
            trade_weight=f("OUTCOME_TRADE_WEIGHT", 3.0),
            min_weight_obs=f("OUTCOME_MIN_WEIGHT_OBS", 6.0),
        )


DEFAULT = OutcomeConfig()


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


# Keys the scanner writes into the per-ticker breakdown dict for LOGGING, which
# are not sources and must never be learned from. `bull_bear_ratio` is capped at
# 999.0, so an all-bullish name contributed a ~999-point "source" — measured on
# production data these six keys absorbed 82.8% of every observation's
# attribution (bull_bear_ratio alone 73.1%), leaving real feeds 17.2%. That
# inflated every source's observation requirement past the min_weight_obs floor,
# so after 53 scans the weights file had never been written and every source
# multiplier was permanently 1.0 — the adaptive learning loop was inert.
_NON_SOURCE_KEYS = frozenset({
    "bull_contrib", "bear_contrib", "neutral_contrib",
    "volume_contrib", "buzz_sentiment", "bull_bear_ratio",
    # Derived SCORE ADJUSTMENTS, not feeds. They are positive floats written into
    # the same breakdown dict, so they were being learned from as if a source
    # named "multi_source_bonus" had predictive value of its own.
    "multi_source_bonus", "insider_social_combo", "single_source_dampener",
})


# ── 1. Record ─────────────────────────────────────────────────────────────────
def record_scan(
    rows: list[dict],
    ranked: list[tuple],
    breakdown: dict[str, dict],
    prices: dict[str, float],
    now: datetime,
    cfg: OutcomeConfig = DEFAULT,
) -> list[dict]:
    """Append one outcome row per top-ranked ticker with a known price.

    A ticker with an OPEN row (5d horizon not yet filled) is skipped — otherwise
    a name that trends for a week would contribute 40+ near-duplicate
    observations and drown everything else.

    Only real SOURCES are recorded — see ``_NON_SOURCE_KEYS``. The caller's
    breakdown dict doubles as a telemetry sink, and those fields were being
    learned from as if they were feeds.
    """
    open_tickers = {
        r.get("ticker") for r in rows
        if not (r.get("fwd") or {}).get("5d")
    }
    out = list(rows)
    for ticker, score in ranked[: cfg.top_n]:
        t = str(ticker).upper()
        price = prices.get(t)
        if not price or price <= 0 or t in open_tickers:
            continue
        sources = {
            k: float(v) for k, v in (breakdown.get(t) or {}).items()
            if isinstance(v, (int, float)) and v > 0 and k not in _NON_SOURCE_KEYS
        }
        if not sources:
            continue
        out.append({
            "ts": now.isoformat(),
            "ticker": t,
            "score": round(float(score), 2),
            "price": round(float(price), 4),
            "sources": sources,
            "fwd": {},
        })
    return out


def pending_tickers(rows: list[dict], now: datetime, cfg: OutcomeConfig = DEFAULT) -> set[str]:
    """Tickers whose rows have an unfilled horizon that is now old enough to fill
    — i.e. the tickers the caller should fetch a price for."""
    need: set[str] = set()
    for r in rows:
        ts = _parse_ts(r.get("ts", ""))
        if ts is None:
            continue
        age_h = (now - ts).total_seconds() / 3600.0
        fwd = r.get("fwd") or {}
        if ("1d" not in fwd and age_h >= cfg.h1_hours) or ("5d" not in fwd and age_h >= cfg.h5_hours):
            need.add(str(r.get("ticker", "")).upper())
    need.discard("")
    return need


# ── 2. Evaluate ───────────────────────────────────────────────────────────────
def update_forward_returns(
    rows: list[dict],
    prices: dict[str, float],
    now: datetime,
    cfg: OutcomeConfig = DEFAULT,
) -> int:
    """Fill 1d/5d forward returns in-place where enough time has elapsed and a
    price is available. Returns how many horizon slots were filled."""
    filled = 0
    for r in rows:
        ts = _parse_ts(r.get("ts", ""))
        entry = float(r.get("price", 0) or 0)
        if ts is None or entry <= 0:
            continue
        t = str(r.get("ticker", "")).upper()
        price = prices.get(t)
        if not price or price <= 0:
            continue
        age_h = (now - ts).total_seconds() / 3600.0
        fwd = r.setdefault("fwd", {})
        for label, min_h in (("1d", cfg.h1_hours), ("5d", cfg.h5_hours)):
            if label not in fwd and age_h >= min_h:
                fwd[label] = {
                    "ret": round(float(price) / entry - 1.0, 5),
                    "ts": now.isoformat(),
                }
                filled += 1
    return filled


def trim_rows(rows: list[dict], now: datetime, cfg: OutcomeConfig = DEFAULT) -> list[dict]:
    """Drop rows older than max_age_days, keep the newest max_rows."""
    cutoff = now - timedelta(days=cfg.max_age_days)
    kept = [r for r in rows if (_parse_ts(r.get("ts", "")) or now) >= cutoff]
    return kept[-cfg.max_rows:]


# ── 3. Weight ─────────────────────────────────────────────────────────────────
@dataclass
class SourceStats:
    n_eff: float = 0.0     # attribution-weighted observation count
    hits: float = 0.0      # attribution-weighted wins (fwd return > 0)
    ret_sum: float = 0.0   # attribution-weighted forward returns

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n_eff if self.n_eff > 0 else 0.0

    @property
    def avg_ret(self) -> float:
        return self.ret_sum / self.n_eff if self.n_eff > 0 else 0.0


def _row_outcome(r: dict) -> Optional[float]:
    """Best available forward return for a row: 5d preferred, else 1d."""
    fwd = r.get("fwd") or {}
    for label in ("5d", "1d"):
        h = fwd.get(label)
        if isinstance(h, dict) and isinstance(h.get("ret"), (int, float)):
            return float(h["ret"])
    return None


def compute_source_stats(
    rows: list[dict],
    trades: Optional[list[dict]] = None,
    cfg: OutcomeConfig = DEFAULT,
) -> dict[str, SourceStats]:
    """Attribute each evaluated observation to its sources proportionally to
    their share of the ticker's score breakdown.

    ``trades`` rows are real closed trades: {"sources": {...}, "pnl_pct": 0.12}
    (pnl_pct as a fraction). They count ``trade_weight``× a scan observation.
    """
    stats: dict[str, SourceStats] = {}

    def _fold(sources: dict, ret: float, obs_weight: float) -> None:
        total = sum(v for v in sources.values() if v > 0)
        if total <= 0:
            return
        hit = 1.0 if ret > 0 else 0.0
        for src, pts in sources.items():
            if pts <= 0:
                continue
            share = (pts / total) * obs_weight
            s = stats.setdefault(src, SourceStats())
            s.n_eff += share
            s.hits += share * hit
            s.ret_sum += share * ret

    for r in rows:
        ret = _row_outcome(r)
        if ret is None:
            continue
        _fold(r.get("sources") or {}, ret, 1.0)
    for tr in trades or []:
        pnl = tr.get("pnl_pct")
        srcs = tr.get("sources") or {}
        if isinstance(pnl, (int, float)) and srcs:
            _fold(srcs, float(pnl), cfg.trade_weight)
    return stats


def source_weights(
    rows: list[dict],
    trades: Optional[list[dict]] = None,
    cfg: OutcomeConfig = DEFAULT,
) -> dict[str, float]:
    """Clamped per-source multipliers from smoothed hit-rate + return edge.

    A source only moves off 1.0 once it has ``min_weight_obs`` effective
    observations; the smoothing pulls everything toward the GLOBAL baseline so
    weights reflect *relative* accuracy, not a hot market.
    """
    stats = compute_source_stats(rows, trades, cfg)
    total_n = sum(s.n_eff for s in stats.values())
    if total_n <= 0:
        return {}
    baseline_hit = sum(s.hits for s in stats.values()) / total_n
    baseline_ret = sum(s.ret_sum for s in stats.values()) / total_n

    out: dict[str, float] = {}
    for src, s in stats.items():
        if s.n_eff < cfg.min_weight_obs:
            out[src] = 1.0
            continue
        hit_s = (cfg.alpha * baseline_hit + s.hits) / (cfg.alpha + s.n_eff)
        ret_s = (cfg.alpha * baseline_ret + s.ret_sum) / (cfg.alpha + s.n_eff)
        edge = cfg.k * (hit_s - baseline_hit) + cfg.k_ret * max(-0.05, min(0.05, ret_s - baseline_ret))
        out[src] = round(max(cfg.lo, min(cfg.hi, 1.0 + edge)), 3)
    return out


# ── File IO (the only impure part) ────────────────────────────────────────────
def load_rows(path: "Path | str") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if isinstance(r, dict):
                    rows.append(r)
            except (ValueError, TypeError):
                continue
    except OSError:
        return []
    return rows


def save_rows(path: "Path | str", rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(p)


def load_weights(path: "Path | str") -> dict[str, float]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        w = data.get("weights", data) if isinstance(data, dict) else {}
        return {str(k): float(v) for k, v in w.items()
                if isinstance(v, (int, float)) and 0.1 <= float(v) <= 5.0}
    except (ValueError, TypeError, OSError):
        return {}


def save_weights(path: "Path | str", weights: dict[str, float], now: datetime,
                 stats: Optional[dict[str, SourceStats]] = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"ts": now.isoformat(), "weights": weights}
    if stats:
        payload["stats"] = {
            k: {"n_eff": round(v.n_eff, 2), "hit_rate": round(v.hit_rate, 4),
                "avg_ret": round(v.avg_ret, 5)}
            for k, v in stats.items()
        }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(p)
