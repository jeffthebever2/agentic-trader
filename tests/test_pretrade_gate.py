"""Tests for tradingagents.portfolio.pretrade_gate.PreTradeGate"""
import datetime as dt
import pytest
from tradingagents.portfolio.pretrade_gate import PreTradeGate, GateResult


NOW = dt.datetime(2026, 6, 6, 14, 0, 0)


def _gate(**kwargs) -> PreTradeGate:
    return PreTradeGate(**kwargs)


def _fresh(offset_seconds: float = 0.0) -> dt.datetime:
    return NOW - dt.timedelta(seconds=offset_seconds)


# ── freshness ─────────────────────────────────────────────────────────────────

def test_fresh_quote_passes():
    g = _gate()
    r = g.check("AAPL", _fresh(1), 150.0, now=NOW)
    assert r.ok
    assert r.reason == "ok"


def test_just_within_age_passes():
    g = _gate(max_quote_age_seconds=300)
    r = g.check("AAPL", _fresh(299), 150.0, now=NOW)
    assert r.ok


def test_exactly_at_limit_fails():
    g = _gate(max_quote_age_seconds=300)
    r = g.check("AAPL", _fresh(300.1), 150.0, now=NOW)
    assert not r.ok
    assert r.reason == "stale_quote"


def test_stale_quote_detail_has_age():
    g = _gate(max_quote_age_seconds=60)
    r = g.check("NVDA", _fresh(120), 400.0, now=NOW)
    assert not r.ok
    assert r.detail["quote_age_seconds"] > 60
    assert r.detail["max_quote_age_seconds"] == 60
    assert r.detail["ticker"] == "NVDA"


def test_custom_max_age():
    g = _gate(max_quote_age_seconds=10)
    r = g.check("TSLA", _fresh(11), 200.0, now=NOW)
    assert not r.ok
    assert r.reason == "stale_quote"


# ── spread ────────────────────────────────────────────────────────────────────

def test_tight_spread_passes():
    g = _gate()
    r = g.check("AAPL", _fresh(1), 150.0, bid=149.90, ask=150.10, now=NOW)
    assert r.ok


def test_wide_spread_fails():
    # bid=100, ask=101 → spread=100bps
    g = _gate(max_spread_bps=75.0)
    r = g.check("SPY", _fresh(1), 450.0, bid=100.0, ask=101.0, now=NOW)
    assert not r.ok
    assert r.reason == "wide_spread"
    assert r.detail["spread_bps"] > 75


def test_spread_exactly_at_limit_fails():
    # bid=99.925, ask=100.075 → spread = 0.15/100 * 10000 = 15 bps
    g = _gate(max_spread_bps=15.0)
    r = g.check("X", _fresh(1), 100.0, bid=99.924, ask=100.076, now=NOW)
    assert not r.ok


def test_spread_passes_when_none_unless_required():
    g = _gate(max_quote_age_seconds=60, max_spread_bps=1.0)  # tight limit but no bid/ask supplied
    r = g.check("AAPL", _fresh(5), 150.0, now=NOW)
    assert r.ok


def test_bid_ask_required_fails_when_missing():
    g = _gate(max_quote_age_seconds=60, require_bid_ask=True)
    r = g.check("AAPL", _fresh(5), 150.0, now=NOW)
    assert not r.ok
    assert r.reason == "no_data"


def test_spread_passes_when_ask_equals_bid():
    g = _gate(max_quote_age_seconds=60, max_spread_bps=1.0)
    r = g.check("AAPL", _fresh(5), 150.0, bid=150.0, ask=150.0, now=NOW)
    assert r.ok  # bid == ask is a zero-width spread


def test_yfinance_only_rejected_when_trusted_source_required():
    g = _gate(max_quote_age_seconds=60, require_trusted_source=True)
    r = g.check("AAPL", _fresh(1), 150.0, now=NOW, quote_source="yfinance")
    assert not r.ok
    assert r.reason == "provider_untrusted"


def test_trusted_primary_source_passes():
    g = _gate(max_quote_age_seconds=60, require_trusted_source=True)
    r = g.check("AAPL", _fresh(1), 150.0, now=NOW, quote_source="alpaca_iex")
    assert r.ok


def test_two_trusted_backups_with_consensus_pass():
    g = _gate(max_quote_age_seconds=60, require_trusted_source=True)
    r = g.check(
        "AAPL",
        _fresh(1),
        150.0,
        now=NOW,
        quote_source="yfinance",
        backup_sources=["finnhub", "twelve_data"],
        consensus_ok=True,
    )
    assert r.ok


def test_one_backup_without_consensus_fails():
    g = _gate(max_quote_age_seconds=60, require_trusted_source=True)
    r = g.check(
        "AAPL",
        _fresh(1),
        150.0,
        now=NOW,
        quote_source="yfinance",
        backup_sources=["finnhub"],
        consensus_ok=True,
    )
    assert not r.ok
    assert r.reason == "provider_untrusted"


def test_price_drift_fails():
    g = _gate(max_quote_age_seconds=60, max_price_drift_bps=50)
    r = g.check("AAPL", _fresh(1), 151.0, now=NOW, signal_price=150.0)
    assert not r.ok
    assert r.reason == "price_drift_too_large"


def test_risk_reward_fails():
    g = _gate(max_quote_age_seconds=60, min_risk_reward=2.0)
    r = g.check("AAPL", _fresh(1), 100.0, now=NOW, stop=95.0, target=105.0)
    assert not r.ok
    assert r.reason == "risk_reward_failed"


# ── stale takes priority over spread ─────────────────────────────────────────

def test_stale_beats_wide_spread():
    g = _gate(max_quote_age_seconds=60, max_spread_bps=10.0)
    r = g.check("Z", _fresh(200), 50.0, bid=49.0, ask=51.0, now=NOW)
    assert not r.ok
    assert r.reason == "stale_quote"


# ── now defaults to datetime.now() ───────────────────────────────────────────

def test_now_defaults():
    g = _gate(max_quote_age_seconds=3600)
    snapshot = dt.datetime.now() - dt.timedelta(seconds=10)
    r = g.check("AAPL", snapshot, 150.0)
    assert r.ok


# ── GateResult is frozen ─────────────────────────────────────────────────────

def test_gate_result_frozen():
    r = GateResult(ok=True, reason="ok", detail={})
    with pytest.raises((AttributeError, TypeError)):
        r.ok = False  # type: ignore


# ── Trusted-source: FMP is the configured execution provider ──────────────────

def test_fmp_single_source_passes_trusted_gate():
    """Regression: 'fmp' must be trusted so a single-provider FMP quote clears the
    require_trusted_source gate (no 2-source consensus needed)."""
    g = _gate(require_trusted_source=True, max_quote_age_seconds=3600)
    r = g.check("NVDA", _fresh(1), 209.0, quote_source="fmp",
                backup_sources=[], consensus_ok=True, market_open=True, now=NOW)
    assert r.ok, r.reason


def test_pretrade_and_gateway_trusted_lists_in_sync():
    from tradingagents.data.quote_gateway import TRUSTED_SOURCES as GW
    assert GW <= PreTradeGate.TRUSTED_SOURCES, "gateway trusted sources must be a subset of the gate's"
