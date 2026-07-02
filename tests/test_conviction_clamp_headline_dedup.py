"""Conviction evidence ceiling + cross-source headline dedup (web/api/thematic_auto.py).

The free LLM can't set 9/10 conviction without corroboration, and one press
release syndicated to four news feeds counts once, not four times.
"""
import pytest

from web.api.thematic_auto import (
    _SEEN_HEADLINES,
    _headline_is_dupe,
    _reset_social_intent,
    clamp_conviction,
    conviction_ceiling,
)


# ── conviction_ceiling ladder ─────────────────────────────────────────────────
def test_no_evidence_caps_at_7():
    assert conviction_ceiling(False, 0, False) == 7
    assert conviction_ceiling(False, 1, False) == 7


def test_single_leg_allows_8():
    assert conviction_ceiling(True, 0, False) == 8    # confirmed only
    assert conviction_ceiling(False, 2, False) == 8   # quality breadth only


def test_confirmed_plus_breadth_allows_9():
    assert conviction_ceiling(True, 2, False) == 9


def test_10_needs_confirmation_and_strong_evidence():
    assert conviction_ceiling(True, 3, False) == 10
    assert conviction_ceiling(True, 1, True) == 10    # insider + social combo
    assert conviction_ceiling(False, 5, True) == 8    # unconfirmed never 9+


def test_low_conviction_never_raised():
    # ceiling only caps; a model 5 stays 5 regardless of evidence
    assert clamp_conviction(5, {"reddit": 10, "insider": 5, "google_news": 3}, True) == 5


def test_clamp_uses_breakdown_quality_sources():
    bd_weak = {"finviz": 5.0, "yahoo_movers": 3.0}        # screeners: not quality
    bd_strong = {"reddit": 10.0, "google_news": 4.0, "insider": 5.0}
    assert clamp_conviction(9, bd_weak, False) == 7
    assert clamp_conviction(9, bd_strong, True) == 9
    assert clamp_conviction(10, bd_strong, True) == 10    # insider+reddit combo


def test_clamp_ignores_non_numeric_breakdown_entries():
    bd = {"reddit": 10.0, "avoid": True, "sell_intent_reason": "x", "google_news": 2.0}
    assert clamp_conviction(9, bd, True) == 9


# ── headline dedup ────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _fresh_registry():
    _SEEN_HEADLINES.clear()
    yield
    _SEEN_HEADLINES.clear()


def test_first_copy_counts_second_skipped():
    h = "Acme Robotics announces record Q2 earnings and raises full-year guidance"
    assert _headline_is_dupe(h) is False
    assert _headline_is_dupe(h) is True


def test_syndication_suffix_still_matches():
    a = "Acme Robotics announces record Q2 earnings and raises full-year guidance today"
    b = "Acme Robotics announces record Q2 earnings and raises full-year guidance today - Yahoo Finance"
    assert _headline_is_dupe(a) is False
    assert _headline_is_dupe(b) is True     # same first-14-word fingerprint


def test_punctuation_and_case_normalized():
    assert _headline_is_dupe("NVIDIA Corp. (NVDA) Beats Estimates, Shares Jump After Hours!") is False
    assert _headline_is_dupe("nvidia corp NVDA beats estimates shares jump after hours") is True


def test_short_texts_never_deduped():
    assert _headline_is_dupe("NVDA up big") is False
    assert _headline_is_dupe("NVDA up big") is False   # still not deduped
    assert _headline_is_dupe("") is False


def test_different_stories_both_count():
    assert _headline_is_dupe("Acme Robotics announces record Q2 earnings beat and raises guidance") is False
    assert _headline_is_dupe("Beta Industries recalls flagship product after safety investigation") is False


def test_reset_clears_registry():
    h = "Acme Robotics announces record Q2 earnings and raises full-year guidance"
    assert _headline_is_dupe(h) is False
    _reset_social_intent()
    assert _headline_is_dupe(h) is False   # fresh scan → counts again
