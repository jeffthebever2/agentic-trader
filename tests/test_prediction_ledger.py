"""Tests for tradingagents.portfolio.prediction_ledger.PredictionLedger"""
import datetime as dt
import json
import uuid
import pytest
from pathlib import Path
from tradingagents.portfolio.prediction_ledger import PredictionLedger, PredictionEntry


NOW = dt.datetime(2026, 6, 6, 14, 0, 0)


@pytest.fixture()
def ledger(tmp_path):
    return PredictionLedger(tmp_path / "prediction_ledger.jsonl")


# ── Basic write / read ────────────────────────────────────────────────────────

def test_log_creates_file(ledger):
    ledger.log("AAPL", "BUY", ml_probability=0.72, now=NOW)
    assert ledger.path.exists()


def test_log_returns_entry(ledger):
    e = ledger.log("AAPL", "BUY", ml_probability=0.72, now=NOW)
    assert isinstance(e, PredictionEntry)
    assert e.ticker == "AAPL"
    assert e.decision == "BUY"
    assert e.ml_probability == 0.72


def test_log_writes_valid_json_line(ledger):
    ledger.log("NVDA", "BUY", ml_probability=0.65, now=NOW)
    lines = ledger.path.read_text().strip().splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d["ticker"] == "NVDA"
    assert d["ml_probability"] == 0.65


def test_multiple_entries_appended(ledger):
    ledger.log("AAPL", "BUY", now=NOW)
    ledger.log("TSLA", "BUY", now=NOW)
    ledger.log("NVDA", "SKIP", skip_reason="stale_quote", now=NOW)
    assert len(ledger.read_all()) == 3


def test_entry_has_unique_id(ledger):
    e1 = ledger.log("AAPL", "BUY", now=NOW)
    e2 = ledger.log("AAPL", "BUY", now=NOW)
    assert e1.entry_id != e2.entry_id


def test_logged_at_iso_format(ledger):
    e = ledger.log("AAPL", "BUY", now=NOW)
    assert e.logged_at.endswith("Z")
    # Should parse without error
    dt.datetime.fromisoformat(e.logged_at.rstrip("Z"))


# ── Fields round-trip ─────────────────────────────────────────────────────────

def test_all_fields_persisted(ledger):
    ledger.log(
        "MSFT",
        "BUY",
        ml_probability=0.71,
        expected_return=0.04,
        large_loss_probability=0.10,
        target_before_stop_probability=0.55,
        timeout_probability=0.30,
        entry_price=420.0,
        stop=405.0,
        target=450.0,
        atr=6.5,
        alpha_tier="A",
        alpha_score=0.82,
        breakout_score=0.77,
        regime="bull",
        model_version="cycle46",
        now=NOW,
    )
    records = ledger.read_all()
    r = records[0]
    assert r["ml_probability"] == 0.71
    assert r["expected_return"] == 0.04
    assert r["entry_price"] == 420.0
    assert r["stop"] == 405.0
    assert r["alpha_tier"] == "A"
    assert r["regime"] == "bull"
    assert r["model_version"] == "cycle46"


def test_extra_kwargs_flattened(ledger):
    ledger.log("SPY", "BUY", strategy="algorithm", shares=10, now=NOW)
    r = ledger.read_all()[0]
    assert r["strategy"] == "algorithm"
    assert r["shares"] == 10


# ── Filters ──────────────────────────────────────────────────────────────────

def test_read_ticker_filter(ledger):
    ledger.log("AAPL", "BUY", now=NOW)
    ledger.log("TSLA", "BUY", now=NOW)
    ledger.log("AAPL", "SKIP", now=NOW)
    aapl = ledger.read_ticker("AAPL")
    assert len(aapl) == 2
    assert all(e["ticker"] == "AAPL" for e in aapl)


def test_read_buys_only(ledger):
    ledger.log("AAPL", "BUY", now=NOW)
    ledger.log("NVDA", "SKIP", skip_reason="wide_spread", now=NOW)
    buys = ledger.read_buys()
    assert len(buys) == 1
    assert buys[0]["decision"] == "BUY"


def test_read_all_empty_file(ledger):
    assert ledger.read_all() == []


def test_read_all_skips_malformed_lines(ledger, tmp_path):
    p = tmp_path / "bad_ledger.jsonl"
    p.write_text('{"ticker": "AAPL", "decision": "BUY"}\n{BROKEN\n{"ticker": "TSLA", "decision": "BUY"}\n')
    bad = PredictionLedger(p)
    records = bad.read_all()
    assert len(records) == 2


# ── Directory creation ────────────────────────────────────────────────────────

def test_creates_parent_dirs(tmp_path):
    deep_path = tmp_path / "a" / "b" / "c" / "ledger.jsonl"
    ledger = PredictionLedger(deep_path)
    ledger.log("AAPL", "BUY", now=NOW)
    assert deep_path.exists()
