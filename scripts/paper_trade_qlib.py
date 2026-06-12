#!/usr/bin/env python3
"""Qlib factor paper portfolio runner.

This runner is intentionally isolated from live execution.  It ranks a ticker
universe with lagged Qlib-style factors, writes every signal to disk, and opens
or exits positions only in a separate paper PortfolioState.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAPER_ONLY = True
ACCOUNT_NAME = "qlib_factor_paper"


@dataclass(frozen=True)
class QlibPaperSignal:
    ticker: str
    score: float
    price: float
    as_of: str
    features: dict[str, float | None]
    thesis: str


def _load_tickers(tickers_file: str | None, tickers: list[str] | None, max_tickers: int = 0) -> list[str]:
    values: list[str] = []
    if tickers:
        values.extend(tickers)
    if tickers_file:
        path = Path(tickers_file)
        if not path.exists():
            raise FileNotFoundError(f"tickers file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            token = line.strip().split(",")[0].upper()
            if token and not token.startswith("#"):
                values.append(token)

    seen: set[str] = set()
    clean: list[str] = []
    for value in values:
        ticker = str(value).strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            clean.append(ticker)

    if max_tickers and max_tickers > 0:
        clean = clean[:max_tickers]
    if not clean:
        raise ValueError("no tickers supplied")
    return clean


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    col_map = {str(c).lower(): c for c in frame.columns}
    close_col = col_map.get("close")
    high_col = col_map.get("high")
    low_col = col_map.get("low")
    if close_col is None:
        raise ValueError("OHLCV frame missing close column")
    data = pd.DataFrame({"close": frame[close_col]})
    if high_col is not None:
        data["high"] = frame[high_col]
    else:
        data["high"] = data["close"]
    if low_col is not None:
        data["low"] = frame[low_col]
    else:
        data["low"] = data["close"]
    data.index = pd.to_datetime(data.index)
    return data.sort_index().dropna(subset=["close"])


def build_price_cache(
    tickers: list[str],
    start: str,
    end: str,
    *,
    batch_size: int = 100,
    no_cache: bool = False,
) -> dict[str, pd.DataFrame]:
    from backtest import download_all

    raw = download_all(tickers, start, end, no_cache=no_cache, batch_size=batch_size, threads=False)
    cache: dict[str, pd.DataFrame] = {}
    for ticker, frame in raw.items():
        if frame is None or frame.empty:
            continue
        try:
            normalized = _normalize_ohlcv(frame)
        except ValueError:
            continue
        if len(normalized) >= 90:
            cache[str(ticker).upper()] = normalized
    return cache


def _to_float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _latest_feature_row(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Series] | None:
    usable = frame.dropna(how="all")
    if usable.empty:
        return None
    idx = usable.index[-1]
    return pd.Timestamp(idx), usable.loc[idx]


def compute_latest_signals(price_cache: dict[str, pd.DataFrame]) -> list[QlibPaperSignal]:
    from tradingagents.qlib_integration.feature_merger import compute_qlib_features

    rows: list[dict[str, Any]] = []
    for ticker, raw_frame in price_cache.items():
        frame = _normalize_ohlcv(raw_frame)
        if len(frame) < 90:
            continue
        features = compute_qlib_features(
            frame["close"],
            frame.get("high"),
            frame.get("low"),
            lag_days=1,
        )
        latest = _latest_feature_row(features)
        if latest is None:
            continue
        as_of, values = latest
        price = _to_float_or_none(frame.loc[:as_of, "close"].dropna().iloc[-1])
        if price is None or price <= 0:
            continue
        row = {"ticker": ticker.upper(), "as_of": as_of.date().isoformat(), "price": price}
        for col in features.columns:
            row[col] = _to_float_or_none(values.get(col))
        rows.append(row)

    if not rows:
        return []

    df = pd.DataFrame(rows)
    score_parts: list[pd.Series] = []
    if "qlib_mom_252_21" in df:
        score_parts.append(df["qlib_mom_252_21"].rank(pct=True, na_option="bottom") * 0.35)
    if "qlib_mom_63" in df:
        score_parts.append(df["qlib_mom_63"].rank(pct=True, na_option="bottom") * 0.30)
    if "qlib_close_rank" in df:
        score_parts.append(df["qlib_close_rank"].rank(pct=True, na_option="bottom") * 0.20)
    if "qlib_vol_ratio" in df:
        score_parts.append((1.0 - df["qlib_vol_ratio"].rank(pct=True, na_option="bottom")) * 0.15)
    if not score_parts:
        return []
    df["score"] = sum(score_parts).fillna(0.0) * 100.0
    df = df.sort_values(["score", "ticker"], ascending=[False, True])

    signals: list[QlibPaperSignal] = []
    feature_cols = [c for c in df.columns if c.startswith("qlib_")]
    for row in df.to_dict("records"):
        score = float(row["score"])
        ticker = str(row["ticker"]).upper()
        features = {col: _to_float_or_none(row.get(col)) for col in feature_cols}
        thesis = (
            f"Qlib lagged factor rank score {score:.1f}; "
            f"mom63={features.get('qlib_mom_63')}, "
            f"mom252_21={features.get('qlib_mom_252_21')}, "
            f"vol_ratio={features.get('qlib_vol_ratio')}"
        )
        signals.append(
            QlibPaperSignal(
                ticker=ticker,
                score=score,
                price=float(row["price"]),
                as_of=str(row["as_of"]),
                features=features,
                thesis=thesis,
            )
        )
    return signals


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")


def _write_summary(account_dir: Path, portfolio: Any, *, signals: int, bought: int, sold: int, skipped: int) -> None:
    positions = [position.to_dict() for position in portfolio.positions.values()]
    summary = {
        "strategy": ACCOUNT_NAME,
        "strategy_label": "Qlib Factor Paper",
        "paper_only": PAPER_ONLY,
        "starting_cash": portfolio.starting_cash,
        "cash": portfolio.cash,
        "realized_pnl": 0.0,
        "total_value": round(portfolio.total_value, 2),
        "open_positions": positions,
        "trades_closed": 0,
        "candidates": signals,
        "signals": signals,
        "bought": bought,
        "sold": sold,
        "skipped": skipped,
    }
    path = account_dir / "summary.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def run_paper_cycle(
    signals: list[QlibPaperSignal],
    *,
    output_dir: Path,
    starting_cash: float = 100_000.0,
    max_positions: int = 10,
    position_pct: float = 0.05,
    min_score: float = 50.0,
    reset: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    from tradingagents.portfolio.state import PortfolioState
    from tradingagents.portfolio.prediction_ledger import PredictionLedger

    account_dir = output_dir / ACCOUNT_NAME
    state_file = account_dir / "state.json"
    if reset and state_file.exists():
        state_file.unlink()

    latest_prices = {signal.ticker: signal.price for signal in signals}
    price_lookup = lambda ticker: latest_prices.get(str(ticker).upper())
    sector_lookup = lambda _ticker: "Qlib"
    portfolio = PortfolioState(
        {
            "portfolio_state_path": str(state_file),
            "trade_log_path": str(account_dir / "trade_results.jsonl"),
            "paper_decision_log_path": str(account_dir / "paper_decisions.jsonl"),
            "starting_cash": starting_cash,
            "max_positions": max_positions,
            "max_position_size": position_pct,
            "max_sector_exposure": 1.0,
        },
        price_lookup=price_lookup,
        sector_lookup=sector_lookup,
    )

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    audit_records: list[dict[str, Any]] = []
    event_records: list[dict[str, Any]] = []
    ledger = PredictionLedger(account_dir / "prediction_ledger.jsonl")
    sold = 0
    bought = 0
    skipped = 0

    for action, ticker, reason in list(portfolio.check_stops_and_limits()):
        if action == "SELL":
            pos = portfolio.positions.get(ticker)
            live_price = price_lookup(ticker)
            if pos is None:
                continue
            if "STOP" in reason.upper():
                exit_price = min(live_price or pos.entry_price, pos.stop_loss)
            elif "TAKE_PROFIT" in reason.upper() or "TARGET" in reason.upper():
                exit_price = max(live_price or pos.entry_price, pos.take_profit)
            else:
                exit_price = live_price or pos.entry_price
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0.0
            if not dry_run:
                portfolio.execute_sell(ticker, reason)
            sold += 1
            audit_records.append(
                {
                    "timestamp": now,
                    "account": ACCOUNT_NAME,
                    "ticker": ticker,
                    "decision": "SELL",
                    "reason": reason,
                    "paper_only": PAPER_ONLY,
                }
            )
            event_records.append(
                {
                    "timestamp": now,
                    "type": "SELL",
                    "ticker": ticker,
                    "reason": reason,
                    "strategy": ACCOUNT_NAME,
                    "paper_only": PAPER_ONLY,
                    "entry_time": pos.entry_date.isoformat(),
                    "exit_price": exit_price,
                    "entry_price": pos.entry_price,
                    "pnl_pct": pnl_pct,
                    "exit_reason": reason,
                    "stop_hit": "STOP" in reason.upper(),
                    "target_hit": "TARGET" in reason.upper() or "TAKE_PROFIT" in reason.upper(),
                    "model_version": "qlib_factor_v1",
                    "alpha_tier": "QLIB",
                }
            )

    for signal in signals:
        if signal.score < min_score:
            skipped += 1
            portfolio.record_paper_decision(signal.ticker, "SKIP", "LOW_SCORE", signal.as_of)
            audit_records.append(
                {
                    "timestamp": now,
                    "account": ACCOUNT_NAME,
                    "ticker": signal.ticker,
                    "decision": "SKIP",
                    "reason": "LOW_SCORE",
                    "score": signal.score,
                    "paper_only": PAPER_ONLY,
                }
            )
            continue
        if signal.ticker in portfolio.positions:
            skipped += 1
            portfolio.record_paper_decision(signal.ticker, "SKIP", "ALREADY_LONG", signal.as_of)
            continue
        if len(portfolio.positions) >= max_positions:
            skipped += 1
            portfolio.record_paper_decision(signal.ticker, "SKIP", "MAX_POSITIONS", signal.as_of)
            continue

        budget = portfolio.total_value * position_pct
        shares = math.floor(budget / signal.price)
        ok, reason = portfolio.can_buy(signal.ticker, shares, signal.price) if shares > 0 else (False, "SIZE_ZERO")
        if not ok:
            skipped += 1
            portfolio.record_paper_decision(signal.ticker, "SKIP", reason, signal.as_of)
            ledger.log(
                signal.ticker,
                "SKIP",
                skip_reason=reason,
                entry_price=signal.price,
                alpha_score=signal.score / 100.0,
                alpha_tier="QLIB",
                model_version="qlib_factor_v1",
                strategy=ACCOUNT_NAME,
                signal_as_of=signal.as_of,
                qlib_features=signal.features,
                paper_only=PAPER_ONLY,
            )
            continue

        ledger.log(
            signal.ticker,
            "BUY",
            entry_price=signal.price,
            stop=signal.price * 0.95,
            target=signal.price * 1.10,
            alpha_score=signal.score / 100.0,
            alpha_tier="QLIB",
            model_version="qlib_factor_v1",
            strategy=ACCOUNT_NAME,
            signal_as_of=signal.as_of,
            qlib_features=signal.features,
            paper_only=PAPER_ONLY,
        )
        if not dry_run:
            portfolio.execute_buy(
                signal.ticker,
                shares,
                signal.price,
                signal.thesis,
                stop_loss=signal.price * 0.95,
                take_profit=signal.price * 1.10,
            )
        actual_entry_time = now
        if not dry_run and signal.ticker in portfolio.positions:
            actual_entry_time = portfolio.positions[signal.ticker].entry_date.isoformat()
        portfolio.record_paper_decision(signal.ticker, "BUY", f"QLIB_SCORE_{signal.score:.1f}", signal.as_of)
        bought += 1
        audit_records.append(
            {
                "timestamp": now,
                "account": ACCOUNT_NAME,
                "ticker": signal.ticker,
                "decision": "BUY",
                "score": signal.score,
                "price": signal.price,
                "shares": shares,
                "dry_run": dry_run,
                "paper_only": PAPER_ONLY,
                "features": signal.features,
            }
        )
        event_records.append(
            {
                "timestamp": actual_entry_time,
                "type": "BUY",
                "ticker": signal.ticker,
                "price": signal.price,
                "shares": shares,
                "strategy": ACCOUNT_NAME,
                "paper_only": PAPER_ONLY,
                "dry_run": dry_run,
                "entry_time": actual_entry_time,
                "entry_price": signal.price,
                "stop": signal.price * 0.95,
                "target": signal.price * 1.10,
                "alpha_score": signal.score / 100.0,
                "alpha_tier": "QLIB",
                "model_version": "qlib_factor_v1",
                "expected_return": 0.10,
                "large_loss_probability": None,
            }
        )

    if audit_records:
        stamp = dt.datetime.now().strftime("%Y%m%d")
        _write_jsonl(account_dir / f"qlib_factor_audit_{stamp}.jsonl", audit_records)
    if event_records:
        _write_jsonl(account_dir / "events.jsonl", event_records)
    _write_summary(account_dir, portfolio, signals=len(signals), bought=bought, sold=sold, skipped=skipped)

    return {
        "account": ACCOUNT_NAME,
        "paper_only": PAPER_ONLY,
        "state_file": str(state_file),
        "signals": len(signals),
        "bought": bought,
        "sold": sold,
        "skipped": skipped,
        "positions": sorted(portfolio.positions.keys()),
        "cash": portfolio.cash,
        "total_value": portfolio.total_value,
        "dry_run": dry_run,
    }


def save_signals(signals: list[QlibPaperSignal], output_dir: Path) -> Path:
    path = output_dir / ACCOUNT_NAME / f"qlib_signals_{dt.datetime.now().strftime('%Y%m%d')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(signal) for signal in signals], indent=2, default=str),
        encoding="utf-8",
    )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a separate Qlib factor paper portfolio.")
    parser.add_argument("--tickers-file", default="all_tickers.txt")
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=dt.date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output-dir", default="tmp/paper_trading_qlib")
    parser.add_argument("--starting-cash", type=float, default=100_000.0)
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--position-pct", type=float, default=0.05)
    parser.add_argument("--min-score", type=float, default=50.0)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tickers = _load_tickers(args.tickers_file, args.tickers, args.max_tickers)
    cache = build_price_cache(
        tickers,
        args.start,
        args.end,
        batch_size=args.batch_size,
        no_cache=args.no_cache,
    )
    signals = compute_latest_signals(cache)
    output_dir = Path(args.output_dir)
    signal_path = save_signals(signals, output_dir)
    result = run_paper_cycle(
        signals,
        output_dir=output_dir,
        starting_cash=args.starting_cash,
        max_positions=args.max_positions,
        position_pct=args.position_pct,
        min_score=args.min_score,
        reset=args.reset,
        dry_run=args.dry_run,
    )
    result["signal_path"] = str(signal_path)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
