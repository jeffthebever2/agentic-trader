"""Quote gateway: provider fan-out, best-quote selection, consensus, gate integration."""
import datetime as dt

import pytest

from tradingagents.data.quote_gateway import GatewayQuote, Quote, QuoteGateway
from tradingagents.portfolio.pretrade_gate import PreTradeGate


def _q(symbol="RKLB", last=10.0, source="finnhub", age_s=1.0, bid=None, ask=None):
    return Quote(
        symbol=symbol,
        last=last,
        source=source,
        quote_time=dt.datetime.now() - dt.timedelta(seconds=age_s),
        bid=bid,
        ask=ask,
    )


def _provider(quote):
    return lambda symbol, timeout: quote


def _failing_provider(symbol, timeout):
    raise RuntimeError("boom")


def _none_provider(symbol, timeout):
    return None


def make_gateway(providers, **kwargs):
    kwargs.setdefault("cache_ttl_seconds", 0.0)
    return QuoteGateway(providers=providers, **kwargs)


class TestBestQuoteSelection:
    def test_trusted_with_bid_ask_beats_untrusted(self):
        trusted = _q(source="finnhub", last=10.02, bid=10.01, ask=10.03, age_s=2.0)
        untrusted = _q(source="yfinance", last=10.00, age_s=0.5)
        gw = make_gateway([("finnhub", _provider(trusted)), ("yfinance", _provider(untrusted))])
        result = gw.get_quote("RKLB")
        assert result is not None
        assert result.best.source == "finnhub"

    def test_freshest_wins_among_equals(self):
        older = _q(source="finnhub", last=10.0, age_s=10.0)
        newer = _q(source="fmp", last=10.0, age_s=1.0)
        gw = make_gateway([("finnhub", _provider(older)), ("fmp", _provider(newer))])
        result = gw.get_quote("RKLB")
        assert result.best.source == "fmp"

    def test_no_quotes_returns_none(self):
        gw = make_gateway([("finnhub", _none_provider), ("fmp", _failing_provider)])
        assert gw.get_quote("RKLB") is None

    def test_stale_quotes_still_reported_when_nothing_fresh(self):
        stale = _q(source="finnhub", last=10.0, age_s=120.0)
        gw = make_gateway([("finnhub", _provider(stale))], max_quote_age_seconds=30.0)
        result = gw.get_quote("RKLB")
        assert result is not None
        assert result.best.age_seconds > 30


class TestConsensus:
    def test_agreeing_sources_pass(self):
        a = _q(source="finnhub", last=10.00)
        b = _q(source="fmp", last=10.01)
        gw = make_gateway([("finnhub", _provider(a)), ("fmp", _provider(b))],
                          consensus_tolerance_bps=50.0)
        result = gw.get_quote("RKLB")
        assert result.consensus_ok is True
        assert result.consensus_spread_bps < 50

    def test_disagreeing_sources_fail(self):
        a = _q(source="finnhub", last=10.00)
        b = _q(source="fmp", last=10.80)  # 8% apart
        gw = make_gateway([("finnhub", _provider(a)), ("fmp", _provider(b))],
                          consensus_tolerance_bps=50.0)
        result = gw.get_quote("RKLB")
        assert result.consensus_ok is False

    def test_single_source_counts_as_consensus(self):
        gw = make_gateway([("finnhub", _provider(_q(source="finnhub")))])
        result = gw.get_quote("RKLB")
        assert result.consensus_ok is True
        assert result.consensus_spread_bps is None


class TestProviderHealth:
    def test_failures_recorded(self):
        gw = make_gateway([("finnhub", _failing_provider), ("fmp", _provider(_q(source="fmp")))])
        gw.get_quote("RKLB")
        health = gw.provider_health()
        assert health["finnhub"]["fail"] == 1
        assert health["fmp"]["ok"] == 1


class TestCache:
    def test_cache_hits_within_ttl(self):
        calls = {"n": 0}

        def counting_provider(symbol, timeout):
            calls["n"] += 1
            return _q(source="fmp")

        gw = make_gateway([("fmp", counting_provider)], cache_ttl_seconds=60.0)
        gw.get_quote("RKLB")
        gw.get_quote("RKLB")
        assert calls["n"] == 1


class TestPreTradeGateIntegration:
    def test_pretrade_kwargs_pass_gate_with_trusted_fresh_quote(self):
        quote = _q(source="finnhub", last=10.02, bid=10.01, ask=10.03, age_s=1.0)
        gw = make_gateway([("finnhub", _provider(quote))])
        result = gw.get_quote("RKLB")
        kwargs = result.pretrade_kwargs()
        gate = PreTradeGate(max_quote_age_seconds=3, require_trusted_source=True)
        check = gate.check(ticker="RKLB", **kwargs)
        assert check.ok, check.reason

    def test_untrusted_only_quote_fails_trusted_gate(self):
        quote = _q(source="yfinance", last=10.0, age_s=1.0)
        gw = make_gateway([("yfinance", _provider(quote))])
        result = gw.get_quote("RKLB")
        gate = PreTradeGate(max_quote_age_seconds=3, require_trusted_source=True)
        check = gate.check(ticker="RKLB", **result.pretrade_kwargs())
        assert not check.ok
        assert check.reason in ("provider_untrusted", "no_consensus")

    def test_stale_quote_fails_gate(self):
        quote = _q(source="finnhub", last=10.0, age_s=10.0)
        gw = make_gateway([("finnhub", _provider(quote))])
        result = gw.get_quote("RKLB")
        gate = PreTradeGate(max_quote_age_seconds=3)
        check = gate.check(ticker="RKLB", **result.pretrade_kwargs())
        assert not check.ok
        assert check.reason == "stale_quote"


class TestShadowLog:
    def test_shadow_log_written(self, tmp_path):
        log = tmp_path / "shadow.jsonl"
        trusted = _q(source="finnhub", last=10.05)
        yahoo = _q(source="yfinance", last=10.00)
        gw = make_gateway(
            [("finnhub", _provider(trusted)), ("yfinance", _provider(yahoo))],
            shadow_log_path=log,
        )
        gw.get_quote("RKLB")
        assert log.exists()
        import json
        row = json.loads(log.read_text().splitlines()[0])
        assert row["symbol"] == "RKLB"
        assert row["gateway_source"] == "finnhub"
        assert row["yf_price"] == 10.00
        assert row["delta_pct"] == pytest.approx(0.5, abs=0.01)
