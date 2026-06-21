"""Real-money safety: the Holdings Brain must never manage a protected account
(Roth IRA / retirement) or a non-equity instrument (money market / mutual fund).

Anchored to the real export Portfolio_Positions_Jun-16-2026.csv:
  Z30299153 Individual - Youth Account : SPAXX(MM), ASTS, HIMS, IREN, NVDA, ONDS
  262502469 ROTH IRA for Minor         : FSPGX (mutual fund)
"""
import os
import pytest

from tradingagents.portfolio import holdings_brain as hb


def _rows():
    return [
        {"symbol": "SPAXX", "description": "HELD IN MONEY MARKET",
         "account_number": "Z30299153", "account_name": "Individual - Youth Account",
         "qty": "0", "market_value": "$617.54", "pct_of_account": "13.81%"},
        {"symbol": "ASTS", "description": "AST SPACEMOBILE INC", "account_number": "Z30299153",
         "account_name": "Individual - Youth Account", "qty": "9.109", "last_price": "$84.30",
         "market_value": "$767.93", "pct_of_account": "17.17%", "total_gain_pct": "-4.01%",
         "cost_per_share": "$87.82"},
        {"symbol": "NVDA", "description": "NVIDIA CORP", "account_number": "Z30299153",
         "account_name": "Individual - Youth Account", "qty": "7", "last_price": "$209.20",
         "market_value": "$1464.40", "pct_of_account": "32.74%", "total_gain_pct": "+4.50%",
         "cost_per_share": "$200.17"},
        {"symbol": "FSPGX", "description": "FIDELITY LARGE CAP GROWTH INDEX FUND",
         "account_number": "262502469", "account_name": "ROTH IRA for Minor",
         "qty": "11.636", "last_price": "$48.75", "market_value": "$567.25",
         "pct_of_account": "100.00%", "total_gain_pct": "+13.45%", "cost_per_share": "$42.97"},
    ]


def test_roth_ira_never_managed():
    held = hb.normalize_holdings(_rows(), "fidelity")
    tickers = {h.ticker for h in held}
    assert "FSPGX" not in tickers
    assert not any(h.account_number == "262502469" for h in held)


def test_money_market_and_mutual_fund_excluded():
    held = hb.normalize_holdings(_rows(), "fidelity")
    tickers = {h.ticker for h in held}
    assert "SPAXX" not in tickers          # money market sweep
    assert "FSPGX" not in tickers          # mutual fund


def test_youth_account_equities_managed():
    held = hb.normalize_holdings(_rows(), "fidelity")
    assert {h.ticker for h in held} == {"ASTS", "NVDA"}  # plus HIMS/IREN/ONDS in full CSV


def test_excluded_report_explains_each():
    rep = {e["symbol"]: e["reason"] for e in hb.excluded_holdings(_rows(), "fidelity")}
    assert "protected account" in rep["FSPGX"]
    assert "non-equity" in rep["SPAXX"]


def test_is_protected_account_patterns():
    assert hb.is_protected_account("ROTH IRA for Minor", "262502469")
    assert hb.is_protected_account("Rollover IRA", "111")
    assert hb.is_protected_account("My 401(k)", "222")
    assert not hb.is_protected_account("Individual - Youth Account", "Z30299153")
    assert not hb.is_protected_account("Brokerage", "999")


def test_allowlist_mode_flips_to_default_deny(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_ALLOWED_ACCOUNTS", "Z30299153")
    # Youth account allowed; Roth (and anything else) denied.
    assert not hb.is_protected_account("Individual - Youth Account", "Z30299153")
    assert hb.is_protected_account("Anything", "262502469")
    assert hb.is_protected_account("Even unnamed", "")


def test_is_non_equity_symbol():
    assert hb.is_non_equity_symbol("SPAXX", "HELD IN MONEY MARKET")
    assert hb.is_non_equity_symbol("FSPGX", "FIDELITY LARGE CAP GROWTH INDEX FUND")
    assert hb.is_non_equity_symbol("FXAIX", "")          # 5-letter ending X = mutual fund
    assert not hb.is_non_equity_symbol("NVDA", "NVIDIA CORP")
    assert not hb.is_non_equity_symbol("HIMS", "HIMS & HERS")


# ── Takeover triage (KEEP vs DROP on full-account takeover) ─────────────────────
def _holding(ticker, unrl=0.0, pct=5.0, last=100.0):
    return hb.Holding(ticker=ticker, shares=10, avg_cost=last, last=last,
                      market_value=last * 10, pct_of_account=pct, unrealized_pct=unrl,
                      account_name="Individual - Youth Account", account_number="Z30299153")


def test_takeover_keeps_high_conviction_drops_low():
    ctx = {"social_scores": {"NVDA": 9, "ONDS": 2}}
    k, conv, _ = hb.takeover_verdict(_holding("NVDA", unrl=5), ctx)
    assert k == "KEEP" and conv == 9
    d, _, _ = hb.takeover_verdict(_holding("ONDS", unrl=-7), ctx)
    assert d == "DROP"


def test_takeover_drops_deep_loser_regardless_of_theme():
    ctx = {"social_scores": {"ASTS": 9}}      # strong theme, but...
    v, _, reason = hb.takeover_verdict(_holding("ASTS", unrl=-30), ctx)
    assert v == "DROP" and "Deep loss" in reason


def test_takeover_lenient_keeps_unknowns():
    v, _, _ = hb.takeover_verdict(_holding("HIMS", unrl=3), {"social_scores": {}})
    assert v == "KEEP"


def test_takeover_strict_drops_unknowns(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_TAKEOVER_STRICT", "true")
    v, _, _ = hb.takeover_verdict(_holding("HIMS", unrl=3), {"social_scores": {}})
    assert v == "DROP"


def test_rule_assess_drop_when_takeover_on(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_TAKEOVER", "true")
    ctx = {"social_scores": {"ONDS": 2}}
    act = hb._rule_assess(_holding("ONDS", unrl=-7), None, ctx)
    assert act.kind == hb.ACTION_EXIT and "takeover_drop" in act.risk_flags


def test_rule_assess_adopts_all_when_takeover_off(monkeypatch):
    monkeypatch.delenv("HOLDINGS_BRAIN_TAKEOVER", raising=False)
    ctx = {"social_scores": {"ONDS": 2}}      # low conviction, but takeover off
    act = hb._rule_assess(_holding("ONDS", unrl=-7), None, ctx)
    assert act.kind == hb.ACTION_ADOPT        # prior conservative behaviour preserved


# ── Trade-budget / churn control ────────────────────────────────────────────────
import datetime as _dt


def _item(ticker, kind, flags=None, conviction=5, pct=15.0, plan=None):
    return {
        "action": {"kind": kind, "risk_flags": flags or [], "conviction": conviction},
        "holding": {"ticker": ticker, "pct_of_account": pct},
        "plan": plan,
    }


def test_budget_caps_discretionary_trades():
    items = [_item(f"T{i}", hb.ACTION_TRIM, ["over_concentration_20pct"], pct=20 + i) for i in range(4)]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=2, min_hold_days=0)
    assert len(surfaced) == 2 and len(deferred) == 2
    assert all("budget" in d["defer_reason"] for d in deferred)


def test_mandatory_exit_always_surfaces():
    items = [_item(f"T{i}", hb.ACTION_TRIM, ["over_concentration_20pct"]) for i in range(3)]
    items.append(_item("STOP", hb.ACTION_EXIT, ["below_managed_stop"]))
    surfaced, _ = hb.prioritize_actions(items, max_trades=1, min_hold_days=0)
    tickers = {s["holding"]["ticker"] for s in surfaced}
    assert "STOP" in tickers                  # bypasses the 1-trade budget


def test_min_hold_defers_fresh_position():
    recent = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat()
    items = [_item("FRESH", hb.ACTION_TRIM, ["target_reached"], plan={"entered_at": recent})]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=5, min_hold_days=5)
    assert not surfaced and len(deferred) == 1 and "min-hold" in deferred[0]["defer_reason"]


def test_min_hold_bypassed_for_mandatory():
    recent = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat()
    items = [_item("FRESH", hb.ACTION_EXIT, ["below_managed_stop"], plan={"entered_at": recent})]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=5, min_hold_days=5)
    assert len(surfaced) == 1 and not deferred


def test_store_only_actions_unbudgeted():
    items = [_item(f"A{i}", hb.ACTION_ADOPT) for i in range(5)]
    items += [_item(f"S{i}", hb.ACTION_SET_STOP) for i in range(3)]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=1, min_hold_days=0)
    assert len(surfaced) == 8 and not deferred   # adopts/set-stops place no order


def test_hold_dropped():
    items = [_item("H", hb.ACTION_HOLD)]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=5, min_hold_days=0)
    assert not surfaced and not deferred


def test_min_stop_distance_scales_with_conviction():
    assert hb._min_stop_distance_pct(5) == 8.0
    assert hb._min_stop_distance_pct(10) == 16.0   # high conviction = more room


def test_adoption_stop_never_hair_trigger():
    # ATR stop ~1% below (the IREN bug) must be widened to the conviction floor.
    tight_atr_stop = 60.58
    s = hb._adoption_stop(61.0, conviction=10, atr_stop=tight_atr_stop)
    assert s < 61.0 * 0.85, f"stop {s} too tight for conv10"   # ≥15% room


def test_high_conviction_adopt_gives_wide_stop():
    h = hb.Holding("IREN", 13, 43.0, 61.0, 841.0, 18.9, 43.0)
    import os
    os.environ["HOLDINGS_BRAIN_TAKEOVER"] = "true"
    act = hb._rule_assess(h, None, {"social_scores": {"IREN": 9}})
    # conviction-9 adopt → stop well below price (not a 1% hair-trigger)
    assert act.kind == hb.ACTION_ADOPT
    assert act.stop is not None and act.stop < 61.0 * 0.88


def test_concentration_ceiling_scales_with_conviction():
    assert hb._concentration_ceiling(3, 10.0) == 10.0
    assert hb._concentration_ceiling(5, 10.0) == 10.0
    assert hb._concentration_ceiling(8, 10.0) == 19.0
    assert hb._concentration_ceiling(10, 10.0) == 25.0


def test_high_conviction_name_not_trimmed_within_ceiling():
    """The contradiction fix: a high-conviction holding within its conviction
    ceiling must NOT be trimmed (don't trim what you want to keep/buy)."""
    h = hb.Holding("IREN", 13, 43.0, 61.0, 841.0, 18.9, 43.0)
    hi = {"status": "managed", "conviction": 10, "stop": 40.0, "target": 80.0}
    lo = {"status": "managed", "conviction": 4, "stop": 40.0, "target": 80.0}
    assert hb._rule_assess(h, hi, {}).kind != hb.ACTION_TRIM   # conv10, 18.9% < 25% ceiling
    assert hb._rule_assess(h, lo, {}).kind == hb.ACTION_TRIM   # conv4, 18.9% > 10% cap


def test_extreme_concentration_still_trims_even_high_conviction():
    h = hb.Holding("NVDA", 7, 200.0, 209.0, 1465.0, 32.8, 4.6)
    hi = {"status": "managed", "conviction": 10, "stop": 190.0, "target": 260.0}
    assert hb._rule_assess(h, hi, {}).kind == hb.ACTION_TRIM   # 32.8% > 25% ceiling


def test_over_concentration_trim_bypasses_min_hold():
    recent = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat()
    # Fresh position (held 1d) but 33% of account → risk trim must NOT be deferred.
    risk = _item("NVDA", hb.ACTION_TRIM, ["over_concentration_33pct"], pct=33, plan={"entered_at": recent})
    # A fresh profit-trim (not concentration) still defers.
    profit = _item("HIMS", hb.ACTION_TRIM, ["target_reached"], plan={"entered_at": recent})
    surfaced, deferred = hb.prioritize_actions([risk, profit], max_trades=5, min_hold_days=5)
    assert any(s["holding"]["ticker"] == "NVDA" for s in surfaced)
    assert any(d["holding"]["ticker"] == "HIMS" for d in deferred)


def test_priority_concentration_beats_add():
    items = [
        _item("ADDME", hb.ACTION_ADD, conviction=9),
        _item("OVER", hb.ACTION_TRIM, ["over_concentration_30pct"], pct=30),
    ]
    surfaced, deferred = hb.prioritize_actions(items, max_trades=1, min_hold_days=0)
    assert surfaced[0]["holding"]["ticker"] == "OVER"
