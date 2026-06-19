"""Max-hold must not market-dump a still-running winner. A green/trending position
past hold_days converts to a trailing stop (lets the right tail run); a flat/red
position still takes the timed exit."""
import asyncio
import datetime as dt
import json

import pandas as pd
import yfinance as yf

import web.api.thematic_auto as t


def _setup(tmp_path, monkeypatch, *, entry, price, ticker="ZZZ", hold_days=5, age_days=10):
    entry_time = (dt.datetime.now() - dt.timedelta(days=age_days)).isoformat()
    state = {"cash": 1000, "positions": {ticker: {
        "_source": "thematic", "ticker": ticker, "entry_price": entry,
        "shares": 10, "stop": entry * 0.5, "target": 999999,
        "hold_days": hold_days, "entry_time": entry_time, "entry_raw_score": 100,
    }}}
    f = tmp_path / "state.json"
    f.write_text(json.dumps(state))
    monkeypatch.setattr(t, "PAPER_STATE_FILE", f)
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame({"Close": [price]}))
    monkeypatch.setattr(t, "_get_latest_scan_scores", lambda: {ticker: 100})  # buzz intact
    return ticker, f


def test_green_winner_past_maxhold_not_dumped(tmp_path, monkeypatch):
    tk, f = _setup(tmp_path, monkeypatch, entry=100.0, price=140.0)   # +40% past max-hold
    exits = asyncio.run(t._check_thematic_exits(execute=True))
    reasons = {e["ticker"]: e["reason"] for e in exits}
    assert tk not in reasons                       # not timed-out
    state = json.loads(f.read_text())
    assert state["positions"][tk]["trailing"] is True   # converted to runner


def test_flat_position_past_maxhold_times_out(tmp_path, monkeypatch):
    tk, f = _setup(tmp_path, monkeypatch, entry=100.0, price=103.0)   # barely green (<12%)
    exits = asyncio.run(t._check_thematic_exits(execute=False))
    reasons = {e["ticker"]: e["reason"] for e in exits}
    assert reasons.get(tk) == "max_hold_exceeded"
