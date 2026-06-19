"""Weak-catalyst cap in _sanitize_picks: a pick with no concrete catalyst (empty
or generic filler like 'momentum'/'hype') is momentum-on-hope, not a thesis — its
conviction is capped at 6 so it can't size up like a real catalyst-backed pick.
Deterministic enforcement of the prompt's concrete-catalyst rule."""
import web.api.thematic_auto as t

ALLOWED = {"NVDA", "AMD"}


def test_generic_catalyst_caps_conviction():
    for cat in ("", "momentum", "social buzz", "hype", "n/a", "trending", "TBD"):
        out = t._sanitize_picks(
            [{"ticker": "NVDA", "conviction": 10, "catalyst": cat}], ALLOWED
        )
        assert out[0]["conviction"] == 6
        assert out[0].get("weak_catalyst") is True


def test_concrete_catalyst_preserves_conviction():
    out = t._sanitize_picks(
        [{"ticker": "NVDA", "conviction": 9, "catalyst": "Q4 earnings on Feb 26 + new Blackwell launch"}],
        ALLOWED,
    )
    assert out[0]["conviction"] == 9
    assert "weak_catalyst" not in out[0]


def test_weak_catalyst_does_not_raise_low_conviction():
    # cap only lowers; a conviction already <= 6 is unchanged.
    out = t._sanitize_picks([{"ticker": "AMD", "conviction": 4, "catalyst": "hype"}], ALLOWED)
    assert out[0]["conviction"] == 4
