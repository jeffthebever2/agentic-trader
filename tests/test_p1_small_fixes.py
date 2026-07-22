"""P1 audit small fail-closed fixes (2026-07-05).

S1: trusted quote-gateway fetchers (finnhub/twelve_data/fmp) must DROP a quote
    whose provider payload carries no timestamp — stamping now() let a stale
    quote from a trusted source pass the execution freshness gate.
S2: position_sizer cash constraint — a NaN cash_available used to become
    INFINITE cash room (the one fail-open input in the module). Non-finite
    numbers now mean 0 room; only a truly-absent None stays unconstrained.
"""
import datetime as dt
import math

import pytest

from tradingagents.data import quote_gateway as qg
from tradingagents.portfolio.position_sizer import SizingCandidate, size_position


# ── S1: timestamp-less trusted quotes are dropped ────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_FETCHERS = [
    # (fetcher, env key, payload WITH timestamp, payload WITHOUT timestamp)
    (qg._fetch_finnhub, "FINNHUB_API_KEY",
     {"c": 10.5, "t": 1_750_000_000}, {"c": 10.5, "t": 0}),
    (qg._fetch_twelve_data, "TWELVEDATA_API_KEY",
     {"close": 10.5, "timestamp": 1_750_000_000}, {"close": 10.5}),
    (qg._fetch_fmp, "FMP_API_KEY",
     [{"price": 10.5, "timestamp": 1_750_000_000}], [{"price": 10.5}]),
]


@pytest.mark.parametrize("fetcher,env_key,with_ts,without_ts", _FETCHERS,
                         ids=["finnhub", "twelve_data", "fmp"])
def test_timestampless_trusted_quote_dropped(monkeypatch, fetcher, env_key, with_ts, without_ts):
    monkeypatch.setenv(env_key, "k")
    monkeypatch.setattr(qg.requests, "get", lambda *a, **kw: _Resp(without_ts))
    assert fetcher("RKLB", 3.0) is None


@pytest.mark.parametrize("fetcher,env_key,with_ts,without_ts", _FETCHERS,
                         ids=["finnhub", "twelve_data", "fmp"])
def test_timestamped_trusted_quote_kept_with_exact_time(monkeypatch, fetcher, env_key, with_ts, without_ts):
    monkeypatch.setenv(env_key, "k")
    monkeypatch.setattr(qg.requests, "get", lambda *a, **kw: _Resp(with_ts))
    q = fetcher("RKLB", 3.0)
    assert q is not None
    assert q.last == 10.5
    assert q.quote_time == dt.datetime.fromtimestamp(1_750_000_000)  # naive-local
    assert q.quote_time.tzinfo is None


# ── S2: NaN cash_available fails closed ──────────────────────────────────────

def _cand(**kw):
    base = dict(ticker="X", conviction=8, score=80.0, expected_return_pct=30.0, stop_pct=8.0)
    base.update(kw)
    return SizingCandidate(**base)


def test_nan_cash_means_zero_room():
    res = size_position(100_000, _cand(), [], cash_available=float("nan"))
    assert res.dollars == 0.0
    assert res.binding_constraint == "cash"


def test_inf_cash_means_zero_room():
    res = size_position(100_000, _cand(), [], cash_available=math.inf)
    assert res.dollars == 0.0
    assert res.binding_constraint == "cash"


def test_none_cash_stays_unconstrained():
    res = size_position(100_000, _cand(), [], cash_available=None)
    assert res.dollars > 0.0
    assert res.binding_constraint != "cash"


def test_real_cash_still_clamps():
    res = size_position(100_000, _cand(), [], cash_available=500.0)
    assert res.dollars <= 500.0
