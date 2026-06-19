"""Portfolio circuit breakers must FAIL CLOSED. A NaN/garbage value in state
would make the heat / daily-loss / cash comparisons evaluate False (NaN compares
False) and silently disable the breaker — letting a trade through that should be
blocked. On unreadable state the breaker must block, not skip."""
import datetime as dt

from web.api.thematic_auto import _check_portfolio_circuit_breakers as cb


def _state(**kw):
    base = {"cash": 10_000, "settled_cash": 10_000, "starting_cash": 10_000,
            "positions": {}, "trades": []}
    base.update(kw)
    return base


def test_allows_normal():
    ok, _ = cb(_state(), {}, base_dollar=500)
    assert ok is True


def test_blocks_on_heat():
    st = _state(cash=1_000, positions={"X": {"entry_price": 100, "shares": 90}})
    ok, reason = cb(st, {"max_portfolio_heat": 80.0}, base_dollar=100)
    assert ok is False and "heat" in reason.lower()


def test_blocks_on_daily_loss():
    today = dt.date.today().isoformat()
    st = _state(trades=[{"exit_time": today, "pnl": -400}])
    ok, reason = cb(st, {"daily_loss_limit_pct": 3.0}, base_dollar=100)
    assert ok is False and "loss" in reason.lower()


def test_blocks_on_insufficient_settled():
    ok, reason = cb(_state(settled_cash=100), {}, base_dollar=500)
    assert ok is False and "settled" in reason.lower()


# ── Fail-closed on unreadable state ─────────────────────────────────────────
def test_nan_cash_fails_closed():
    ok, _ = cb(_state(cash=float("nan")), {}, base_dollar=100)
    assert ok is False


def test_nan_position_fails_closed():
    st = _state(positions={"X": {"entry_price": float("nan"), "shares": 10}})
    ok, _ = cb(st, {}, base_dollar=100)
    assert ok is False


def test_nan_starting_cash_fails_closed():
    ok, _ = cb(_state(starting_cash=float("nan")), {}, base_dollar=100)
    assert ok is False


def test_garbage_string_cash_fails_closed():
    ok, _ = cb(_state(cash="oops"), {}, base_dollar=100)
    assert ok is False
