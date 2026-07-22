"""Tests for the thematic-v2 correlation risk layer: N_eff + weighted load."""
from __future__ import annotations

import pytest

from tradingagents.portfolio.correlation import (
    effective_bets, correlation_load, correlation_matrix,
)
from tradingagents.portfolio.position_sizer import correlation_factor, SizerConfig

CFG = SizerConfig()  # corr_threshold 0.70, corr_penalty_max 0.50

# Deterministic series: a zigzag correlates +1 with itself, ~-1 with its inverse.
UP = [100, 102, 100, 102, 100, 102, 100, 102]
DOWN = [102, 100, 102, 100, 102, 100, 102, 100]


# ── effective_bets ────────────────────────────────────────────────────────────

def test_neff_identity_matrix_equals_n():
    mat = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert effective_bets([1, 1, 1], mat) == pytest.approx(3.0)


def test_neff_all_ones_collapses_to_one():
    mat = [[1, 1, 1], [1, 1, 1], [1, 1, 1]]
    assert effective_bets([1, 1, 1], mat) == pytest.approx(1.0)


def test_neff_partial_between_one_and_n():
    mat = [[1, 0.5, 0.5], [0.5, 1, 0.5], [0.5, 0.5, 1]]
    neff = effective_bets([1, 1, 1], mat)
    assert 1.0 < neff < 3.0


def test_neff_degenerate_inputs_return_none():
    assert effective_bets([], []) is None
    assert effective_bets([1, 1], [[1, 0]]) is None            # mis-shaped
    assert effective_bets([0, 0], [[1, 0], [0, 1]]) is None    # zero weight sum
    assert effective_bets([1], [[1]]) == 1.0                   # single name


# ── correlation_load ──────────────────────────────────────────────────────────

def test_load_whole_book_below_max_corr():
    # One holding tracks the candidate (+1), one is its inverse (~-1). Single-worst
    # max_corr = 1.0, but the whole-book LOAD averages to ~0.5 (negative floored to 0).
    out = correlation_load(UP, {"SAME": UP, "ANTI": DOWN})
    assert out["max_corr"] == pytest.approx(1.0, abs=0.05)
    assert out["load"] == pytest.approx(0.5, abs=0.1)
    assert out["load"] < out["max_corr"]
    assert out["linked"] == 1  # only SAME clears the 0.65 link threshold


def test_load_empty_book_is_none():
    out = correlation_load(UP, {})
    assert out["load"] is None and out["max_corr"] is None and out["corr_vector"] == {}


def test_load_negative_correlation_floored_to_zero():
    out = correlation_load(UP, {"ANTI": DOWN})
    assert out["max_corr"] < 0          # genuinely anti-correlated
    assert out["load"] == pytest.approx(0.0)  # a hedge never inflates the load


# ── correlation_factor (rewired to shrink off the load) ───────────────────────

def test_factor_load_below_threshold_neutral():
    assert correlation_factor(0.5, CFG) == pytest.approx(1.0)


def test_factor_load_above_threshold_shrinks():
    f = correlation_factor(0.85, CFG)
    assert f < 1.0 and f >= 1.0 - CFG.corr_penalty_max


def test_factor_falls_back_to_max_corr_when_no_load():
    # No load computed → use the legacy single-worst max_corr so callers still shrink.
    assert correlation_factor(None, CFG, max_corr=0.9) < 1.0
    assert correlation_factor(None, CFG, max_corr=None) == pytest.approx(1.0)


def test_factor_floored_never_blocks():
    # Even at load 1.0 the shrink is a soft floor, never 0 (hard floor = macro layer).
    assert correlation_factor(1.0, CFG) == pytest.approx(1.0 - CFG.corr_penalty_max)


# ── correlation_matrix → effective_bets round trip ────────────────────────────

def test_matrix_feeds_effective_bets():
    tks, mat = correlation_matrix({"A": UP, "B": UP, "C": DOWN})
    assert len(tks) == 3 and len(mat) == 3 and mat[0][0] == 1.0
    neff = effective_bets([1, 1, 1], mat)
    assert neff is not None and 1.0 <= neff <= 3.0
