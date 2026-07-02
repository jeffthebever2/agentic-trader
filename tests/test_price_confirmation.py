"""Price/volume confirmation of social signals (web/api/thematic_auto.py).

The composite score is pure narrative; price_confirmation() reads the tape:
accumulation (up on volume) boosts, distribution (down on volume) cuts,
quiet/missing data is neutral.
"""
import pytest

from web.api.thematic_auto import (
    _load_source_weights,
    _price_confirm_for,
    _source_weights_cache,
    price_confirmation,
)


def _bars(daily_ret=0.0, rvol=1.0, n=30, base=100.0, vol=1_000_000.0):
    """Synthetic bars: constant daily return; last 2 days at rvol× volume."""
    closes, volumes = [], []
    px = base
    for i in range(n):
        px *= (1 + daily_ret)
        closes.append(px)
        volumes.append(vol * (rvol if i >= n - 2 else 1.0))
    return closes, volumes


def test_neutral_when_too_few_bars():
    assert price_confirmation([100.0] * 5, [1e6] * 5) == 1.0
    assert price_confirmation([], []) == 1.0


def test_flat_tape_is_neutral():
    c, v = _bars(daily_ret=0.0, rvol=1.0)
    assert price_confirmation(c, v) == pytest.approx(1.0, abs=0.02)


def test_accumulation_boosts():
    c, v = _bars(daily_ret=0.02, rvol=2.5)   # ~+10% over 5d on heavy volume
    m = price_confirmation(c, v)
    assert m > 1.05
    assert m <= 1.12


def test_distribution_cuts():
    c, v = _bars(daily_ret=-0.02, rvol=2.5)  # ~-10% over 5d on heavy volume
    m = price_confirmation(c, v)
    assert m < 0.95
    assert m >= 0.85


def test_down_move_on_quiet_volume_cuts_less_than_heavy():
    c1, v1 = _bars(daily_ret=-0.02, rvol=0.5)
    c2, v2 = _bars(daily_ret=-0.02, rvol=2.5)
    quiet, heavy = price_confirmation(c1, v1), price_confirmation(c2, v2)
    assert heavy < quiet < 1.0


def test_bounds_respected_on_extreme_moves():
    c, v = _bars(daily_ret=0.15, rvol=5.0)
    assert price_confirmation(c, v) == 1.12
    c, v = _bars(daily_ret=-0.15, rvol=5.0)
    assert price_confirmation(c, v) == 0.85


def test_garbage_values_ignored():
    c, v = _bars(daily_ret=0.02, rvol=2.0)
    c[3] = float("nan")
    v[4] = float("inf")
    m = price_confirmation(c, v)
    assert 0.85 <= m <= 1.12


def test_wrapper_neutral_on_fetch_failure():
    assert _price_confirm_for("NVDA", fetch=lambda t: (_ for _ in ()).throw(RuntimeError)) == 1.0
    assert _price_confirm_for("NVDA", fetch=lambda t: {}) == 1.0


def test_wrapper_uses_bars():
    c, v = _bars(daily_ret=0.02, rvol=2.5)
    m = _price_confirm_for("NVDA", fetch=lambda t: {"closes": c, "volumes": v})
    assert m > 1.05


# ── adaptive weight loading (merge-side) ─────────────────────────────────────
def test_load_source_weights_disabled(monkeypatch):
    monkeypatch.setenv("THEMATIC_ADAPTIVE_WEIGHTS", "false")
    assert _load_source_weights() == {}


def test_load_source_weights_reads_file(monkeypatch, tmp_path):
    import web.api.thematic_auto as ta
    monkeypatch.setenv("THEMATIC_ADAPTIVE_WEIGHTS", "true")
    p = tmp_path / "weights.json"
    p.write_text('{"weights": {"reddit": 1.25}}')
    monkeypatch.setattr(ta, "SOURCE_WEIGHTS_FILE", p)
    _source_weights_cache["mtime"] = None
    assert ta._load_source_weights() == {"reddit": 1.25}
    _source_weights_cache["mtime"] = None
