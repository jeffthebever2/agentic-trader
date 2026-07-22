"""Tests for profit-linked X data scaling (thematic revamp)."""
from __future__ import annotations

from tradingagents.screening.data_scaling import (
    monthly_post_budget, scan_plan, DataScaleConfig,
    BASE_POSTS, ACCOUNT_POST_CAP, MAX_RESULTS, DEFAULT_MONTHLY_CREDIT_USD,
    POST_READ_UNIT_COST_USD, estimate_post_read_cost, posts_for_credit,
)

SAFE = DataScaleConfig(intensity="safe")
AGG = DataScaleConfig(intensity="aggressive")


# ── Profit-scaled budget ──────────────────────────────────────────────────────

def test_credit_to_posts_for_25_credit_case():
    assert DEFAULT_MONTHLY_CREDIT_USD == 25.0
    assert POST_READ_UNIT_COST_USD == 0.005
    assert posts_for_credit(25.0, SAFE) == 5_000
    assert estimate_post_read_cost(5_000, SAFE) == 25.0


def test_budget_base_at_zero_profit():
    assert monthly_post_budget(0.0, SAFE) == BASE_POSTS["safe"]  # $5 of $25 = 1k posts


def test_budget_grows_with_profit():
    # +$100 realized, 5% reinvestment → +$5 → +1k posts on top of the base.
    assert monthly_post_budget(100.0, SAFE) == 1_000 + 1_000


def test_legacy_posts_per_profit_override_is_explicit():
    cfg = DataScaleConfig(intensity="safe", posts_per_profit_dollar=20.0)
    assert monthly_post_budget(100.0, cfg) == 1_000 + 2_000


def test_budget_capped_at_account_ceiling():
    # huge profit can't exceed the configured credit/post cap
    assert monthly_post_budget(10_000.0, AGG) == ACCOUNT_POST_CAP


def test_loss_floors_at_base():
    assert monthly_post_budget(-500.0, SAFE) == BASE_POSTS["safe"]  # never below base


def test_intensity_tiers():
    assert monthly_post_budget(0.0, DataScaleConfig(intensity="safe")) == 1_000
    assert monthly_post_budget(0.0, DataScaleConfig(intensity="rich")) == 2_500
    assert monthly_post_budget(0.0, DataScaleConfig(intensity="aggressive")) == 4_000


# ── Scan pacing ───────────────────────────────────────────────────────────────

def test_scan_plan_paces_budget_across_remaining_scans():
    # Safe $25-credit mode: 1k posts over 20 scans → 50 posts/scan → 1 search.
    p = scan_plan(0.0, posts_used_this_month=0, scans_left_this_month=20, cfg=SAFE)
    assert p["monthly_budget"] == 1_000
    assert p["searches"] == 1
    assert p["max_results"] == MAX_RESULTS
    assert p["deep_dive_top_n"] == 0           # safe → no deep dive
    assert p["estimated_monthly_cost_usd"] == 5.0


def test_scan_plan_zero_when_budget_spent():
    p = scan_plan(0.0, posts_used_this_month=1_000, scans_left_this_month=100, cfg=SAFE)
    assert p["searches"] == 0 and p["remaining_posts"] == 0 and p["deep_dive_top_n"] == 0


def test_scan_plan_respects_per_scan_ceiling():
    cfg = DataScaleConfig(intensity="max", max_searches_per_scan=10)
    # 5k-post credit over one scan would demand 50 searches → clamp to 10
    p = scan_plan(0.0, posts_used_this_month=0, scans_left_this_month=1, cfg=cfg)
    assert p["searches"] == 10


def test_more_profit_more_searches():
    lo = scan_plan(0.0, 0, 10, cfg=SAFE)["searches"]
    hi = scan_plan(200.0, 0, 10, cfg=SAFE)["searches"]   # +$200 → +$10 budget
    assert hi > lo   # "the more money it makes, the more data it gets"


def test_from_env(monkeypatch):
    monkeypatch.setenv("X_INTENSITY", "rich")
    monkeypatch.setenv("X_MONTHLY_CREDIT_USD", "50")
    monkeypatch.setenv("X_PROFIT_REINVEST_PCT", "2.5")
    c = DataScaleConfig.from_env()
    assert c.intensity == "rich"
    assert c.monthly_credit_usd == 50.0
    assert c.profit_reinvest_pct == 0.025
    monkeypatch.setenv("X_INTENSITY", "bogus")
    assert DataScaleConfig.from_env().intensity == "safe"  # invalid → default
