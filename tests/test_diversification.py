"""Tests for the pure market-section diversification module (thematic v2)."""
from __future__ import annotations

import math

import pytest

from tradingagents.portfolio.diversification import (
    macro_cluster,
    diversification_room,
    BookItem,
    DiversifyConfig,
    _bucket_keys,
    _decay_for,
)

AGG = DiversifyConfig(  # the shipped "aggressive" profile: 3 names / 25% per cluster
    enabled=True, max_names_per_cluster=3, max_cluster_pct=25.0,
    soft_names_per_cluster=2, cluster_decay=0.6,
    max_names_per_sector=4, max_sector_pct=30.0,
)
AV = 100_000.0


# ── Taxonomy fold ─────────────────────────────────────────────────────────────

def test_ai_themes_fold_to_one_cluster():
    for t in ("ai_leaders", "ai_infrastructure", "optical_network", "memory_hbm", "datacenter_power"):
        assert macro_cluster(t) == "ai_complex", t


def test_nuclear_quantum_minerals_stay_standalone():
    # Product decision 2026-07-06 — these are NOT folded into ai_complex.
    assert macro_cluster("nuclear_energy") == "nuclear"
    assert macro_cluster("quantum_future") == "quantum"
    assert macro_cluster("critical_minerals") == "minerals"


def test_gics_sector_fallback_when_no_theme():
    assert macro_cluster(None, "Technology") == "ai_complex"
    assert macro_cluster(None, "Utilities") == "utilities"
    assert macro_cluster(None, "Healthcare") == "biotech"


def test_theme_wins_over_sector():
    assert macro_cluster("nuclear_energy", "Technology") == "nuclear"


def test_unresolved_is_none():
    assert macro_cluster(None, None) is None
    assert macro_cluster("not_a_theme", "not_a_sector") is None


def test_env_override_remaps(monkeypatch):
    monkeypatch.setenv("THEMATIC_CLUSTER_MAP", '{"nuclear_energy": "ai_complex"}')
    assert macro_cluster("nuclear_energy") == "ai_complex"


# ── Fail-closed bucketing ─────────────────────────────────────────────────────

def test_bucket_keys_fail_closed_singleton():
    ck, sk = _bucket_keys("ZZZZ", None, None)
    assert ck == "solo:ZZZZ" and sk == "solo:ZZZZ"  # never grouped, never infinite


def test_bucket_keys_derives_cluster_from_sector():
    ck, sk = _bucket_keys("AAA", None, "Technology")
    assert ck == "ai_complex" and sk == "technology"


# ── Decay sequence ────────────────────────────────────────────────────────────

def test_decay_sequence():
    assert _decay_for(0, AGG) == 1.0
    assert _decay_for(1, AGG) == 1.0
    assert _decay_for(2, AGG) == pytest.approx(0.6)
    assert _decay_for(3, AGG) == pytest.approx(0.36)


# ── Room: count caps ──────────────────────────────────────────────────────────

def test_cluster_names_cap_blocks_the_fourth():
    book = [BookItem(f"AI{i}", cluster="ai_complex", dollars=1000) for i in range(3)]
    r = diversification_room("NVDA", "ai_complex", "Technology", book, AV, AGG)
    assert r.blocked and r.dollar_cap == 0.0 and "cluster_names_cap" in r.reason


def test_sector_names_cap_blocks_across_distinct_clusters():
    # 4 names, all sector "technology" but 4 DIFFERENT clusters → cluster cap
    # never trips; the SECTOR count cap (4) does.
    book = [BookItem("A", cluster="ai_complex", sector="technology", dollars=500),
            BookItem("B", cluster="nuclear", sector="technology", dollars=500),
            BookItem("C", cluster="quantum", sector="technology", dollars=500),
            BookItem("D", cluster="minerals", sector="technology", dollars=500)]
    r = diversification_room("EEE", "defense", "technology", book, AV, AGG)
    assert r.blocked and "sector_names_cap" in r.reason


def test_third_name_allowed_but_decayed():
    book = [BookItem("AI0", cluster="ai_complex", dollars=1000),
            BookItem("AI1", cluster="ai_complex", dollars=1000)]
    r = diversification_room("AI2", "ai_complex", "Technology", book, AV, AGG)
    assert not r.blocked
    assert r.decay_mult == pytest.approx(0.6)  # 3rd name in the cluster is sized down


# ── Room: dollar budgets ──────────────────────────────────────────────────────

def test_cluster_dollar_budget_clamps():
    # 2 names, $24k of a $25k (25% of $100k) cluster budget → only $1k room left.
    book = [BookItem("AI0", cluster="ai_complex", dollars=12_000),
            BookItem("AI1", cluster="ai_complex", dollars=12_000)]
    r = diversification_room("AI2", "ai_complex", "Technology", book, AV, AGG)
    assert not r.blocked
    assert r.dollar_cap == pytest.approx(1_000.0)


def test_cluster_dollar_budget_exhausted_blocks():
    book = [BookItem("AI0", cluster="ai_complex", dollars=25_000)]
    r = diversification_room("AI2", "ai_complex", "Technology", book, AV, AGG)
    assert r.blocked and "cluster_dollar_cap" in r.reason


# ── Fail-open discipline: unknown never falsely blocks / never infinite ────────

def test_unknown_first_name_fits():
    r = diversification_room("ZZZZ", None, None, [], AV, AGG)
    assert not r.blocked
    # A singleton bucket is bounded by the cluster budget, NOT infinite.
    assert math.isfinite(r.dollar_cap)
    assert r.dollar_cap == pytest.approx(AV * AGG.max_cluster_pct / 100.0)


def test_disabled_returns_infinite_no_block():
    cfg = DiversifyConfig(enabled=False)
    book = [BookItem(f"AI{i}", cluster="ai_complex", dollars=9_000) for i in range(5)]
    r = diversification_room("NVDA", "ai_complex", "Technology", book, AV, cfg)
    assert not r.blocked and math.isinf(r.dollar_cap)
    assert r.decay_mult == pytest.approx(1.0)  # disabled → fully neutral, NO decay


def test_zero_account_value_decays_but_never_blocks_under_count():
    book = [BookItem(f"AI{i}", cluster="ai_complex", dollars=0) for i in range(2)]
    r = diversification_room("NVDA", "ai_complex", "Technology", book, 0.0, AGG)
    assert not r.blocked and math.isinf(r.dollar_cap)
    assert r.decay_mult == pytest.approx(0.6)  # decay needs only the count


def test_zero_account_value_still_blocks_count_cap():
    # No dollar context must NOT disable the hard NAME cap (fail-closed floor —
    # the exact regression the review caught: count caps were skipped at av<=0).
    book = [BookItem(f"AI{i}", cluster="ai_complex", dollars=0) for i in range(3)]
    r = diversification_room("NVDA", "ai_complex", "Technology", book, 0.0, AGG)
    assert r.blocked and "cluster_names_cap" in r.reason


# ── Structural guarantee ──────────────────────────────────────────────────────

def test_full_book_spans_minimum_clusters():
    # With a 3-name cluster cap, a 10-name book cannot be one cluster: greedily
    # filling must spill into >= ceil(10/3) = 4 clusters.
    book: list[BookItem] = []
    added = 0
    clusters_used = set()
    themes = ["ai_leaders", "nuclear_energy", "quantum_future", "critical_minerals",
              "space_defense", "reshoring", "fintech_consumer", "future_tech"]
    ti = 0
    while added < 10:
        theme = themes[ti % len(themes)]
        cl = macro_cluster(theme)
        r = diversification_room(f"T{added}", cl, None, book, AV, AGG)
        if r.blocked:
            ti += 1
            continue
        book.append(BookItem(f"T{added}", cluster=cl, dollars=1000))
        clusters_used.add(cl)
        added += 1
        ti += 1
    assert len(clusters_used) >= math.ceil(10 / AGG.max_names_per_cluster)
