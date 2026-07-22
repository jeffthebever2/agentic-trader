"""Credit-aware X data scaling — pure (thematic revamp).

X's self-serve API is pay-per-use. Recent-search post reads are billed per returned
Post, so this planner starts from a dollar credit budget, converts it to post reads,
and spreads those reads across the remaining scans. Profit can add budget, but only
as a small reinvestment percentage so social data cannot consume more than the
trading edge it is meant to support.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

POST_READ_UNIT_COST_USD = 0.005       # X API Posts: Read, per returned Post
DEFAULT_MONTHLY_CREDIT_USD = 25.0     # safe default for a small credit/recharge
POSTS_PER_DOLLAR = int(round(1.0 / POST_READ_UNIT_COST_USD))
DEFAULT_ACCOUNT_POST_CAP = int(DEFAULT_MONTHLY_CREDIT_USD * POSTS_PER_DOLLAR)
MAX_RESULTS = 100                     # X recent-search max_results ceiling
RATE_LIMIT_PER_15MIN = 450            # x-rate-limit-limit on /2/tweets/search/recent

# Fraction of the monthly credit budget used before any profit reinvestment.
BASE_CREDIT_FRACTIONS = {
    "safe": 0.20,
    "rich": 0.50,
    "aggressive": 0.80,
    "max": 1.00,
}

# Backward-friendly view of the default $25 credit budget expressed as posts.
BASE_POSTS = {
    k: int(DEFAULT_ACCOUNT_POST_CAP * v) for k, v in BASE_CREDIT_FRACTIONS.items()
}
ACCOUNT_POST_CAP = DEFAULT_ACCOUNT_POST_CAP


@dataclass
class DataScaleConfig:
    intensity: str = "safe"                # safe | rich | aggressive | max
    monthly_credit_usd: float = DEFAULT_MONTHLY_CREDIT_USD
    post_read_cost_usd: float = POST_READ_UNIT_COST_USD
    account_cap: int = ACCOUNT_POST_CAP
    profit_reinvest_pct: float = 0.05      # spend at most 5% of realized profit
    posts_per_profit_dollar: float = 0.0   # legacy override; prefer profit_reinvest_pct
    deep_dive_top_n: int = 5               # dedicated deep-pull queries for the leaders
    max_searches_per_scan: int = 10        # small-credit ceiling, under the 450/15min limit

    @classmethod
    def from_env(cls) -> "DataScaleConfig":
        def _f(k, d):
            try:
                return float(os.getenv(k, "") or d)
            except (TypeError, ValueError):
                return d

        def _i(k, d):
            try:
                return int(float(os.getenv(k, "") or d))
            except (TypeError, ValueError):
                return d

        intensity = (os.getenv("X_INTENSITY") or "safe").strip().lower()
        if intensity not in BASE_CREDIT_FRACTIONS:
            intensity = "safe"
        return cls(
            intensity=intensity,
            monthly_credit_usd=max(0.0, _f("X_MONTHLY_CREDIT_USD", DEFAULT_MONTHLY_CREDIT_USD)),
            post_read_cost_usd=max(0.000001, _f("X_POST_READ_COST_USD", POST_READ_UNIT_COST_USD)),
            account_cap=_i("X_ACCOUNT_POST_CAP", ACCOUNT_POST_CAP),
            profit_reinvest_pct=max(0.0, _f("X_PROFIT_REINVEST_PCT", 5.0)) / 100.0,
            posts_per_profit_dollar=max(0.0, _f("X_POSTS_PER_PROFIT_DOLLAR", 0.0)),
            deep_dive_top_n=max(0, _i("X_DEEP_DIVE_TOP_N", 5)),
            max_searches_per_scan=max(1, _i("X_MAX_SEARCHES_PER_SCAN", 10)),
        )


def estimate_post_read_cost(posts: int, cfg: "DataScaleConfig | None" = None) -> float:
    """Estimated X API cost for returned Posts."""
    cfg = cfg or DataScaleConfig()
    return round(max(0, int(posts or 0)) * cfg.post_read_cost_usd, 4)


def posts_for_credit(credit_usd: float, cfg: "DataScaleConfig | None" = None) -> int:
    """Convert available X API credits to billable Post reads."""
    cfg = cfg or DataScaleConfig()
    return int(max(0.0, float(credit_usd or 0.0)) / cfg.post_read_cost_usd)


def monthly_post_budget(realized_profit: float, cfg: "DataScaleConfig | None" = None) -> int:
    """Credit-scaled monthly X post budget.

    Base budget is a fraction of ``X_MONTHLY_CREDIT_USD``. Positive realized profit
    can add budget by ``X_PROFIT_REINVEST_PCT``. If the legacy
    ``X_POSTS_PER_PROFIT_DOLLAR`` is set, it overrides the reinvestment conversion.
    """
    cfg = cfg or DataScaleConfig()
    frac = BASE_CREDIT_FRACTIONS.get(cfg.intensity, BASE_CREDIT_FRACTIONS["safe"])
    base_credit = cfg.monthly_credit_usd * frac
    profit = max(0.0, float(realized_profit or 0.0))
    if cfg.posts_per_profit_dollar > 0:
        bonus_posts = profit * cfg.posts_per_profit_dollar
    else:
        bonus_posts = posts_for_credit(profit * cfg.profit_reinvest_pct, cfg)
    base = posts_for_credit(base_credit, cfg)
    bonus = max(0.0, bonus_posts)
    return int(min(cfg.account_cap, base + bonus))


def scan_plan(realized_profit: float, posts_used_this_month: int,
              scans_left_this_month: int, cfg: "DataScaleConfig | None" = None) -> dict:
    """How many X searches THIS scan gets — pacing the profit-scaled monthly budget
    evenly across the month's remaining scans so it lasts the whole cycle.

    Returns {searches, max_results, deep_dive_top_n, monthly_budget, remaining_posts,
    estimated_monthly_cost_usd, estimated_remaining_cost_usd}.
    searches=0 when the budget is spent (the caller then skips X this scan).
    """
    cfg = cfg or DataScaleConfig()
    budget = monthly_post_budget(realized_profit, cfg)
    remaining = max(0, budget - max(0, int(posts_used_this_month or 0)))
    scans_left = max(1, int(scans_left_this_month or 1))
    if remaining <= 0:
        searches = 0
    else:
        posts_this_scan = remaining / scans_left
        searches = int(math.ceil(posts_this_scan / MAX_RESULTS))
        searches = max(1, min(cfg.max_searches_per_scan, RATE_LIMIT_PER_15MIN, searches))
    deep = cfg.deep_dive_top_n if (cfg.intensity in ("aggressive", "max") and searches > 0) else 0
    return {"searches": searches, "max_results": MAX_RESULTS, "deep_dive_top_n": deep,
            "monthly_budget": budget, "remaining_posts": remaining,
            "estimated_monthly_cost_usd": estimate_post_read_cost(budget, cfg),
            "estimated_remaining_cost_usd": estimate_post_read_cost(remaining, cfg)}
