"""Market-regime engine (pure). Validates trend/vol/breadth classification, the
risk-on composite, the adaptive threshold multiplier, and graceful degradation on
missing/garbage inputs."""
import math

from tradingagents.portfolio import regime as r


def test_trend_state_up_down_neutral():
    up = list(range(1, 261))                       # steadily rising → uptrend
    assert r.trend_state(up) == "up"
    down = list(range(260, 0, -1))                 # steadily falling
    assert r.trend_state(down) == "down"
    assert r.trend_state([100] * 50) == "unknown"  # < slow window


def test_realized_vol_and_regime():
    calm = [100 + 0.01 * i for i in range(30)]     # tiny moves → low vol
    assert r.vol_regime(closes=calm) in ("calm", "normal")
    assert r.vol_regime(vix=11) == "calm"
    assert r.vol_regime(vix=18) == "normal"
    assert r.vol_regime(vix=25) == "elevated"
    assert r.vol_regime(vix=40) == "high"
    assert r.vol_regime(vix=None, closes=None) == "unknown"


def test_breadth_state():
    assert r.breadth_state(75) == "strong"
    assert r.breadth_state(50) == "neutral"
    assert r.breadth_state(20) == "weak"
    assert r.breadth_state(None) == "unknown"
    assert r.breadth_state(float("nan")) == "unknown"


def test_risk_on_score_ordering():
    best = r.risk_on_score(trend="up", volatility="calm", breadth="strong")
    worst = r.risk_on_score(trend="down", volatility="high", breadth="weak")
    assert best == 100.0 and worst == 0.0
    mid = r.risk_on_score(trend="neutral", volatility="normal", breadth="neutral")
    assert worst < mid < best


def test_threshold_multiplier_monotonic():
    assert r.regime_threshold_multiplier(100) == 1.0
    assert r.regime_threshold_multiplier(60) == 1.1
    assert r.regime_threshold_multiplier(40) == 1.25
    assert r.regime_threshold_multiplier(10) == 1.5
    # risk-off demands a strictly higher gate than risk-on
    assert r.regime_threshold_multiplier(10) > r.regime_threshold_multiplier(90)
    assert r.regime_threshold_multiplier(float("nan")) == 1.25


def test_assess_regime_risk_off_flag():
    bear = r.assess_regime(spy_closes=list(range(260, 0, -1)), vix=42, pct_above_50dma=15)
    assert bear["risk_off"] is True and bear["threshold_multiplier"] >= 1.25
    bull = r.assess_regime(spy_closes=list(range(1, 261)), vix=11, pct_above_50dma=70)
    assert bull["risk_off"] is False and bull["threshold_multiplier"] == 1.0


def test_unknown_inputs_degrade_to_neutral():
    snap = r.assess_regime()
    assert 0 <= snap["risk_on_score"] <= 100
    assert math.isfinite(snap["threshold_multiplier"])
    assert snap["risk_off"] is False   # missing data must not force a stand-down


# ── thematic_auto wiring (flag + injectable fetch + cache) ──────────────────
import web.api.thematic_auto as ta


def test_regime_gate_disabled_returns_one(monkeypatch):
    monkeypatch.delenv("THEMATIC_REGIME_GATE", raising=False)
    ta._regime_cache.update({"ts": 0.0, "mult": 1.0})
    assert ta._regime_threshold_multiplier(fetch=lambda: list(range(260, 0, -1))) == 1.0


def test_regime_gate_risk_off_raises_gate(monkeypatch):
    monkeypatch.setenv("THEMATIC_REGIME_GATE", "true")
    ta._regime_cache.update({"ts": 0.0, "mult": 0.0})
    # falling SPY → downtrend → risk-off → multiplier > 1
    mult = ta._regime_threshold_multiplier(fetch=lambda: list(range(260, 0, -1)), now=1.0)
    assert mult > 1.0


def test_regime_gate_missing_data_no_change(monkeypatch):
    monkeypatch.setenv("THEMATIC_REGIME_GATE", "true")
    ta._regime_cache.update({"ts": 0.0, "mult": 0.0})
    assert ta._regime_threshold_multiplier(fetch=lambda: [], now=1.0) == 1.0
