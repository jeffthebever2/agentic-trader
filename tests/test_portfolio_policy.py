"""Tests for the portfolio-manager policy (the deterministic discipline floor).

Covers the decision hierarchy (manage-first / capacity / replace / new),
must-see-twice, concentration sizing, and the top-N cap.
"""
import pytest

from tradingagents.compliance import MAX_POSITION_PCT_OF_ACCOUNT
from tradingagents.portfolio import portfolio_policy as pp

CFG = pp.PolicyConfig()  # max 10, near 8, replace_margin 2, top_n 3


def _cand(t, conv, ret=20, confirmed=True):
    return pp.Candidate(ticker=t, conviction=conv, expected_return=ret, confirmed=confirmed)


def _pos(t, conv=5, status="HOLD", progress=0.85, ret=5):
    return pp.ExistingPosition(
        ticker=t, conviction=conv, status=status, target_progress=progress, expected_return=ret,
    )


# ── must-see-twice ──────────────────────────────────────────────────────────────
def test_unconfirmed_candidate_is_never_actionable():
    r = pp.evaluate([], [_cand("X", 10, 50, confirmed=False)], CFG)
    assert r.decisions == []
    assert r.suppress_generation is True


# ── capacity tiers ──────────────────────────────────────────────────────────────
def test_empty_book_opens_top_n_new():
    cands = [_cand(f"T{i}", conv=9 - i, ret=30 - i) for i in range(6)]
    r = pp.evaluate([], cands, CFG)
    assert len(r.decisions) == CFG.top_n == 3
    assert all(d.kind == pp.KIND_NEW for d in r.decisions)
    # best-first
    assert r.decisions[0].ticker == "T0"


def test_near_capacity_requires_higher_conviction():
    existing = [_pos(f"H{i}", conv=5) for i in range(8)]  # 8/10 = near
    r = pp.evaluate(
        existing,
        [_cand("LOW", conv=6, ret=20), _cand("HIGH", conv=9, ret=20)],
        CFG,
    )
    kinds = {d.ticker: d.kind for d in r.decisions}
    assert "HIGH" in kinds and kinds["HIGH"] == pp.KIND_NEW
    assert "LOW" not in kinds              # below near-capacity conviction bar
    assert "near full" in r.capacity_note


def test_full_book_no_plain_new():
    existing = [_pos(f"H{i}", conv=7, ret=10) for i in range(10)]  # full
    # candidate not clearly superior to weakest (conv 7) → no replace, no new
    r = pp.evaluate(existing, [_cand("MEH", conv=6, ret=8)], CFG)
    assert all(d.kind != pp.KIND_NEW for d in r.decisions)


def test_hard_cap_is_manage_only():
    existing = [_pos(f"H{i}", conv=9, status="HOLD", progress=0.1, ret=30) for i in range(20)]
    r = pp.evaluate(existing, [_cand("STAR", 10, 99)], CFG)
    assert r.decisions == []
    assert r.suppress_generation is True
    assert "HARD CAP" in r.capacity_note


# ── manage-first suppression ────────────────────────────────────────────────────
def test_strong_positions_progressing_suppress_generation_near_capacity():
    """Manage-first is a SCARCITY rule — it fires once slots are scarce (n >= near)."""
    existing = [_pos(f"H{i}", conv=8, status="HOLD", progress=0.3, ret=20)
                for i in range(CFG.near_capacity_count)]
    r = pp.evaluate(existing, [_cand("BBB", conv=6, ret=15)], CFG)
    assert r.suppress_generation is True
    assert "Managing existing" in r.reason


def test_one_strong_holding_does_not_freeze_the_whole_book():
    """Regression: manage-first used to trigger on the mere EXISTENCE of a strong
    position, with no capacity condition.

    `is_strong` is only conviction>=6 and target_progress<0.8, and the unified
    view folds REAL broker holdings in — so a single conviction-7 name returned an
    empty decision set on every cycle, forever. The consumer treats an empty dict
    as "nothing passes", so the scanner proposed ZERO candidates regardless of the
    tape while cash sat undeployed, and max_positions=10 was unreachable: the book
    could never grow past its first holding.
    """
    existing = [_pos("AAA", conv=8, status="HOLD", progress=0.3, ret=20)]
    r = pp.evaluate(existing, [_cand("BBB", conv=6, ret=15)], CFG)
    assert r.suppress_generation is False
    assert any(d.ticker == "BBB" for d in r.decisions)


@pytest.mark.parametrize("n_held", range(1, 8))
def test_book_can_grow_toward_capacity_with_strong_holdings(n_held):
    """Every position count below `near` must still admit new ideas."""
    existing = [_pos(f"H{i}", conv=8, status="HOLD", progress=0.3, ret=20)
                for i in range(n_held)]
    r = pp.evaluate(existing, [_cand("NEW", conv=6, ret=15)], CFG)
    assert r.suppress_generation is False, f"frozen at {n_held}/{CFG.max_positions}"


def test_clearly_superior_candidate_overrides_manage_first():
    # A strong holding would normally suppress new generation, but a clearly
    # superior candidate must stay actionable. With free slots it is a NEW entry
    # (replace only kicks in when the book is full).
    existing = [_pos("STRONG", conv=8, status="HOLD", progress=0.3, ret=20),
                _pos("WEAK", conv=3, status="TRIM", ret=2)]
    r = pp.evaluate(existing, [_cand("STAR", conv=9, ret=40)], CFG)
    assert r.suppress_generation is False
    assert any(d.ticker == "STAR" and d.kind == pp.KIND_NEW for d in r.decisions)


def test_clearly_superior_replaces_weakest_when_full():
    existing = [_pos(f"H{i}", conv=7, status="HOLD", progress=0.85, ret=8) for i in range(9)]
    existing.append(_pos("WEAK", conv=3, status="TRIM", ret=2))  # full, weakest replaceable
    r = pp.evaluate(existing, [_cand("STAR", conv=9, ret=40)], CFG)
    rep = [d for d in r.decisions if d.kind == pp.KIND_REPLACE]
    assert rep and rep[0].replace_target == "WEAK"


# ── replace-weakest ─────────────────────────────────────────────────────────────
def test_replace_requires_margin_and_higher_return():
    existing = [_pos(f"H{i}", conv=5, status="HOLD", ret=5) for i in range(9)]
    existing.append(_pos("WEAK", conv=3, status="TRIM", ret=2))  # weakest, replaceable
    # conv 4 = only +1 over weakest (margin is 2) → no replace
    r1 = pp.evaluate(existing, [_cand("NOPE", conv=4, ret=30)], CFG)
    assert all(d.kind != pp.KIND_REPLACE for d in r1.decisions)
    # conv 9 clears margin and beats expected return → replace
    r2 = pp.evaluate(existing, [_cand("STAR", conv=9, ret=40)], CFG)
    rep = [d for d in r2.decisions if d.kind == pp.KIND_REPLACE]
    assert rep and rep[0].replace_target == "WEAK" and rep[0].ticker == "STAR"


def test_full_no_superior_suggests_add_to_best():
    existing = [_pos(f"H{i}", conv=7, status="HOLD", progress=0.85, ret=10) for i in range(10)]
    r = pp.evaluate(existing, [_cand("MEH", conv=6, ret=8)], CFG)
    adds = [d for d in r.decisions if d.kind == pp.KIND_ADD]
    assert adds and adds[0].add_target  # add to a held name
    assert "add to highest-conviction" in adds[0].reason


# ── top-N cap ───────────────────────────────────────────────────────────────────
def test_top_n_caps_output():
    cands = [_cand(f"T{i}", conv=9, ret=30) for i in range(10)]
    r = pp.evaluate([], cands, pp.PolicyConfig(top_n=2))
    assert len(r.decisions) == 2


# ── concentration sizing ────────────────────────────────────────────────────────
def test_size_shrinks_as_book_fills():
    empty = pp.size_position(1000, conviction=8, n_existing=0, account_value=0, cfg=CFG)
    full  = pp.size_position(1000, conviction=8, n_existing=9, account_value=0, cfg=CFG)
    assert empty > full > 0


def test_size_never_exceeds_compliance_ceiling():
    # conviction 10 on a small account must clamp to the per-position cap.
    acct = 5000.0
    s = pp.size_position(1000, conviction=10, n_existing=0, account_value=acct, cfg=CFG)
    assert s <= acct * (float(MAX_POSITION_PCT_OF_ACCOUNT) / 100.0) + 1e-6


def test_conviction_scale_matches_thematic_curve():
    assert pp.conviction_scale(1) == pytest.approx(0.4)
    assert pp.conviction_scale(10) == pytest.approx(1.5)
    assert pp.conviction_scale(6) == pytest.approx(1.0111, abs=1e-3)


def test_conviction_scale_clamps_and_fails_safe():
    # Out-of-range clamps to the [1,10] endpoints.
    assert pp.conviction_scale(99) == pp.conviction_scale(10)
    assert pp.conviction_scale(0) == pp.conviction_scale(1)
    assert pp.conviction_scale(-5) == pp.conviction_scale(1)
    # Malformed conviction must not crash the policy sizer — falls back to 1.
    for bad in (None, float("nan"), "n/a", object()):
        assert pp.conviction_scale(bad) == pytest.approx(0.4)


# ── config from env ─────────────────────────────────────────────────────────────
def test_config_from_env_clamps(monkeypatch):
    monkeypatch.setenv("THEMATIC_MAX_POSITIONS", "5")    # below floor → clamp to 10
    monkeypatch.setenv("THEMATIC_HARD_MAX_POSITIONS", "99")  # above ceiling → clamp to 20
    cfg = pp.PolicyConfig.from_env()
    assert cfg.max_positions == 10
    assert cfg.hard_max == 20

    monkeypatch.setenv("THEMATIC_MAX_POSITIONS", "15")
    cfg2 = pp.PolicyConfig.from_env()
    assert cfg2.max_positions == 15
    assert cfg2.near_capacity_count == 12  # round(15 * 0.8)
