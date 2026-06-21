"""Portfolio-aware position sizer: factor behaviour + hard portfolio constraints.

Sizing moves real money, so the invariants here matter: never exceed the
per-position compliance cap, respect sector / heat / cash limits, scale with
conviction + reward/risk, scale *down* with volatility and correlation, and
degrade to a neutral factor (never crash) when an input is missing.
"""
import math

import pytest

from tradingagents.portfolio.position_sizer import (
    BookPosition, SizerConfig, SizingCandidate,
    candidate_adv_dollars, correlation_factor, inverse_vol_factor, quality_factor,
    realized_vol_pct, size_position,
)

CFG = SizerConfig()


def _cand(**kw):
    base = dict(ticker="X", conviction=5, score=50.0, expected_return_pct=0.0, stop_pct=0.0)
    base.update(kw)
    return SizingCandidate(**base)


# ── factors ──────────────────────────────────────────────────────────────────
def test_quality_factor_scales_with_score():
    lo = quality_factor(50, 0, 0, CFG)
    hi = quality_factor(85, 0, 0, CFG)
    assert hi > lo
    assert CFG.quality_floor <= lo <= CFG.quality_cap


def test_quality_factor_rewards_rr():
    rich = quality_factor(70, 60, 10, CFG)   # R:R 6
    thin = quality_factor(70, 12, 10, CFG)   # R:R 1.2
    assert rich > thin


def test_inverse_vol_factor_direction_and_neutral():
    calm = inverse_vol_factor(20, CFG)       # below target 40 → size up
    wild = inverse_vol_factor(120, CFG)      # well above → size down (floored)
    assert calm > 1.0 and wild < 1.0
    assert inverse_vol_factor(None, CFG) == 1.0   # unknown → neutral
    assert inverse_vol_factor(0, CFG) == 1.0
    assert wild >= CFG.vol_factor_floor and calm <= CFG.vol_factor_cap


def test_correlation_factor_penalizes_above_threshold():
    assert correlation_factor(0.5, CFG) == 1.0          # below threshold → neutral
    assert correlation_factor(None, CFG) == 1.0          # unknown → neutral
    assert correlation_factor(0.85, CFG) < 1.0
    assert correlation_factor(1.0, CFG) == pytest.approx(1.0 - CFG.corr_penalty_max)


# ── conviction direction ───────────────────────────────────────────────────────
def test_higher_conviction_sizes_bigger():
    lo = size_position(100_000, _cand(conviction=3, score=60), [])
    hi = size_position(100_000, _cand(conviction=10, score=60), [])
    assert hi.dollars > lo.dollars


# ── hard constraints ───────────────────────────────────────────────────────────
def test_never_exceeds_per_position_cap():
    # Max everything: top conviction, top score, calm vol, no correlation.
    res = size_position(
        100_000,
        _cand(conviction=10, score=100, expected_return_pct=200, stop_pct=10, volatility_pct=5),
        [],
    )
    assert res.dollars <= 100_000 * CFG.max_position_pct / 100.0 + 0.01
    assert res.binding_constraint == "per_position_cap"


def test_sector_cap_limits_when_overweight():
    existing = [
        BookPosition("NVDA", weight_pct=15, sector="Technology"),
        BookPosition("AMD", weight_pct=12, sector="Technology"),
    ]  # 27% already in Tech; cap 30 → only 3% room
    res = size_position(
        100_000,
        _cand(ticker="MU", conviction=9, score=90, sector="Technology"),
        existing,
    )
    assert res.dollars <= 3_000 + 0.01
    assert res.binding_constraint == "sector_cap"


def test_unknown_sector_no_sector_clamp():
    existing = [BookPosition("NVDA", weight_pct=28, sector="Technology")]
    res = size_position(100_000, _cand(conviction=8, score=85, sector=None), existing)
    assert res.binding_constraint != "sector_cap"


def test_portfolio_heat_caps_when_almost_fully_deployed():
    existing = [BookPosition(f"T{i}", weight_pct=9.7, sector=None) for i in range(8)]  # ~77.6%
    res = size_position(100_000, _cand(conviction=8, score=85), existing)
    # heat cap 80% → ~2.4% room
    assert res.dollars <= 100_000 * 0.80 - 77.6 / 100 * 100_000 + 0.01
    assert res.binding_constraint in ("portfolio_heat", "per_position_cap", "sector_cap")


def test_cash_available_caps_size():
    res = size_position(100_000, _cand(conviction=9, score=90), [], cash_available=500.0)
    assert res.dollars <= 500.0
    assert res.binding_constraint == "cash"


def test_below_min_dollar_returns_zero():
    res = size_position(1_000, _cand(conviction=1, score=50), [], cfg=SizerConfig(min_dollar=100.0))
    assert res.dollars == 0.0


def test_zero_account_value_safe():
    res = size_position(0, _cand(conviction=9, score=90), [])
    assert res.dollars == 0.0 and res.binding_constraint == "no_account_value"


def test_correlation_shrinks_size():
    plain = size_position(100_000, _cand(conviction=7, score=80, max_corr=0.2), [])
    corr = size_position(100_000, _cand(conviction=7, score=80, max_corr=0.95), [])
    assert corr.dollars < plain.dollars


def test_result_to_dict_shape():
    d = size_position(100_000, _cand(conviction=7, score=80), []).to_dict()
    assert set(d) == {"dollars", "weight_pct", "factors", "binding_constraint", "notes"}
    assert {"conviction", "quality", "inverse_vol", "correlation"} <= set(d["factors"])


# ── vol helper ─────────────────────────────────────────────────────────────────
def test_realized_vol_pct():
    assert realized_vol_pct([100, 101, 100, 102, 101, 103]) is not None
    assert realized_vol_pct([100, 101]) is None          # too short
    flat = realized_vol_pct([100] * 10)
    assert flat == pytest.approx(0.0)


def test_from_env_caps_to_compliance(monkeypatch):
    monkeypatch.setenv("SIZER_MAX_POSITION_PCT", "50")  # attempt to exceed compliance
    from tradingagents.portfolio.position_sizer import MAX_POSITION_PCT_OF_ACCOUNT
    cfg = SizerConfig.from_env()
    assert cfg.max_position_pct <= float(MAX_POSITION_PCT_OF_ACCOUNT)


# ── liquidity / ADV throttle ────────────────────────────────────────────────────
def test_liquid_megacap_adv_does_not_constrain():
    # ADV $5B, 1% participation = $50M >> any size → liquidity neutral, not binding.
    res = size_position(
        100_000,
        _cand(conviction=7, score=80, adv_dollars=5_000_000_000),
        [],
    )
    assert res.binding_constraint != "liquidity_adv"
    assert res.factors["liquidity"] == 1.0


def test_thin_stock_adv_clamps_size():
    # ADV $200k, 1% = $2k cap — well below the unconstrained target → binds.
    res = size_position(
        100_000,
        _cand(conviction=7, score=80, adv_dollars=200_000),
        [],
    )
    assert res.binding_constraint == "liquidity_adv"
    assert res.dollars <= 2_000 + 0.01
    assert res.factors["liquidity"] < 1.0


def test_missing_adv_is_neutral():
    res = size_position(100_000, _cand(conviction=7, score=80, adv_dollars=None), [])
    assert res.factors["liquidity"] == 1.0
    assert res.binding_constraint != "liquidity_adv"


def test_adv_participation_config_override():
    cand = _cand(conviction=7, score=80, adv_dollars=200_000)
    tight = size_position(100_000, cand, [], cfg=SizerConfig(max_adv_participation_pct=1.0))
    loose = size_position(100_000, cand, [], cfg=SizerConfig(max_adv_participation_pct=5.0))
    assert loose.dollars > tight.dollars                 # higher participation → bigger
    assert tight.binding_constraint == "liquidity_adv"   # 1% still binds


def test_adv_participation_env_override(monkeypatch):
    monkeypatch.setenv("SIZER_MAX_ADV_PARTICIPATION_PCT", "2.5")
    assert SizerConfig.from_env().max_adv_participation_pct == 2.5


def test_candidate_adv_dollars_sources():
    # explicit adv_dollars wins
    assert candidate_adv_dollars(_cand(adv_dollars=1_000_000, avg_volume=10, price=10)) == 1_000_000
    # else avg_volume × price
    assert candidate_adv_dollars(_cand(avg_volume=500_000, price=20)) == 10_000_000
    # neither → None (neutral)
    assert candidate_adv_dollars(_cand()) is None
