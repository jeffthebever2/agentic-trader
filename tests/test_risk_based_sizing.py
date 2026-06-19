"""Risk-based (volatility-targeted) position sizing: shares = risk_budget /
(entry - stop), so a stop-out loses ~constant % of the account regardless of the
name's volatility — vs equal-dollar which puts wildly different risk per name.
Bounded by the position cap; 0 on unusable inputs (caller falls back to dollars)."""
import web.api.thematic_auto as t


def test_constant_dollar_risk_across_volatility():
    # 1% of $100k = $1,000 risk budget. Use stops >=10% away so the 10% position
    # cap doesn't bind (a stop tighter than 10% would correctly hit the cap).
    # stop 88 → $12 risk/share → 83 shares ; stop 80 → $20 → 50 shares
    s_tight = t._risk_based_shares(100_000, 100.0, 88.0, 1.0)
    s_wide = t._risk_based_shares(100_000, 100.0, 80.0, 1.0)
    # each position's dollar risk ≈ the $1,000 budget (within one share's worth)
    assert abs(s_tight * 12 - 1000) <= 12
    assert abs(s_wide * 20 - 1000) <= 20
    assert s_tight > s_wide                                # tighter stop → more shares


def test_position_cap_enforced():
    # tiny stop distance would imply a huge position; capped at 10% of account
    shares = t._risk_based_shares(100_000, 100.0, 99.9, 1.0)   # $0.10 risk/share
    assert shares * 100.0 <= 100_000 * 0.10                    # <= 10% cap ($10k → 100 sh)
    assert shares == 100


def test_unusable_inputs_return_zero():
    assert t._risk_based_shares(100_000, 100.0, 100.0, 1.0) == 0   # stop == price
    assert t._risk_based_shares(100_000, 100.0, 110.0, 1.0) == 0   # stop above price
    assert t._risk_based_shares(0, 100.0, 95.0, 1.0) == 0          # no account
    assert t._risk_based_shares(100_000, float("nan"), 95.0, 1.0) == 0
    assert t._risk_based_shares(100_000, 100.0, 95.0, 0.0) == 0


def test_risk_pct_clamped(monkeypatch):
    monkeypatch.setenv("THEMATIC_RISK_PCT_PER_TRADE", "999")
    assert t._risk_pct_per_trade() == 5.0       # clamped to 5%
    monkeypatch.setenv("THEMATIC_RISK_PCT_PER_TRADE", "junk")
    assert t._risk_pct_per_trade() == 1.0       # default on garbage


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("THEMATIC_RISK_SIZING", raising=False)
    assert t._risk_sizing_enabled() is False
