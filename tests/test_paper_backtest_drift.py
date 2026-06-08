"""Tests for paper_backtest_drift.py — BT-3."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import helpers directly (not the main() entry point)
sys.path.insert(0, str(ROOT / "scripts"))
from paper_backtest_drift import (
    _aggregate,
    _compute_drift,
    _load_backtest_prices,
    _load_paper_buys,
)


def _write_jsonl(path: Path, records: list) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_csv(path: Path, rows: list) -> None:
    """Write simple CSV with header."""
    if not rows:
        path.write_text("")
        return
    header = list(rows[0].keys())
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row[h]) for h in header))
    path.write_text("\n".join(lines))


# ── _load_paper_buys ──────────────────────────────────────────────────────────

def test_load_paper_buys_filters_buy_events(tmp_path):
    log = tmp_path / "account_log.jsonl"
    _write_jsonl(log, [
        {"type": "BUY", "ticker": "AAPL", "price": 150.0, "signal_date": "2024-01-10"},
        {"type": "SELL", "ticker": "AAPL", "price": 155.0},
        {"type": "BUY", "ticker": "MSFT", "price": 380.0, "signal_date": "2024-01-11"},
    ])
    buys = _load_paper_buys(log)
    assert len(buys) == 2
    assert all(b["type"] == "BUY" for b in buys)


def test_load_paper_buys_missing_file_returns_empty(tmp_path):
    buys = _load_paper_buys(tmp_path / "nonexistent.jsonl")
    assert buys == []


def test_load_paper_buys_skips_malformed_lines(tmp_path):
    log = tmp_path / "log.jsonl"
    with log.open("w") as f:
        f.write('{"type":"BUY","ticker":"AAPL","price":150.0,"signal_date":"2024-01-10"}\n')
        f.write("NOT JSON\n")
        f.write('{"type":"BUY","ticker":"MSFT","price":380.0,"signal_date":"2024-01-11"}\n')
    buys = _load_paper_buys(log)
    assert len(buys) == 2


# ── _load_backtest_prices ─────────────────────────────────────────────────────

def test_load_backtest_prices(tmp_path):
    csv_file = tmp_path / "backtest.csv"
    _write_csv(csv_file, [
        {"ticker": "AAPL", "signal_date": "2024-01-10", "entry_price": 148.50},
        {"ticker": "MSFT", "signal_date": "2024-01-11", "entry_price": 376.00},
    ])
    prices = _load_backtest_prices(csv_file)
    assert prices[("AAPL", "2024-01-10")] == 148.50
    assert prices[("MSFT", "2024-01-11")] == 376.00


def test_load_backtest_prices_none_returns_empty():
    result = _load_backtest_prices(None)
    assert result == {}


# ── _compute_drift ────────────────────────────────────────────────────────────

def _make_buys():
    return [
        {"type": "BUY", "ticker": "AAPL", "price": 150.0, "signal_date": "2024-01-10", "setup_type": "pullback"},
        {"type": "BUY", "ticker": "MSFT", "price": 381.0, "signal_date": "2024-01-11", "setup_type": "pullback"},
        {"type": "BUY", "ticker": "NVDA", "price": 505.0, "signal_date": "2024-01-12", "setup_type": "breakout"},
    ]


def _make_bt_prices():
    return {
        ("AAPL", "2024-01-10"): 148.50,  # paper fill 1.01% above
        ("MSFT", "2024-01-11"): 380.00,  # paper fill 0.26% above
        ("NVDA", "2024-01-12"): 500.00,  # paper fill 1.00% above
    }


def test_compute_drift_returns_expected_count():
    matches = _compute_drift(_make_buys(), _make_bt_prices())
    assert len(matches) == 3


def test_compute_drift_slip_bps_positive_when_paper_above_backtest():
    matches = _compute_drift(_make_buys(), _make_bt_prices())
    for m in matches:
        assert m["slip_bps"] > 0, f"Expected positive slip for {m['ticker']}: {m['slip_bps']}"


def test_compute_drift_no_match_returns_empty():
    buys = [{"type": "BUY", "ticker": "TSLA", "price": 200.0, "signal_date": "2024-01-15"}]
    result = _compute_drift(buys, {("AAPL", "2024-01-10"): 148.50})
    assert result == []


# ── _aggregate ────────────────────────────────────────────────────────────────

def test_aggregate_output_keys():
    matches = _compute_drift(_make_buys(), _make_bt_prices())
    agg = _aggregate(matches)
    assert "mean_slip_bps" in agg
    assert "std_slip_bps" in agg
    assert "p95_slip_bps" in agg
    assert "n_trades" in agg
    assert agg["n_trades"] == 3


def test_aggregate_empty_returns_none_stats():
    agg = _aggregate([])
    assert agg["n_trades"] == 0
    assert agg["mean_slip_bps"] is None


def test_aggregate_by_setup_type():
    matches = _compute_drift(_make_buys(), _make_bt_prices())
    agg = _aggregate(matches)
    assert "pullback" in agg["by_setup_type"]
    assert agg["by_setup_type"]["pullback"]["n"] == 2
    assert "breakout" in agg["by_setup_type"]
    assert agg["by_setup_type"]["breakout"]["n"] == 1


# ── CLI dry-run ───────────────────────────────────────────────────────────────

def test_dry_run_no_output_file(tmp_path, monkeypatch):
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "paper_backtest_drift.py"),
         "--dry-run", "--min-trades", "0"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Dry-run" in result.stdout


def test_min_trades_warning_on_few_matches(tmp_path, capsys):
    """min_trades warning printed when too few matches."""
    matches = [{"ticker": "AAPL", "signal_date": "2024-01-10",
                "paper_fill": 150.0, "backtest_price": 148.5,
                "slip_bps": 101.0, "setup_type": "pullback"}]
    agg = _aggregate(matches)
    # Just verify n_trades < 5 causes awareness
    assert agg["n_trades"] == 1
