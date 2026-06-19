"""
Portfolio-manager policy — the deterministic discipline layer for the thematic
auto-picker.

Pure, sync, network-free (same contract as ``tradingagents.portfolio.holdings_brain``):
given the CURRENT portfolio (real Fidelity holdings + open thematic positions) and
a list of fresh candidate signals, decide — following a strict hierarchy — whether
to manage what we already hold, add to the best, replace the weakest, or open
something new, and at what size. This is the safety floor; any LLM/heuristic
ranking is clamped back to these rules.

Decision hierarchy (highest first):
    1. Manage existing positions.
    2. Add to the highest-conviction existing positions.
    3. Replace the weakest holding — only with a *clearly superior* candidate.
    4. Open a new position — only when capacity + confidence allow.

The goal is risk-adjusted return through concentration in the best ideas, not
trade count. "must-see-twice" confirmation is still required before any new entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT
except Exception:  # pragma: no cover - compliance is always importable in-app
    MAX_POSITION_PCT_OF_ACCOUNT = 10.0


# ── Conviction scaling — shared with thematic _conviction_dollar ────────────────
def conviction_scale(conviction: int) -> float:
    """0.4x (conviction 1) … 1.5x (conviction 10), linear.

    Mirrors ``web.api.thematic_auto._conviction_dollar`` so the policy and the
    paper/live sizing agree on what a conviction is worth.

    Conviction may arrive malformed (None / NaN / "n/a") from an LLM pick — coerce
    to the [1,10] band rather than crashing the policy sizing layer.
    """
    try:
        c = int(conviction)
    except (TypeError, ValueError):
        c = 1
    c = max(1, min(10, c))
    return 0.4 + (c - 1) / 9.0 * 1.1


# ── Inputs ──────────────────────────────────────────────────────────────────────
@dataclass
class ExistingPosition:
    """One position already in the unified portfolio (real holding or thematic)."""
    ticker: str
    conviction: int = 5             # 1..10 (holdings_brain assessment / signal)
    pct_of_account: float = 0.0     # current weight
    unrealized_pct: float = 0.0
    status: str = "HOLD"            # HOLD/ADD/TRIM/EXIT/SET_STOP/ADOPT or "thematic"
    target_progress: float = 0.0    # 0..1 toward price target (>=1 = at/over target)
    time_progress: float = 0.0      # 0..1 toward expected hold horizon
    expected_return: float = 0.0    # remaining-upside proxy (target_pct, %)

    @property
    def is_strong(self) -> bool:
        """Worth protecting: constructive status, real conviction, still room to run."""
        return (
            self.status in ("HOLD", "ADD", "ADOPT", "thematic")
            and self.conviction >= 6
            and self.target_progress < 0.8
        )

    @property
    def is_replaceable(self) -> bool:
        """Weak enough that a clearly-superior idea may take its slot."""
        return self.status in ("EXIT", "TRIM") or self.conviction <= 4


@dataclass
class Candidate:
    """A fresh signal competing for portfolio space."""
    ticker: str
    conviction: int = 5
    expected_return: float = 0.0    # target_pct
    confirmed: bool = False         # must-see-twice (appeared in 2+ scans)
    raw_score: float = 0.0

    @property
    def score(self) -> float:
        return conviction_scale(self.conviction) * max(self.expected_return, 0.0)


@dataclass
class PolicyConfig:
    max_positions: int = 10
    hard_max: int = 20
    near_capacity_frac: float = 0.8
    near_capacity_min_conviction: int = 8
    replace_margin: int = 2          # candidate must beat weakest conviction by this
    top_n: int = 3

    @property
    def near_capacity_count(self) -> int:
        return max(1, int(round(self.max_positions * self.near_capacity_frac)))

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        def _i(key: str, default: int) -> int:
            try:
                return int(float(os.getenv(key, "") or default))
            except Exception:
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, "") or default)
            except Exception:
                return default

        max_p = max(10, min(20, _i("THEMATIC_MAX_POSITIONS", 10)))
        hard = max(max_p, min(20, _i("THEMATIC_HARD_MAX_POSITIONS", 20)))
        return cls(
            max_positions=max_p,
            hard_max=hard,
            near_capacity_frac=_f("THEMATIC_NEAR_CAPACITY_FRAC", 0.8),
            near_capacity_min_conviction=_i("THEMATIC_NEAR_CAPACITY_MIN_CONVICTION", 8),
            replace_margin=_i("THEMATIC_REPLACE_CONVICTION_MARGIN", 2),
            top_n=_i("THEMATIC_TOP_N_ACTIONABLE", 3),
        )


# ── Outputs ─────────────────────────────────────────────────────────────────────
KIND_NEW = "NEW"
KIND_ADD = "ADD"
KIND_REPLACE = "REPLACE"
_KIND_PRIORITY = {KIND_ADD: 0, KIND_REPLACE: 1, KIND_NEW: 2}


@dataclass
class PolicyDecision:
    kind: str                              # NEW / ADD / REPLACE
    ticker: str                            # candidate (NEW/REPLACE) or existing (ADD)
    conviction: int
    reason: str
    replace_target: Optional[str] = None   # weakest holding to exit (REPLACE)
    add_target: Optional[str] = None       # existing holding to add to (ADD)
    size_factor: float = 1.0               # multiply base size by this
    capacity_note: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ticker": self.ticker,
            "conviction": self.conviction,
            "reason": self.reason,
            "replace_target": self.replace_target,
            "add_target": self.add_target,
            "size_factor": round(self.size_factor, 4),
            "capacity_note": self.capacity_note,
        }


@dataclass
class PolicyResult:
    decisions: List[PolicyDecision] = field(default_factory=list)
    suppress_generation: bool = False
    n_existing: int = 0
    capacity_note: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "decisions": [d.to_dict() for d in self.decisions],
            "suppress_generation": self.suppress_generation,
            "n_existing": self.n_existing,
            "capacity_note": self.capacity_note,
            "reason": self.reason,
        }


# ── Sizing ──────────────────────────────────────────────────────────────────────
def size_factor(conviction: int, n_existing: int, cfg: PolicyConfig) -> float:
    """Conviction x concentration-room.

    Conviction leans size into the best ideas; concentration-room shrinks new
    sizes as the book fills (capital concentrates in established winners) with a
    0.5 floor so entries stay meaningful.
    """
    room = max(0.0, (cfg.max_positions - n_existing) / max(cfg.max_positions, 1))
    concentration = 0.5 + 0.5 * min(room, 1.0)   # 1.0 empty book → 0.5 full
    return round(conviction_scale(conviction) * concentration, 4)


def size_position(
    base_dollar: float,
    conviction: int,
    n_existing: int,
    account_value: float,
    cfg: Optional[PolicyConfig] = None,
) -> float:
    """Dollar size = base x conviction x concentration-room, clamped to the
    compliance per-position concentration ceiling (never exceeds it)."""
    cfg = cfg or PolicyConfig()
    dollars = float(base_dollar) * size_factor(conviction, n_existing, cfg)
    if account_value and account_value > 0:
        ceiling = account_value * (float(MAX_POSITION_PCT_OF_ACCOUNT) / 100.0)
        dollars = min(dollars, ceiling)
    return round(max(dollars, 0.0), 2)


# ── The policy ──────────────────────────────────────────────────────────────────
def evaluate(
    existing: List[ExistingPosition],
    candidates: List[Candidate],
    cfg: Optional[PolicyConfig] = None,
) -> PolicyResult:
    """Apply the decision hierarchy and return at most ``cfg.top_n`` actions."""
    cfg = cfg or PolicyConfig()
    n = len(existing)
    max_p = cfg.max_positions
    near = cfg.near_capacity_count

    if n >= cfg.hard_max:
        cap_note = f"{n}/{max_p} · HARD CAP {cfg.hard_max} — manage only"
    elif n >= max_p:
        cap_note = f"{n}/{max_p} · full — manage / replace only"
    elif n >= near:
        cap_note = f"{n}/{max_p} · near full — higher bar"
    else:
        cap_note = f"{n}/{max_p}"

    # must-see-twice gate + best-first ordering
    valid = sorted(
        [c for c in candidates if c.confirmed],
        key=lambda c: c.score, reverse=True,
    )

    weakest = (
        min(existing, key=lambda p: (p.conviction, p.expected_return))
        if existing else None
    )
    strong = [p for p in existing if p.is_strong]
    best = valid[0] if valid else None
    clearly_superior = bool(
        best and weakest
        and best.conviction >= weakest.conviction + cfg.replace_margin
        and best.expected_return > weakest.expected_return
    )

    # Hard cap → manage only.
    if n >= cfg.hard_max:
        return PolicyResult(
            [], suppress_generation=True, n_existing=n, capacity_note=cap_note,
            reason=f"At hard cap {cfg.hard_max} positions — manage existing only.",
        )

    # Manage-first: strong positions still progressing and nothing clearly beats
    # them → suppress new-signal generation this cycle.
    if strong and not clearly_superior:
        return PolicyResult(
            [], suppress_generation=True, n_existing=n, capacity_note=cap_note,
            reason=(
                f"Managing existing — {len(strong)} high-conviction position(s) "
                f"still progressing; no candidate clearly superior."
            ),
        )

    decisions: List[PolicyDecision] = []
    new_count = 0
    for c in valid:
        if len(decisions) >= cfg.top_n:
            break
        at_capacity = (n + new_count) >= max_p

        if at_capacity:
            # Full: only a clearly-superior candidate may replace the weakest.
            if (
                weakest and weakest.is_replaceable
                and c.conviction >= weakest.conviction + cfg.replace_margin
                and c.expected_return > weakest.expected_return
            ):
                decisions.append(PolicyDecision(
                    kind=KIND_REPLACE, ticker=c.ticker, conviction=c.conviction,
                    reason=(
                        f"Replace weakest {weakest.ticker} (conv {weakest.conviction}, "
                        f"{weakest.status}) — {c.ticker} conv {c.conviction}, "
                        f"+{c.expected_return:.0f}% target is clearly superior."
                    ),
                    replace_target=weakest.ticker,
                    size_factor=size_factor(c.conviction, n, cfg),
                    capacity_note=cap_note,
                ))
            # else: full and not superior → don't open; prefer adding to the best.
            continue

        # Near capacity → require a higher conviction bar for plain new entries.
        if n >= near and c.conviction < cfg.near_capacity_min_conviction:
            continue

        decisions.append(PolicyDecision(
            kind=KIND_NEW, ticker=c.ticker, conviction=c.conviction,
            reason=(
                f"New position — conv {c.conviction}, +{c.expected_return:.0f}% target"
                + (f"; cleared near-capacity bar ({cfg.near_capacity_min_conviction}+)"
                   if n >= near else "")
            ),
            size_factor=size_factor(c.conviction, n, cfg),
            capacity_note=cap_note,
        ))
        new_count += 1

    # Full with no superior new idea → suggest adding to the best existing position.
    if not decisions and n >= max_p and existing:
        best_existing = max(existing, key=lambda p: (p.conviction, p.expected_return))
        decisions.append(PolicyDecision(
            kind=KIND_ADD, ticker=best_existing.ticker, conviction=best_existing.conviction,
            reason=(
                f"Portfolio full ({n}/{max_p}) and no clearly-superior new idea — "
                f"add to highest-conviction holding {best_existing.ticker} "
                f"(conv {best_existing.conviction}) instead of opening a new one."
            ),
            add_target=best_existing.ticker,
            size_factor=size_factor(best_existing.conviction, n, cfg),
            capacity_note=cap_note,
        ))

    decisions.sort(key=lambda d: (_KIND_PRIORITY.get(d.kind, 9), -d.conviction))
    decisions = decisions[:cfg.top_n]

    return PolicyResult(
        decisions=decisions,
        suppress_generation=(len(decisions) == 0),
        n_existing=n,
        capacity_note=cap_note,
        reason=(decisions[0].reason if decisions else "No actionable opportunity — hold."),
    )
