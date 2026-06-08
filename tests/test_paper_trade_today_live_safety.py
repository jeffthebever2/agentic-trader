import datetime as dt
from types import SimpleNamespace

from scripts.paper_trade_today import (
    Candidate,
    _build_fidelity_trade_payload,
    _precheck_live_fidelity_payload,
)


NOW = dt.datetime.utcnow().replace(microsecond=0)


def _candidate() -> Candidate:
    return Candidate(
        ticker="AAPL",
        signal_date="2026-06-06",
        score=80.0,
        entry=100.0,
        target=110.0,
        stop=95.0,
        signal_close=99.5,
        atr=2.0,
    )


def _args(**overrides):
    base = {
        "fidelity_quote_time": "",
        "fidelity_quote_source": "",
        "fidelity_backup_sources": "",
        "fidelity_consensus_ok": False,
        "fidelity_bid": None,
        "fidelity_ask": None,
        "fidelity_market_open": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_fidelity_preview_payload_does_not_require_execution_quote():
    payload = _build_fidelity_trade_payload(_candidate(), 10, execute=False, args=_args())

    ok, reason = _precheck_live_fidelity_payload(payload)

    assert ok
    assert reason == "preview"


def test_fidelity_execute_payload_without_trusted_quote_is_blocked_before_hil():
    payload = _build_fidelity_trade_payload(_candidate(), 10, execute=True, args=_args())

    ok, reason = _precheck_live_fidelity_payload(payload)

    assert not ok
    assert "quote_time" in reason


def test_fidelity_execute_payload_with_fresh_trusted_quote_passes_precheck():
    payload = _build_fidelity_trade_payload(
        _candidate(),
        10,
        execute=True,
        args=_args(
            fidelity_quote_time=NOW.isoformat(),
            fidelity_quote_source="alpaca_iex",
            fidelity_bid=99.99,
            fidelity_ask=100.01,
            fidelity_market_open=True,
        ),
    )
    payload["now"] = NOW.isoformat()

    ok, reason = _precheck_live_fidelity_payload(payload)

    assert ok
    assert reason == "ok"
