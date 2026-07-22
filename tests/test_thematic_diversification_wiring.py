"""Thematic v2 diversification wiring: the _resolve_cluster classifier + book read.

The pure caps/decay/correlation math is covered by test_diversification.py and
test_correlation_layer.py; here we lock the web-layer glue that feeds them —
especially the classification the whole feature depends on.
"""
from __future__ import annotations

import web.api.thematic_auto as ta


def test_resolve_cluster_prefers_explicit_theme():
    assert ta._resolve_cluster("nuclear_energy", None, "CCJ") == "nuclear"
    assert ta._resolve_cluster("ai_leaders", None, "NVDA") == "ai_complex"


def test_resolve_cluster_theme_from_membership_when_absent():
    # No theme given but the ticker IS a THEMES_MAP member → use that theme's cluster.
    assert ta._resolve_cluster(None, None, "NVDA") == "ai_complex"
    assert ta._resolve_cluster(None, None, "IONQ") == "quantum"


def test_resolve_cluster_no_guess_theme_biotech_trap():
    # A real holding with NO theme and NOT in THEMES_MAP must fall back to its GICS
    # SECTOR — never _guess_theme's 'future_tech' catch-all (which would misfile a
    # real AAPL into the biotech cluster). This is the exact trap the design flagged.
    assert ta._resolve_cluster(None, "Technology", "AAPL") == "ai_complex"
    assert ta._resolve_cluster(None, "Healthcare", "PFE") == "biotech"


def test_resolve_cluster_unresolvable_is_none_fail_closed():
    # No theme, no sector, not a member → None → the pure layer fails CLOSED to a
    # per-ticker singleton (never an infinite cap).
    assert ta._resolve_cluster(None, None, "ZZZZ") is None
    assert ta._resolve_cluster(None, None, None) is None


def test_real_fidelity_book_missing_snapshot_is_empty():
    # No snapshot for a bogus user → empty book (never blocks the approval path).
    assert ta._real_fidelity_book("no-such-user@example.invalid") == {}
    assert ta._real_fidelity_book("") == {}


def test_diversify_enforce_live_default_on(monkeypatch):
    monkeypatch.delenv("DIVERSIFY_ENFORCE_LIVE", raising=False)
    assert ta._diversify_enforce_live() is True
    monkeypatch.setenv("DIVERSIFY_ENFORCE_LIVE", "false")
    assert ta._diversify_enforce_live() is False
