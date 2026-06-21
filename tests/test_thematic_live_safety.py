import pytest

from web.api.thematic_auto import ApproveBody, _fidelity_request_kwargs_from_approval


@pytest.mark.unit
def test_thematic_fidelity_approval_preserves_execution_quote_evidence():
    body = ApproveBody(
        dollar_amount=750.0,
        fidelity_trade=True,
        execute_fidelity=True,
        fidelity_quote_time="2026-06-06T14:00:00",
        fidelity_quote_source="alpaca_iex",
        fidelity_backup_sources=["finnhub", "polygon"],
        fidelity_consensus_ok=True,
        fidelity_bid=99.99,
        fidelity_ask=100.01,
        fidelity_market_open=True,
    )

    payload = _fidelity_request_kwargs_from_approval(
        "AAPL",
        body,
        stop_pct=5.0,
        target_pct=10.0,
        dollar_amount=750.0,
    )

    assert payload["ticker"] == "AAPL"
    assert payload["dollar_amount"] == pytest.approx(750.0)
    assert payload["execute"] is True
    assert payload["also_paper_trade"] is False
    assert payload["quote_time"] == "2026-06-06T14:00:00"
    assert payload["quote_source"] == "alpaca_iex"
    assert payload["backup_sources"] == ["finnhub", "polygon"]
    assert payload["consensus_ok"] is True
    assert payload["bid"] == pytest.approx(99.99)
    assert payload["ask"] == pytest.approx(100.01)
    assert payload["market_open"] is True
