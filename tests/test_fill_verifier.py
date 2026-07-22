"""Fill verification — accepted is not filled.

Internal state used to commit when Fidelity ACCEPTED an order. These are DAY
limit orders, so one that never trades expires at 16:00 silently, leaving a
position the system believes in and the broker has never heard of: marked to
market, sized against, stopped against, and eventually "exited" for a fictitious
P&L, while copy-trade records the ticker as owned and never retries it.

Holdings are the source of truth. These tests pin the decision logic, especially
the two directions that matter: never delete a real position on a transient
scrape failure, and never keep a phantom one after the order can no longer fill.
"""
from __future__ import annotations

import pytest

from tradingagents.brokers.fill_verifier import (
    STATUS_FILLED, STATUS_PARTIAL, STATUS_PENDING, STATUS_UNFILLED, STATUS_UNKNOWN,
    FillVerdict, PendingFill, classify_fill, holdings_share_map,
    reconcile_pending_fills,
)


def _p(**kw) -> PendingFill:
    base = dict(ticker="NVDA", intended_shares=100.0, shares_before=0.0, side="buy")
    base.update(kw)
    return PendingFill(**base)


# ── buy side ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_full_fill_detected_as_delta():
    v = classify_fill(_p(), {"NVDA": 100.0})
    assert v.status == STATUS_FILLED and v.filled_shares == 100.0


@pytest.mark.unit
def test_fill_is_a_delta_not_mere_presence():
    """Adding to an existing position must still be verifiable — presence alone
    would report a fill the moment any shares existed."""
    v = classify_fill(_p(shares_before=50.0), {"NVDA": 50.0})
    assert v.status == STATUS_PENDING, "unchanged share count is not a fill"

    v2 = classify_fill(_p(shares_before=50.0), {"NVDA": 150.0})
    assert v2.status == STATUS_FILLED and v2.filled_shares == 100.0


@pytest.mark.unit
def test_partial_fill_reports_real_quantity():
    """Sizing, stops and the concentration cap must all use shares actually
    owned — recording the intended 300 against a 120 fill makes every later
    decision wrong by 60% and eventually sells 180 shares we do not have."""
    v = classify_fill(_p(intended_shares=300.0), {"NVDA": 120.0})
    assert v.status == STATUS_PARTIAL
    assert v.filled_shares == 120.0 and v.intended_shares == 300.0
    assert v.discrepancies and "120" in v.discrepancies[0]


@pytest.mark.unit
def test_unfilled_only_once_expired():
    """Before expiry an absent position is merely pending — the order may still
    work. After expiry it is definitively a phantom."""
    assert classify_fill(_p(), {}).status == STATUS_PENDING
    assert classify_fill(_p(), {}, expired=True).status == STATUS_UNFILLED


@pytest.mark.unit
def test_missing_holdings_snapshot_is_never_evidence():
    """The safety-critical direction: a transient scrape failure must not be read
    as 'never filled' and delete a real position."""
    for expired in (False, True):
        v = classify_fill(_p(), None, expired=expired)
        assert v.status == STATUS_UNKNOWN
        assert not v.is_terminal, "unknown must never trigger cleanup"


@pytest.mark.unit
def test_tolerance_absorbs_fractional_dust():
    v = classify_fill(_p(intended_shares=100.0), {"NVDA": 99.9}, tolerance_shares=0.5)
    assert v.status == STATUS_FILLED


@pytest.mark.unit
def test_non_positive_intent_is_terminal_not_pending():
    v = classify_fill(_p(intended_shares=0.0), {})
    assert v.status == STATUS_UNFILLED and v.is_terminal


@pytest.mark.unit
@pytest.mark.parametrize("garbage", [None, "", "abc", float("nan"), float("inf")])
def test_garbage_share_counts_never_propagate(garbage):
    v = classify_fill(_p(intended_shares=garbage), {"NVDA": 10.0})
    assert v.status in (STATUS_UNFILLED, STATUS_FILLED, STATUS_PARTIAL, STATUS_PENDING)
    assert v.filled_shares == v.filled_shares          # not NaN
    assert v.filled_shares not in (float("inf"), float("-inf"))


# ── sell side ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_sell_fill_detected_as_reduction():
    v = classify_fill(_p(side="sell", shares_before=100.0), {"NVDA": 0.0})
    assert v.status == STATUS_FILLED and v.filled_shares == 100.0


@pytest.mark.unit
def test_partial_sell_leaves_a_stub():
    v = classify_fill(_p(side="sell", intended_shares=100.0, shares_before=100.0),
                      {"NVDA": 40.0})
    assert v.status == STATUS_PARTIAL and v.filled_shares == 60.0


@pytest.mark.unit
def test_unfilled_sell_is_the_dangerous_one():
    """An exit that never filled while the system records the position as closed
    leaves real shares with no stop and no owner."""
    v = classify_fill(_p(side="sell", shares_before=100.0), {"NVDA": 100.0}, expired=True)
    assert v.status == STATUS_UNFILLED


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_holdings_share_map_accepts_objects_and_dicts_and_sums_duplicates():
    class H:
        def __init__(self, t, s): self.ticker, self.shares = t, s
    m = holdings_share_map([H("nvda", 10), {"ticker": "NVDA", "shares": 5},
                            {"ticker": "TSLA", "quantity": 3}, {"ticker": "", "shares": 9}])
    assert m == {"NVDA": 15.0, "TSLA": 3.0}


@pytest.mark.unit
def test_reconcile_marks_only_expired_tickers_terminal():
    pend = [_p(ticker="NVDA"), _p(ticker="TSLA")]
    out = reconcile_pending_fills(pend, {}, expired_tickers=["NVDA"])
    by = {v.ticker: v for v in out}
    assert by["NVDA"].status == STATUS_UNFILLED
    assert by["TSLA"].status == STATUS_PENDING


@pytest.mark.unit
def test_pending_fill_roundtrips_through_dict():
    p = _p(limit_price=12.5, order_id="abc", submitted_at="2026-07-21T10:00:00")
    assert PendingFill.from_dict(p.as_dict()) == p


# ── the broker snapshot's own key dialect ────────────────────────────────────

@pytest.mark.unit
def test_reads_the_fidelity_snapshot_key_dialect():
    """The Fidelity positions scraper writes `symbol`/`qty`; the normalized
    Holding dataclass exposes `ticker`/`shares`. Reading only one dialect made
    holdings_share_map return an EMPTY dict — which classify_fill then treats as
    positive evidence the broker holds nothing, so after the close every
    genuinely filled order was reported as a PHANTOM POSITION.
    """
    scraped = [{"symbol": "NVDA", "qty": 4.0, "last_price": 110.0},
               {"symbol": "IREN", "qty": 13.669}]
    assert holdings_share_map(scraped) == {"NVDA": 4.0, "IREN": 13.669}

    class Holding:
        def __init__(self, ticker, shares):
            self.ticker, self.shares = ticker, shares
    assert holdings_share_map([Holding("NVDA", 4.0)]) == {"NVDA": 4.0}


@pytest.mark.unit
def test_a_real_holding_is_never_reported_as_a_phantom():
    """End-to-end guard on the alert that would otherwise cry wolf every evening."""
    holdings = holdings_share_map([{"symbol": "NVDA", "qty": 4.0}])
    v = classify_fill(PendingFill(ticker="NVDA", intended_shares=4.0,
                                  shares_before=0.0), holdings, expired=True)
    assert v.status == STATUS_FILLED


@pytest.mark.unit
def test_unreadable_share_count_is_skipped_not_zeroed():
    """A row we cannot parse must not become an implicit 0 — indistinguishable
    from 'sold everything', which would fabricate a phantom."""
    assert holdings_share_map([{"symbol": "NVDA"}]) == {}
    assert holdings_share_map([{"qty": 5}]) == {}


# ── the broker stores share counts as SCRAPED STRINGS ────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("1,500", 1500.0), ("12,000", 12000.0), ("1,500.000", 1500.0),
    ("300", 300.0), (300, 300.0), (13.669, 13.669), ("0", 0.0), ("(50)", -50.0),
])
def test_scraped_string_share_counts_parse(raw, expected):
    """`qty` in the Fidelity snapshot is raw innerText, so a 1,500-share holding
    arrives as the STRING "1,500". A bare float() raises on that and the holding
    read as 0 shares — which classify_fill treats as "the broker holds nothing",
    reporting a real position as a PHANTOM. Fixing the key alias without fixing
    the VALUE parsing left every holding >=1000 shares broken."""
    assert holdings_share_map([{"symbol": "X", "qty": raw}]) == {"X": expected}


@pytest.mark.unit
@pytest.mark.parametrize("junk", ["—", "n/a", "", "  ", None, "abc"])
def test_unreadable_share_count_is_skipped_not_zeroed_2(junk):
    """A spurious 0 is indistinguishable from 'sold everything' and fabricates a
    phantom, so an unparseable row must be dropped entirely."""
    assert holdings_share_map([{"symbol": "X", "qty": junk}]) == {}


@pytest.mark.unit
def test_large_real_holding_is_not_reported_as_a_phantom():
    holdings = holdings_share_map([{"symbol": "SOUN", "qty": "1,500"}])
    v = classify_fill(PendingFill(ticker="SOUN", intended_shares=1500.0,
                                  shares_before=0.0), holdings, expired=True)
    assert v.status == STATUS_FILLED, "a filled 1,500-share order must not read as phantom"
