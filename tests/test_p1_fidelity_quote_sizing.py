"""P1 Fidelity execution-path fixes (F1-F4) — real-money invariants.

F1/F2: for execute=True the SERVER's trusted gateway quote is the sole source of
quote evidence (_apply_execution_quote); forged/caller-supplied quote fields are
overwritten and an empty gateway result raises (→ 503, order blocked). The exit
path's self-stamped 'fidelity_realtime' + scrape-time now() trick is gone, so
the freshness gate is real again and the label is no longer trusted.
F3: every execute order carries a naive-local 'now' matching the gateway's
naive-local quote_time convention.
F4: sizing is scoped to the ONE target account (_account_scoped_balances) —
never household totals, protected (Roth) accounts never contribute, and every
parse ambiguity refuses to size.

All pure-logic — no Playwright, no network.
"""
import datetime as dt

import pytest

import web.api.fidelity as f
from tradingagents.compliance import validate_live_order
from tradingagents.portfolio.pretrade_gate import PreTradeGate


def _fake_tq(ref: float = 100.0, *, age_seconds: float = 0.0, source: str = "fmp") -> dict:
    """Mimic _trusted_quote_fields output (naive-local timestamps)."""
    now = dt.datetime.now()
    return {
        "quote_price":    ref,
        "quote_time":     (now - dt.timedelta(seconds=age_seconds)).isoformat(),
        "quote_source":   source,
        "backup_sources": [],
        "consensus_ok":   None,
        "bid":            None,
        "ask":            None,
        "now":            now.isoformat(),
        "max_quote_age_seconds": 120,
        "_trusted_reference_price": ref,
    }


# ── _apply_execution_quote (F1/F2 fail-closed core) ─────────────────────────

def test_apply_execution_quote_empty_tq_raises():
    with pytest.raises(ValueError):
        f._apply_execution_quote({}, {}, limit_factor=1.002)
    # A tq without a usable reference price must also refuse.
    tq = _fake_tq()
    tq.pop("_trusted_reference_price")
    with pytest.raises(ValueError):
        f._apply_execution_quote({}, tq, limit_factor=1.002)
    tq = _fake_tq(ref=0.0)
    with pytest.raises(ValueError):
        f._apply_execution_quote({}, tq, limit_factor=1.002)


def test_apply_execution_quote_overwrites_forged_caller_fields():
    forged_time = (dt.datetime.now() - dt.timedelta(seconds=1)).isoformat()
    order = {
        "symbol": "NVDA", "action": "Buy", "order_type": "Limit",
        "quantity": 5, "execute": True,
        # Forged caller evidence — must NOT survive.
        "quote_source": "fmp", "quote_time": forged_time,
        "consensus_ok": True, "bid": 1.0, "ask": 2.0,
        "backup_sources": ["fmp", "finnhub"], "quote_price": 1.0,
    }
    tq = _fake_tq(ref=100.0)
    ref = f._apply_execution_quote(order, tq, limit_factor=1.002)
    assert ref == 100.0
    for key in ("quote_time", "quote_source", "backup_sources", "consensus_ok",
                "bid", "ask", "now", "max_quote_age_seconds"):
        assert order[key] == tq[key], f"{key} kept the forged caller value"
    assert order["quote_price"] == 100.0
    assert order["limit_price"] == round(100.0 * 1.002, 2)


def test_apply_execution_quote_limit_factor_none_keeps_limit_price():
    # /fidelity/trade: caller's limit_price is an order parameter, not quote
    # evidence — only the evidence is stamped. Market sells stay limit_price=None.
    order = {"limit_price": 42.5}
    f._apply_execution_quote(order, _fake_tq(ref=100.0), limit_factor=None)
    assert order["limit_price"] == 42.5
    assert order["quote_price"] == 100.0


def test_apply_execution_quote_now_is_naive_local():
    order = {}
    f._apply_execution_quote(order, _fake_tq(), limit_factor=1.002)
    now = dt.datetime.fromisoformat(order["now"])
    assert now.tzinfo is None, "'now' must stay naive-local (repo convention)"
    assert abs((dt.datetime.now() - now).total_seconds()) < 5
    qt = dt.datetime.fromisoformat(order["quote_time"])
    assert qt.tzinfo is None, "quote_time must stay naive-local (repo convention)"


# ── Freshness gate is a real gate again (F1) ────────────────────────────────

def test_stale_gateway_quote_blocks_execute():
    order = {
        "symbol": "NVDA", "action": "Sell", "order_type": "Limit",
        "quantity": 5, "limit_price": None, "execute": True,
    }
    f._apply_execution_quote(order, _fake_tq(ref=100.0, age_seconds=600), limit_factor=0.998)
    decision = validate_live_order(order)
    assert not decision.allowed
    assert "stale_quote" in decision.reason


def test_fresh_gateway_quote_allows_execute():
    # Control: same order with a fresh trusted quote passes — the block above is
    # the freshness gate, not some other broken invariant.
    order = {
        "symbol": "NVDA", "action": "Sell", "order_type": "Limit",
        "quantity": 5, "limit_price": None, "execute": True,
    }
    f._apply_execution_quote(order, _fake_tq(ref=100.0, age_seconds=1), limit_factor=0.998)
    decision = validate_live_order(order)
    assert decision.allowed, decision.reason


def test_fidelity_realtime_no_longer_trusted():
    assert PreTradeGate._source_is_trusted("fidelity_realtime") is False
    # An otherwise-perfect fresh execute order self-stamped fidelity_realtime blocks.
    now = dt.datetime.now()
    order = {
        "symbol": "NVDA", "action": "Sell", "order_type": "Limit",
        "quantity": 5, "limit_price": 100.0, "execute": True,
        "quote_price": 100.0,
        "quote_time": now.isoformat(),
        "quote_source": "fidelity_realtime",
        "now": now.isoformat(),
        "max_quote_age_seconds": 120,
    }
    decision = validate_live_order(order)
    assert not decision.allowed


def test_preview_paths_unaffected():
    # execute=False orders skip the quote gate entirely — previews keep working.
    decision = validate_live_order({
        "symbol": "NVDA", "action": "Buy", "order_type": "Limit",
        "quantity": 5, "limit_price": 100.0, "execute": False,
    })
    assert decision.allowed, decision.reason


# ── _account_scoped_balances (F4) ───────────────────────────────────────────

_BALANCES = {
    "total_value": 55_000.0,
    "available_cash": 3_000.0,  # household — must never be used for sizing
    "accounts": [
        {"number": "111111111", "name": "Individual", "value": 5_000.0, "cash": 1_000.0},
        {"number": "262502469", "name": "Roth IRA",   "value": 50_000.0, "cash": 2_000.0},
    ],
}


def test_scoped_balances_explicit_account():
    total, cash = f._account_scoped_balances(_BALANCES, "111111111")
    assert (total, cash) == (5_000.0, 1_000.0)
    # Roth values must never leak into the result.
    assert total != 55_000.0 and cash != 3_000.0


def test_scoped_balances_target_missing_raises():
    with pytest.raises(ValueError):
        f._account_scoped_balances(_BALANCES, "999999999")
    with pytest.raises(ValueError):
        f._account_scoped_balances({"total_value": 55_000.0, "accounts": []}, "111111111")
    with pytest.raises(ValueError):
        f._account_scoped_balances({}, "111111111")


def test_scoped_balances_none_account_rules(monkeypatch):
    # Two non-protected accounts, no explicit target → ambiguous → refuse.
    monkeypatch.delenv("FIDELITY_PROTECTED_ACCOUNTS", raising=False)
    with pytest.raises(ValueError):
        f._account_scoped_balances(_BALANCES, None)
    # Exactly one non-protected account (Roth filtered) → use it.
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    total, cash = f._account_scoped_balances(_BALANCES, None)
    assert (total, cash) == (5_000.0, 1_000.0)


def test_scoped_balances_protected_target_raises(monkeypatch):
    # Defense in depth: even if _assert_account_tradeable were bypassed, the
    # sizer refuses to scope to a protected account.
    monkeypatch.setenv("FIDELITY_PROTECTED_ACCOUNTS", "262502469")
    with pytest.raises(ValueError):
        f._account_scoped_balances(_BALANCES, "262502469")


def test_scoped_balances_reconciliation_failure_raises():
    bad = {
        "total_value": 55_000.0,
        "accounts": [
            {"number": "111111111", "name": "Individual", "value": 5_000.0, "cash": 1_000.0},
            {"number": "262502469", "name": "Roth IRA",   "value": 25_000.0, "cash": 2_000.0},
        ],
    }
    # sum(values)=30k vs grand 55k (>5% off) — parse unreliable even though the
    # target account itself parsed cleanly.
    with pytest.raises(ValueError):
        f._account_scoped_balances(bad, "111111111")


def test_scoped_balances_cash_none_raises():
    b = {
        "total_value": 5_000.0,
        "accounts": [{"number": "111111111", "name": "Individual", "value": 5_000.0, "cash": None}],
    }
    with pytest.raises(ValueError):
        f._account_scoped_balances(b, "111111111")
    # Unscrapeable value fails too (E2FP5 hard-abort survives per-account scoping).
    b2 = {
        "total_value": 5_000.0,
        "accounts": [{"number": "111111111", "name": "Individual", "value": None, "cash": 1_000.0}],
    }
    with pytest.raises(ValueError):
        f._account_scoped_balances(b2, "111111111")


# ── Sizing at scoped balances + trusted reference price (F4 + F2) ──────────

def test_sizer_cap_shrinks_under_scoping():
    # Household sizing let 10% of $55k ($5,500) fund a $5k account. Scoped, the
    # cap is 10% of $5k = $500.
    shares, cost = f._size_fidelity_position(5_000, 1_000, 50.0)
    assert cost <= 5_000 * 0.10
    assert shares == 10
    household_shares, household_cost = f._size_fidelity_position(55_000, 3_000, 50.0)
    assert household_cost > cost  # demonstrates the oversizing F4 closes


def test_resize_at_trusted_ref_respects_cash_cap():
    # Sized at yfinance price 100 → 10 shares; re-sized at trusted ref 120 the
    # share count shrinks and cost stays within cash.
    yf_shares, _ = f._size_fidelity_position(100_000, 1_000, 100.0, dollar_amount=1_000)
    ref_shares, ref_cost = f._size_fidelity_position(100_000, 1_000, 120.0, dollar_amount=1_000)
    assert yf_shares == 10
    assert ref_shares < yf_shares
    assert ref_shares * 120.0 <= 1_000
    assert ref_cost <= 1_000
    # Ref so high no whole share fits → (0, 0.0) → the endpoint's 400 branch.
    assert f._size_fidelity_position(100_000, 1_000, 1_500.0, dollar_amount=1_000) == (0, 0.0)
