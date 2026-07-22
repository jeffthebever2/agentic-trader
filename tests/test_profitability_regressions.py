"""Regressions for defects that silently cost money.

Each test here pins a bug that produced no error, no log, and no failing test —
the system reported success while losing money or refusing to trade. Grouped by
the subsystem that owned the defect.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradingagents.data.quote_gateway import Quote, QuoteGateway
from tradingagents.screening import buzz_score as bz
from tradingagents.screening import signal_outcomes as so


# ── Execution: the gateway must not substitute an untrusted source ─────────────

def _gw(max_age: float = 30.0) -> QuoteGateway:
    gw = QuoteGateway.__new__(QuoteGateway)
    gw.max_quote_age_seconds = max_age
    gw.consensus_tolerance_bps = 50.0
    return gw


def _q(source: str, age_s: float, last: float = 10.0) -> Quote:
    return Quote(symbol="XYZ", last=last, source=source,
                 quote_time=dt.datetime.now() - dt.timedelta(seconds=age_s))


@pytest.mark.unit
def test_fresh_untrusted_quote_never_displaces_an_older_trusted_one():
    """The bug: yfinance self-stamps quote_time=now(), so it was ALWAYS inside the
    gateway's 30s window. `fresh` was therefore never empty, `fresh or quotes`
    never fell back, and any FMP/Finnhub print older than 30s was dropped from
    the pool entirely. Execution then saw best.trusted=False and refused the
    order with a 503 — on entries *and exits*. The mere presence of a fresh
    untrusted quote made the system trade less, and made positions un-exitable.
    """
    g = _gw()._aggregate("XYZ", [_q("fmp", 45), _q("yfinance", 0)])
    assert g.best.source == "fmp"
    assert g.best.trusted is True


@pytest.mark.unit
@pytest.mark.parametrize("trusted_age", [31, 45, 120, 600])
def test_trusted_source_wins_at_every_age_beyond_the_window(trusted_age):
    g = _gw()._aggregate("XYZ", [_q("finnhub", trusted_age), _q("yfinance", 0)])
    assert g.best.trusted is True, f"untrusted source won at age {trusted_age}s"


@pytest.mark.unit
def test_freshest_trusted_source_still_wins_among_trusted():
    g = _gw()._aggregate("XYZ", [_q("fmp", 45), _q("finnhub", 10)])
    assert g.best.source == "finnhub"


@pytest.mark.unit
def test_untrusted_only_is_still_reported_untrusted():
    """Safety direction: with no trusted provider available the gateway must NOT
    launder yfinance into a trusted quote — execution has to keep refusing."""
    g = _gw()._aggregate("XYZ", [_q("yfinance", 0)])
    assert g.best.trusted is False


@pytest.mark.unit
def test_stale_trusted_quote_is_surfaced_not_swallowed():
    """Keeping the trusted quote in the pool must not hide staleness — PreTradeGate
    is the freshness authority, so the age has to survive to it intact."""
    g = _gw()._aggregate("XYZ", [_q("fmp", 600), _q("yfinance", 0)])
    assert g.best.trusted is True
    assert g.best.age_seconds > 500, "age must reach the gate unmodified"


# ── Signals: sentiment must not saturate on a single mention ───────────────────

@pytest.mark.unit
def test_one_mention_is_not_total_conviction():
    """net_sentiment used to be exactly 1.0 for any unopposed bullish mention,
    feeding the composite's 1.25x multiplier at full strength."""
    assert bz.compute_buzz(10.0, dict(bull_w=1.0, bear_w=0.0, neut_w=0.0)).net_sentiment < 0.4


# ── Learning loop: telemetry is not a data source ──────────────────────────────

@pytest.mark.unit
def test_telemetry_keys_are_not_recorded_as_sources():
    """The scanner's breakdown dict doubles as a telemetry sink. Those fields were
    being learned from as if they were feeds — `bull_bear_ratio` is capped at
    999.0, so an all-bullish name contributed a ~999-point 'source' and the six
    telemetry keys absorbed ~83% of every observation's attribution. The adaptive
    per-source weights consequently never met their observation floor and stayed
    pinned at 1.0 forever."""
    breakdown = {
        "AAA": {
            "reddit": 12.0,
            "trusted_twitter": 30.0,
            # telemetry — must all be excluded
            "bull_contrib": 40.0,
            "bear_contrib": 5.0,
            "neutral_contrib": 1.0,
            "volume_contrib": 15.0,
            "buzz_sentiment": 0.9,
            "bull_bear_ratio": 999.0,
        }
    }
    rows = so.record_scan(
        rows=[],
        ranked=[("AAA", 80.0)],
        breakdown=breakdown,
        prices={"AAA": 10.0},
        now=dt.datetime(2026, 7, 21, 12, 0, 0),
    )
    assert len(rows) == 1
    sources = rows[0]["sources"]
    assert set(sources) == {"reddit", "trusted_twitter"}
    for junk in so._NON_SOURCE_KEYS:
        assert junk not in sources
    # The dominant-telemetry pathology: no single key may swamp the observation.
    assert max(sources.values()) / sum(sources.values()) < 0.8


@pytest.mark.unit
def test_scan_memory_is_not_a_quality_source():
    """scan_memory is a ticker's OWN prior score. Counting it as independent
    corroboration let a name launder its history into the multi-source bonus,
    the quality gate and the conviction ceiling — worth a full conviction point
    (+7.5 composite) on most ranked names."""
    from web.api.thematic_auto import _QUALITY_SOURCES
    assert "scan_memory" not in _QUALITY_SOURCES


# ── Execution: one lock per (user, ticker) across every action ─────────────────

@pytest.mark.unit
def test_order_lock_key_dialects_collapse_to_one_lock():
    """Four key namespaces existed for the same position, so buy/sell/exit/
    auto-exit took DIFFERENT locks. The armed autonomous exit executor
    ("auto-exit:") and a human approving the same proposal ("exit:") could both
    sell the same shares — a 100-share holding becomes 100 SHORT, and compliance
    cannot see it because each order is individually a valid "Sell 100"."""
    from web.api.fidelity import _get_order_lock
    dialects = ["u@x.com:NVDA", "u@x.com:NVDA:buy", "u@x.com:NVDA:sell",
                "u@x.com:exit:NVDA", "u@x.com:auto-exit:NVDA", "u@x.com:trim:nvda"]
    assert len({id(_get_order_lock(k)) for k in dialects}) == 1


@pytest.mark.unit
def test_order_lock_still_isolates_ticker_user_and_broker():
    """The tightening must not serialise the whole book: key shapes differ per
    broker (`webull:{email}:{ticker}:{ACTION}`), and a positional parse would
    collapse DIFFERENT tickers onto one lock."""
    from web.api.fidelity import _get_order_lock
    nvda = id(_get_order_lock("u@x.com:NVDA"))
    assert id(_get_order_lock("u@x.com:TSLA")) != nvda          # other ticker
    assert id(_get_order_lock("v@x.com:NVDA")) != nvda          # other user
    wb_aapl = id(_get_order_lock("webull:u@x.com:AAPL:BUY"))
    assert id(_get_order_lock("webull:u@x.com:AAPL:SELL")) == wb_aapl
    assert id(_get_order_lock("webull:u@x.com:MSFT:BUY")) != wb_aapl


@pytest.mark.unit
def test_order_in_flight_is_refcounted():
    """Two concurrent orders for one user (different tickers → different locks,
    both legal) each marked the same key in a set; whichever finished FIRST
    cleared it, un-protecting the second mid-flight. The keepalive loop then
    reset the browser and the live order died after submission — real position,
    untracked, unstopped."""
    from web.api import fidelity as fx
    key = "refcount-probe"
    fx._order_in_flight_acquire(key)
    fx._order_in_flight_acquire(key)
    fx._order_in_flight_release(key)
    assert key in fx._ORDER_IN_FLIGHT, "second order lost browser protection"
    fx._order_in_flight_release(key)
    assert key not in fx._ORDER_IN_FLIGHT
    fx._order_in_flight_release(key)          # underflow must be harmless
    assert key not in fx._ORDER_IN_FLIGHT


# ── Sizing: the live leg must re-anchor to the REAL account ───────────────────

@pytest.mark.unit
def test_live_order_size_scales_with_conviction_not_pinned_at_the_cap():
    """The live leg was handed dollars denominated in the ~$100k PAPER book while
    _size_fidelity_position clamped against the real (much smaller) account, so
    min(paper_dollars, 10% cap, cash) always chose the cap. Every real order was
    max-size and conviction/vol/correlation were numerically inert."""
    from web.api.fidelity import _size_fidelity_position
    real_av, cash, px = 4612.78, 3000.0, 20.0
    sizes = []
    for weight_pct in (2.1, 3.0, 4.3, 8.6):
        _, cost = _size_fidelity_position(account_value=real_av, available_cash=cash,
                                          price=px, dollar_amount=None,
                                          pct_of_account=weight_pct)
        sizes.append(cost)
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes), \
        f"size must increase with conviction, got {sizes}"
    assert max(sizes) <= real_av * 0.10 + px, "compliance cap still binds"

    # The old paper-dollar path collapses to one value — the cap — for every input.
    old = [_size_fidelity_position(account_value=real_av, available_cash=cash, price=px,
                                   dollar_amount=100_000.0 * w / 100.0,
                                   pct_of_account=None)[1]
           for w in (2.1, 3.0, 4.3, 8.6)]
    assert len(set(old)) == 1, "sanity: the old path really was degenerate"


# ── Paper competition: concentration backstop ─────────────────────────────────

@pytest.mark.unit
def test_missing_atr_cannot_produce_a_half_account_position():
    """Risk-parity sizing has no position ceiling, and the tighter the stop the
    bigger the position — so a MISSING ATR (widest uncertainty) produced the
    LARGEST bet: a 1% risk budget against a 2.1% fallback stop is ~48% of equity
    in one name."""
    from tradingagents.portfolio.paper_engine import size_shares, POSITION_CAP_PCT
    from tradingagents.portfolio.paper_configs import all_portfolios

    class _Acct:
        settled_cash = 10_000.0
        def current_equity(self):  # noqa: D102
            return 10_000.0

    cfg = next(p for p in all_portfolios())
    entry = 100.0
    tight_stop = entry * (1 - 0.021)          # the ATR-missing fallback
    shares = size_shares(_Acct(), cfg, entry, tight_stop)
    assert shares * entry <= 10_000.0 * POSITION_CAP_PCT / 100.0 + entry


# ── Sizing: compounding and ADD step ──────────────────────────────────────────

@pytest.mark.unit
def test_add_sizes_off_available_room_not_the_existing_position():
    """`fraction` is consumed as `dollar = market_value * fraction`, so the old
    `min(0.25, room / pct_of_account)` had the base backwards: the MORE room a
    name had, the larger that ratio grew and the harder the 0.25 clamp bit. A
    conviction-10 name at 1% of account with 9 points of room produced a
    25%-of-$46 ≈ $11 add — ~11 sequential step-up-2FA approvals to reach the cap,
    so in practice the best idea never got there."""
    from tradingagents.portfolio.holdings_brain import _add_max_step_pct

    cap, pct_of_account = 10.0, 1.0
    room = cap - pct_of_account
    step = min(room, _add_max_step_pct())
    frac = max(0.05, min(step / pct_of_account, 10.0))

    account_value = 4600.0
    market_value = account_value * pct_of_account / 100.0     # $46
    dollars = market_value * frac
    assert dollars > 100.0, f"add is still a rounding error: ${dollars:.2f}"
    # ...and still rate-limited to at most one step of the account.
    assert dollars <= account_value * _add_max_step_pct() / 100.0 + 1e-6


@pytest.mark.unit
def test_account_value_marks_positions_to_market_not_to_cost():
    """Valuing the book at entry makes account value identically
    `start + realized`, which switches compounding off: a book up 60% unrealized
    still sizes every new position off the original balance."""
    import json
    from web.api import thematic_auto as ta

    path = ta.PAPER_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.read_text() if path.exists() else None

    def _value(pos: dict) -> float:
        path.write_text(json.dumps({"cash": 1000.0, "positions": {"NVDA": pos}}))
        return ta._thematic_account_value("probe@example.com")

    try:
        # Marked to the last OBSERVED price — unrealized P&L reaches the sizer.
        assert _value({"shares": 10, "entry_price": 100.0, "last_price": 160.0}) \
            == pytest.approx(1000.0 + 10 * 160.0)
        # ...and DOWN as well as up. Valuing at cost froze compounding; this is
        # the direction that matters for not over-risking into a drawdown.
        assert _value({"shares": 10, "entry_price": 100.0, "last_price": 60.0}) \
            == pytest.approx(1000.0 + 10 * 60.0)
        # peak_price must NEVER be the mark: it is a one-way ratchet
        # (max(peak, entry, price)), so a name that ran +150% and gave it all
        # back would still value at +150% forever — an account value that can
        # only increase, which is worse than valuing at cost.
        assert _value({"shares": 10, "entry_price": 100.0,
                       "peak_price": 250.0, "last_price": 60.0}) \
            == pytest.approx(1000.0 + 10 * 60.0), "peak_price leaked into the mark"
        # No mark yet (just opened) → cost is the honest fallback, not the peak.
        assert _value({"shares": 10, "entry_price": 100.0, "peak_price": 250.0}) \
            == pytest.approx(1000.0 + 10 * 100.0)
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup)


# ── Execution: page-scan noise must not fail a live order ─────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("noise", ["buying power", "after hours", "error", "invalid"])
def test_ordinary_page_chrome_does_not_reject_a_confirmed_order(noise):
    """These tokens appear in normal Fidelity chrome (ticket labels, extended-hours
    UI, hidden aria-live templates). Matched against the whole page BEFORE the
    confirm patterns, they turned a live, already-submitted order into a 502 —
    leaving real shares with no stop that no part of the system tracks."""
    from web.api.fidelity import _verify_fidelity_order_page
    page = f"Your order has been received. Order number 12345. {noise} shown elsewhere."
    ok, reason = _verify_fidelity_order_page(page)
    assert ok is True, f"'{noise}' falsely rejected a confirmed order: {reason}"


@pytest.mark.unit
@pytest.mark.parametrize("real", ["insufficient", "rejected", "unable to process",
                                  "cannot process", "not enough", "market is closed"])
def test_genuine_rejection_language_is_still_caught(real):
    from web.api.fidelity import _verify_fidelity_order_page
    ok, _ = _verify_fidelity_order_page(f"Order number 12345 — {real} to complete.")
    assert ok is False
