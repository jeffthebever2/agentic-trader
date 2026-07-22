"""Pure, network-free market-section diversification for the thematic book (v2).

The thematic picker/sizer is otherwise blind to how concentrated the book is in
one section of the market: ~7 of the 12 narrative themes are really one
AI/semiconductor macro trade, and the old guards either count a noisy per-theme
label (so 3+3+3 correlated AI names slip through) or fail OPEN to an infinite
cap when sector data is missing. This module is the fix.

It folds the 12 narrative themes into a small set of MACRO-CLUSTERS, then bounds
how much of the book any one cluster (or GICS sector) may occupy:
  • a hard NAMES count cap per cluster / per sector,
  • a hard DOLLAR budget (% of account) per cluster / per sector,
  • a soft geometric SIZE-DECAY so the hot narrative can still lead but every
    marginal name in it is sized down.

Design rules honoured here:
  • Pure list/dict arithmetic — no I/O, no network. It therefore keeps biting
    even when the live sector/price enrichment degrades (the old cap did not).
  • FAIL-CLOSED on classification: an unknown cluster/sector becomes its OWN
    singleton bucket (never an infinite cap, never a false block of the 1st name).
  • Propose-only: this only ever SHRINKS or blocks a size; it never places an
    order and never relaxes a compliance limit.

Wire points (see docs/plans/THEMATIC_V2_DIVERSIFICATION_2026-07-06.md):
  macro_cluster()        — theme/sector → cluster label
  diversification_room() — (dollar_cap, decay_mult, reason) for one candidate
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ── Taxonomy ─────────────────────────────────────────────────────────────────
# Theme (a THEMES_MAP key in web/api/thematic_auto.py) → macro-cluster. The
# AI/semis/datacenter-compute core collapses into one bucket; nuclear, quantum
# and critical-minerals are kept STANDALONE by product decision (2026-07-06) —
# the correlation layer, not this map, catches their hidden linkage to AI.
# Override the whole map with env THEMATIC_CLUSTER_MAP (JSON: theme → cluster).
_DEFAULT_THEME_TO_MACRO: dict[str, str] = {
    "ai_leaders":        "ai_complex",
    "ai_infrastructure": "ai_complex",
    "optical_network":   "ai_complex",
    "memory_hbm":        "ai_complex",
    "datacenter_power":  "ai_complex",
    "nuclear_energy":    "nuclear",
    "quantum_future":    "quantum",
    "critical_minerals": "minerals",
    "space_defense":     "defense",
    "reshoring":         "industrials",
    "fintech_consumer":  "fintech",
    "future_tech":       "biotech",
}

# GICS/yfinance sector → macro-cluster, so a real holding that carries NO theme
# still lands in a bucket. Coarse on purpose; an unrecognised sector falls
# through to a fail-closed singleton in _bucket_keys (never an infinite cap).
_GICS_TO_MACRO: dict[str, str] = {
    "technology":             "ai_complex",
    "information technology":  "ai_complex",
    "communication services": "ai_complex",
    "energy":                 "energy",
    "utilities":              "utilities",
    "financial services":     "fintech",
    "financials":             "fintech",
    "healthcare":             "biotech",
    "health care":            "biotech",
    "industrials":            "industrials",
    "basic materials":        "minerals",
    "materials":              "minerals",
    "consumer cyclical":      "consumer",
    "consumer defensive":     "consumer",
    "consumer staples":       "consumer",
    "consumer discretionary": "consumer",
    "real estate":            "real_estate",
}


def _theme_to_macro() -> dict[str, str]:
    """The theme→cluster map, allowing a wholesale env override (JSON)."""
    raw = os.getenv("THEMATIC_CLUSTER_MAP")
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                return {str(k).strip().lower(): str(v).strip().lower() for k, v in override.items()}
        except (ValueError, TypeError):
            pass
    return _DEFAULT_THEME_TO_MACRO


def macro_cluster(theme: Optional[str], sector: Optional[str] = None) -> Optional[str]:
    """Fold a narrative theme (preferred) or GICS sector into a macro-cluster.

    Returns None when neither is recognised — the caller/room then fails CLOSED
    to a per-ticker singleton rather than an infinite cap.
    """
    if theme:
        t = str(theme).strip().lower()
        m = _theme_to_macro().get(t)
        if m:
            return m
    if sector:
        s = str(sector).strip().lower()
        m = _GICS_TO_MACRO.get(s)
        if m:
            return m
    return None


# ── Inputs ───────────────────────────────────────────────────────────────────
@dataclass
class BookItem:
    """One position already in the (unified real + paper) book."""
    ticker: str
    cluster: Optional[str] = None    # macro-cluster label, if already resolved
    sector: Optional[str] = None     # GICS/yfinance sector, if known
    dollars: float = 0.0             # current market value in the book


@dataclass
class DiversifyConfig:
    enabled: bool = True
    max_names_per_cluster: int = 3           # hard count cap per macro-cluster
    max_cluster_pct: float = 25.0            # hard dollar budget per cluster (% of account)
    soft_names_per_cluster: int = 2          # names allowed at full size before decay
    cluster_decay: float = 0.6               # geometric size decay per marginal name
    max_names_per_sector: int = 4            # hard count cap per GICS sector
    max_sector_pct: float = 30.0             # hard dollar budget per sector (% of account)

    @classmethod
    def from_env(cls, hil: Optional[dict] = None) -> "DiversifyConfig":
        hil = hil or {}

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, "") or default)
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(float(os.getenv(key, "") or default))
            except (TypeError, ValueError):
                return default

        def _b(key: str, default: bool) -> bool:
            v = os.getenv(key)
            if v is None:
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        return cls(
            enabled=_b("DIVERSIFY_ENABLED", True),
            max_names_per_cluster=max(1, _i("DIVERSIFY_MAX_NAMES_PER_CLUSTER", 3)),
            max_cluster_pct=max(1.0, _f("DIVERSIFY_MAX_CLUSTER_PCT", 25.0)),
            soft_names_per_cluster=max(1, _i("DIVERSIFY_SOFT_NAMES_PER_CLUSTER", 2)),
            cluster_decay=min(1.0, max(0.1, _f("DIVERSIFY_CLUSTER_DECAY", 0.6))),
            max_names_per_sector=max(1, _i("DIVERSIFY_MAX_NAMES_PER_SECTOR", 4)),
            max_sector_pct=max(1.0, _f("DIVERSIFY_MAX_SECTOR_PCT", 30.0)),
        )


# ── Output ───────────────────────────────────────────────────────────────────
@dataclass
class DiversifyRoom:
    dollar_cap: float            # hardest remaining dollar room for THIS candidate
    decay_mult: float            # soft size multiplier (1.0 = no decay)
    reason: str                  # binding rule, "" if nothing binds
    cluster: str = ""            # resolved cluster bucket key
    sector_bucket: str = ""      # resolved sector bucket key
    blocked: bool = False        # True → open NO new position (a hard count/budget cap)

    def to_dict(self) -> dict:
        return {
            "dollar_cap": None if math.isinf(self.dollar_cap) else round(self.dollar_cap, 2),
            "decay_mult": round(self.decay_mult, 4),
            "reason": self.reason,
            "cluster": self.cluster,
            "sector_bucket": self.sector_bucket,
            "blocked": self.blocked,
        }


def _bucket_keys(ticker: str, cluster: Optional[str], sector: Optional[str]) -> tuple[str, str]:
    """(cluster_key, sector_key), both FAIL-CLOSED to a per-ticker singleton.

    An unresolved cluster/sector becomes ``solo:TICKER`` — a bucket of one — so
    an unknown name is never grouped with other unknowns and never handed an
    infinite cap (the old `math.inf` bug). The first unknown name still fits
    (a singleton's count/budget bind only at the 2nd name / over-budget).
    """
    resolved = cluster or macro_cluster(None, sector)
    cluster_key = resolved if resolved else f"solo:{ticker.upper()}"
    sector_key = sector.strip().lower() if sector else f"solo:{ticker.upper()}"
    return cluster_key, sector_key


def _decay_for(n_existing: int, cfg: DiversifyConfig) -> float:
    """Geometric size-decay for the (n_existing+1)-th name in a cluster.

    n<soft → 1.0; then cluster_decay**(n - soft + 1). With soft=2, decay=0.6:
    names size at 1.0, 1.0, 0.6, 0.36, ...
    """
    if n_existing < cfg.soft_names_per_cluster:
        return 1.0
    return cfg.cluster_decay ** (n_existing - cfg.soft_names_per_cluster + 1)


def diversification_room(
    ticker: str,
    cluster: Optional[str],
    sector: Optional[str],
    book: Iterable[BookItem],
    account_value: float,
    cfg: Optional[DiversifyConfig] = None,
) -> DiversifyRoom:
    """Remaining room for a candidate under the cluster + sector budgets.

    Returns the HARDEST binding limit across both dimensions: a hard names count
    cap or exhausted dollar budget → blocked (dollar_cap 0). Otherwise the
    tighter of the two dollar budgets, plus the soft cluster size-decay.

    Never returns an infinite dollar_cap on a KNOWN bucket; a singleton (unknown)
    bucket is unconstrained on its 1st name by construction, which is correct.
    """
    cfg = cfg or DiversifyConfig.from_env()
    ck, sk = _bucket_keys(ticker, cluster, sector)

    # Feature OFF → fully neutral: no block, no decay, unlimited room. (A decay
    # here would silently shrink orders the operator believes they disabled.)
    if not cfg.enabled:
        return DiversifyRoom(math.inf, 1.0, "", ck, sk, blocked=False)

    # Tally the candidate's buckets — needed for BOTH the hard NAME caps (which are
    # dollar-independent) and the dollar budgets.
    cluster_names = 0
    cluster_dollars = 0.0
    sector_names = 0
    sector_dollars = 0.0
    for b in book:
        bck, bsk = _bucket_keys(b.ticker, b.cluster, b.sector)
        if bck == ck:
            cluster_names += 1
            cluster_dollars += max(0.0, b.dollars)
        if bsk == sk:
            sector_names += 1
            sector_dollars += max(0.0, b.dollars)

    # Hard NAME count caps — evaluated FIRST and independently of account_value, so
    # they keep biting even with no dollar context. This is the fail-closed floor: a
    # crowded bucket is NEVER handed an infinite cap just because dollars are unknown.
    if cluster_names >= cfg.max_names_per_cluster:
        return DiversifyRoom(0.0, 1.0, f"cluster_names_cap({ck}:{cluster_names}/{cfg.max_names_per_cluster})",
                             ck, sk, blocked=True)
    if sector_names >= cfg.max_names_per_sector:
        return DiversifyRoom(0.0, 1.0, f"sector_names_cap({sk}:{sector_names}/{cfg.max_names_per_sector})",
                             ck, sk, blocked=True)

    decay = _decay_for(cluster_names, cfg)

    # No account context → dollar budgets can't be computed. The NAME caps above
    # already applied, so return decay-only (never an infinite cap on a crowded bucket).
    if account_value <= 0:
        return DiversifyRoom(math.inf, decay, "", ck, sk, blocked=False)

    # Hard DOLLAR budgets (remaining room in each bucket).
    cluster_room = account_value * cfg.max_cluster_pct / 100.0 - cluster_dollars
    sector_room = account_value * cfg.max_sector_pct / 100.0 - sector_dollars
    if cluster_room <= 0:
        return DiversifyRoom(0.0, 1.0, f"cluster_dollar_cap({ck})", ck, sk, blocked=True)
    if sector_room <= 0:
        return DiversifyRoom(0.0, 1.0, f"sector_dollar_cap({sk})", ck, sk, blocked=True)

    dollar_cap = min(cluster_room, sector_room)
    reason = "cluster_room" if cluster_room <= sector_room else "sector_room"
    if decay < 1.0:
        reason += f"+decay({decay:.2f})"
    return DiversifyRoom(dollar_cap, decay, reason, ck, sk, blocked=False)
