"""Buzz-based exits (buzz_collapse / buzz_decay) must require price confirmation:
a GREEN position must not be liquidated just because social attention faded. A
flat/red name with faded buzz still exits. Stop/target/trailing are unaffected."""
import asyncio
import datetime as dt
import json

import pandas as pd
import yfinance as yf

import web.api.thematic_auto as t


def _setup(tmp_path, monkeypatch, *, entry, price, ticker="ZZZ"):
    # state file with one thematic position, entered 3 days ago, wide stop/target
    entry_time = (dt.datetime.now() - dt.timedelta(days=3)).isoformat()
    state = {"cash": 1000, "positions": {ticker: {
        "_source": "thematic", "ticker": ticker, "entry_price": entry,
        "shares": 10, "stop": entry * 0.5, "target": 999999,
        "hold_days": 30, "entry_time": entry_time, "entry_raw_score": 100,
    }}}
    f = tmp_path / "state.json"
    f.write_text(json.dumps(state))
    monkeypatch.setattr(t, "PAPER_STATE_FILE", f)
    # price fetch (nested closure calls yf.download → Close series)
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame({"Close": [price]}))
    # latest scan: ticker ABSENT (truthy dict) → buzz_collapse candidate
    monkeypatch.setattr(t, "_get_latest_scan_scores", lambda: {"OTHER": 50})
    return ticker


def test_green_position_not_buzz_exited(tmp_path, monkeypatch):
    tk = _setup(tmp_path, monkeypatch, entry=100.0, price=120.0)  # +20% green
    exits = asyncio.run(t._check_thematic_exits(execute=False))
    reasons = {e["ticker"]: e["reason"] for e in exits}
    assert tk not in reasons   # faded buzz must NOT sell a green winner


def test_red_position_still_buzz_exits(tmp_path, monkeypatch):
    tk = _setup(tmp_path, monkeypatch, entry=100.0, price=96.0)   # red, above stop
    exits = asyncio.run(t._check_thematic_exits(execute=False))
    reasons = {e["ticker"]: e["reason"] for e in exits}
    assert reasons.get(tk) == "buzz_collapse"


def test_stop_still_fires_on_green_logic_path(tmp_path, monkeypatch):
    # sanity: a real stop breach still exits regardless of buzz logic
    tk = _setup(tmp_path, monkeypatch, entry=100.0, price=40.0)   # below 50% stop
    exits = asyncio.run(t._check_thematic_exits(execute=False))
    reasons = {e["ticker"]: e["reason"] for e in exits}
    assert reasons.get(tk) == "stop_hit"
