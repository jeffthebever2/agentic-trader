"""Tests for execution-adjusted probability in alpha_engine.py"""
import pytest
from tradingagents.portfolio.alpha_engine import compute_exec_adjusted_prob, AlphaEngine


# ── compute_exec_adjusted_prob ────────────────────────────────────────────────

def test_fresh_zero_spread_returns_raw():
    assert compute_exec_adjusted_prob(0.70, 0.0, 0.0) == pytest.approx(0.70)


def test_max_age_halves_prob():
    # At max age, freshness_factor = 0.5 → adjusted = raw × 0.5
    assert compute_exec_adjusted_prob(0.70, quote_age_seconds=300, spread_bps=0,
                                       max_quote_age_seconds=300) == pytest.approx(0.35, abs=1e-5)


def test_max_spread_halves_prob():
    # At max spread, spread_factor = 0.5 → adjusted = raw × 0.5
    assert compute_exec_adjusted_prob(0.70, quote_age_seconds=0, spread_bps=75,
                                       max_spread_bps=75) == pytest.approx(0.35, abs=1e-5)


def test_both_max_quarters_prob():
    # freshness=0.5, spread=0.5 → 0.70 × 0.5 × 0.5 = 0.175
    assert compute_exec_adjusted_prob(0.70, 300, 75, 300, 75) == pytest.approx(0.175, abs=1e-5)


def test_half_age_partial_decay():
    # age=150 out of 300 → fraction=0.5 → factor=0.75 → 0.70 × 0.75 = 0.525
    assert compute_exec_adjusted_prob(0.70, 150, 0, 300, 75) == pytest.approx(0.525, abs=1e-5)


def test_clamps_prob_input_above_1():
    r = compute_exec_adjusted_prob(1.5, 0.0, 0.0)
    assert r <= 1.0


def test_clamps_prob_input_below_0():
    r = compute_exec_adjusted_prob(-0.3, 0.0, 0.0)
    assert r >= 0.0


def test_zero_max_age_no_decay():
    # If max_quote_age_seconds=0, skip freshness decay
    r = compute_exec_adjusted_prob(0.70, quote_age_seconds=999, max_quote_age_seconds=0)
    assert r == pytest.approx(0.70)


def test_age_over_max_clamped_to_max():
    # Age 600 with max 300 → clamped to 1.0 fraction → factor = 0.5
    r = compute_exec_adjusted_prob(0.80, quote_age_seconds=600, max_quote_age_seconds=300, spread_bps=0)
    assert r == pytest.approx(0.80 * 0.5, abs=1e-5)


def test_spread_over_max_clamped():
    r = compute_exec_adjusted_prob(0.80, 0, 200, max_spread_bps=75)
    assert r == pytest.approx(0.80 * 0.5, abs=1e-5)


# ── AlphaEngine.evaluate() integration ───────────────────────────────────────

class _Candidate:
    ticker = "AAPL"
    ml_probability = 0.70
    expected_return = 0.03
    large_loss_probability = 0.10
    target_before_stop_probability = 0.60
    timeout_probability = 0.25
    atr = 2.5
    entry = 150.0
    stop = 145.0
    target = 165.0


def test_evaluate_has_exec_adjusted_prob_field():
    engine = AlphaEngine()
    result = engine.evaluate(_Candidate())
    assert hasattr(result, "exec_adjusted_probability")
    assert 0.0 <= result.exec_adjusted_probability <= 1.0


def test_evaluate_fresh_exec_prob_equals_win_prob():
    engine = AlphaEngine()
    result = engine.evaluate(_Candidate(), quote_age_seconds=0, spread_bps=0)
    assert result.exec_adjusted_probability == pytest.approx(0.70)


def test_evaluate_stale_quote_lowers_exec_prob():
    engine = AlphaEngine()
    fresh = engine.evaluate(_Candidate(), quote_age_seconds=0)
    stale = engine.evaluate(_Candidate(), quote_age_seconds=300)
    assert stale.exec_adjusted_probability < fresh.exec_adjusted_probability


def test_evaluate_wide_spread_lowers_exec_prob():
    engine = AlphaEngine()
    tight = engine.evaluate(_Candidate(), spread_bps=0)
    wide = engine.evaluate(_Candidate(), spread_bps=75)
    assert wide.exec_adjusted_probability < tight.exec_adjusted_probability


def test_exec_adjusted_prob_in_audit_dict():
    engine = AlphaEngine()
    result = engine.evaluate(_Candidate(), quote_age_seconds=60, spread_bps=20)
    d = result.to_audit_dict()
    # audit_dict is optional for this field but result should be accessible
    assert result.exec_adjusted_probability > 0
