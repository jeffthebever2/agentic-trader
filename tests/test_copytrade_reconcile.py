"""Tests for the pure copy-trade reconciliation engine."""
import pytest

from tradingagents.portfolio.copytrade_reconcile import (
    CopyAction,
    compute_copy_actions,
)


def _pos(ticker, shares, price, **kw):
    return {"ticker": ticker, "shares": shares, "current_price": price, **kw}


def test_buy_new_names_weighted():
    positions = [_pos("NVDA", 10, 8), _pos("AMD", 10, 2)]  # 80 + 20, book 1000
    actions = compute_copy_actions(positions, 1000.0, owned={}, external_holdings=set())
    buys = [a for a in actions if a.action == "buy"]
    assert {a.ticker for a in buys} == {"NVDA", "AMD"}
    nvda = next(a for a in buys if a.ticker == "NVDA")
    amd = next(a for a in buys if a.ticker == "AMD")
    assert nvda.target_weight == pytest.approx(0.08)  # 80/1000
    assert amd.target_weight == pytest.approx(0.02)
    # Sorted by descending weight
    assert buys[0].ticker == "NVDA"


def test_weight_clamped_to_max():
    positions = [_pos("NVDA", 10, 500)]  # 5000 / 10000 = 50% -> clamp 10%
    actions = compute_copy_actions(positions, 10000.0, owned={}, external_holdings=set())
    assert actions[0].target_weight == pytest.approx(0.10)
    assert actions[0].paper_weight_raw == pytest.approx(0.50)


def test_dust_below_min_ignored():
    positions = [_pos("PENNY", 1, 50)]  # 50 / 10000 = 0.5% < 1% default
    actions = compute_copy_actions(positions, 10000.0, owned={}, external_holdings=set())
    assert actions == []


def test_sell_exited_names():
    positions = [_pos("NVDA", 10, 80)]
    owned = {"NVDA": {"shares": 10}, "TSLA": {"shares": 5}}  # TSLA gone from portfolio
    actions = compute_copy_actions(positions, 1000.0, owned=owned, external_holdings=set())
    sells = [a for a in actions if a.action == "sell"]
    assert [a.ticker for a in sells] == ["TSLA"]
    # NVDA already owned -> no re-buy
    assert not any(a.action == "buy" and a.ticker == "NVDA" for a in actions)


def test_sells_come_before_buys():
    positions = [_pos("AMD", 10, 100)]  # new buy
    owned = {"TSLA": {"shares": 5}}      # exited -> sell
    actions = compute_copy_actions(positions, 1000.0, owned=owned, external_holdings=set())
    assert actions[0].action == "sell"
    assert actions[-1].action == "buy"


def test_external_holding_never_bought():
    positions = [_pos("NVDA", 10, 80)]
    actions = compute_copy_actions(
        positions, 1000.0, owned={}, external_holdings={"NVDA"}
    )
    assert actions == []  # user already holds NVDA outside copy-trade — no stacking


def test_external_holding_not_sold_even_if_recorded_owned():
    positions = []  # portfolio holds nothing
    owned = {"NVDA": {"shares": 10}}
    actions = compute_copy_actions(
        positions, 1000.0, owned=owned, external_holdings={"NVDA"}
    )
    assert actions == []  # ambiguous ownership -> never auto-sell


def test_zero_equity_fails_closed_no_buys():
    positions = [_pos("NVDA", 10, 80)]
    actions = compute_copy_actions(positions, 0.0, owned={}, external_holdings=set())
    assert not any(a.action == "buy" for a in actions)


def test_negative_equity_fails_closed():
    positions = [_pos("NVDA", 10, 80)]
    actions = compute_copy_actions(positions, -500.0, owned={}, external_holdings=set())
    assert actions == []


def test_case_insensitive_ticker_match():
    positions = [_pos("nvda", 10, 80)]
    owned = {"NVDA": {"shares": 10}}
    actions = compute_copy_actions(positions, 1000.0, owned=owned, external_holdings=set())
    # Already owned (case-folded) -> no buy, no sell
    assert actions == []


def test_entry_price_fallback_when_no_current():
    positions = [{"ticker": "NVDA", "shares": 10, "entry_price": 8}]  # no current_price, 80/1000
    actions = compute_copy_actions(positions, 1000.0, owned={}, external_holdings=set())
    assert actions and actions[0].target_weight == pytest.approx(0.08)


def test_malformed_position_does_not_crash():
    positions = [{"ticker": None, "shares": "x", "current_price": None}, _pos("AMD", 10, 100)]
    actions = compute_copy_actions(positions, 1000.0, owned={}, external_holdings=set())
    assert [a.ticker for a in actions if a.action == "buy"] == ["AMD"]


def test_to_dict_shape():
    a = CopyAction(action="buy", ticker="NVDA", target_weight=0.08,
                   reason="x", paper_shares=10, paper_weight_raw=0.08)
    d = a.to_dict()
    assert d["target_pct"] == 8.0
    assert d["ticker"] == "NVDA"
    assert d["action"] == "buy"
