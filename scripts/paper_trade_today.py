#!/usr/bin/env python3
"""Run today's live paper-trading loop from the backtest algorithm.

This script does not place broker orders. It builds the same confirmed-pullback
signals used by backtest.py from the last completed daily bar, applies the saved
ML gate when available, then checks live Yahoo prices every 15 minutes and
simulates fills in a local paper account. It does not use news or LLM research
agents.

Example:
    python scripts/paper_trade_today.py --reset
    python scripts/paper_trade_today.py --ml-algo-only --reset
    python scripts/paper_trade_today.py --reset --openrouter-model openai/gpt-4o-mini
    python scripts/paper_trade_today.py --tickers all_tickers.txt --starting-cash 10000
    python scripts/paper_trade_today.py --once --max-tickers 100 --force
"""

from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Suppress noisy sklearn/joblib parallel warnings on Python 3.14
warnings.filterwarnings("ignore", message=".*sklearn.utils.parallel.delayed.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*sklearn.utils.parallel.Parallel.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Skipping features without any observed values.*", category=UserWarning)
warnings.filterwarnings("ignore", category=UserWarning, module=r"sklearn\..*")
warnings.filterwarnings("ignore", category=UserWarning)
# Suppress all ResourceWarnings (unclosed sockets/files from yfinance/sklearn — benign, GC cleans up)
warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.simplefilter("ignore", ResourceWarning)

import sqlite3
import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except Exception:
    box = None
    Console = Group = Live = Panel = Table = Text = None
    RICH_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import (  # noqa: E402
    MIN_HISTORY,
    SECTOR_ETFS,
    _extract_ticker_dfs,
    _ml_design_matrix,
    build_sector_breadth,
    build_spy_regime,
    build_vix_regime,
    build_vix_term_structure,
    load_tickers,
    precompute,
    score_at,
)
from tradingagents.openrouter_usage import record_openrouter_request  # noqa: E402


DEFAULT_MODEL_PATHS = [
    Path("ml_models/stock_universe/model_bundle.joblib"),
    Path("ml_models/latest/model_bundle.joblib"),
]

STRATEGY_LABELS = {
    "algorithm": "Algorithm",
    "machine_learning": "ML Old",
    "ml_new": "ML New",
    "combined": "Algorithm + ML",
    "pure_ai": "Rule-Based",
    "long_hold": "Long Hold",
}

STRATEGY_SHORT_LABELS = {
    "algorithm": "Algo",
    "machine_learning": "MLOld",
    "ml_new": "MLNew",
    "combined": "Algo+ML",
    "pure_ai": "Rule",
    "long_hold": "LongHold",
}


def strategy_label(strategy: str) -> str:
    return STRATEGY_LABELS.get(strategy, strategy.replace("_", " ").title())


def strategy_short_label(strategy: str) -> str:
    return STRATEGY_SHORT_LABELS.get(strategy, strategy_label(strategy))


def dollars(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}"


def countdown_text(now: dt.datetime, target: dt.datetime | None) -> str:
    if target is None:
        return "n/a"
    seconds = max(0, int((target - now).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class TerminalDashboard:
    """Small Rich-powered CLI dashboard with a plain-print fallback."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and RICH_AVAILABLE
        self.console = Console() if self.enabled else None
        self.live = None
        self.started_at = dt.datetime.now()
        self.account: PaperAccount | None = None
        self.accounts: dict[str, PaperAccount] = {}
        self.candidates: list[Candidate] = []
        self.candidates_by_strategy: dict[str, list[Candidate]] = {}
        self.prices: dict[str, float] = {}
        self.phase = "Starting"
        self.market_open: dt.datetime | None = None
        self.market_close: dt.datetime | None = None
        self.next_scan_at: dt.datetime | None = None
        self.events: list[str] = []
        self.last_printed_phase = ""

    def start(self) -> None:
        if not self.enabled or self.live is not None:
            return
        self.live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            transient=False,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self.live.start()

    def stop(self) -> None:
        if self.live is not None:
            self.live.update(self.render())
            self.live.stop()
            self.live = None

    def event(self, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.events.append(line)
        self.events = self.events[-10:]
        if self.enabled and self.live is not None:
            self.live.update(self.render())
        else:
            print(line, flush=True)

    def update(
        self,
        *,
        account: "PaperAccount | None" = None,
        accounts: dict[str, "PaperAccount"] | None = None,
        candidates: list["Candidate"] | None = None,
        candidates_by_strategy: dict[str, list["Candidate"]] | None = None,
        prices: dict[str, float] | None = None,
        phase: str | None = None,
        market_open: dt.datetime | None = None,
        market_close: dt.datetime | None = None,
        next_scan_at: dt.datetime | None = None,
    ) -> None:
        if account is not None:
            self.account = account
            if not accounts and not self.accounts:
                self.accounts = {"paper": account}
        if accounts is not None:
            self.accounts = accounts
        if candidates is not None:
            self.candidates = candidates
            if not candidates_by_strategy and not self.candidates_by_strategy:
                self.candidates_by_strategy = {"paper": candidates}
        if candidates_by_strategy is not None:
            self.candidates_by_strategy = candidates_by_strategy
        if prices is not None:
            self.prices = prices
        if phase is not None:
            self.phase = phase
        if market_open is not None:
            self.market_open = market_open
        if market_close is not None:
            self.market_close = market_close
        self.next_scan_at = next_scan_at

        if self.enabled and self.live is not None:
            self.live.update(self.render())
        elif phase and phase != self.last_printed_phase:
            self.last_printed_phase = phase
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {phase}", flush=True)

    def wait(self, seconds: float, phase: str) -> None:
        if seconds <= 0:
            return
        end = dt.datetime.now(tz=self.market_open.tzinfo if self.market_open else None) + dt.timedelta(seconds=seconds)
        remaining = seconds
        heartbeat_at = 0
        while remaining > 0:
            self.update(phase=phase, next_scan_at=end)
            sleep_for = min(1.0, remaining)
            time.sleep(max(0.1, sleep_for))
            remaining = (end - dt.datetime.now(tz=end.tzinfo)).total_seconds()
            if not self.enabled:
                elapsed = int(seconds - max(0, remaining))
                if elapsed >= heartbeat_at:
                    self.event(f"{phase}; next action in {countdown_text(dt.datetime.now(tz=end.tzinfo), end)}")
                    heartbeat_at += 30
        self.update(next_scan_at=None)

    def render(self):
        if not self.enabled:
            return ""
        tz = self.market_open.tzinfo if self.market_open else None
        now = dt.datetime.now(tz)
        elapsed = now.replace(tzinfo=None) - self.started_at.replace(tzinfo=None)
        market = "unknown"
        if self.market_open and self.market_close:
            if now < self.market_open:
                market = "pre-market"
            elif now <= self.market_close:
                market = "open"
            else:
                market = "closed"

        clock = Table.grid(expand=True)
        clock.add_column(ratio=1)
        clock.add_column(ratio=1)
        clock.add_row(f"[bold]Phase[/bold] {self.phase}", f"[bold]Market[/bold] {market}")
        clock.add_row(f"[bold]Now[/bold] {now.strftime('%Y-%m-%d %H:%M:%S')}", f"[bold]Elapsed[/bold] {str(elapsed).split('.')[0]}")
        clock.add_row(
            f"[bold]Next Scan[/bold] {countdown_text(now, self.next_scan_at)}",
            f"[bold]Window[/bold] {self._market_window()}",
        )

        accounts = self.accounts or ({"paper": self.account} if self.account else {})
        acct = Table(title="Paper Accounts", box=box.SIMPLE_HEAVY, expand=True)
        acct.add_column("Strategy")
        acct.add_column("Cash", justify="right")
        acct.add_column("Total Value", justify="right")
        acct.add_column("Day P/L", justify="right")
        acct.add_column("Return", justify="right")
        acct.add_column("Open", justify="right")
        acct.add_column("Closed", justify="right")
        acct.add_column("Candidates", justify="right")
        if accounts:
            for strategy, account in accounts.items():
                total = account.total_value(self.prices)
                day_pnl = total - account.starting_cash
                ret = day_pnl / account.starting_cash if account.starting_cash else 0.0
                pnl_style = "green" if day_pnl >= 0 else "red"
                acct.add_row(
                    strategy_short_label(strategy),
                    dollars(account.cash),
                    dollars(total),
                    f"[{pnl_style}]{dollars(day_pnl)}[/{pnl_style}]",
                    f"[{pnl_style}]{pct(ret)}[/{pnl_style}]",
                    str(len(account.positions)),
                    str(len(account.trades)),
                    str(len(self.candidates_by_strategy.get(strategy, []))),
                )
        else:
            acct.add_row("-", "-", "-", "-", "-", "-", "-", "-")

        positions = Table(title="Open Positions", box=box.SIMPLE, expand=True)
        for col in ["Strategy", "Ticker", "Shares", "Entry", "Last", "P/L", "Stop", "Target"]:
            positions.add_column(col, justify="right" if col != "Ticker" else "left")
        has_positions = False
        for strategy, account in accounts.items():
            for ticker, posn in sorted(account.positions.items()):
                has_positions = True
                last = self.prices.get(ticker, posn.entry_price)
                pnl_value = posn.pnl(last)
                pnl_style = "green" if pnl_value >= 0 else "red"
                positions.add_row(
                    strategy_short_label(strategy),
                    ticker,
                    str(posn.shares),
                    f"{posn.entry_price:.2f}",
                    f"{last:.2f}",
                    f"[{pnl_style}]{dollars(pnl_value)}[/{pnl_style}]",
                    f"{posn.stop:.2f}",
                    f"{posn.target:.2f}",
                )
        if not has_positions:
            positions.add_row("-", "-", "-", "-", "-", "-", "-", "-")

        candidates = Table(title="Top Candidates", box=box.SIMPLE, expand=True)
        for col in ["Strategy", "Ticker", "Entry", "Stop", "Target", "ML", "Exp Ret"]:
            candidates.add_column(col, justify="right" if col != "Ticker" else "left")
        has_candidates = False
        candidate_map = self.candidates_by_strategy or {"paper": self.candidates}
        for strategy, strategy_candidates in candidate_map.items():
            for candidate in strategy_candidates[:4]:
                has_candidates = True
                candidates.add_row(
                    strategy_short_label(strategy),
                    candidate.ticker,
                    f"{candidate.entry:.2f}",
                    f"{candidate.stop:.2f}",
                    f"{candidate.target:.2f}",
                    pct(candidate.ml_probability),
                    pct(candidate.expected_return),
                )
        if not has_candidates:
            candidates.add_row("-", "-", "-", "-", "-", "-", "-")

        event_text = Text("\n".join(self.events[-8:]) or "No events yet")
        return Group(
            Panel(clock, title="TradingAgents Paper Runner", border_style="cyan"),
            acct,
            positions,
            candidates,
            Panel(event_text, title="Activity", border_style="green"),
        )

    def _market_window(self) -> str:
        if not self.market_open or not self.market_close:
            return "n/a"
        return f"{self.market_open.strftime('%H:%M')} - {self.market_close.strftime('%H:%M')}"


@dataclass
class Candidate:
    ticker: str
    signal_date: str
    score: float
    entry: float
    target: float
    stop: float
    signal_close: float
    atr: float
    ml_probability: float | None = None
    expected_return: float | None = None
    large_loss_probability: float | None = None
    target_before_stop_probability: float | None = None
    timeout_probability: float | None = None
    ml_pass: bool = True
    rule_pass: bool = False
    gate_status: str = ""
    decision_reason: str = "rule_pass"
    signals: dict[str, Any] = field(default_factory=dict)
    ai_reason: str = ""


@dataclass
class Position:
    ticker: str
    shares: int
    entry_price: float
    stop: float
    target: float
    entry_time: str
    signal_date: str
    score: float
    ml_probability: float | None = None
    expected_return: float | None = None
    atr: float = 0.0
    breakeven_moved: bool = False
    peak_price: float = 0.0
    scans_held: int = 0
    partial_sold: bool = False
    defensive_trimmed: bool = False
    scaled_in: bool = False
    sector: str = "unknown"
    entry_date: str = ""
    funded_by_unsettled: bool = False
    unsettled_settle_date: str = ""

    def market_value(self, price: float) -> float:
        return self.shares * price

    def pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.shares


class PaperAccount:
    def __init__(
        self,
        state_path: Path,
        event_log_path: Path,
        starting_cash: float,
        commission: float,
        reset: bool = False,
        strategy: str = "paper",
        webhook_url: str = "",
        sms_number: str = "",
        sms_on_fills: bool = False,
    ) -> None:
        self.state_path = state_path
        self.event_log_path = event_log_path
        self.starting_cash = float(starting_cash)
        self.commission = float(commission)
        self.cash = float(starting_cash)
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.trades: list[dict[str, Any]] = []
        self.strategy = strategy
        self.webhook_url = webhook_url
        self.sms_number = sms_number
        # Per-fill BUY/SELL texts. Off by default — paper fills would flood SMS.
        # HIL approval texts use a separate path and are unaffected.
        self.sms_on_fills = bool(sms_on_fills)

        self.settled_cash: float = float(starting_cash)
        self.unsettled_cash: float = 0.0
        self.settlement_queue: list[dict] = []
        # GFV — buy unsettled funds, sell new position before funds settle (3 = 90-day ban)
        self.gfv_count: int = 0
        self.gfv_events: list[dict] = []
        self.gfv_restricted: bool = False
        # Freeriding — buy with zero settled cash, fund via sale of same security (instant ban)
        self.freeriding_count: int = 0
        self.freeriding_events: list[dict] = []
        # Cash Liquidation Violation — sell different security same day to cover unsettled purchase (3 = 90-day ban)
        self.clv_count: int = 0
        self.clv_events: list[dict] = []
        # PDT — 4+ day trades in 5 business days with < $25k account
        self.day_trades_today: list[dict] = []
        self.day_trade_history: list[dict] = []
        self.pdt_flagged: bool = False

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)

        journal_path = self.state_path.parent / "trades_journal.db"
        self._journal_conn = _init_journal_db(journal_path)
        self._drift_path = self.state_path.parent / "ml_drift.jsonl"
        self._drift_window: list[dict] = []

        if self.state_path.exists() and not reset:
            self._load()
        else:
            self.save()

    def _load(self) -> None:
        data = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        self.cash = float(data.get("cash", self.starting_cash))
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.positions = {
            ticker: Position(**position)
            for ticker, position in data.get("positions", {}).items()
        }
        self.trades = list(data.get("trades", []))
        self.settled_cash = float(data.get("settled_cash", self.cash))
        self.unsettled_cash = float(data.get("unsettled_cash", 0.0))
        self.settlement_queue = list(data.get("settlement_queue", []))
        self.gfv_count = int(data.get("gfv_count", 0))
        self.gfv_events = list(data.get("gfv_events", []))
        self.gfv_restricted = bool(data.get("gfv_restricted", False))
        self.freeriding_count = int(data.get("freeriding_count", 0))
        self.freeriding_events = list(data.get("freeriding_events", []))
        self.clv_count = int(data.get("clv_count", 0))
        self.clv_events = list(data.get("clv_events", []))
        self.day_trades_today = list(data.get("day_trades_today", []))
        self.day_trade_history = list(data.get("day_trade_history", []))
        self.pdt_flagged = bool(data.get("pdt_flagged", False))

    def save(self) -> None:
        payload = {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "positions": {
                ticker: asdict(position)
                for ticker, position in sorted(self.positions.items())
            },
            "trades": self.trades[-500:],
            "settled_cash": round(self.settled_cash, 2),
            "unsettled_cash": round(self.unsettled_cash, 2),
            "settlement_queue": self.settlement_queue,
            "gfv_count": self.gfv_count,
            "gfv_events": self.gfv_events[-50:],
            "gfv_restricted": self.gfv_restricted,
            "freeriding_count": self.freeriding_count,
            "freeriding_events": self.freeriding_events[-20:],
            "clv_count": self.clv_count,
            "clv_events": self.clv_events[-50:],
            "day_trades_today": self.day_trades_today,
            "day_trade_history": self.day_trade_history[-100:],
            "pdt_flagged": self.pdt_flagged,
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def log_event(self, event: dict[str, Any]) -> None:
        event = {"timestamp": dt.datetime.now().isoformat(), **event}
        with self.event_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_jsonable(event)) + "\n")

    def close(self) -> None:
        try:
            self._journal_conn.close()
        except Exception:
            pass

    def total_value(self, prices: dict[str, float] | None = None) -> float:
        prices = prices or {}
        value = self.cash
        for ticker, position in self.positions.items():
            value += position.market_value(prices.get(ticker, position.entry_price))
        return value

    @staticmethod
    def _next_settle_date(trade_date: dt.date) -> str:
        """Return T+1 settlement date (skip weekends). Fidelity settled to T+1 in 2024."""
        d = trade_date + dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        return d.isoformat()

    def process_settlements(self, today: dt.date) -> None:
        """Move funds from settlement_queue to settled_cash when settle_date <= today."""
        today_str = today.isoformat()
        remaining = []
        settled_amount = 0.0
        for entry in self.settlement_queue:
            if entry["settle_date"] <= today_str:
                settled_amount += entry["amount"]
            else:
                remaining.append(entry)
        if settled_amount > 0:
            self.settled_cash = min(self.cash, self.settled_cash + settled_amount)
            self.unsettled_cash = max(0.0, self.cash - self.settled_cash)
            self.settlement_queue = remaining

        # Purge rolling 12-month windows
        cutoff = (today - dt.timedelta(days=365)).isoformat()
        self.gfv_events = [e for e in self.gfv_events if e.get("date", "") >= cutoff]
        self.gfv_count = len(self.gfv_events)
        self.clv_events = [e for e in self.clv_events if e.get("date", "") >= cutoff]
        self.clv_count = len(self.clv_events)

        # GFV + CLV: 3 in 12 months → cash-only (90-day equivalent)
        self.gfv_restricted = (
            self.freeriding_count > 0  # freeriding = instant permanent restriction
            or self.gfv_count >= 3
            or self.clv_count >= 3
        )

        # PDT: count day trades in last 5 business days
        five_days_ago = (today - dt.timedelta(days=7)).isoformat()
        recent_dt = [d for d in self.day_trade_history if d.get("date", "") >= five_days_ago]
        self.pdt_flagged = len(recent_dt) >= 4

        # Reset today's day trades if date rolled over
        if self.day_trades_today and self.day_trades_today[0].get("date", "") != today_str:
            self.day_trades_today = []

    def _record_violation(self, vtype: str, ticker: str, now: dt.datetime, extra: dict | None = None) -> None:
        today_str = now.strftime("%Y-%m-%d")
        event = {"date": today_str, "time": now.isoformat(), "ticker": ticker, **(extra or {})}

        if vtype == "GFV":
            self.gfv_events.append(event)
            self.gfv_count = len(self.gfv_events)
            restricted = self.gfv_count >= 3
            self.log_event({
                "type": "VIOLATION_GFV",
                "ticker": ticker,
                "gfv_count": self.gfv_count,
                "restricted": restricted,
                "description": "Good Faith Violation: sold position funded by unsettled proceeds before those proceeds settled",
            })
            if restricted and not self.gfv_restricted:
                self.log_event({
                    "type": "RESTRICTION_CASH_ONLY",
                    "reason": "3 Good Faith Violations in 12 months — 90-day cash-only restriction",
                    "gfv_count": self.gfv_count,
                })

        elif vtype == "FREERIDING":
            self.freeriding_events.append(event)
            self.freeriding_count = len(self.freeriding_events)
            self.log_event({
                "type": "VIOLATION_FREERIDING",
                "ticker": ticker,
                "freeriding_count": self.freeriding_count,
                "description": "Freeriding: bought with zero settled cash and sold same position to fund the purchase",
            })
            self.log_event({
                "type": "RESTRICTION_CASH_ONLY",
                "reason": "Freeriding violation — immediate 90-day cash-only restriction (1 strike)",
                "freeriding_count": self.freeriding_count,
            })

        elif vtype == "CLV":
            self.clv_events.append(event)
            self.clv_count = len(self.clv_events)
            restricted = self.clv_count >= 3
            self.log_event({
                "type": "VIOLATION_CLV",
                "ticker": ticker,
                "clv_count": self.clv_count,
                "restricted": restricted,
                "description": "Cash Liquidation Violation: sold a different security same-day to cover purchase made with unsettled funds",
            })
            if restricted and self.clv_count == 3:
                self.log_event({
                    "type": "RESTRICTION_CASH_ONLY",
                    "reason": "3 Cash Liquidation Violations in 12 months — 90-day cash-only restriction",
                    "clv_count": self.clv_count,
                })

        # Recompute restriction flag after recording
        self.gfv_restricted = (
            self.freeriding_count > 0
            or self.gfv_count >= 3
            or self.clv_count >= 3
        )

    def _record_day_trade(self, ticker: str, now: dt.datetime) -> None:
        today_str = now.strftime("%Y-%m-%d")
        entry = {"date": today_str, "time": now.isoformat(), "ticker": ticker}
        self.day_trades_today.append(entry)
        self.day_trade_history.append(entry)
        # Count in rolling 5-business-day window (~7 calendar days)
        five_days_ago = (now.date() - dt.timedelta(days=7)).isoformat()
        recent = [d for d in self.day_trade_history if d.get("date", "") >= five_days_ago]
        was_flagged = self.pdt_flagged
        self.pdt_flagged = len(recent) >= 4
        if self.pdt_flagged and not was_flagged:
            self.log_event({
                "type": "PDT_FLAGGED",
                "day_trades_in_5d": len(recent),
                "description": "Pattern Day Trader flag: 4+ day trades in 5 business days with <$25k account. Requires $25,000 margin minimum.",
            })

    def buy(self, candidate: Candidate, price: float, shares: int, now: dt.datetime) -> None:
        cost = price * shares + self.commission
        if shares <= 0:
            raise ValueError("shares must be positive")
        if cost > self.cash:
            raise ValueError(f"insufficient cash for {candidate.ticker}: need {cost:.2f}")
        # Always require settled cash — never trade with unsettled proceeds.
        # This eliminates GFV, Freeriding, and CLV violations entirely.
        if cost > self.settled_cash:
            raise ValueError(
                f"insufficient settled cash for {candidate.ticker}: "
                f"need ${cost:.2f}, only ${self.settled_cash:.2f} settled "
                f"(${self.unsettled_cash:.2f} pending T+1 settlement)"
            )

        funded_by_unsettled = False
        freeriding_risk = False
        settle_date = ""

        self.cash -= cost
        self.settled_cash -= cost
        self.settled_cash = max(0.0, self.settled_cash)
        self.unsettled_cash = max(0.0, self.cash - self.settled_cash)

        self.positions[candidate.ticker] = Position(
            ticker=candidate.ticker,
            shares=shares,
            entry_price=round(price, 4),
            stop=candidate.stop,
            target=candidate.target,
            entry_time=now.isoformat(),
            signal_date=candidate.signal_date,
            score=candidate.score,
            ml_probability=candidate.ml_probability,
            expected_return=candidate.expected_return,
            atr=candidate.atr,
            breakeven_moved=False,
            entry_date=now.strftime("%Y-%m-%d"),
            funded_by_unsettled=funded_by_unsettled,
            unsettled_settle_date=settle_date,
        )
        self.log_event(
            {
                "type": "BUY",
                "ticker": candidate.ticker,
                "shares": shares,
                "price": round(price, 4),
                "cash_after": round(self.cash, 2),
                "settled_cash": round(self.settled_cash, 2),
                "funded_by_unsettled": funded_by_unsettled,
                "freeriding_risk": freeriding_risk,
                "score": candidate.score,
                "ml_probability": candidate.ml_probability,
                "expected_return": candidate.expected_return,
                "stop": candidate.stop,
                "target": candidate.target,
                "ai_reason": candidate.ai_reason or candidate.signals.get("ai_thesis") or candidate.signals.get("ai_reason") or "",
            }
        )
        self.save()
        if self.webhook_url:
            fire_webhook(self.webhook_url, {
                "type": "BUY", "strategy": self.strategy, "ticker": candidate.ticker,
                "shares": shares, "price": round(price, 4),
                "stop": candidate.stop, "target": candidate.target,
                "ml_prob": candidate.ml_probability,
            })
        if self.sms_number and self.sms_on_fills:
            fire_sms(self.sms_number, f"BUY {candidate.ticker} x{shares} @ ${round(price,2)} [{self.strategy}] stop=${candidate.stop} target=${candidate.target}")

    def add_to_position(self, candidate: Candidate, price: float, shares: int, now: dt.datetime) -> None:
        if shares <= 0:
            raise ValueError("shares must be positive")
        if candidate.ticker not in self.positions:
            self.buy(candidate, price, shares, now)
            return
        cost = price * shares + self.commission
        if cost > self.cash:
            raise ValueError(f"insufficient cash for scale-in {candidate.ticker}: need {cost:.2f}")

        position = self.positions[candidate.ticker]
        old_shares = position.shares
        new_shares = old_shares + shares
        position.entry_price = round(
            ((position.entry_price * old_shares) + (price * shares)) / new_shares,
            4,
        )
        position.shares = new_shares
        position.stop = max(position.stop, candidate.stop)
        position.target = max(position.target, candidate.target)
        position.score = max(position.score, candidate.score)
        position.ml_probability = candidate.ml_probability if candidate.ml_probability is not None else position.ml_probability
        position.expected_return = candidate.expected_return if candidate.expected_return is not None else position.expected_return
        position.atr = candidate.atr or position.atr
        position.scaled_in = True
        self.cash -= cost
        self.log_event({
            "type": "SCALE_IN",
            "ticker": candidate.ticker,
            "shares_added": shares,
            "shares_total": new_shares,
            "price": round(price, 4),
            "avg_entry": position.entry_price,
            "cash_after": round(self.cash, 2),
            "ml_probability": candidate.ml_probability,
        })
        self.save()

    def _check_sell_violation(self, ticker: str, now: dt.datetime) -> str | None:
        """Return a block reason string if sell must be prevented, else None.

        Blocks:
        - Freeriding (1 strike = instant 90-day ban) — always blocked
        - GFV at 2 strikes (3rd would trigger 90-day ban) — blocked
        - CLV at 2 strikes (3rd would trigger 90-day ban) — blocked
        """
        today_str = now.strftime("%Y-%m-%d")
        position = self.positions.get(ticker)
        if position is None:
            return None

        # Freeriding: instant 90-day ban — always block
        if position.funded_by_unsettled and getattr(position, "_freeriding_risk", False):
            if position.unsettled_settle_date and today_str < position.unsettled_settle_date:
                return (
                    f"Freeriding violation (instant 90-day ban). "
                    f"Funds settle {position.unsettled_settle_date}. Hold position."
                )

        # GFV at 2 strikes: 3rd = 90-day ban
        if self.gfv_count >= 2 and position.funded_by_unsettled and position.unsettled_settle_date:
            if today_str < position.unsettled_settle_date:
                return (
                    f"3rd Good Faith Violation would trigger 90-day restriction. "
                    f"Wait until {position.unsettled_settle_date}."
                )

        # CLV at 2 strikes: 3rd = 90-day ban
        if self.clv_count >= 2:
            for open_ticker, open_pos in self.positions.items():
                if open_ticker != ticker and open_pos.funded_by_unsettled and open_pos.entry_date == today_str:
                    if open_pos.unsettled_settle_date and today_str < open_pos.unsettled_settle_date:
                        return (
                            f"3rd Cash Liquidation Violation would trigger 90-day restriction. "
                            f"Wait until {open_pos.unsettled_settle_date}."
                        )
        return None

    def sell_partial(self, ticker: str, price: float, shares: int, reason: str, now: dt.datetime) -> int:
        position = self.positions.get(ticker)
        if position is None or shares <= 0:
            return 0
        shares = min(int(shares), position.shares - 1)
        if shares <= 0:
            return 0
        block_reason = self._check_sell_violation(ticker, now)
        if block_reason:
            self.log_event({"type": "SELL_BLOCKED", "ticker": ticker, "reason": block_reason})
            return 0

        proceeds = price * shares - self.commission
        pnl = (price - position.entry_price) * shares - self.commission
        cost_basis = position.entry_price * shares
        pnl_pct = pnl / cost_basis if cost_basis > 0 else 0.0
        position.shares -= shares
        self.cash += proceeds
        self.realized_pnl += pnl

        today_str = now.strftime("%Y-%m-%d")
        gfv_triggered = False
        freeriding_triggered = False
        clv_triggered = False

        # Day trade: same-day open + partial close
        is_day_trade = position.entry_date == today_str
        if is_day_trade:
            self._record_day_trade(ticker, now)

        # Freeriding: bought with zero settled cash
        if position.funded_by_unsettled and getattr(position, "_freeriding_risk", False):
            if position.unsettled_settle_date and today_str < position.unsettled_settle_date:
                freeriding_triggered = True
                self._record_violation("FREERIDING", ticker, now)

        # GFV: bought with unsettled funds, partial sell before settlement
        elif position.funded_by_unsettled and position.unsettled_settle_date:
            if today_str < position.unsettled_settle_date:
                gfv_triggered = True
                self._record_violation("GFV", ticker, now)

        # CLV: selling different ticker same-day to cover unsettled purchase
        for open_ticker, open_pos in self.positions.items():
            if open_ticker != ticker and open_pos.funded_by_unsettled and open_pos.entry_date == today_str:
                if open_pos.unsettled_settle_date and today_str < open_pos.unsettled_settle_date:
                    clv_triggered = True
                    self._record_violation("CLV", ticker, now, {"covered_ticker": open_ticker})
                    break

        # Partial proceeds → settlement queue
        settle_date = self._next_settle_date(now.date())
        if proceeds > 0:
            self.settlement_queue.append({
                "amount": round(proceeds, 2),
                "settle_date": settle_date,
                "ticker": ticker,
            })
            self.unsettled_cash = min(self.cash, self.unsettled_cash + proceeds)
            self.settled_cash = max(0.0, self.cash - self.unsettled_cash)

        self.trades.append({
            "ticker": ticker,
            "shares": shares,
            "entry_time": position.entry_time,
            "exit_time": now.isoformat(),
            "entry_price": round(position.entry_price, 4),
            "exit_price": round(price, 4),
            "stop": position.stop,
            "target": position.target,
            "exit_reason": reason,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "score": position.score,
            "ml_probability": position.ml_probability,
            "expected_return": position.expected_return,
            "partial": True,
            "day_trade": is_day_trade,
            "gfv": gfv_triggered,
            "freeriding": freeriding_triggered,
            "clv": clv_triggered,
        })
        self.log_event({
            "type": reason,
            "ticker": ticker,
            "shares_sold": shares,
            "shares_kept": position.shares,
            "price": round(price, 4),
            "pnl": round(pnl, 2),
            "cash_after": round(self.cash, 2),
            "settled_cash": round(self.settled_cash, 2),
            "proceeds_settle_date": settle_date,
            "day_trade": is_day_trade,
            "gfv": gfv_triggered,
            "freeriding": freeriding_triggered,
            "clv": clv_triggered,
        })
        self.save()
        return shares

    def sell(self, ticker: str, price: float, reason: str, now: dt.datetime) -> None:
        block_reason = self._check_sell_violation(ticker, now)
        if block_reason:
            self.log_event({"type": "SELL_BLOCKED", "ticker": ticker, "reason": block_reason})
            return
        position = self.positions.pop(ticker)
        proceeds = price * position.shares - self.commission
        pnl = (price - position.entry_price) * position.shares - (2 * self.commission)
        cost_basis = position.entry_price * position.shares + self.commission
        pnl_pct = pnl / cost_basis if cost_basis > 0 else 0.0
        self.cash += proceeds
        self.realized_pnl += pnl

        today_str = now.strftime("%Y-%m-%d")
        gfv_triggered = False
        freeriding_triggered = False
        clv_triggered = False

        # Day trade detection: same-day buy + sell
        is_day_trade = position.entry_date == today_str
        if is_day_trade:
            self._record_day_trade(ticker, now)

        # Freeriding: bought with zero settled cash → selling same position to fund itself
        if position.funded_by_unsettled and getattr(position, "_freeriding_risk", False):
            if position.unsettled_settle_date and today_str < position.unsettled_settle_date:
                freeriding_triggered = True
                self._record_violation("FREERIDING", ticker, now)

        # GFV: bought with unsettled funds (not freeriding), sell before they settle
        elif position.funded_by_unsettled and position.unsettled_settle_date:
            if today_str < position.unsettled_settle_date:
                gfv_triggered = True
                self._record_violation("GFV", ticker, now)

        # CLV: selling a DIFFERENT ticker today to cover unsettled purchase cost
        # (detected when this sell is same-day but NOT the funded position itself)
        # Check if any open positions were funded by unsettled funds from today
        for open_ticker, open_pos in self.positions.items():
            if open_ticker != ticker and open_pos.funded_by_unsettled and open_pos.entry_date == today_str:
                if open_pos.unsettled_settle_date and today_str < open_pos.unsettled_settle_date:
                    clv_triggered = True
                    self._record_violation("CLV", ticker, now, {"covered_ticker": open_ticker})
                    break

        # Proceeds → settlement queue (T+1)
        settle_date = self._next_settle_date(now.date())
        if proceeds > 0:
            self.settlement_queue.append({
                "amount": round(proceeds, 2),
                "settle_date": settle_date,
                "ticker": ticker,
            })
            self.unsettled_cash = min(self.cash, self.unsettled_cash + proceeds)
            self.settled_cash = max(0.0, self.cash - self.unsettled_cash)

        trade = {
            "ticker": ticker,
            "shares": position.shares,
            "entry_time": position.entry_time,
            "exit_time": now.isoformat(),
            "entry_price": round(position.entry_price, 4),
            "exit_price": round(price, 4),
            "stop": position.stop,
            "target": position.target,
            "exit_reason": reason,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "score": position.score,
            "ml_probability": position.ml_probability,
            "expected_return": position.expected_return,
            "day_trade": is_day_trade,
            "gfv": gfv_triggered,
            "freeriding": freeriding_triggered,
            "clv": clv_triggered,
        }
        self.trades.append(trade)
        self.log_event({
            "type": "SELL", **trade,
            "cash_after": round(self.cash, 2),
            "settled_cash": round(self.settled_cash, 2),
            "proceeds_settle_date": settle_date,
        })
        self.save()

        # SQLite journal
        try:
            self._journal_conn.execute(
                "INSERT INTO trades (strategy,ticker,shares,entry_time,exit_time,entry_price,exit_price,"
                "exit_reason,pnl,pnl_pct,score,ml_probability,expected_return) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.strategy, ticker, position.shares, position.entry_time, now.isoformat(),
                 position.entry_price, round(price, 4), reason,
                 round(pnl, 2), round(pnl_pct, 4), position.score,
                 position.ml_probability, position.expected_return),
            )
            self._journal_conn.commit()
        except Exception:
            pass

        # ML drift tracking
        if position.ml_probability is not None:
            won = pnl > 0
            entry = {"ml_prob": round(position.ml_probability, 4), "won": int(won), "t": now.isoformat()}
            self._drift_window.append(entry)
            if len(self._drift_window) > 20:
                self._drift_window = self._drift_window[-20:]
            try:
                with self._drift_path.open("a", encoding="utf-8") as _df:
                    _df.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            if len(self._drift_window) >= 10:
                avg_pred = sum(r["ml_prob"] for r in self._drift_window) / len(self._drift_window)
                avg_actual = sum(r["won"] for r in self._drift_window) / len(self._drift_window)
                drift = abs(avg_pred - avg_actual)
                drift_summary = {"predicted_win_rate": round(avg_pred, 4), "actual_win_rate": round(avg_actual, 4),
                                 "drift": round(drift, 4), "n": len(self._drift_window), "t": now.isoformat()}
                try:
                    (self.state_path.parent / "ml_drift.json").write_text(json.dumps(drift_summary, indent=2))
                except Exception:
                    pass
                if drift > 0.15:
                    self.log_event({"type": "ML_DRIFT_ALERT", **drift_summary})

        # Webhook
        if self.webhook_url:
            fire_webhook(self.webhook_url, {
                "type": "SELL", "strategy": self.strategy, "ticker": ticker,
                "shares": position.shares, "exit_price": round(price, 4),
                "pnl": round(pnl, 2), "pnl_pct": f"{pnl_pct*100:.2f}%", "reason": reason,
            })
        if self.sms_number and self.sms_on_fills:
            sign = "+" if pnl >= 0 else ""
            fire_sms(self.sms_number, f"SELL {ticker} x{position.shares} @ ${round(price,2)} P/L:{sign}${round(pnl,2)} ({reason}) [{self.strategy}]")


def create_strategy_accounts(
    output_dir: Path,
    starting_cash: float,
    commission: float,
    reset: bool,
    webhook_url: str = "",
    sms_number: str = "",
    sms_on_fills: bool = False,
) -> dict[str, PaperAccount]:
    accounts: dict[str, PaperAccount] = {}
    previous_dir = None if reset else latest_previous_account_dir(output_dir)
    for strategy in STRATEGY_LABELS:
        strategy_dir = output_dir / strategy
        state_path = strategy_dir / "state.json"
        previous_state_path = previous_dir / strategy / "state.json" if previous_dir else None
        should_carry_forward = bool(previous_state_path and previous_state_path.exists() and not state_path.exists())
        if should_carry_forward:
            previous_state = json.loads(previous_state_path.read_text(encoding="utf-8") or "{}")
            starting_cash_for_account = float(previous_state.get("starting_cash", starting_cash))
        else:
            starting_cash_for_account = starting_cash
        accounts[strategy] = PaperAccount(
            state_path=state_path,
            event_log_path=strategy_dir / "events.jsonl",
            starting_cash=starting_cash_for_account,
            commission=commission,
            reset=reset,
            strategy=strategy,
            webhook_url=webhook_url,
            sms_number=sms_number,
            sms_on_fills=sms_on_fills,
        )
        if should_carry_forward and previous_state_path:
            previous_state = json.loads(previous_state_path.read_text(encoding="utf-8") or "{}")
            accounts[strategy].cash = float(previous_state.get("cash", accounts[strategy].cash))
            accounts[strategy].realized_pnl = float(previous_state.get("realized_pnl", accounts[strategy].realized_pnl))
            accounts[strategy].positions = {
                ticker: Position(**position)
                for ticker, position in previous_state.get("positions", {}).items()
            }
            accounts[strategy].trades = list(previous_state.get("trades", []))
            accounts[strategy].save()
            accounts[strategy].log_event({
                "type": "CARRY_FORWARD",
                "from": str(previous_state_path),
                "cash": round(accounts[strategy].cash, 2),
                "positions": len(accounts[strategy].positions),
            })
    return accounts


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Paper trade four accounts for today: algorithm-only, "
            "machine-learning-only, algorithm+ML, and OpenRouter Pure AI, "
            "using live Yahoo prices."
        )
    )
    parser.add_argument("--tickers", default="all_tickers.txt", help="Ticker file to scan.")
    parser.add_argument("--starting-cash", type=float, default=10000.0, help="Paper cash for today's account.")
    parser.add_argument("--scan-interval-minutes", type=float, default=15.0, help="Live scan cadence.")
    parser.add_argument("--output-dir", default="tmp/paper_trading_today", help="Directory for state/logs.")
    parser.add_argument("--model-bundle", default="ml_models/latest/model_bundle.joblib", help="Path to model_bundle.joblib.")
    parser.add_argument("--new-model-bundle", default=None, help="Optional challenger model_bundle.joblib for separate ML New account. Never replaces --model-bundle.")
    parser.add_argument("--no-ml", action="store_true", help="Run the rule algorithm without the saved ML gate.")
    parser.add_argument("--ml-probability-threshold", type=float, default=0.51,
                        help="Override ML win-probability threshold from bundle (default: use bundle value, typically 0.51).")
    parser.add_argument("--ml-large-loss-max", type=float, default=None,
                        help="Override max large-loss probability from bundle (default: use bundle value, typically 0.20).")
    parser.add_argument("--ml-expected-return-min", type=float, default=None,
                        help="Override minimum expected return from bundle (default: use bundle value, typically 0.0).")
    parser.add_argument("--max-ml-candidates", type=int, default=200,
                        help="Keep only this many top-ranked ML-only candidates per ML account after scoring. 0 = unlimited.")
    parser.add_argument(
        "--ml-algo-only",
        action="store_true",
        help="Explicit no-news mode. Keeps the algorithm, ML, combined, and Pure AI paper accounts.",
    )
    parser.add_argument(
        "--no-news",
        action="store_true",
        help="Alias for clarity. News is already disabled in this paper runner.",
    )
    parser.add_argument("--no-ai", action="store_true", help="Disable the OpenRouter Pure AI account.")
    parser.add_argument("--openrouter-model", default="openai/gpt-4o-mini", help="OpenRouter model for the Pure AI account.")
    parser.add_argument("--ai-shortlist-size", type=int, default=30, help="Number of market snapshots to send to the Pure AI agent.")
    parser.add_argument("--ai-max-picks", type=int, default=5, help="Maximum Pure AI trade candidates to accept.")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable the live terminal dashboard.")
    parser.add_argument("--once", action="store_true", help="Run one live scan cycle and exit.")
    parser.add_argument("--force", action="store_true", help="Allow one-off scans outside regular market hours.")
    parser.add_argument("--reset", action="store_true", help="Reset today's paper account to starting cash.")
    parser.add_argument("--max-tickers", type=int, default=0, help="Limit tickers for a quick test. 0 means all.")
    parser.add_argument("--batch-size", type=int, default=100, help="Daily history download batch size.")
    parser.add_argument("--price-batch-size", type=int, default=200, help="Live price download batch size.")
    parser.add_argument("--lookback-days", type=int, default=620, help="Calendar days of daily history to download.")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark used by the gate.")
    parser.add_argument("--min-price", type=float, default=15.0)
    parser.add_argument("--max-price", type=float, default=100.0)
    parser.add_argument("--threshold", type=float, default=100.0)
    parser.add_argument("--allow-near-miss-rule-candidates", action=argparse.BooleanOptionalAction, default=True,
                        help="Allow Algorithm/Combined/Long Hold candidates that pass hard safety gates but miss a few soft confirmation gates.")
    parser.add_argument("--near-miss-max-soft-failures", type=int, default=3,
                        help="Max soft confirmed-pullback gate misses allowed when near-miss candidates are enabled.")
    parser.add_argument("--target-mult", type=float, default=0.75)
    parser.add_argument("--stop-mult", type=float, default=1.0)
    parser.add_argument("--max-hold-days", type=int, default=14,
                        help="Time-stop: exit confirmed_pullback positions after this many "
                             "calendar days (~10 trading days). Validated optimum.")
    parser.add_argument("--hold-overnight", action=argparse.BooleanOptionalAction, default=True,
                        help="Carry paper positions overnight instead of end-of-day flattening. "
                             "Default true to avoid day-trade/PDT churn; use --no-hold-overnight "
                             "to restore intraday-only behavior.")
    parser.add_argument("--position-cap-pct", type=float, default=25.0, help="Max account %% per position at high confidence.")
    parser.add_argument("--position-cap-min-pct", type=float, default=10.0, help="Min account %% per position at ML threshold confidence.")
    parser.add_argument("--position-high-confidence-threshold", type=float, default=0.80, help="ML probability at which position reaches full size (position-cap-pct).")
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.0, help="Risk this %% of account per trade via ATR-based sizing. e.g. 1.0 = risk 1%% of account. 0=disabled, use cap-pct.")
    parser.add_argument("--min-risk-reward", type=float, default=0.6, help="Skip entry if live R:R (target-price)/(price-stop) falls below this. Default 0.6 (validated config runs target 0.75 / stop 1.0 ATR = 0.75 R:R; a 1.0 floor would block every entry).")
    parser.add_argument("--bear-regime-size-factor", type=float, default=0.5, help="Multiply position size by this in bear/sell regime (0–1). Default 0.5.")
    parser.add_argument("--neutral-regime-size-factor", type=float, default=0.75, help="Multiply position size by this in neutral regime (0–1). Default 0.75.")
    parser.add_argument("--take-profit-pct", type=float, default=0.0, help="Percentage-based take-profit override (e.g. 2.5 = exit at +2.5%%). 0 = use ATR-based target.")
    parser.add_argument("--stop-loss-pct", type=float, default=0.0, help="Percentage-based stop-loss override (e.g. 1.5 = exit at -1.5%%). 0 = use ATR-based stop.")
    parser.add_argument("--partial-profit-pct", type=float, default=0.5,
        help="Trigger partial sell when price reaches this fraction of the way to target (0=disabled).")
    parser.add_argument("--partial-profit-fraction", type=float, default=0.5,
        help="Fraction of shares to sell at partial-profit trigger (default 0.5 = half).")
    parser.add_argument("--trade-fidelity", action="store_true", help="Send approved Combined candidates to Fidelity.")
    parser.add_argument("--trade-fidelity-execute", action="store_true", help="If --trade-fidelity is enabled, actually PLACE the order (live money).")
    parser.add_argument("--defensive-trim-buffer-pct", type=float, default=35.0,
        help="Trim before stop when price falls into this %% of the entry-stop distance above stop. 0=disabled.")
    parser.add_argument("--defensive-trim-fraction", type=float, default=0.5,
        help="Fraction of shares to defensively trim before stop (default 0.5 = half).")
    parser.add_argument("--early-stop-buffer-pct", type=float, default=15.0,
        help="Exit before hard stop when price falls into this %% of the entry-stop distance above stop. 0=disabled.")
    parser.add_argument("--scale-in-min-probability", type=float, default=0.55,
        help="Minimum ML probability required to add shares to an existing winner.")
    parser.add_argument("--scale-in-trigger-atr", type=float, default=0.5,
        help="Add shares after price moves this many ATRs above average entry. 0 allows immediate scale-in.")
    parser.add_argument("--scale-in-add-pct", type=float, default=5.0,
        help="Max account %% to add on a scale-in, still capped by position-cap-pct. 0=disabled.")
    parser.add_argument("--trailing-stop-atr-mult", type=float, default=0.5,
        help="After breakeven, trail stop this many ATR below peak price (0=disable).")
    parser.add_argument("--time-decay-scans", type=int, default=0,
        help="Exit position after this many scan cycles. 0=disabled.")
    parser.add_argument("--max-heat-pct", type=float, default=80.0,
        help="Max %% of account value to have deployed in open positions at once. 0=disabled.")
    parser.add_argument("--double-target-exit-pct", type=float, default=0.5,
        help="Sell this fraction when price reaches 2x the initial target gain. 0=disabled.")
    parser.add_argument("--sector-max-positions", type=int, default=3,
        help="Max open positions in same GICS sector. 0=disabled.")
    parser.add_argument("--daily-loss-limit-pct", type=float, default=2.0,
        help="Stop new entries if today realized PnL lost this %% of starting cash. 0=disabled.")
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--sms-on-fills", dest="sms_on_fills", action="store_true", default=False,
                        help="Text every paper BUY/SELL fill. Off by default (would flood SMS).")
    parser.add_argument("--hil-timeout-minutes", type=int, default=15,
                        help="Minutes to wait for SMS HIL approval before timing out.")
    parser.add_argument("--hil-auto-reject", dest="hil_auto_reject", action="store_true", default=True,
                        help="On HIL timeout, reject the trade (default, safe).")
    parser.add_argument("--hil-auto-approve", dest="hil_auto_reject", action="store_false",
                        help="On HIL timeout, auto-approve the trade instead of rejecting.")
    parser.add_argument("--commission", type=float, default=0.0)
    parser.add_argument("--max-entry-extension-atr", type=float, default=0.7, help="Skip entries extended this many ATR above signal close.")
    parser.add_argument("--breadth-threshold", type=float, default=0.40, help="Skip new entries when market breadth (fraction above 50d SMA) is below this.")
    parser.add_argument("--max-portfolio-drawdown", type=float, default=0.05, help="Flatten all positions if account drawdown exceeds this fraction (e.g. 0.05 = 5%%). 0 = disabled.")
    parser.add_argument("--webhook-url", default="", help="Discord or Slack webhook URL for trade notifications.")
    parser.add_argument("--sms-number", default="", help="Phone number for SMS trade alerts via the primary provider, TextBelt by default (e.g. +16145078688).")
    parser.add_argument("--alert-candidates", action="store_true", help="Text candidate tickers (with chart links) as soon as they are found, not just on executed trades. Uses the primary SMS provider (TextBelt).")
    parser.add_argument("--alert-strategies", default="long_hold", help="Comma-separated strategies whose candidates trigger SMS alerts (default: long_hold).")
    parser.add_argument("--min-avg-volume", type=int, default=500_000, help="Skip tickers with 20-day avg volume below this. 0 = disabled.")
    parser.add_argument("--long-hold-days", type=int, default=20, help="Max hold period (calendar days) for the Long Hold strategy.")
    parser.add_argument("--timezone", default="America/New_York")
    return parser.parse_args()


def today_dir(base: Path, trade_date: dt.date) -> Path:
    return base / trade_date.strftime("%Y%m%d")


def has_strategy_state(path: Path) -> bool:
    return any((path / strategy / "state.json").exists() for strategy in STRATEGY_LABELS)


def latest_previous_account_dir(output_dir: Path) -> Path | None:
    base = output_dir.parent
    if not base.exists():
        return None
    dated = [
        path
        for path in base.iterdir()
        if path.is_dir()
        and path.name.isdigit()
        and len(path.name) == 8
        and path.name < output_dir.name
        and has_strategy_state(path)
    ]
    return sorted(dated, key=lambda path: path.name)[-1] if dated else None


def market_times(day: dt.date, tz: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    open_time = dt.datetime.combine(day, dt.time(9, 30), tzinfo=tz)
    close_time = dt.datetime.combine(day, dt.time(16, 0), tzinfo=tz)
    return open_time, close_time


def is_regular_market_day(day: dt.date) -> bool:
    return day.weekday() < 5


def seconds_until_next_scan(now: dt.datetime, interval_minutes: float) -> float:
    interval = max(1, int(interval_minutes * 60))
    epoch = int(now.timestamp())
    return float(interval - (epoch % interval))


def choose_model_path(user_path: str | None) -> Path | None:
    if user_path:
        return Path(user_path)
    for path in DEFAULT_MODEL_PATHS:
        if path.exists():
            return path
    return None


def load_model_bundle(path: Path | None, disabled: bool) -> dict[str, Any] | None:
    if disabled:
        print("ML gate disabled by --no-ml.")
        return None
    if path is None:
        raise SystemExit(
            "No ML model bundle found. Use --model-bundle PATH or --no-ml."
        )
    if not path.exists():
        raise SystemExit(f"ML model bundle not found: {path}")
    import joblib

    print(f"Loading ML gate: {path}")
    return joblib.load(path)


def load_openrouter_api_key() -> str | None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env", override=False)
    return os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY")


_circuit_breaker_failures: int = 0
_circuit_breaker_open_until: float = 0.0
_CB_THRESHOLD = 5
_CB_PAUSE_SECS = 60
_sector_cache: dict[str, str] = {}


def _yf_download_with_retry(max_retries: int = 3, **kwargs):
    """yf.download with exponential backoff retries and a circuit breaker."""
    global _circuit_breaker_failures, _circuit_breaker_open_until
    now = time.monotonic()
    if _circuit_breaker_open_until > now:
        remaining = int(_circuit_breaker_open_until - now)
        raise RuntimeError(f"yfinance circuit breaker open for {remaining}s (too many consecutive failures)")
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            result = yf.download(**kwargs)
            _circuit_breaker_failures = 0
            return result
        except Exception as exc:
            last_exc = exc
            _circuit_breaker_failures += 1
            if _circuit_breaker_failures >= _CB_THRESHOLD:
                _circuit_breaker_open_until = time.monotonic() + _CB_PAUSE_SECS
                raise RuntimeError(f"yfinance circuit breaker opened after {_CB_THRESHOLD} failures: {exc}") from exc
            wait = 2 ** attempt
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def download_daily_history(
    tickers: list[str],
    start: dt.date,
    end: dt.date,
    batch_size: int,
    dashboard: TerminalDashboard | None = None,
) -> dict[str, pd.DataFrame]:
    all_data: dict[str, pd.DataFrame] = {}
    batches = [tickers[i : i + batch_size] for i in range(0, len(tickers), batch_size)]
    msg = f"Downloading daily history for {len(tickers)} symbols in {len(batches)} batches"
    if dashboard:
        dashboard.event(msg + "...")
    else:
        print(msg + "...")
    for batch_num, batch in enumerate(batches, start=1):
        try:
            if dashboard:
                dashboard.update(phase=f"Daily history batch {batch_num}/{len(batches)}")
            raw = _yf_download_with_retry(
                tickers=batch,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            all_data.update(_extract_ticker_dfs(raw, batch))
            if dashboard:
                dashboard.event(
                    f"Daily batch {batch_num}/{len(batches)} complete; "
                    f"{len(all_data)} symbols loaded"
                )
        except Exception as exc:
            msg = f"daily batch {batch_num}/{len(batches)} failed: {exc}"
            if dashboard:
                dashboard.event(msg)
            else:
                print(f"  {msg}")
    return all_data


def download_index_close(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame | None:
    try:
        raw = _yf_download_with_retry(
            tickers=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return raw[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in raw.columns]].dropna(subset=["Close"])
    except Exception:
        return None


def clean_daily_frame(df: pd.DataFrame, trade_date: dt.date) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out = out.sort_index()
    out = out[out.index.date < trade_date]
    out = out[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in out.columns]]
    return out.dropna(subset=["Close"])


def regime_value(series: pd.Series | None, as_of: pd.Timestamp, default: str = "unknown") -> str:
    if series is None or series.empty:
        return default
    pos = series.index.searchsorted(as_of, side="right") - 1
    if pos < 0:
        return default
    value = series.iloc[pos]
    return default if pd.isna(value) else str(value)


def numeric_series_value(series: pd.Series | None, as_of: pd.Timestamp) -> float | None:
    if series is None or series.empty:
        return None
    pos = series.index.searchsorted(as_of, side="right") - 1
    if pos < 0:
        return None
    value = series.iloc[pos]
    return None if pd.isna(value) else float(value)


def spy_gate_values(spy_df: pd.DataFrame | None, as_of: pd.Timestamp) -> dict[str, float | None]:
    values = {
        "spy_close": None,
        "spy_sma50": None,
        "spy_sma200": None,
        "spy_ret1": None,
        "spy_ret5": None,
        "spy_ret20": None,
    }
    if spy_df is None or spy_df.empty:
        return values
    pos = spy_df.index.searchsorted(as_of, side="right") - 1
    if pos < 200:
        return values
    close = float(spy_df["Close"].iloc[pos])
    values["spy_close"] = close
    values["spy_sma50"] = float(spy_df["Close"].iloc[pos - 49 : pos + 1].mean())
    values["spy_sma200"] = float(spy_df["Close"].iloc[pos - 199 : pos + 1].mean())
    values["spy_ret1"] = float(close / spy_df["Close"].iloc[pos - 1] - 1) if pos >= 1 else None
    values["spy_ret5"] = float(close / spy_df["Close"].iloc[pos - 5] - 1) if pos >= 5 else None
    values["spy_ret20"] = float(close / spy_df["Close"].iloc[pos - 20] - 1) if pos >= 20 else None
    return values


def predict_ml(row: dict[str, Any], bundle: dict[str, Any],
               ml_prob_threshold: float | None = None,
               ml_large_loss_max: float | None = None,
               ml_expected_return_min: float | None = None) -> dict[str, Any]:
    numeric = bundle.get("numeric_features", [])
    categorical = bundle.get("categorical_features", [])
    feature_names = bundle.get("feature_names")
    imputer = bundle.get("imputer")
    models = bundle.get("models", {})
    thresholds = bundle.get("thresholds", {})

    frame = pd.DataFrame([row])

    if "rsi14" in frame.columns and "rsi9" in frame.columns:
        frame["rsi_spread"] = pd.to_numeric(frame["rsi14"], errors="coerce") - pd.to_numeric(frame["rsi9"], errors="coerce")
    if "macd_hist" in frame.columns and "macd_hist_prev2" in frame.columns:
        frame["macd_hist_slope3"] = pd.to_numeric(frame["macd_hist"], errors="coerce") - pd.to_numeric(frame["macd_hist_prev2"], errors="coerce")
    if "vol_ratio_10d" in frame.columns and "vol_ratio_20d" in frame.columns:
        v10 = pd.to_numeric(frame["vol_ratio_10d"], errors="coerce")
        v20 = pd.to_numeric(frame["vol_ratio_20d"], errors="coerce")
        frame["vol_accel"] = v10 / v20.replace(0, np.nan)
    entry_price = pd.to_numeric(frame.get("entry", pd.Series([None])), errors="coerce")
    if "sma50" in frame.columns and not entry_price.isna().all():
        frame["above_sma50"] = (entry_price > pd.to_numeric(frame["sma50"], errors="coerce")).astype(float)
    if "sma200" in frame.columns and not entry_price.isna().all():
        frame["above_sma200"] = (entry_price > pd.to_numeric(frame["sma200"], errors="coerce")).astype(float)
    if "vol_ratio_10d" in frame.columns:
        frame["vol_dryup"] = (pd.to_numeric(frame["vol_ratio_10d"], errors="coerce") < 1.0).astype(float)
    if "rsi9_slope3" in frame.columns:
        frame["rsi_recovering"] = (pd.to_numeric(frame["rsi9_slope3"], errors="coerce") > 0).astype(float)

    x, _ = _ml_design_matrix(frame, numeric, categorical, feature_names)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        x_imp = imputer.transform(x) if imputer is not None else x

    def class_prob(model_key: str) -> float | None:
        model = models.get(model_key)
        if model is None:
            return None
        if not hasattr(model, "predict_proba"):
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            probs = model.predict_proba(x_imp)
        if probs.shape[1] == 1:
            return float(probs[0, 0])
        return float(probs[0, 1])

    win_prob = class_prob("win_probability")
    loss_prob = class_prob("large_loss_probability")
    target_prob = class_prob("target_before_stop_probability")
    timeout_prob = class_prob("timeout_probability")

    expected_return = None
    ret_model = models.get("expected_return")
    if ret_model is not None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            expected_return = float(ret_model.predict(x_imp)[0])

    ml_probability_threshold = ml_prob_threshold if ml_prob_threshold is not None else float(thresholds.get("ml_probability_threshold", 0.58))
    ml_expected_return_min   = ml_expected_return_min if ml_expected_return_min is not None else float(thresholds.get("ml_expected_return_min", 0.0))
    ml_large_loss_max        = ml_large_loss_max if ml_large_loss_max is not None else float(thresholds.get("ml_large_loss_max", 0.20))

    failed = []
    if win_prob is None or win_prob < ml_probability_threshold:
        failed.append(f"ml_probability_below_{ml_probability_threshold:.2f}")
    if expected_return is None or expected_return <= ml_expected_return_min:
        failed.append("expected_return_not_positive")
    if loss_prob is not None and loss_prob > ml_large_loss_max:
        failed.append(f"large_loss_probability_above_{ml_large_loss_max:.2f}")

    return {
        "ml_probability": win_prob,
        "expected_return": expected_return,
        "large_loss_probability": loss_prob,
        "target_before_stop_probability": target_prob,
        "timeout_probability": timeout_prob,
        "ml_pass": not failed,
        "decision_reason": "rule_pass_and_ml_pass" if not failed else "ml_gate_failed:" + ",".join(failed),
    }


# ML Old runs the stock_universe model, whose live win-prob scale tops out
# ~0.52 — a different calibration from the latest model. It needs its own low
# bar or it never trades. Kept separate from the latest-model regime scheme.
ML_OLD_THRESHOLD = 0.51


def regime_ml_threshold(spy_regime: str | None, vix_regime: str | None) -> float:
    """Regime-aware ML gate for the *latest* model. Its live win-prob maxes
    ~0.69, so the old 0.72 bar was unreachable. Use 0.63 in bull/calm tape
    (top ~10%, high precision) and relax to 0.58 in bear/neutral or high-vol so
    ML still trades when the tape is weaker."""
    spy = (spy_regime or "").lower()
    vix = (vix_regime or "").lower()
    if spy == "bull" and vix in ("low_vol", "normal"):
        return 0.63
    return 0.58


def ai_shortlist_sort_key(candidate: Candidate) -> tuple:
    signals = candidate.signals or {}
    return (
        float(signals.get("dollar_vol20") or 0.0),
        float(signals.get("rel_ret20_vs_spy") or -9.0),
        -abs(float(signals.get("rsi9") or 50.0) - 50.0),
        float(candidate.score or 0.0),
    )


def compact_ai_snapshot(candidate: Candidate) -> dict[str, Any]:
    signals = candidate.signals or {}
    return {
        "ticker": candidate.ticker,
        "last_close": round(candidate.signal_close, 2),
        "algorithm_score": round(candidate.score, 2),
        "rule_gate_status": candidate.gate_status,
        "entry_hint": round(candidate.entry, 2),
        "stop_hint": round(candidate.stop, 2),
        "target_hint": round(candidate.target, 2),
        "atr": round(candidate.atr, 3),
        "atr_pct": signals.get("atr_pct"),
        "risk_reward_hint": signals.get("risk_reward"),
        "rsi9": signals.get("rsi9"),
        "rsi14": signals.get("rsi14"),
        "macd_hist": signals.get("macd_hist"),
        "ret_1d": signals.get("ret_1d"),
        "ret_5d": signals.get("ret_5d"),
        "ret_20d": signals.get("ret_20d"),
        "rel_ret20_vs_spy": signals.get("rel_ret20_vs_spy"),
        "vol_ratio_20d": signals.get("vol_ratio_20d"),
        "dollar_vol20": signals.get("dollar_vol20"),
        "pct_from_10d_high": signals.get("pct_from_10d_high"),
        "pct_from_52w_high": signals.get("pct_from_52w_high"),
        "sma200": signals.get("sma200"),
        "sma200_rising_20d": signals.get("sma200_rising_20d"),
        "spy_ret5": signals.get("spy_ret5"),
        "spy_ret20": signals.get("spy_ret20"),
        "vix_regime_features": {
            "vix_ts": signals.get("vix_ts"),
            "sector_breadth": signals.get("sector_breadth"),
        },
    }


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_openrouter_ai(
    args: argparse.Namespace,
    snapshots: list[dict[str, Any]],
    dashboard: TerminalDashboard | None = None,
) -> dict[str, Any] | None:
    api_key = load_openrouter_api_key()
    if not api_key:
        if dashboard:
            dashboard.event("Pure AI skipped: set OPENROUTER_API_KEY to enable it")
        return None

    system_prompt = (
        "You are a cautious intraday paper-trading agent. You only use the "
        "market snapshots provided by the user. Do not use news, rumors, or "
        "outside knowledge. Choose only liquid US stock setups for today's "
        "paper-trading session. Prefer trades with clear asymmetric risk. "
        "Return JSON only."
    )
    user_prompt = {
        "task": "Choose intraday long paper-trade candidates for today.",
        "rules": [
            "Return at most ai_max_picks picks.",
            "Every pick must have ticker, entry, stop, target, confidence, and thesis.",
            "Use action='buy' for picks; omit or skip weak names.",
            "Require target > entry > stop and reward/risk >= 1.0.",
            "These are paper trades only; no broker orders.",
        ],
        "ai_max_picks": args.ai_max_picks,
        "snapshots": snapshots,
        "response_schema": {
            "picks": [
                {
                    "ticker": "AAPL",
                    "action": "buy",
                    "entry": 123.45,
                    "stop": 121.0,
                    "target": 127.0,
                    "confidence": 0.62,
                    "thesis": "short reason using only provided data",
                }
            ]
        },
    }
    payload = {
        "model": args.openrouter_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, default=_jsonable)},
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost/tradingagents-paper-runner",
        "X-Title": "TradingAgents Paper Runner",
    }
    try:
        if dashboard:
            dashboard.event(f"Asking OpenRouter Pure AI agent ({args.openrouter_model}) for picks")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        record_openrouter_request("paper_pure_ai")
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
        return extract_json_object(str(content))
    except Exception as exc:
        if dashboard:
            dashboard.event(f"Pure AI OpenRouter call failed: {exc}")
        else:
            print(f"Pure AI OpenRouter call failed: {exc}")
        return None


def generate_candidate_reasons(
    api_key: str,
    model: str,
    candidates: list["Candidate"],
    dashboard: "TerminalDashboard | None" = None,
) -> dict[str, str]:
    """Call OpenRouter once to get a 1-2 sentence AI thesis for each candidate."""
    if not api_key or not candidates:
        return {}
    cap = candidates[:25]
    snapshots = []
    for c in cap:
        sig = c.signals or {}
        snapshots.append({
            "ticker": c.ticker,
            "algo_score": round(c.score, 2),
            "ml_win_prob": round(c.ml_probability, 3) if c.ml_probability is not None else None,
            "ml_exp_return": round(c.expected_return, 4) if c.expected_return is not None else None,
            "rsi9": sig.get("rsi9"),
            "rsi14": sig.get("rsi14"),
            "macd_hist": sig.get("macd_hist"),
            "ret_5d": sig.get("ret_5d"),
            "ret_20d": sig.get("ret_20d"),
            "vol_ratio_20d": sig.get("vol_ratio_20d"),
            "rel_ret20_vs_spy": sig.get("rel_ret20_vs_spy"),
            "entry": round(c.entry, 2),
            "target": round(c.target, 2),
            "stop": round(c.stop, 2),
            "decision": c.decision_reason,
        })
    prompt = {
        "task": (
            "For each ticker give a 1-2 sentence trader thesis explaining WHY it is a "
            "candidate today. Use ONLY the provided quantitative data. Be specific about "
            "which signals drove the selection. Do not speculate about news or fundamentals."
        ),
        "candidates": snapshots,
        "response_schema": {"reasons": [{"ticker": "AAPL", "reason": "short thesis here"}]},
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise technical trading assistant. Return JSON only."},
            {"role": "user", "content": json.dumps(prompt, default=_jsonable)},
        ],
        "temperature": 0.15,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost/tradingagents-paper-runner",
        "X-Title": "TradingAgents Candidate Reasons",
    }
    try:
        if dashboard:
            dashboard.event(f"Generating AI reasons for {len(cap)} candidates via {model}…")
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        record_openrouter_request("paper_candidate_reasons")
        resp.raise_for_status()
        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in content)
        data = extract_json_object(str(content))
        result = {}
        for r in data.get("reasons", []):
            t = str(r.get("ticker", "")).upper().strip()
            if t:
                result[t] = str(r.get("reason", ""))[:500]
        if dashboard:
            dashboard.event(f"AI reasons received for {len(result)} tickers")
        return result
    except Exception as exc:
        if dashboard:
            dashboard.event(f"AI reason generation failed: {exc}")
        return {}


def build_pure_ai_candidates(
    args: argparse.Namespace,
    ai_pool: list[Candidate],
    dashboard: TerminalDashboard | None = None,
) -> list[Candidate]:
    if args.no_ai:
        return []
    if not ai_pool:
        return []

    shortlist_size = max(1, int(args.ai_shortlist_size or 30))
    shortlist = sorted(ai_pool, key=ai_shortlist_sort_key, reverse=True)[:shortlist_size]
    snapshots = [compact_ai_snapshot(candidate) for candidate in shortlist]
    ai_response = call_openrouter_ai(args, snapshots, dashboard=dashboard)
    if not ai_response:
        return []

    by_ticker = {candidate.ticker: candidate for candidate in shortlist}
    candidates: list[Candidate] = []
    for pick in ai_response.get("picks", []):
        ticker = str(pick.get("ticker", "")).upper().strip()
        if not ticker or ticker not in by_ticker:
            continue
        action = str(pick.get("action", "buy")).lower()
        if action != "buy":
            continue
        try:
            entry = float(pick["entry"])
            stop = float(pick["stop"])
            target = float(pick["target"])
            confidence = float(pick.get("confidence", 0.5))
        except Exception:
            continue
        if not (target > entry > stop > 0):
            continue
        if (target - entry) / max(entry - stop, 0.01) < 1.0:
            continue
        base = by_ticker[ticker]
        ai_candidate = Candidate(
            ticker=ticker,
            signal_date=base.signal_date,
            score=round(max(0.0, min(confidence, 1.0)) * 100, 2),
            entry=round(entry, 2),
            target=round(target, 2),
            stop=round(stop, 2),
            signal_close=base.signal_close,
            atr=base.atr,
            ml_probability=None,
            expected_return=None,
            large_loss_probability=None,
            target_before_stop_probability=None,
            timeout_probability=None,
            ml_pass=False,
            rule_pass=False,
            gate_status=base.gate_status,
            decision_reason="openrouter_pure_ai",
            signals={
                **base.signals,
                "ai_confidence": round(confidence, 4),
                "ai_thesis": str(pick.get("thesis", ""))[:500],
            },
        )
        candidates.append(ai_candidate)
        if len(candidates) >= args.ai_max_picks:
            break

    if dashboard:
        dashboard.event(f"Pure AI produced {len(candidates)} valid candidates")
    return candidates


MIN_AVG_VOLUME = 500_000

HARD_CONFIRMED_PULLBACK_GATES = {
    "price_not_5_500",
    "dollar_volume_below_5m",
    "atr_pct_not_0p5_to_8",
    "spy_not_above_50_200",
    "spy_5d_too_weak",
    "stock_not_above_sma200",
    "sma200_not_rising_20d",
}


def validate_ticker_data(df: pd.DataFrame, ticker: str) -> tuple[bool, str]:
    """Return (ok, reason). Rejects stale, zero-volume, or spike data."""
    if df is None or len(df) < 5:
        return False, "insufficient_rows"
    last_date = pd.to_datetime(df.index[-1]).date()
    age_days = (dt.date.today() - last_date).days
    if age_days > 5:
        return False, f"stale_data_{age_days}d"
    if "Volume" in df.columns:
        zero_vol_pct = float((df["Volume"] == 0).mean())
        if zero_vol_pct > 0.10:
            return False, f"zero_volume_{zero_vol_pct:.0%}"
    if "Close" in df.columns and len(df) > 1:
        pct_change = df["Close"].pct_change().abs()
        if (pct_change > 0.50).any():
            return False, "price_spike_gt50pct"
    return True, "ok"


def build_candidates(
    args: argparse.Namespace,
    trade_date: dt.date,
    bundle: dict[str, Any] | None,
    new_bundle: dict[str, Any] | None = None,
    dashboard: TerminalDashboard | None = None,
) -> dict[str, list[Candidate]]:
    tickers = load_tickers(args.tickers)
    if args.max_tickers and args.max_tickers > 0:
        tickers = tickers[: args.max_tickers]
    tickers = list(dict.fromkeys([t.upper() for t in tickers if t.strip()]))
    start = trade_date - dt.timedelta(days=args.lookback_days)
    end = trade_date + dt.timedelta(days=1)

    market_symbols = list(dict.fromkeys(tickers + [args.benchmark] + SECTOR_ETFS))
    raw = download_daily_history(market_symbols, start, end, args.batch_size, dashboard=dashboard)
    if dashboard:
        dashboard.update(phase="Building market regime context")
    spy_df = clean_daily_frame(raw.get(args.benchmark), trade_date) if raw.get(args.benchmark) is not None else None
    spy_regime = build_spy_regime(spy_df) if spy_df is not None and len(spy_df) >= 200 else pd.Series(dtype=str)

    vix_raw = download_index_close("^VIX", start, end)
    vix3m_raw = download_index_close("^VIX3M", start, end)
    vix_df = clean_daily_frame(vix_raw, trade_date) if vix_raw is not None else None
    vix3m_df = clean_daily_frame(vix3m_raw, trade_date) if vix3m_raw is not None else None
    vix_regime = build_vix_regime(vix_df) if vix_df is not None and not vix_df.empty else None
    vix_ts = build_vix_term_structure(vix_df, vix3m_df) if vix_df is not None and vix3m_df is not None else None
    vix_1d_chg_series = vix_df["Close"].pct_change(1) if vix_df is not None and not vix_df.empty else None

    sector_dfs = {
        ticker: clean_daily_frame(df, trade_date)
        for ticker, df in raw.items()
        if ticker in SECTOR_ETFS and df is not None
    }
    sector_breadth = build_sector_breadth(sector_dfs) if sector_dfs else None

    # Volume pre-filter: drop tickers with <500K avg 20-day volume before scoring
    min_vol = getattr(args, "min_avg_volume", MIN_AVG_VOLUME)
    pre_filter_count = len(tickers)
    if min_vol > 0:
        filtered_tickers = []
        for t in tickers:
            df_raw = raw.get(t)
            if df_raw is not None and "Volume" in df_raw.columns and len(df_raw) >= 20:
                avg_vol = float(df_raw["Volume"].iloc[-20:].mean())
                if avg_vol >= min_vol:
                    filtered_tickers.append(t)
            else:
                filtered_tickers.append(t)
        tickers = filtered_tickers
    if dashboard:
        dashboard.event(f"Volume pre-filter: {len(tickers):,} of {pre_filter_count:,} tickers pass ≥{min_vol/1e6:.1f}M avg vol")

    candidates_by_strategy: dict[str, list[Candidate]] = {
        "algorithm": [],
        "machine_learning": [],
        "ml_new": [],
        "combined": [],
        "pure_ai": [],
        "long_hold": [],
    }
    ai_pool: list[Candidate] = []
    scanned = 0
    rule_pass = 0
    ml_pass = 0
    ml_new_pass = 0
    combined_pass = 0
    if dashboard:
        dashboard.event("Scoring tickers with confirmed-pullback rules (parallel)...")

    # Pre-compute shared regime values once — same for every ticker
    _regime_cache: dict[pd.Timestamp, tuple] = {}

    def _apply_ml(candidate: Candidate, ml: dict[str, Any], reason_prefix: str = "") -> Candidate:
        updated = copy.copy(candidate)
        updated.ml_probability = ml["ml_probability"]
        updated.expected_return = ml["expected_return"]
        updated.large_loss_probability = ml["large_loss_probability"]
        updated.target_before_stop_probability = ml["target_before_stop_probability"]
        updated.timeout_probability = ml["timeout_probability"]
        updated.ml_pass = bool(ml["ml_pass"])
        updated.decision_reason = f"{reason_prefix}{ml['decision_reason']}" if reason_prefix else str(ml["decision_reason"])
        return updated

    def _gate_reasons(gate_status: str) -> list[str]:
        return [r for r in str(gate_status or "").split(",") if r]

    def _is_near_miss_rule_candidate(gate_status: str) -> bool:
        if not getattr(args, "allow_near_miss_rule_candidates", True):
            return False
        reasons = _gate_reasons(gate_status)
        if not reasons:
            return False
        if any(reason in HARD_CONFIRMED_PULLBACK_GATES for reason in reasons):
            return False
        max_soft = max(0, int(getattr(args, "near_miss_max_soft_failures", 3)))
        return len(reasons) <= max_soft

    def _score_ticker(ticker: str) -> "tuple[Candidate, Candidate | None] | None":
        df_raw = raw.get(ticker)
        if df_raw is None:
            return None
        ok, reason = validate_ticker_data(df_raw, ticker)
        if not ok:
            return None
        df = clean_daily_frame(df_raw, trade_date)
        if len(df) <= MIN_HISTORY:
            return None
        pos = len(df) - 1
        as_of = pd.Timestamp(df.index[pos])
        try:
            pc = precompute(df)
            regime = regime_value(spy_regime, as_of)
            vix_reg = regime_value(vix_regime, as_of) if vix_regime is not None else "unknown"
            gate_values = spy_gate_values(spy_df, as_of)
            score, signals = score_at(
                pc, df, pos,
                target_mult=args.target_mult,
                stop_mult=args.stop_mult,
                regime=regime,
                vix_reg=vix_reg,
                vix_ts=numeric_series_value(vix_ts, as_of),
                sector_breadth=numeric_series_value(sector_breadth, as_of),
                score_mode="confirmed_pullback",
                vix_1d_chg=numeric_series_value(vix_1d_chg_series, as_of),
                **gate_values,
            )
        except Exception:
            return None

        if not signals:
            return None

        gate_status = str(signals.get("confirmed_pullback_gates") or "")
        rule_ok = score >= args.threshold and gate_status == "pass"
        near_miss_rule_ok = (not rule_ok) and _is_near_miss_rule_candidate(gate_status)

        if not (rule_ok or near_miss_rule_ok) and bundle is None and args.no_ai:
            return None

        row = {
            "ticker": ticker,
            "scan_date": str(as_of.date()),
            "day_of_week": int(as_of.dayofweek),
            "month": str(as_of.to_period("M")),
            "year": int(as_of.year),
            "score": score,
            "spy_regime": regime,
            "vix_regime": vix_reg,
            "candidate_status": "executed" if rule_ok else "rejected",
            "rejection_reasons": [] if rule_ok else [r for r in gate_status.split(",") if r],
            **signals,
        }
        ml = {
            "ml_probability": None,
            "expected_return": None,
            "large_loss_probability": None,
            "target_before_stop_probability": None,
            "timeout_probability": None,
            "ml_pass": True,
            "decision_reason": "rule_pass_no_ml",
        }
        eff_ml_threshold = regime_ml_threshold(regime, vix_reg)
        if bundle is not None:
            ml = predict_ml(
                row, bundle,
                ml_prob_threshold=ML_OLD_THRESHOLD,
                ml_large_loss_max=getattr(args, "ml_large_loss_max", None),
                ml_expected_return_min=getattr(args, "ml_expected_return_min", None),
            )

        # If a risk model is available, require it to approve near-miss rule setups.
        # Strict rule passes remain rule-driven; this only guards relaxed entries.
        if near_miss_rule_ok and bundle is not None and not bool(ml["ml_pass"]):
            near_miss_rule_ok = False

        rule_candidate_score = float(score)
        if near_miss_rule_ok:
            soft_misses = len(_gate_reasons(gate_status))
            rule_candidate_score = max(60.0, 100.0 - soft_misses * 10.0)

        base_candidate = Candidate(
            ticker=ticker,
            signal_date=str(as_of.date()),
            score=rule_candidate_score,
            entry=float(signals["entry"]),
            target=float(signals["target"]),
            stop=float(signals["stop"]),
            signal_close=float(df["Close"].iloc[pos]),
            atr=float(signals.get("atr") or 0.0),
            ml_probability=ml["ml_probability"],
            expected_return=ml["expected_return"],
            large_loss_probability=ml["large_loss_probability"],
            target_before_stop_probability=ml["target_before_stop_probability"],
            timeout_probability=ml["timeout_probability"],
            ml_pass=bool(ml["ml_pass"]),
            rule_pass=rule_ok or near_miss_rule_ok,
            gate_status=gate_status,
            decision_reason=("rule_near_miss:" + gate_status) if near_miss_rule_ok else str(ml["decision_reason"]),
            signals=signals,
        )
        new_candidate = None
        if new_bundle is not None:
            new_ml = predict_ml(
                row, new_bundle,
                ml_prob_threshold=eff_ml_threshold,
                ml_large_loss_max=getattr(args, "ml_large_loss_max", None),
                ml_expected_return_min=getattr(args, "ml_expected_return_min", None),
            )
            new_candidate = _apply_ml(base_candidate, new_ml, reason_prefix="new_model:")
        return base_candidate, new_candidate

    import concurrent.futures
    import os
    n_workers = min(os.cpu_count() or 4, 8)
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_score_ticker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if dashboard and completed % 250 == 0:
                dashboard.update(phase=f"Scoring tickers: {completed:,}/{len(tickers):,} done, {rule_pass:,} rule-pass")
            result = future.result()
            if result is None:
                continue
            candidate, new_candidate = result
            scanned += 1
            if candidate.rule_pass:
                rule_pass += 1
                candidates_by_strategy["algorithm"].append(candidate)
            if bundle is not None and candidate.ml_pass:
                ml_pass += 1
                candidates_by_strategy["machine_learning"].append(candidate)
            if new_candidate is not None and new_candidate.ml_pass:
                ml_new_pass += 1
                candidates_by_strategy["ml_new"].append(new_candidate)
            if candidate.rule_pass and bundle is not None and candidate.ml_pass:
                combined_pass += 1
                candidates_by_strategy["combined"].append(candidate)
            if not args.no_ai:
                ai_pool.append(candidate)
            # Long-hold: strong-score rule passes, ML confirmation preferred but not required
            if candidate.rule_pass:
                if bundle is None or candidate.ml_pass or candidate.score >= args.threshold * 1.3:
                    candidates_by_strategy["long_hold"].append(candidate)

    # pure_ai slot now runs the validated rule-based algorithm (confirmed_pullback,
    # 0.75/1.0 ATR, 10d hold) instead of LLM picks. Backtest: 68.8% WR, +27.9%/yr
    # over 7.3yr. Reuses the same rule-pass candidates as the "algorithm" book;
    # build_pure_ai_candidates (openrouter) is intentionally no longer called.
    candidates_by_strategy["pure_ai"] = [
        copy.copy(c) for c in candidates_by_strategy["algorithm"]
    ]

    for strategy, candidates in candidates_by_strategy.items():
        if strategy in {"machine_learning", "ml_new", "combined"}:
            candidates.sort(key=_ml_composite_score, reverse=True)
        else:
            candidates.sort(
                key=lambda c: (
                    c.ml_probability if c.ml_probability is not None else 0.0,
                    c.expected_return if c.expected_return is not None else 0.0,
                    c.score,
                ),
                reverse=True,
            )

    raw_ml_old = len(candidates_by_strategy["machine_learning"])
    raw_ml_new = len(candidates_by_strategy["ml_new"])
    max_ml_candidates = int(getattr(args, "max_ml_candidates", 100) or 0)
    if max_ml_candidates > 0:
        for strategy in ("machine_learning", "ml_new"):
            candidates_by_strategy[strategy] = candidates_by_strategy[strategy][:max_ml_candidates]

    print(
        f"Scanned {scanned} scoreable tickers. "
        f"Algorithm candidates: {rule_pass}. ML Old candidates: {len(candidates_by_strategy['machine_learning'])}"
        f" of {ml_pass}. ML New candidates: {len(candidates_by_strategy['ml_new'])} of {ml_new_pass}. "
        f"Combined candidates: {combined_pass}. Pure AI candidates: "
        f"{len(candidates_by_strategy['pure_ai'])}."
    )
    if dashboard:
        if max_ml_candidates > 0 and (raw_ml_old != len(candidates_by_strategy["machine_learning"]) or raw_ml_new != len(candidates_by_strategy["ml_new"])):
            dashboard.event(
                f"ML shortlist cap: ML Old {raw_ml_old:,}->{len(candidates_by_strategy['machine_learning']):,}, "
                f"ML New {raw_ml_new:,}->{len(candidates_by_strategy['ml_new']):,}"
            )
        dashboard.event(
            f"Candidate scan complete: {scanned:,} scoreable, "
            f"{rule_pass:,} algorithm, {len(candidates_by_strategy['machine_learning']):,} ML old, "
            f"{len(candidates_by_strategy['ml_new']):,} ML new, {combined_pass:,} combined, "
            f"{len(candidates_by_strategy['pure_ai']):,} pure AI"
        )
        dashboard.update(candidates_by_strategy=candidates_by_strategy, phase="Candidate lists ready")
    return candidates_by_strategy, raw


def save_candidates(path: Path, candidates: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "signal_date",
        "score",
        "entry",
        "target",
        "stop",
        "signal_close",
        "atr",
        "ml_probability",
        "expected_return",
        "large_loss_probability",
        "target_before_stop_probability",
        "timeout_probability",
        "rule_pass",
        "gate_status",
        "decision_reason",
        "ai_reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            row = asdict(candidate)
            writer.writerow({field_name: row.get(field_name) for field_name in fields})


def save_strategy_candidates(output_dir: Path, candidates_by_strategy: dict[str, list[Candidate]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_dt = dt.datetime.now().isoformat(timespec="seconds")
    scan_date = scan_dt[:10]
    base_dir = output_dir.parent  # strip the day dir so history lives at base level
    for strategy, candidates in candidates_by_strategy.items():
        save_candidates(output_dir / f"{strategy}_candidates.csv", candidates)
        if not candidates:
            continue
        log_path = base_dir / f"{strategy}_candidates_history.jsonl"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                for c in candidates:
                    row = asdict(c)
                    row["scan_dt"] = scan_dt
                    row["scan_date"] = scan_date
                    row["strategy"] = strategy
                    f.write(json.dumps(row) + "\n")
        except Exception:
            pass


def latest_prices(
    tickers: list[str],
    batch_size: int,
    dashboard: TerminalDashboard | None = None,
) -> dict[str, float]:
    prices: dict[str, float] = {}
    tickers = list(dict.fromkeys([t for t in tickers if t]))
    if not tickers:
        return prices

    if dashboard:
        dashboard.update(phase=f"Fetching live prices for {len(tickers)} symbols")
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            raw = _yf_download_with_retry(
                tickers=batch,
                period="1d",
                interval="1m",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            prices.update(extract_latest_close(raw, batch))
        except Exception as exc:
            msg = f"live price batch failed for {batch[:3]}...: {exc}"
            if dashboard:
                dashboard.event(msg)
            else:
                print(f"  {msg}")
    missing = [ticker for ticker in tickers if ticker not in prices]
    if missing:
        fallback_prices = latest_prices_chart_fallback(missing)
        if fallback_prices:
            prices.update(fallback_prices)
            if dashboard:
                dashboard.event(
                    f"Live price fallback loaded {len(fallback_prices)}/{len(missing)} missing symbols"
                )
    if dashboard:
        dashboard.event(f"Live prices loaded for {len(prices)}/{len(tickers)} symbols")
    return prices


def latest_prices_chart_fallback(tickers: list[str]) -> dict[str, float]:
    """Fetch 1-minute Yahoo chart prices one symbol at a time for bulk misses."""
    prices: dict[str, float] = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for ticker in tickers:
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"range": "1d", "interval": "1m"},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            result = (response.json().get("chart", {}).get("result") or [None])[0]
            if not result:
                continue
            quote = ((result.get("indicators") or {}).get("quote") or [None])[0]
            closes = (quote or {}).get("close") or []
            last = next((value for value in reversed(closes) if value is not None), None)
            if last is not None:
                prices[ticker] = float(last)
        except Exception:
            continue
    return prices


def extract_latest_close(raw: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = raw.columns.get_level_values(0)
        level1 = raw.columns.get_level_values(1)
        for ticker in tickers:
            series = None
            if "Close" in level0 and ticker in level1:
                try:
                    series = raw["Close"][ticker]
                except Exception:
                    series = None
            elif "Close" in level1 and ticker in level0:
                try:
                    series = raw[ticker]["Close"]
                except Exception:
                    series = None
            if series is not None:
                series = series.dropna()
                if not series.empty:
                    out[ticker] = float(series.iloc[-1])
    elif len(tickers) == 1 and "Close" in raw.columns:
        series = raw["Close"].dropna()
        if not series.empty:
            out[tickers[0]] = float(series.iloc[-1])
    return out


def fire_webhook(url: str, payload: dict) -> None:
    """POST trade event to Discord or Slack webhook. Best-effort, never raises."""
    if not url:
        return
    try:
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        if hostname.endswith("discord.com") or hostname.endswith("discordapp.com"):
            fields = [{"name": k, "value": str(v), "inline": True} for k, v in payload.items() if k not in ("type",)]
            body = {"embeds": [{"title": payload.get("type", "TRADE"), "fields": fields[:10], "color": 0x22d3ee}]}
        else:
            lines = [f"*{payload.get('type', 'TRADE')}*"] + [f"{k}: {v}" for k, v in payload.items() if k != "type"]
            body = {"text": "\n".join(lines)}
        requests.post(url, json=body, timeout=5)
    except Exception:
        pass


def fire_sms(sms_number: str, message: str) -> None:
    """Send SMS via TextNow. Best-effort, never raises."""
    try:
        import sys
        _root = str(Path(__file__).parent.parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from scripts.sms_alerts import send_sms
        if not sms_number:
            sms_number = (
                os.getenv("PAPER_SMS_NUMBER")
                or os.getenv("TEXTNOW_PHONE")
                or os.getenv("TEXTNOW_ALERT_NUMBER")
                or os.getenv("SMS_NUMBER")
                or ""
            ).strip()
        if sms_number:
            result = send_sms(sms_number, message)
            if not result.get("success"):
                print(f"[sms] send failed for {sms_number}: {result.get('error') or result}", flush=True)
    except Exception as exc:
        print(f"[sms] send error for {sms_number}: {exc}", flush=True)


def chart_link(ticker: str) -> str:
    """Public chart URL for a ticker (TradingView)."""
    return f"https://www.tradingview.com/chart/?symbol={ticker.upper()}"


_TUNNEL_URL_FILE = Path("tmp/tunnel_url.txt")


def public_dashboard_url() -> str:
    """Stable dashboard URL used in notifications.

    Keep this as one link so users do not need one-off approval URLs or SMS
    reply workflows. The dashboard's HIL panel reads the pending approval from
    server state after the user signs in through Cloudflare Access.
    """
    return (
        os.getenv("PUBLIC_DASHBOARD_URL")
        or os.getenv("DASHBOARD_URL")
        or os.getenv("APP_URL")
        or "https://app.agentictrader.org"
    ).strip().rstrip("/")


def _tunnel_proc_alive() -> bool:
    """True if a cloudflared/localtunnel process is currently running."""
    import subprocess
    r = subprocess.run(
        "pgrep -f 'cloudflared tunnel|localtunnel --port' >/dev/null 2>&1",
        shell=True,
    )
    return r.returncode == 0


def ensure_public_tunnel(timeout: float = 25.0) -> str:
    """Return a persistent public URL for the dashboard.

    Reuses an already-running tunnel (URL cached in tmp/tunnel_url.txt) instead
    of starting and tearing one down per trade. Only launches a new tunnel when
    none is alive. Never kills an existing tunnel. Falls back to Localtunnel,
    then localhost. The tunnel is intentionally NOT torn down after a trade so
    the approval page and dashboard stay reachable.
    """
    import subprocess
    import re
    import time
    import os as _os

    import glob

    def _emit_webhook(u: str) -> None:
        """Print + persist the Sendblue inbound webhook URL (idempotent)."""
        if not u.startswith("https://"):
            return
        sec = _os.getenv("SENDBLUE_INBOUND_SECRET", "").strip()
        if not sec:
            # env may not be loaded yet — read straight from .env
            try:
                for _ln in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
                    _ln = _ln.strip()
                    if _ln.startswith("SENDBLUE_INBOUND_SECRET="):
                        sec = _ln.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except Exception:
                pass
        if not sec:
            return
        hook = f"{u}/api/paper/sms/inbound?key={sec}"
        try:
            Path("tmp/sendblue_webhook.txt").write_text(hook)
        except Exception:
            pass
        print(f"[HIL] Sendblue inbound webhook (set once in Sendblue dashboard): {hook}")
    import os

    tmp_dir = Path("tmp")
    tmp_dir.mkdir(exist_ok=True)
    log_path = tmp_dir / "tunnel.log"

    def _url_from_log() -> str | None:
        for pat in (r"https://[a-zA-Z0-9-]+\.trycloudflare\.com",
                    r"https://[a-zA-Z0-9-]+\.loca\.lt"):
            try:
                m = re.search(pat, log_path.read_text())
                if m:
                    return m.group(0)
            except Exception:
                pass
        return None

    # 1. Reuse a live tunnel if one is already running.
    if _tunnel_proc_alive():
        cached = ""
        if _TUNNEL_URL_FILE.exists():
            cached = _TUNNEL_URL_FILE.read_text().strip()
        if not cached.startswith("https://"):
            cached = _url_from_log() or ""
        if cached.startswith("https://"):
            try:
                _TUNNEL_URL_FILE.write_text(cached)
            except Exception:
                pass
            print(f"[HIL] Reusing live tunnel: {cached}")
            _emit_webhook(cached)
            return cached

    # cloudflared/localtunnel via the cached npx binary. The npm registry is
    # often MITM-blocked on locked-down networks, so prefer the already-cached
    # binary (direct path) and `npx --offline` before a registry-hitting npx.
    cf_bin = None
    for cand in sorted(glob.glob(
        os.path.expanduser("~/.npm/_npx/*/node_modules/cloudflared/bin/cloudflared"))):
        if os.path.exists(cand):
            cf_bin = cand
            break
    if cf_bin:
        cf_cmd = f'"{cf_bin}" tunnel --url http://localhost:8001'
    else:
        cf_cmd = "npx --offline cloudflared tunnel --url http://localhost:8001 || " \
                 "npx cloudflared tunnel --url http://localhost:8001"
    lt_cmd = "npx --offline localtunnel --port 8001 || npx localtunnel --port 8001"

    def _poll_log(pattern: str, to: float, interval: float = 0.5) -> str | None:
        deadline = time.time() + to
        rx = re.compile(pattern)
        while time.time() < deadline:
            try:
                m = rx.search(log_path.read_text())
                if m:
                    return m.group(0)
            except Exception:
                pass
            time.sleep(interval)
        return None

    # 2. No live tunnel — start a fresh Cloudflare quick tunnel.
    try:
        log_path.write_text("")
    except Exception:
        pass
    try:
        subprocess.run(
            f"nohup sh -c '{cf_cmd}' > tmp/tunnel.log 2>&1 &",
            shell=True,
        )
        url = _poll_log(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", timeout)
        if not url:
            print("[HIL] Cloudflare timed out. Trying Localtunnel failover...")
            subprocess.run("pkill -f 'cloudflared tunnel' 2>/dev/null || true", shell=True)
            try:
                log_path.write_text("")
            except Exception:
                pass
            subprocess.run(f"nohup sh -c '{lt_cmd}' > tmp/tunnel.log 2>&1 &", shell=True)
            url = _poll_log(r"https://[a-zA-Z0-9-]+\.loca\.lt", 20)
    except Exception as e:
        print(f"[HIL] Tunnel start error: {e}")
        url = None

    if url:
        try:
            _TUNNEL_URL_FILE.write_text(url)
        except Exception:
            pass
        print(f"[HIL] Public tunnel ready: {url}")
        _emit_webhook(url)
        return url

    print("[HIL] No public tunnel available — falling back to localhost.")
    return "http://localhost:8001"


def fire_candidate_alerts(
    sms_number: str,
    candidates_by_strategy: dict[str, list["Candidate"]],
    strategies: list[str],
    state_dir: Path,
    max_per_msg: int = 8,
) -> None:
    """Text new candidates (with chart links) via the primary SMS provider
    (TextBelt by default). Batches all candidates into ONE message because
    the free TextBelt key allows ~1 send/day. Per-day dedupe so the same
    ticker is not re-alerted on repeat scans. Best-effort, never raises."""
    try:
        if not sms_number:
            sms_number = (
                os.getenv("PAPER_SMS_NUMBER")
                or os.getenv("TEXTNOW_PHONE")
                or os.getenv("TEXTNOW_ALERT_NUMBER")
                or os.getenv("SMS_NUMBER")
                or ""
            ).strip()
        if not sms_number:
            return

        today = dt.datetime.now().strftime("%Y-%m-%d")
        state_path = state_dir / ".alerted_candidates.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        already = set(state.get(today, []))

        picked: list[tuple[str, "Candidate"]] = []
        seen: set[str] = set()
        for strat in strategies:
            for c in candidates_by_strategy.get(strat, []):
                if c.ticker in seen or c.ticker in already:
                    continue
                seen.add(c.ticker)
                picked.append((strat, c))

        if not picked:
            return
        picked = picked[:max_per_msg]

        label = "/".join(dict.fromkeys(strategies))
        lines = [f"TradingAgents [{label}] {len(picked)} candidate(s):"]
        for strat, c in picked:
            entry = getattr(c, "entry", None)
            score = getattr(c, "score", None)
            px = f"${round(entry, 2)}" if isinstance(entry, (int, float)) else "?"
            sc = f" s={round(score)}" if isinstance(score, (int, float)) else ""
            lines.append(f"{c.ticker} {px}{sc} {chart_link(c.ticker)}")

        from scripts.sms_alerts import send_sms
        result = send_sms(sms_number, "\n".join(lines))
        if result.get("success"):
            state.setdefault(today, [])
            state[today] = sorted(set(state[today]) | {c.ticker for _, c in picked})
            # keep only last 5 days of state
            for d in sorted(state)[:-5]:
                state.pop(d, None)
            try:
                state_path.write_text(json.dumps(state), encoding="utf-8")
            except Exception:
                pass
            print(f"[sms] candidate alert sent: {len(picked)} ticker(s) -> {sms_number}", flush=True)
        else:
            print(f"[sms] candidate alert failed: {result.get('error') or result}", flush=True)
    except Exception as exc:
        print(f"[sms] candidate alert error: {exc}", flush=True)


def _init_journal_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            ticker TEXT,
            shares INTEGER,
            entry_time TEXT,
            exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl REAL,
            pnl_pct REAL,
            score REAL,
            ml_probability REAL,
            expected_return REAL
        )
    """)
    conn.commit()
    return conn


def _rolling_trade_stats(trades: list[dict], n: int = 20) -> dict:
    """Compute rolling win rate, Kelly fraction, and streak from last N closed trades."""
    closed = [t for t in trades if t.get("pnl") is not None][-n:]
    if not closed:
        return {"win_rate": 0.5, "kelly": 0.0, "loss_streak": 0, "win_streak": 0,
                "avg_win": 0.0, "avg_loss": 0.0, "n": 0}
    pnls = [float(t["pnl"]) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.5
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0
    rr = avg_win / max(avg_loss, 0.01)
    # Half-Kelly: conservative, limits blow-up risk
    kelly_raw = win_rate - (1.0 - win_rate) / max(rr, 0.01)
    kelly = max(0.0, kelly_raw * 0.5)

    # Current streak
    loss_streak = win_streak = 0
    for p in reversed(pnls):
        if p <= 0:
            if win_streak > 0:
                break
            loss_streak += 1
        else:
            if loss_streak > 0:
                break
            win_streak += 1

    return {
        "win_rate": win_rate, "kelly": kelly,
        "loss_streak": loss_streak, "win_streak": win_streak,
        "avg_win": avg_win, "avg_loss": avg_loss, "n": len(pnls),
    }


def position_size(
    account: PaperAccount,
    price: float,
    account_value: float,
    args: argparse.Namespace,
    ml_probability: float | None = None,
    atr: float = 0.0,
    stop: float = 0.0,
    regime_factor: float = 1.0,
    now: "dt.datetime | None" = None,
    prices: "dict | None" = None,
) -> int:
    """Dynamic position sizer. Layers: Kelly → ML confidence → streak → time-of-day →
    daily-profit lock-in → drawdown → regime → ATR risk → heat cap → cash floor."""
    if price <= 0 or account_value <= 0:
        return 0

    cap_max = args.position_cap_pct / 100.0
    cap_min = getattr(args, "position_cap_min_pct", 10.0) / 100.0
    risk_per_trade_pct = getattr(args, "risk_per_trade_pct", 0.0)

    # ── 1. Kelly-fraction base pct ─────────────────────────────────────────────
    stats = _rolling_trade_stats(account.trades, n=20)
    kelly_pct = stats["kelly"]
    if stats["n"] < 5:
        # Too few trades — start at midpoint, let system learn
        base_pct = (cap_min + cap_max) / 2.0
    else:
        # Kelly gives optimal bet size; clamp between cap_min and cap_max
        base_pct = max(cap_min, min(cap_max, kelly_pct))

    # ── 2. ML confidence scalar (min→max within Kelly range) ──────────────────
    if ml_probability is not None and ml_probability > 0:
        ml_threshold = getattr(args, "ml_probability_threshold", None) or 0.58
        high_conf = getattr(args, "position_high_confidence_threshold", 0.80)
        t = (ml_probability - ml_threshold) / max(high_conf - ml_threshold, 0.01)
        t = max(0.0, min(1.0, t))
        # Scale base_pct up toward cap_max based on ML conviction
        base_pct = base_pct + t * (cap_max - base_pct) * 0.6
    else:
        base_pct = cap_min

    # ── 3. Streak adjustment ───────────────────────────────────────────────────
    loss_streak = stats["loss_streak"]
    win_streak = stats["win_streak"]
    if loss_streak >= 3:
        base_pct *= 0.50   # 3+ losses: cut in half — protect from hole-digging
    elif loss_streak == 2:
        base_pct *= 0.70   # 2 losses: trim significantly
    elif loss_streak == 1:
        base_pct *= 0.85   # 1 loss: slight caution
    elif win_streak >= 4:
        base_pct = min(cap_max, base_pct * 1.20)  # hot streak: press a bit
    elif win_streak >= 2:
        base_pct = min(cap_max, base_pct * 1.10)  # 2+ wins: mild press

    # ── 4. Time-of-day factor ──────────────────────────────────────────────────
    tod_factor = 1.0
    if now is not None:
        market_open_minutes = (now.hour - 9) * 60 + now.minute - 30
        if market_open_minutes < 15:
            tod_factor = 0.0   # first 15 min: no entries (erratic price action)
        elif market_open_minutes < 45:
            tod_factor = 0.85  # 9:45-10:15: cautious, still settling
        elif market_open_minutes > 360:
            tod_factor = 0.0   # last 30 min: no new entries (avoid MOC risk)
        elif market_open_minutes > 300:
            tod_factor = 0.80  # last hour: reduce size
        elif 90 <= market_open_minutes <= 210:
            tod_factor = 0.90  # midday 11:00-12:30: typically choppy
    base_pct *= tod_factor
    if base_pct <= 0:
        return 0

    # ── 5. Daily profit lock-in: protect gains if up big today ────────────────
    today_str = now.strftime("%Y-%m-%d") if now else ""
    today_pnl = sum(
        float(t.get("pnl", 0)) for t in account.trades
        if str(t.get("exit_time", ""))[:10] == today_str
    ) if today_str else 0.0
    daily_profit_target = account.starting_cash * 0.01  # 1% daily profit target
    if today_pnl >= daily_profit_target * 2:
        base_pct *= 0.50  # up 2%+ today: half size to protect
    elif today_pnl >= daily_profit_target:
        base_pct *= 0.75  # hit daily target: reduce slightly

    # ── 6. Apply regime factor (already includes drawdown from caller) ─────────
    base_pct *= regime_factor
    base_pct = max(cap_min * 0.5, min(cap_max, base_pct))  # soft clamp

    # ── 7. ATR risk-based sizing: size so max loss = risk_pct of account ───────
    if risk_per_trade_pct > 0 and (atr > 0 or stop > 0):
        risk_dollars = account_value * (risk_per_trade_pct / 100.0)
        stop_dist = (price - stop) if stop > 0 and price > stop else max(atr, price * 0.01)
        atr_shares = int(math.floor(risk_dollars / stop_dist)) if stop_dist > 0 else 0
        cap_shares = int(math.floor(account_value * cap_max / price))
        shares_from_risk = min(atr_shares, cap_shares)
        # Blend: 60% ATR-risk, 40% Kelly-pct
        pct_shares = int(math.floor(account_value * base_pct / price))
        blended = int(round(shares_from_risk * 0.6 + pct_shares * 0.4))
        # Use settled_cash as ceiling — never size into unsettled funds
        budget = max(0.0, account.settled_cash - args.commission)
        return max(0, min(blended, int(math.floor(budget / price))))

    # ── 8. Percentage-of-account sizing ───────────────────────────────────────
    max_position_value = account_value * base_pct
    # Use settled_cash as ceiling — never size into unsettled funds
    budget = max(0.0, min(account.settled_cash - args.commission, max_position_value))
    return max(0, int(math.floor(budget / price)))


def compute_market_breadth(raw: dict[str, pd.DataFrame], trade_date: dt.date) -> float:
    """Fraction of universe tickers with last close above 50-day SMA (excluding trade_date)."""
    above = 0
    total = 0
    cutoff = pd.Timestamp(trade_date)
    for df in raw.values():
        if df is None or "Close" not in df.columns:
            continue
        idx = pd.to_datetime(df.index).tz_localize(None).normalize()
        closes = df["Close"].copy()
        closes.index = idx
        closes = closes[closes.index < cutoff].dropna()
        if len(closes) < 51:
            continue
        sma50 = float(closes.iloc[-50:].mean())
        last = float(closes.iloc[-1])
        total += 1
        if last > sma50:
            above += 1
    return above / total if total > 0 else 0.5


def fetch_vwap_batch(tickers: list[str]) -> dict[str, float]:
    """Download today's 5-min bars and compute VWAP for each ticker."""
    if not tickers:
        return {}
    vwaps: dict[str, float] = {}
    try:
        raw = _yf_download_with_retry(
            tickers=tickers,
            period="1d",
            interval="5m",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            return {}
        if isinstance(raw.columns, pd.MultiIndex):
            for ticker in tickers:
                try:
                    df = raw.xs(ticker, axis=1, level=1).dropna(subset=["Close"])
                    if df.empty:
                        continue
                    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
                    vwaps[ticker] = float((tp * df["Volume"]).sum() / df["Volume"].replace(0, np.nan).sum())
                except Exception:
                    pass
        else:
            # single ticker returned flat columns
            t = tickers[0]
            df = raw.dropna(subset=["Close"])
            if not df.empty:
                tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
                vwaps[t] = float((tp * df["Volume"]).sum() / df["Volume"].replace(0, np.nan).sum())
    except Exception:
        pass
    return vwaps


def earnings_near(ticker: str, trade_date: dt.date, window: int = 3) -> bool:
    """Return True if earnings is within `window` calendar days of trade_date."""
    try:
        cal = yf.Ticker(ticker).calendar
        if not cal:
            return False
        dates = cal.get("Earnings Date") or []
        if not isinstance(dates, (list, tuple)):
            dates = [dates]
        for d in dates:
            try:
                ed = pd.Timestamp(d).date()
                if abs((ed - trade_date).days) <= window:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def filter_earnings_blackout(
    candidates_by_strategy: dict[str, list],
    trade_date: dt.date,
    window: int = 3,
    dashboard=None,
) -> None:
    """Remove candidates within earnings window in-place (parallel fetch)."""
    import concurrent.futures
    all_tickers = list({c.ticker for cands in candidates_by_strategy.values() for c in cands})
    if not all_tickers:
        return
    blacklist: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(all_tickers), 12)) as pool:
        results = {pool.submit(earnings_near, t, trade_date, window): t for t in all_tickers}
        for future in concurrent.futures.as_completed(results):
            t = results[future]
            try:
                if future.result():
                    blacklist.add(t)
            except Exception:
                pass
    if blacklist:
        msg = f"Earnings blackout: skipping {sorted(blacklist)}"
        print(msg)
        if dashboard:
            dashboard.event(msg)
        for strategy in candidates_by_strategy:
            candidates_by_strategy[strategy] = [c for c in candidates_by_strategy[strategy] if c.ticker not in blacklist]


def _ticker_sector(ticker: str) -> str:
    """Lazy GICS sector lookup with module-level cache."""
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    try:
        sector = (_ticker_fundamentals(ticker).get("sector")) or "unknown"
    except Exception:
        sector = "unknown"
    _sector_cache[ticker] = sector
    return sector


_fundamentals_cache: dict[str, dict] = {}

def _ticker_fundamentals(ticker: str) -> dict:
    """Fetch and cache all fundamental data for a ticker in one yfinance call.

    Returns dict with keys:
      sector, industry, market_cap, float_shares, beta, short_ratio,
      short_pct_float, fifty_two_week_high, fifty_two_week_low,
      earnings_date (nearest upcoming, as date or None)
    """
    if ticker in _fundamentals_cache:
        return _fundamentals_cache[ticker]
    result: dict = {}
    try:
        info = yf.Ticker(ticker).info or {}
        result["sector"]             = info.get("sector") or "unknown"
        result["industry"]           = info.get("industry") or "unknown"
        result["market_cap"]         = info.get("marketCap")
        result["float_shares"]       = info.get("floatShares")
        result["beta"]               = info.get("beta")
        result["short_ratio"]        = info.get("shortRatio")       # days-to-cover
        result["short_pct_float"]    = info.get("shortPercentOfFloat")
        result["fifty_two_week_high"] = info.get("fiftyTwoWeekHigh")
        result["fifty_two_week_low"]  = info.get("fiftyTwoWeekLow")
        # Earnings date from fast_info if available, else calendar
        try:
            cal = yf.Ticker(ticker).calendar
            dates = (cal or {}).get("Earnings Date") or []
            if not isinstance(dates, (list, tuple)):
                dates = [dates]
            upcoming = [pd.Timestamp(d).date() for d in dates if d is not None]
            result["earnings_dates"] = [str(d) for d in sorted(upcoming)]
        except Exception:
            result["earnings_dates"] = []
    except Exception:
        pass
    _fundamentals_cache[ticker] = result
    return result


def _enrich_candidate_fundamentals(
    candidates_by_strategy: dict[str, list],
    trade_date: dt.date,
    dashboard=None,
) -> None:
    """Parallel-fetch fundamentals for all candidates, filter on key gates, attach to signals."""
    import concurrent.futures

    all_tickers = list({c.ticker for cands in candidates_by_strategy.values() for c in cands})
    if not all_tickers:
        return

    # Parallel fetch
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(all_tickers), 16)) as pool:
        futs = {pool.submit(_ticker_fundamentals, t): t for t in all_tickers}
        for f in concurrent.futures.as_completed(futs):
            f.result()  # populates cache

    removed: dict[str, str] = {}  # ticker → reason

    for strategy, cands in candidates_by_strategy.items():
        keep = []
        for c in cands:
            f = _ticker_fundamentals(c.ticker)
            price = c.signals.get("entry") or c.signals.get("close") or 0.0

            # ── Gate 1: Earnings blackout (5-day window) ───────────────────────
            edate_strs = f.get("earnings_dates") or []
            near_earnings = False
            for es in edate_strs:
                try:
                    ed = dt.date.fromisoformat(str(es))
                    if abs((ed - trade_date).days) <= 5:
                        near_earnings = True
                        break
                except Exception:
                    pass
            if near_earnings:
                removed[c.ticker] = "near_earnings_5d"
                continue

            # ── Gate 2: 52-week high proximity (stock must be within 35% of high) ──
            high52 = f.get("fifty_two_week_high")
            if high52 and price and price > 0:
                pct_from_52wk_high = (price - high52) / high52  # negative = below high
                if pct_from_52wk_high < -0.35:
                    removed[c.ticker] = "more_than_35pct_below_52wk_high"
                    continue
                c.signals["pct_from_52wk_high"] = round(pct_from_52wk_high, 4)

            # ── Gate 3: Short interest (avoid extreme short squeeze risk >25% float) ──
            short_pct = f.get("short_pct_float")
            if short_pct is not None:
                c.signals["short_pct_float"] = round(float(short_pct), 4)
                if short_pct > 0.25:
                    removed[c.ticker] = f"short_interest_too_high_{round(short_pct*100)}pct"
                    continue

            # ── Gate 4: Market cap filter (avoid micro-caps < $300M) ──────────
            mkt_cap = f.get("market_cap")
            if mkt_cap is not None and mkt_cap < 300_000_000:
                removed[c.ticker] = f"market_cap_too_small_{mkt_cap//1_000_000}M"
                continue

            # ── Attach enrichment data to signals (used by ML features) ───────
            if f.get("beta") is not None:
                c.signals["beta"] = round(float(f["beta"]), 3)
            if f.get("float_shares") is not None:
                c.signals["float_shares_M"] = round(float(f["float_shares"]) / 1_000_000, 1)
            if mkt_cap is not None:
                c.signals["market_cap_B"] = round(float(mkt_cap) / 1_000_000_000, 2)
            if f.get("short_ratio") is not None:
                c.signals["short_ratio"] = round(float(f["short_ratio"]), 2)
            if f.get("fifty_two_week_low") and high52:
                low52 = f["fifty_two_week_low"]
                rng = high52 - low52
                c.signals["pct_in_52wk_range"] = round((price - low52) / rng, 3) if rng > 0 else None

            keep.append(c)
        candidates_by_strategy[strategy] = keep

    if removed and dashboard:
        for ticker, reason in removed.items():
            dashboard.event(f"⛔ {ticker} removed: {reason}")


def _ml_composite_score(c) -> float:
    """Combined ML signal quality score for candidate ranking. Higher = better."""
    p = c.ml_probability if c.ml_probability is not None else 0.5
    er = c.expected_return if c.expected_return is not None else 0.0
    tbs = c.target_before_stop_probability if c.target_before_stop_probability is not None else p
    ll = c.large_loss_probability if c.large_loss_probability is not None else (1.0 - p)
    to = c.timeout_probability if c.timeout_probability is not None else 0.3
    er_boost = 1.0 + max(-0.5, min(2.0, er))
    return p * er_boost * tbs * (1.0 - ll * 0.5) * (1.0 - to * 0.3)


def scan_account_once(
    account: PaperAccount,
    strategy: str,
    candidates: list[Candidate],
    prices: dict[str, float],
    args: argparse.Namespace,
    now: dt.datetime,
    market_close: dt.datetime,
    vwaps: dict[str, float] | None = None,
    market_breadth: float | None = None,
    dashboard: TerminalDashboard | None = None,
    spy_regime: str = "unknown",
) -> dict[str, int]:
    bought = 0
    sold = 0
    skipped = 0

    # ── Regime-aware sizing factor ─────────────────────────────────────────────
    bear_factor    = getattr(args, "bear_regime_size_factor", 0.5)
    neutral_factor = getattr(args, "neutral_regime_size_factor", 0.75)
    _r = spy_regime.lower() if spy_regime else "unknown"
    if _r in ("bear", "sell", "downtrend"):
        regime_size_factor = bear_factor
    elif _r in ("neutral", "sideways", "mixed"):
        regime_size_factor = neutral_factor
    else:
        regime_size_factor = 1.0  # bull / uptrend / unknown → full size

    # ── Daily loss limit check ────────────────────────────────────────────────
    daily_loss_limit = getattr(args, "daily_loss_limit_pct", 0.0)
    daily_loss_exceeded = False
    if daily_loss_limit > 0:
        today_str = now.strftime("%Y-%m-%d")
        today_pnl = sum(
            float(t.get("pnl", 0)) for t in account.trades
            if str(t.get("exit_time", ""))[:10] == today_str
        )
        if today_pnl < -(account.starting_cash * daily_loss_limit / 100.0):
            daily_loss_exceeded = True
            account.log_event({"type": "DAILY_LOSS_LIMIT_HIT", "today_pnl": round(today_pnl, 2)})

    partial_profit_pct = getattr(args, "partial_profit_pct", 0.5)
    partial_profit_fraction = getattr(args, "partial_profit_fraction", 0.5)
    trailing_atr_mult = getattr(args, "trailing_stop_atr_mult", 0.5)
    time_decay_scans = getattr(args, "time_decay_scans", 0)
    defensive_trim_buffer_pct = getattr(args, "defensive_trim_buffer_pct", 35.0)
    defensive_trim_fraction = getattr(args, "defensive_trim_fraction", 0.5)
    early_stop_buffer_pct = getattr(args, "early_stop_buffer_pct", 15.0)

    # ── Manage open positions ──────────────────────────────────────────────────
    for ticker, position in list(account.positions.items()):
        price = prices.get(ticker)
        if price is None:
            continue

        # Track peak price
        if price > position.peak_price:
            position.peak_price = price

        # Increment scan hold counter
        position.scans_held += 1

        # Dynamic trailing stop: after breakeven, trail below peak
        if position.breakeven_moved and trailing_atr_mult > 0 and position.atr > 0:
            trail_stop = round(position.peak_price - trailing_atr_mult * position.atr, 4)
            if trail_stop > position.stop:
                position.stop = trail_stop
                account.log_event({
                    "type": "TRAIL_STOP_UPDATE",
                    "ticker": ticker,
                    "new_stop": trail_stop,
                    "peak": round(position.peak_price, 4),
                    "price": round(price, 4),
                })
                account.save()
                if dashboard:
                    dashboard.event(f"{strategy_label(strategy)} {ticker} trail stop → {trail_stop:.2f}")

        # Breakeven stop: move to entry once price clears entry + 1 ATR
        if not position.breakeven_moved and position.atr > 0 and price >= position.entry_price + position.atr:
            new_stop = round(position.entry_price, 4)
            if new_stop > position.stop:
                position.stop = new_stop
                position.breakeven_moved = True
                account.log_event({
                    "type": "BREAKEVEN_STOP",
                    "ticker": ticker,
                    "new_stop": new_stop,
                    "price": round(price, 4),
                })
                account.save()
                if dashboard:
                    dashboard.event(f"{strategy_label(strategy)} {ticker} stop → break-even {new_stop:.2f}")

        stop_distance = position.entry_price - position.stop

        # Full exits take priority over partial management.
        if price <= position.stop:
            account.sell(ticker, price, "STOP" if not position.breakeven_moved else "BREAKEVEN_STOP", now)
            if dashboard:
                dashboard.event(f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: STOP")
            sold += 1
            continue
        if price >= position.target:
            account.sell(ticker, price, "TARGET", now)
            if dashboard:
                dashboard.event(f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: TARGET")
            sold += 1
            continue

        if stop_distance > 0 and early_stop_buffer_pct > 0:
            early_exit_price = position.stop + stop_distance * (early_stop_buffer_pct / 100.0)
            if price <= early_exit_price:
                account.sell(ticker, price, "EARLY_STOP_EXIT", now)
                if dashboard:
                    dashboard.event(
                        f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: early stop exit"
                    )
                sold += 1
                continue

        # Double-target exit: sell fraction when price overshoots target by 2×
        double_exit_pct = getattr(args, "double_target_exit_pct", 0.5)
        if (double_exit_pct > 0 and not getattr(position, "double_exited", False)
                and position.shares > 1 and position.entry_price > 0
                and position.target > position.entry_price):
            target_gain = position.target - position.entry_price
            double_trigger = position.entry_price + 2.0 * target_gain
            if price >= double_trigger:
                exit_shares = max(1, int(math.floor(position.shares * double_exit_pct)))
                sold_shares = account.sell_partial(ticker, price, exit_shares, "DOUBLE_TARGET_EXIT", now)
                if sold_shares:
                    position.double_exited = True  # type: ignore[attr-defined]
                    # Ratchet stop to breakeven so remainder rides for free
                    if position.stop < position.entry_price:
                        position.stop = round(position.entry_price, 4)
                        position.breakeven_moved = True
                    account.save()
                    if dashboard:
                        dashboard.event(
                            f"{strategy_label(strategy)} {ticker} 2× target: "
                            f"sold {sold_shares} @ {price:.2f}, stop→breakeven, {position.shares} riding"
                        )
                    sold += 1

        # Partial profit: sell fraction when price reaches partial_profit_pct of way to target
        if (partial_profit_pct > 0 and not position.partial_sold
                and position.shares > 1 and position.entry_price > 0
                and position.target > position.entry_price):
            partial_trigger = position.entry_price + partial_profit_pct * (
                position.target - position.entry_price
            )
            if price >= partial_trigger:
                partial_shares = max(1, int(math.floor(position.shares * partial_profit_fraction)))
                sold_shares = account.sell_partial(ticker, price, partial_shares, "PARTIAL_PROFIT", now)
                if sold_shares:
                    position.partial_sold = True
                    account.save()
                    if dashboard:
                        dashboard.event(
                            f"{strategy_label(strategy)} {ticker} partial profit: "
                            f"sold {sold_shares} @ {price:.2f}, keeping {position.shares}"
                        )
                    sold += 1

        if stop_distance > 0 and position.shares > 1 and price < position.entry_price:
            defensive_trim_price = position.stop + stop_distance * (defensive_trim_buffer_pct / 100.0)
            if (
                defensive_trim_buffer_pct > 0
                and not position.defensive_trimmed
                and price <= defensive_trim_price
            ):
                trim_shares = max(1, int(math.floor(position.shares * defensive_trim_fraction)))
                sold_shares = account.sell_partial(ticker, price, trim_shares, "DEFENSIVE_TRIM", now)
                if sold_shares:
                    position.defensive_trimmed = True
                    account.save()
                    if dashboard:
                        dashboard.event(
                            f"{strategy_label(strategy)} {ticker} defensive trim: "
                            f"sold {sold_shares} @ {price:.2f} before stop"
                        )
                    sold += 1

        # Time-decay exit after price-based full exits and one-shot trims.
        if time_decay_scans > 0 and position.scans_held >= time_decay_scans:
            account.sell(ticker, price, "TIME_DECAY", now)
            if dashboard:
                dashboard.event(
                    f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: "
                    f"time decay ({position.scans_held} scans)"
                )
            sold += 1

    # confirmed_pullback time-stop: validated optimum exits unresolved positions
    # at ~10 trading days (14 calendar). Without this the live algo diverges from
    # the backtested config (the +profit edge depends on the timeout exit).
    if strategy != "long_hold":
        cp_max_hold = getattr(args, "max_hold_days", 14)
        for ticker, position in list(account.positions.items()):
            if not position.entry_date:
                continue
            try:
                held = (now.date() - dt.date.fromisoformat(position.entry_date)).days
            except Exception:
                continue
            if held >= cp_max_hold:
                price = prices.get(ticker, position.entry_price)
                account.sell(ticker, price, "MAX_HOLD_DAYS", now)
                sold += 1
                if dashboard:
                    dashboard.event(
                        f"{strategy_label(strategy)} time-stop {ticker} @ {price:.2f} "
                        f"({held}d held)"
                    )

    # Long-hold: exit positions that exceeded max hold days
    if strategy == "long_hold":
        max_hold = getattr(args, "long_hold_days", 20)
        for ticker, position in list(account.positions.items()):
            if not position.entry_date:
                continue
            try:
                held_days = (now.date() - dt.date.fromisoformat(position.entry_date)).days
            except Exception:
                continue
            if held_days >= max_hold:
                price = prices.get(ticker, position.entry_price)
                account.sell(ticker, price, "MAX_HOLD_DAYS", now)
                sold += 1
                if dashboard:
                    dashboard.event(
                        f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: max hold {held_days}d"
                    )

    # EOD flatten is optional. Holding overnight keeps the paper runner closer
    # to swing-trade behavior and avoids creating day trades by default.
    flatten_now = (
        now >= (market_close - dt.timedelta(minutes=1))
        and strategy != "long_hold"
        and not getattr(args, "hold_overnight", True)
    )
    if flatten_now:
        for ticker, position in list(account.positions.items()):
            price = prices.get(ticker, position.entry_price)
            account.sell(ticker, price, "EOD_FLATTEN", now)
            if dashboard:
                dashboard.event(
                    f"{strategy_label(strategy)} sold {ticker} at {price:.2f}: end-of-day flatten"
                )
            sold += 1
        return {"bought": bought, "sold": sold, "skipped": skipped}

    # Portfolio-level hard stop
    max_dd = getattr(args, "max_portfolio_drawdown", 0.0)
    if max_dd > 0 and account.positions:
        current_value = account.total_value(prices)
        drawdown = (current_value - account.starting_cash) / account.starting_cash
        if drawdown < -max_dd:
            for port_ticker in list(account.positions.keys()):
                port_price = prices.get(port_ticker, account.positions[port_ticker].entry_price)
                account.sell(port_ticker, port_price, "PORTFOLIO_STOP", now)
                sold += 1
            account.log_event({
                "type": "PORTFOLIO_STOP",
                "drawdown_pct": round(drawdown * 100, 2),
                "threshold_pct": round(max_dd * 100, 2),
                "account_value": round(current_value, 2),
            })
            if dashboard:
                dashboard.event(
                    f"{strategy_label(strategy)} PORTFOLIO HARD STOP: drawdown {drawdown*100:.1f}%"
                )
            return {"bought": bought, "sold": sold, "skipped": skipped}

    # Breadth gate
    breadth_threshold = getattr(args, "breadth_threshold", 0.40)
    if market_breadth is not None and market_breadth < breadth_threshold:
        return {"bought": bought, "sold": sold, "skipped": skipped}

    # Time-of-day gate: no new entries in first or last 15 min of session
    market_open = market_close.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < market_open + dt.timedelta(minutes=15) or now > market_close - dt.timedelta(minutes=15):
        return {"bought": bought, "sold": sold, "skipped": skipped}

    # Daily loss limit gate
    if daily_loss_exceeded:
        return {"bought": bought, "sold": sold, "skipped": skipped}

    # Sector concentration: build current exposure map
    sector_max = getattr(args, "sector_max_positions", 3)
    sector_counts: dict[str, int] = {}
    if sector_max > 0:
        for pos in account.positions.values():
            s = getattr(pos, "sector", "unknown")
            if s and s != "unknown":
                sector_counts[s] = sector_counts.get(s, 0) + 1

    # Drawdown-adjusted sizing: shrink positions when account is underwater
    current_value = account.total_value(prices)
    account_drawdown = (current_value - account.starting_cash) / max(account.starting_cash, 1.0)
    dd_size_factor = max(0.5, 1.0 + account_drawdown * 5.0)  # -10% DD → 0.5x size

    # Combined sizing factor: drawdown × regime
    combined_size_factor = dd_size_factor * regime_size_factor

    min_rr = getattr(args, "min_risk_reward", 1.0)

    # Re-rank candidates by composite ML score
    ranked_candidates = sorted(candidates, key=_ml_composite_score, reverse=True)

    for candidate in ranked_candidates:
        if candidate.ticker in account.positions:
            position = account.positions[candidate.ticker]
            price = prices.get(candidate.ticker)
            scale_in_min_prob = getattr(args, "scale_in_min_probability", 0.55)
            scale_in_trigger_atr = getattr(args, "scale_in_trigger_atr", 0.5)
            scale_in_add_pct = getattr(args, "scale_in_add_pct", 5.0)
            can_scale_probability = (
                candidate.ml_probability is not None
                and candidate.ml_probability >= scale_in_min_prob
            )
            trigger_move = (candidate.atr or position.atr or 0.0) * scale_in_trigger_atr
            if (
                price is not None
                and not position.scaled_in
                and scale_in_add_pct > 0
                and can_scale_probability
                and price > position.entry_price + trigger_move
                and price < position.target
            ):
                account_value = account.total_value(prices)
                max_position_value = (
                    account_value
                    * (getattr(args, "position_cap_pct", 20.0) / 100.0)
                    * combined_size_factor
                )
                current_position_value = position.shares * price
                add_budget = min(
                    max(0.0, account.settled_cash - args.commission),
                    max(0.0, max_position_value - current_position_value),
                    account_value * (scale_in_add_pct / 100.0),
                )
                add_shares = int(math.floor(add_budget / price)) if price > 0 else 0
                if add_shares > 0:
                    try:
                        account.add_to_position(candidate, price, add_shares, now)
                        if dashboard:
                            dashboard.event(
                                f"{strategy_label(strategy)} scaled into {candidate.ticker}: "
                                f"+{add_shares} @ {price:.2f}"
                            )
                        bought += 1
                    except ValueError as exc:
                        skipped += 1
                        account.log_event({
                            "type": "SKIP",
                            "ticker": candidate.ticker,
                            "reason": str(exc),
                            "price": round(price, 4),
                        })
            continue
        if len(account.positions) >= args.max_positions:
            break
        price = prices.get(candidate.ticker)
        if price is None:
            skipped += 1
            continue
        if price < candidate.entry:
            continue

        # Use the actual order levels for entry validation, live R:R, and risk sizing.
        take_profit_pct = getattr(args, "take_profit_pct", 0.0)
        stop_loss_pct = getattr(args, "stop_loss_pct", 0.0)
        buy_candidate = candidate
        if take_profit_pct > 0 or stop_loss_pct > 0:
            buy_candidate = copy.copy(candidate)
            if take_profit_pct > 0:
                buy_candidate.target = round(price * (1 + take_profit_pct / 100.0), 4)
            if stop_loss_pct > 0:
                buy_candidate.stop = round(price * (1 - stop_loss_pct / 100.0), 4)

        if price <= buy_candidate.stop or price >= buy_candidate.target:
            skipped += 1
            account.log_event({
                "type": "SKIP",
                "ticker": candidate.ticker,
                "reason": "price_outside_stop_target_band",
                "price": round(price, 4),
                "entry": candidate.entry,
                "stop": buy_candidate.stop,
                "target": buy_candidate.target,
            })
            continue
        # Live R:R check at current price
        if min_rr > 0 and buy_candidate.stop > 0 and buy_candidate.target > 0:
            live_reward = buy_candidate.target - price
            live_risk   = price - buy_candidate.stop
            if live_risk <= 0 or (live_reward / live_risk) < min_rr:
                skipped += 1
                account.log_event({
                    "type": "SKIP",
                    "ticker": candidate.ticker,
                    "reason": "live_rr_below_min",
                    "price": round(price, 4),
                    "live_rr": round(live_reward / live_risk, 3) if live_risk > 0 else 0,
                    "min_rr": min_rr,
                })
                continue
        if candidate.atr > 0 and price > candidate.signal_close + args.max_entry_extension_atr * candidate.atr:
            skipped += 1
            account.log_event({
                "type": "SKIP",
                "ticker": candidate.ticker,
                "reason": "entry_too_extended_vs_signal_close",
                "price": round(price, 4),
                "signal_close": candidate.signal_close,
                "atr": candidate.atr,
            })
            continue
        # VWAP gate
        if vwaps is not None and candidate.ticker in vwaps:
            vwap = vwaps[candidate.ticker]
            if not np.isnan(vwap) and price < vwap:
                skipped += 1
                account.log_event({
                    "type": "SKIP",
                    "ticker": candidate.ticker,
                    "reason": "price_below_vwap",
                    "price": round(price, 4),
                    "vwap": round(vwap, 4),
                })
                continue
        # RVOL gate: require quiet volume (≤1.8) at entry — breakouts on volume spike
        # are chasing; want to enter before the crowd (vol dryup → quiet accumulation)
        rvol_val = candidate.signals.get("rvol")
        if rvol_val is not None and rvol_val > 1.8:
            skipped += 1
            account.log_event({
                "type": "SKIP",
                "ticker": candidate.ticker,
                "reason": "rvol_too_high",
                "rvol": round(rvol_val, 2),
            })
            continue
        # Sector RS gate: require sector 20d return ≥ -5% — don't buy pullbacks
        # in sectors that are fundamentally breaking down vs market
        sector_rs20 = candidate.signals.get("sector_etf_rs20")
        if sector_rs20 is not None and sector_rs20 < -0.05:
            skipped += 1
            account.log_event({
                "type": "SKIP",
                "ticker": candidate.ticker,
                "reason": "sector_rs_weak",
                "sector_etf_rs20": round(sector_rs20, 4),
            })
            continue
        # Sector concentration gate
        ticker_sector = "unknown"
        if sector_max > 0:
            ticker_sector = _ticker_sector(candidate.ticker)
            if ticker_sector != "unknown" and sector_counts.get(ticker_sector, 0) >= sector_max:
                skipped += 1
                account.log_event({
                    "type": "SKIP",
                    "ticker": candidate.ticker,
                    "reason": "sector_concentration",
                    "sector": ticker_sector,
                    "sector_count": sector_counts.get(ticker_sector, 0),
                })
                continue

        account_value = account.total_value(prices)

        # ── Total heat cap: never deploy more than 80% of account ─────────────
        deployed = sum(
            pos.shares * prices.get(pos.ticker, pos.entry_price)
            for pos in account.positions.values()
        )
        heat_pct = deployed / max(account_value, 1.0)
        max_heat = getattr(args, "max_heat_pct", 80.0) / 100.0
        if heat_pct >= max_heat:
            skipped += 1
            account.log_event({
                "type": "SKIP", "ticker": candidate.ticker,
                "reason": "max_heat_reached",
                "deployed_pct": round(heat_pct * 100, 1),
            })
            continue

        shares = position_size(
            account, price, account_value, args,
            ml_probability=candidate.ml_probability,
            atr=candidate.atr,
            stop=buy_candidate.stop,
            regime_factor=combined_size_factor,
            now=now,
            prices=prices,
        )
        if shares <= 0:
            skipped += 1
            continue

        # Clamp shares so we don't breach the heat cap with this single buy
        remaining_heat = max(0.0, (max_heat - heat_pct) * account_value)
        max_shares_by_heat = int(math.floor(remaining_heat / price)) if price > 0 else 0
        shares = min(shares, max_shares_by_heat)
        if shares <= 0:
            skipped += 1
            continue

        try:
            account.buy(buy_candidate, price, shares, now)
            # Stamp sector and peak_price on new position
            if candidate.ticker in account.positions:
                account.positions[candidate.ticker].sector = ticker_sector
                account.positions[candidate.ticker].peak_price = price
                account.save()
            # Update sector count for subsequent candidates this cycle
            if sector_max > 0 and ticker_sector != "unknown":
                sector_counts[ticker_sector] = sector_counts.get(ticker_sector, 0) + 1
            if dashboard:
                dashboard.event(
                    f"{strategy_label(strategy)} bought {shares} {candidate.ticker} @ {price:.2f} "
                    f"sector={ticker_sector} regime={spy_regime} score={_ml_composite_score(candidate):.3f}"
                )
            bought += 1
        except ValueError as exc:
            skipped += 1
            account.log_event({
                "type": "SKIP",
                "ticker": candidate.ticker,
                "reason": str(exc),
                "price": round(price, 4),
            })

    return {"bought": bought, "sold": sold, "skipped": skipped}


# ── RVOL + Sector RS helpers ──────────────────────────────────────────────────

_SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLRE", "XLU", "XLB", "XLC"]

def _fetch_rvol(tickers: list[str], now: dt.datetime) -> dict[str, float]:
    """Relative Volume: today's volume so far vs the average for this time-of-day over last 20 days.
    RVOL > 1.0 = above-average activity. RVOL < 0.7 = quiet (good for pullback entries)."""
    if not tickers:
        return {}
    rvol: dict[str, float] = {}
    try:
        # Today's 1-min data already fetched via latest_prices — fetch 20d daily for avg volume
        hist = _yf_download_with_retry(
            tickers=tickers, period="22d", interval="1d",
            auto_adjust=True, progress=False, threads=True,
        )
        if hist is None or hist.empty:
            return {}
        if isinstance(hist.columns, pd.MultiIndex):
            vol_df = hist["Volume"] if "Volume" in hist.columns.get_level_values(0) else pd.DataFrame()
        else:
            vol_df = hist[["Volume"]] if "Volume" in hist.columns else pd.DataFrame()

        # Intraday volume for today
        intra = _yf_download_with_retry(
            tickers=tickers, period="1d", interval="1m",
            auto_adjust=True, progress=False, threads=True,
        )
        if intra is None or intra.empty:
            return {}

        market_open_minutes = (now.hour - 9) * 60 + (now.minute - 30)
        if market_open_minutes <= 0:
            return {}
        day_fraction = min(1.0, market_open_minutes / 390.0)  # 390 min in full session

        for ticker in tickers:
            try:
                if isinstance(vol_df, pd.DataFrame) and ticker in vol_df.columns:
                    avg_daily = float(vol_df[ticker].dropna().tail(20).mean())
                elif not isinstance(vol_df.columns, pd.MultiIndex) and "Volume" in vol_df.columns:
                    avg_daily = float(vol_df["Volume"].dropna().tail(20).mean())
                else:
                    continue
                if avg_daily <= 0:
                    continue
                if isinstance(intra.columns, pd.MultiIndex):
                    today_vol = float(intra["Volume"][ticker].sum()) if ticker in intra["Volume"].columns else 0.0
                else:
                    today_vol = float(intra["Volume"].sum()) if "Volume" in intra.columns else 0.0
                expected = avg_daily * day_fraction
                rvol[ticker] = round(today_vol / expected, 2) if expected > 0 else 1.0
            except Exception:
                continue
    except Exception:
        pass
    return rvol


def _fetch_sector_rs(now: dt.datetime) -> dict[str, float]:
    """Return 20-day return for each sector ETF. Higher = stronger sector."""
    rs: dict[str, float] = {}
    try:
        raw = _yf_download_with_retry(
            tickers=_SECTOR_ETFS, period="30d", interval="1d",
            auto_adjust=True, progress=False, threads=True,
        )
        if raw is None or raw.empty:
            return rs
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) and "Close" in raw.columns.get_level_values(0) else raw
        for etf in _SECTOR_ETFS:
            if etf in close.columns:
                s = close[etf].dropna()
                if len(s) >= 21:
                    rs[etf] = round(float(s.iloc[-1] / s.iloc[-21] - 1), 4)
    except Exception:
        pass
    return rs


def scan_accounts_once(
    accounts: dict[str, PaperAccount],
    candidates_by_strategy: dict[str, list[Candidate]],
    args: argparse.Namespace,
    now: dt.datetime,
    market_close: dt.datetime,
    raw_daily: dict[str, pd.DataFrame] | None = None,
    trade_date: dt.date | None = None,
    dashboard: TerminalDashboard | None = None,
    current_spy_regime: str = "unknown",
) -> dict[str, Any]:
    tickers_needed: list[str] = []
    for strategy, account in accounts.items():
        tickers_needed.extend(
            candidate.ticker
            for candidate in candidates_by_strategy.get(strategy, [])
            if candidate.ticker not in account.positions
        )
        tickers_needed.extend(account.positions.keys())

    prices = latest_prices(tickers_needed, args.price_batch_size, dashboard=dashboard)

    # Compute market breadth from already-downloaded daily data
    market_breadth: float | None = None
    if raw_daily and trade_date:
        market_breadth = compute_market_breadth(raw_daily, trade_date)
        if dashboard:
            dashboard.event(f"Market breadth: {market_breadth:.1%} above 50d SMA")

    # Fetch VWAP for all candidate tickers
    candidate_tickers = list({
        c.ticker
        for cands in candidates_by_strategy.values()
        for c in cands
    })
    vwaps = fetch_vwap_batch(candidate_tickers) if candidate_tickers else {}

    # Fetch RVOL (relative volume) — quiet volume on pullback is bullish
    rvols = _fetch_rvol(candidate_tickers, now) if candidate_tickers else {}

    # Fetch sector relative strength — attach to candidates for context
    sector_rs = _fetch_sector_rs(now)

    # Attach RVOL + sector RS to candidate signals
    _SECTOR_ETF_MAP = {
        "Technology": "XLK", "Financial Services": "XLF", "Financials": "XLF",
        "Healthcare": "XLV", "Energy": "XLE", "Industrials": "XLI",
        "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
        "Real Estate": "XLRE", "Utilities": "XLU", "Basic Materials": "XLB",
        "Communication Services": "XLC",
    }
    for cands in candidates_by_strategy.values():
        for c in cands:
            rvol_val = rvols.get(c.ticker)
            if rvol_val is not None:
                c.signals["rvol"] = rvol_val
            sector = c.signals.get("sector") or _ticker_sector(c.ticker)
            etf = _SECTOR_ETF_MAP.get(sector)
            if etf and etf in sector_rs:
                c.signals["sector_etf_rs20"] = sector_rs[etf]

    if dashboard:
        dashboard.update(
            accounts=accounts,
            candidates_by_strategy=candidates_by_strategy,
            prices=prices,
            phase="Checking all three accounts",
        )

    summaries: dict[str, Any] = {}
    for strategy, account in accounts.items():
        cycle = scan_account_once(
            account,
            strategy,
            candidates_by_strategy.get(strategy, []),
            prices,
            args,
            now,
            market_close,
            vwaps=vwaps,
            market_breadth=market_breadth,
            dashboard=dashboard,
            spy_regime=current_spy_regime,
        )
        summaries[strategy] = write_summary(
            account,
            prices,
            candidates_by_strategy.get(strategy, []),
            cycle["bought"],
            cycle["sold"],
            cycle["skipped"],
            now,
            strategy=strategy,
        )

    if dashboard:
        dashboard.update(
            accounts=accounts,
            candidates_by_strategy=candidates_by_strategy,
            prices=prices,
            phase="Scan cycle complete",
        )
    return {"prices": prices, "summaries": summaries}


def write_summary(
    account: PaperAccount,
    prices: dict[str, float],
    candidates: list[Candidate],
    bought: int,
    sold: int,
    skipped: int,
    now: dt.datetime,
    strategy: str = "paper",
) -> dict[str, Any]:
    open_positions = []
    for ticker, position in sorted(account.positions.items()):
        price = prices.get(ticker, position.entry_price)
        open_positions.append(
            {
                "ticker": ticker,
                "shares": position.shares,
                "entry_price": position.entry_price,
                "last_price": round(price, 4),
                "stop": position.stop,
                "target": position.target,
                "atr": position.atr,
                "entry_date": getattr(position, "entry_date", None),
                "entry_time": getattr(position, "entry_time", None),
                "breakeven_moved": getattr(position, "breakeven_moved", False),
                "partial_sold": getattr(position, "partial_sold", False),
                "peak_price": getattr(position, "peak_price", position.entry_price),
                "scans_held": getattr(position, "scans_held", 0),
                "sector": getattr(position, "sector", "unknown"),
                "unrealized_pnl": round(position.pnl(price), 2),
                "unrealized_pnl_pct": round(
                    (price - position.entry_price) / position.entry_price,
                    4,
                )
                if position.entry_price
                else 0.0,
            }
        )
    summary = {
        "timestamp": now.isoformat(),
        "strategy": strategy,
        "strategy_label": strategy_label(strategy),
        "starting_cash": account.starting_cash,
        "cash": round(account.cash, 2),
        "settled_cash": round(account.settled_cash, 2),
        "unsettled_cash": round(account.unsettled_cash, 2),
        "gfv_count": account.gfv_count,
        "clv_count": account.clv_count,
        "freeriding_count": account.freeriding_count,
        "pdt_flagged": account.pdt_flagged,
        "gfv_restricted": account.gfv_restricted,
        "total_value": round(account.total_value(prices), 2),
        "realized_pnl": round(account.realized_pnl, 2),
        "open_positions": open_positions,
        "trades_closed": len(account.trades),
        "candidates": len(candidates),
        "cycle": {"bought": bought, "sold": sold, "skipped": skipped},
    }
    summary_path = account.state_path.parent / "summary.json"
    summary_path.write_text(json.dumps(_jsonable(summary), indent=2), encoding="utf-8")

    equity_path = account.state_path.parent / "equity_curve.jsonl"
    _is_first_point = not equity_path.exists() or equity_path.stat().st_size == 0
    with equity_path.open("a", encoding="utf-8") as _ef:
        if _is_first_point:
            # Seed with starting value so % normalization starts at 0%
            _ef.write(json.dumps({"t": now.isoformat(), "v": account.starting_cash, "c": account.starting_cash, "s": account.starting_cash}) + "\n")
        _ef.write(json.dumps({"t": now.isoformat(), "v": summary["total_value"], "c": summary["cash"], "s": account.starting_cash}) + "\n")

    print(
        f"[{now.strftime('%H:%M:%S')}] {strategy_label(strategy)} candidates={len(candidates)} "
        f"bought={bought} sold={sold} open={len(account.positions)} "
        f"value=${summary['total_value']:,.2f} cash=${summary['cash']:,.2f}"
    )
    return summary


def strategy_statistics(
    strategy: str,
    account: PaperAccount,
    prices: dict[str, float],
    candidates: list[Candidate],
    now: dt.datetime,
) -> dict[str, Any]:
    trades = list(account.trades)
    final_value = account.total_value(prices)
    day_pnl = final_value - account.starting_cash
    wins = [trade for trade in trades if float(trade.get("pnl", 0.0)) > 0]
    losses = [trade for trade in trades if float(trade.get("pnl", 0.0)) <= 0]
    pnl_values = [float(trade.get("pnl", 0.0)) for trade in trades]
    pnl_pct_values = [float(trade.get("pnl_pct", 0.0)) for trade in trades]
    best_trade = max(trades, key=lambda t: float(t.get("pnl", 0.0)), default=None)
    worst_trade = min(trades, key=lambda t: float(t.get("pnl", 0.0)), default=None)
    return {
        "timestamp": now.isoformat(),
        "strategy": strategy,
        "strategy_label": strategy_label(strategy),
        "starting_cash": round(account.starting_cash, 2),
        "ending_cash": round(account.cash, 2),
        "final_value": round(final_value, 2),
        "day_pnl": round(day_pnl, 2),
        "return_pct": round(day_pnl / account.starting_cash, 4) if account.starting_cash else 0.0,
        "realized_pnl": round(account.realized_pnl, 2),
        "open_positions": len(account.positions),
        "candidate_count": len(candidates),
        "closed_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "avg_trade_pnl": round(float(np.mean(pnl_values)), 2) if pnl_values else 0.0,
        "avg_trade_return_pct": round(float(np.mean(pnl_pct_values)), 4) if pnl_pct_values else 0.0,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "settled_cash": round(account.settled_cash, 2),
        "unsettled_cash": round(account.unsettled_cash, 2),
        "gfv_count": account.gfv_count,
        "clv_count": account.clv_count,
        "freeriding_count": account.freeriding_count,
        "pdt_flagged": account.pdt_flagged,
        "gfv_restricted": account.gfv_restricted,
    }


def write_end_of_day_positions(
    output_dir: Path,
    accounts: dict[str, PaperAccount],
    prices: dict[str, float],
    now: dt.datetime,
    dashboard: TerminalDashboard | None = None,
) -> list[dict[str, Any]]:
    """Significant per-position end-of-day log for every strategy account.

    Captures each open position with live price, unrealized P/L, distance to
    stop/target and hold age so positions can be reviewed and adjusted.
    Writes a dated snapshot (today's output dir) and appends one row per
    position to a persistent history JSONL at the runner root for trend review.
    """
    records: list[dict[str, Any]] = []
    stamp = now.isoformat()
    for strategy, account in accounts.items():
        label = STRATEGY_LABELS.get(strategy, strategy)
        for ticker, p in sorted(account.positions.items()):
            price = prices.get(ticker, p.entry_price)
            cost = p.entry_price * p.shares
            mv = p.shares * price
            upnl = (price - p.entry_price) * p.shares
            upnl_pct = ((price / p.entry_price) - 1.0) * 100.0 if p.entry_price else 0.0
            to_stop = ((price - p.stop) / price * 100.0) if (price and p.stop) else None
            to_target = ((p.target - price) / price * 100.0) if (price and p.target) else None
            records.append({
                "timestamp": stamp,
                "date": now.date().isoformat(),
                "strategy": strategy,
                "strategy_label": label,
                "ticker": ticker,
                "shares": p.shares,
                "entry_price": round(p.entry_price, 4),
                "current_price": round(price, 4),
                "cost_basis": round(cost, 2),
                "market_value": round(mv, 2),
                "unrealized_pnl": round(upnl, 2),
                "unrealized_pnl_pct": round(upnl_pct, 2),
                "stop": round(p.stop, 4),
                "target": round(p.target, 4),
                "pct_to_stop": round(to_stop, 2) if to_stop is not None else None,
                "pct_to_target": round(to_target, 2) if to_target is not None else None,
                "peak_price": round(p.peak_price, 4) if p.peak_price else None,
                "scans_held": p.scans_held,
                "entry_date": p.entry_date,
                "signal_date": p.signal_date,
                "sector": p.sector,
                "score": round(p.score, 3) if p.score is not None else None,
                "ml_probability": round(p.ml_probability, 4) if p.ml_probability is not None else None,
                "breakeven_moved": p.breakeven_moved,
                "partial_sold": p.partial_sold,
            })

    # Dated snapshot for today.
    snap_path = output_dir / "end_of_day_positions.json"
    snap_path.write_text(json.dumps(_jsonable({"timestamp": stamp, "positions": records}), indent=2), encoding="utf-8")

    # Per-position CSV for quick spreadsheet review.
    if records:
        csv_path = output_dir / "end_of_day_positions.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
            w.writeheader()
            for r in records:
                w.writerow(r)

    # Persistent append-only history across all days (for trend review / changes).
    hist_path = Path(output_dir).parent / "positions_eod_log.jsonl"
    try:
        with hist_path.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(_jsonable(r)) + "\n")
    except Exception as e:
        print(f"[EOD] Could not append position history: {e}")

    msg = f"End-of-day positions logged: {len(records)} open across {len(accounts)} accounts → {snap_path}"
    print(msg)
    for r in records:
        print(f"  [{r['strategy_label']}] {r['ticker']} x{r['shares']} @ ${r['entry_price']} "
              f"now ${r['current_price']} P/L ${r['unrealized_pnl']} ({r['unrealized_pnl_pct']}%) "
              f"stop ${r['stop']} target ${r['target']} held {r['scans_held']} scans")
    if dashboard:
        dashboard.event(msg)
    return records


def write_end_of_day_statistics(
    output_dir: Path,
    accounts: dict[str, PaperAccount],
    prices: dict[str, float],
    candidates_by_strategy: dict[str, list[Candidate]],
    now: dt.datetime,
    dashboard: TerminalDashboard | None = None,
) -> dict[str, Any]:
    rows = [
        strategy_statistics(
            strategy,
            account,
            prices,
            candidates_by_strategy.get(strategy, []),
            now,
        )
        for strategy, account in accounts.items()
    ]
    payload = {
        "timestamp": now.isoformat(),
        "statistics": rows,
    }
    stats_path = output_dir / "end_of_day_statistics.json"
    stats_path.write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")

    csv_path = output_dir / "end_of_day_statistics.csv"
    csv_fields = [
        "strategy",
        "strategy_label",
        "starting_cash",
        "ending_cash",
        "final_value",
        "day_pnl",
        "return_pct",
        "realized_pnl",
        "open_positions",
        "candidate_count",
        "closed_trades",
        "winning_trades",
        "losing_trades",
        "win_rate",
        "avg_trade_pnl",
        "avg_trade_return_pct",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in csv_fields})

    lines = []
    for row in rows:
        lines.append(
            f"{row['strategy_label']}: value={dollars(row['final_value'])}, "
            f"P/L={dollars(row['day_pnl'])} ({pct(row['return_pct'])}), "
            f"trades={row['closed_trades']}, win_rate={row['win_rate']:.1%}"
        )
    message = "End-of-day statistics written: " + str(stats_path)
    print(message)
    for line in lines:
        print("  " + line)
    if dashboard:
        dashboard.event(message)
        for line in lines:
            dashboard.event(line)

    # Significant per-position EOD log across all portfolios.
    write_end_of_day_positions(output_dir, accounts, prices, now, dashboard)
    return payload


def run() -> None:
    args = parse_args()
    tz = ZoneInfo(args.timezone)
    now = dt.datetime.now(tz)
    trade_date = now.date()
    market_open, market_close = market_times(trade_date, tz)

    if not is_regular_market_day(trade_date) and not args.force:
        raise SystemExit(f"{trade_date} is not a regular weekday market day. Use --force for a smoke run.")

    output_dir = today_dir(Path(args.output_dir), trade_date)
    accounts = create_strategy_accounts(
        output_dir=output_dir,
        starting_cash=args.starting_cash,
        commission=args.commission,
        reset=args.reset,
        webhook_url=getattr(args, "webhook_url", ""),
        sms_number=getattr(args, "sms_number", ""),
        sms_on_fills=getattr(args, "sms_on_fills", False),
    )

    dashboard = TerminalDashboard(enabled=not args.no_dashboard)
    candidates_by_strategy: dict[str, list[Candidate]] = {
        strategy: [] for strategy in STRATEGY_LABELS
    }
    dashboard.update(
        accounts=accounts,
        candidates_by_strategy=candidates_by_strategy,
        phase="Starting paper runner",
        market_open=market_open,
        market_close=market_close,
    )
    dashboard.start()

    final_prices: dict[str, float] = {}
    try:
        model_path = choose_model_path(args.model_bundle)
        new_model_path = Path(args.new_model_bundle) if getattr(args, "new_model_bundle", None) else None
        if args.ml_algo_only or args.no_news:
            dashboard.event("Mode: four paper accounts, no news agents")
        else:
            dashboard.event("Mode: four paper accounts by default; news agents disabled")
        dashboard.event("Accounts: Algorithm, ML Old, ML New, Algorithm + ML, Pure AI")
        dashboard.event(
            "ML gate disabled" if args.no_ml else f"Loading ML gate from {model_path}"
        )
        bundle = load_model_bundle(model_path, args.no_ml)
        new_bundle = None
        if new_model_path is not None:
            dashboard.event(f"Loading ML New challenger gate from {new_model_path}")
            new_bundle = load_model_bundle(new_model_path, args.no_ml)
        candidates_by_strategy, raw_daily = build_candidates(args, trade_date, bundle, new_bundle=new_bundle, dashboard=dashboard)
        # Enrich with fundamentals (earnings filter, 52wk high, short interest,
        # market cap, beta, float) and remove candidates that fail gates
        _enrich_candidate_fundamentals(candidates_by_strategy, trade_date, dashboard=dashboard)

        # Derive current SPY regime string for regime-aware sizing
        _spy_df_for_regime = clean_daily_frame(raw_daily.get(args.benchmark), trade_date) if raw_daily.get(args.benchmark) is not None else None
        _spy_regime_series = build_spy_regime(_spy_df_for_regime) if _spy_df_for_regime is not None and len(_spy_df_for_regime) >= 200 else None
        _today_ts = pd.Timestamp(trade_date)
        current_spy_regime = regime_value(_spy_regime_series, _today_ts) if _spy_regime_series is not None else "unknown"
        dashboard.event(f"SPY regime: {current_spy_regime}")

        # Generate AI reasons for all unique candidates
        if not getattr(args, "no_ai", False):
            ai_key = load_openrouter_api_key()
            if ai_key:
                seen: set[str] = set()
                all_unique: list[Candidate] = []
                for cands in candidates_by_strategy.values():
                    for c in cands:
                        if c.ticker not in seen:
                            seen.add(c.ticker)
                            all_unique.append(c)
                reasons = generate_candidate_reasons(ai_key, args.openrouter_model, all_unique, dashboard=dashboard)
                for cands in candidates_by_strategy.values():
                    for c in cands:
                        if c.ticker in reasons:
                            c.ai_reason = reasons[c.ticker]

        save_strategy_candidates(output_dir, candidates_by_strategy)

        if getattr(args, "alert_candidates", False):
            _alert_strats = [s.strip() for s in getattr(args, "alert_strategies", "long_hold").split(",") if s.strip()]
            dashboard.event(f"Texting candidate alerts for: {', '.join(_alert_strats)}")
            fire_candidate_alerts(
                getattr(args, "sms_number", ""),
                candidates_by_strategy,
                _alert_strats,
                state_dir=output_dir.parent,
            )

        if getattr(args, "trade_fidelity", False):
            dashboard.event("Sending ML candidates to Fidelity API...")
            import requests
            for candidate in candidates_by_strategy["combined"]:
                trade_size = 1000.0  # Example default: 10% of 10k
                shares = int(trade_size / candidate.entry)
                if shares > 0:
                    payload = {
                        "symbol": candidate.ticker,
                        "action": "Buy",
                        "quantity": shares,
                        "order_type": "Limit",
                        "limit_price": round(candidate.entry, 2),
                        "execute": getattr(args, "trade_fidelity_execute", False)
                    }
                    if getattr(args, "trade_fidelity_execute", False):
                        dashboard.stop() # Pause dashboard to get user input
                        
                        # Send MMS via Gmail (as a notification only)
                        from scripts.telegram_sender import send_telegram_notification
                        from scripts.sms_alerts import send_sms
                        import uuid
                        import json
                        import time
                        import secrets
                        
                        dashboard_url = public_dashboard_url()
                        hil_token = secrets.token_urlsafe(50)
                        hil_id = str(uuid.uuid4())
                        hil_file = Path("tmp/hil_state.json")
                        hil_file.parent.mkdir(exist_ok=True)
                        hil_file.write_text(json.dumps({
                            "id": hil_id,
                            "ticker": candidate.ticker,
                            "shares": shares,
                            "price": payload['limit_price'],
                            "status": "pending",
                            "token": hil_token
                        }))
                            
                        msg = (
                            f"🚨 <b>Trade Proposal: {candidate.ticker}</b>\n"
                            f"<b>Action:</b> BUY\n"
                            f"<b>Shares:</b> {shares}\n"
                            f"<b>Limit Price:</b> ${payload['limit_price']:.2f}\n\n"
                            f"🔐 <a href=\"{dashboard_url}\">Open Dashboard to Approve/Reject</a>\n\n"
                            f"<i>Expires in 15 minutes</i>"
                        )

                        # Primary HIL sender: Sendblue/default SMS provider.
                        # Users approve/reject inside the dashboard, not by
                        # replying to the text, so the link is stable every time.
                        _lp = float(payload['limit_price'])
                        _cost = _lp * shares
                        sms_msg = (
                            f"📈 TRADE PROPOSAL\n"
                            f"\n"
                            f"{candidate.ticker} · BUY\n"
                            f"{shares} share{'s' if shares != 1 else ''} @ ${_lp:,.2f} limit\n"
                            f"Est. cost  ${_cost:,.2f}\n"
                            f"\n"
                            f"Open the dashboard to approve or reject:\n"
                            f"{dashboard_url}\n"
                            f"\n"
                            f"Auto-rejects in 15 min\n"
                        )
                        print("\n[HIL] Sending dashboard approval link via SMS...")
                        sms_ok = False
                        try:
                            _hil_sms_to = (
                                getattr(args, "sms_number", "")
                                or os.getenv("PAPER_SMS_NUMBER")
                                or os.getenv("SMS_NUMBER")
                                or ""
                            ).strip()
                            if _hil_sms_to:
                                _r = send_sms(_hil_sms_to, sms_msg)
                                sms_ok = bool(_r.get("success"))
                                if not sms_ok:
                                    print(f"[HIL] SMS send failed: {_r.get('error') or _r}")
                            else:
                                print("[HIL] No SMS number set (PAPER_SMS_NUMBER) — cannot send dashboard approval link.")
                        except Exception as _e:
                            print(f"[HIL] SMS error: {_e}")
                        # Telegram kept as best-effort backup only.
                        if not sms_ok:
                            print("[HIL] Falling back to Telegram notification...")
                            try:
                                send_telegram_notification(msg)
                            except Exception:
                                pass

                        _hil_timeout_s = max(60, int(getattr(args, "hil_timeout_minutes", 15)) * 60)
                        _hil_on_timeout = "n" if getattr(args, "hil_auto_reject", True) else "y"
                        print(f"\n[HIL] Waiting for dashboard approval ({_hil_timeout_s // 60}m timeout, "
                              f"on timeout: {'reject' if _hil_on_timeout == 'n' else 'auto-approve'})...")
                        approval = _hil_on_timeout
                        wait_start = time.time()

                        while time.time() - wait_start < _hil_timeout_s:
                            try:
                                state = json.loads(hil_file.read_text())
                                if state.get("status") == "approved":
                                    approval = "y"
                                    break
                                elif state.get("status") == "rejected":
                                    approval = "n"
                                    break
                            except Exception:
                                pass
                            time.sleep(2)

                        if time.time() - wait_start >= _hil_timeout_s:
                            print(f"[HIL] Timeout reached. Auto-{'rejecting' if _hil_on_timeout=='n' else 'approving'}.")
                            approval = _hil_on_timeout
                            
                        # clear the file
                        if hil_file.exists():
                            hil_file.unlink()
                            
                        # Tunnel is persistent — do NOT tear it down here, or the
                        # approval page and dashboard die the moment the user
                        # taps approve/reject. It stays up for the next trade.
                        print("\n[HIL] Action recorded. Tunnel kept alive.")

                        dashboard.start() # Resume dashboard
                        
                        if approval.strip().lower() != 'y':
                            dashboard.event(f"HIL Rejected: Skipped trade for {candidate.ticker}")
                            continue
                        
                        payload["execute"] = True
                        
                    try:
                        dashboard.event(f"Fidelity POST: {'EXECUTING' if payload['execute'] else 'PREVIEWING'} {shares} {candidate.ticker} Limit {payload['limit_price']}")
                        res = requests.post("http://127.0.0.1:8001/api/fidelity/trade", json=payload, timeout=30)
                        if res.status_code == 200:
                            dashboard.event(f"Fidelity Order Status: {res.json().get('status', 'unknown')}")
                        else:
                            dashboard.event(f"Fidelity Error: {res.text}")
                    except Exception as e:
                        dashboard.event(f"Fidelity Request Failed: {e}")

        for strategy, account in accounts.items():
            account.log_event(
                {
                    "type": "START",
                    "strategy": strategy,
                    "strategy_label": strategy_label(strategy),
                    "trade_date": trade_date.isoformat(),
                    "starting_cash": args.starting_cash,
                    "candidates": len(candidates_by_strategy.get(strategy, [])),
                    "model_bundle": (
                        str(new_model_path) if strategy == "ml_new" and new_model_path is not None
                        else str(model_path) if not args.no_ml else None
                    ),
                    "model_role": "new_challenger" if strategy == "ml_new" else "old_current",
                    "mode": "three_account_ml_algorithm_only",
                    "news_enabled": False,
                    "state_path": str(account.state_path),
                }
            )
        dashboard.event(f"Each paper account initialized with {dollars(args.starting_cash)}")
        dashboard.event(f"State/logs: {output_dir}")
        dashboard.update(
            accounts=accounts,
            candidates_by_strategy=candidates_by_strategy,
            phase="Ready to scan live prices",
            market_open=market_open,
            market_close=market_close,
        )

        while True:
            now = dt.datetime.now(tz)
            for account in accounts.values():
                account.process_settlements(now.date())
            if not args.force:
                if now < market_open:
                    wait = min((market_open - now).total_seconds(), args.scan_interval_minutes * 60)
                    dashboard.wait(wait, "Waiting for market open")
                    continue
                if now > market_close:
                    held = [ticker for account in accounts.values() for ticker in account.positions]
                    final_prices = latest_prices(held, args.price_batch_size, dashboard=dashboard)
                    for strategy, account in accounts.items():
                        for ticker, position in list(account.positions.items()):
                            if strategy == "long_hold" or getattr(args, "hold_overnight", True):
                                continue  # carry positions overnight
                            exit_price = final_prices.get(ticker, position.entry_price)
                            account.sell(ticker, exit_price, "EOD_FLATTEN_AFTER_CLOSE", now)
                            dashboard.event(
                                f"{strategy_label(strategy)} sold {ticker} at {exit_price:.2f}: after-close flatten"
                            )
                        write_summary(
                            account,
                            final_prices,
                            candidates_by_strategy.get(strategy, []),
                            0,
                            0,
                            0,
                            now,
                            strategy=strategy,
                        )
                    dashboard.update(
                        accounts=accounts,
                        candidates_by_strategy=candidates_by_strategy,
                        prices=final_prices,
                        phase="Market closed",
                    )
                    dashboard.event("Market is closed. Today's paper run is complete.")
                    break

            result = scan_accounts_once(
                accounts,
                candidates_by_strategy,
                args,
                now,
                market_close,
                raw_daily=raw_daily,
                trade_date=trade_date,
                dashboard=dashboard,
                current_spy_regime=current_spy_regime,
            )
            final_prices = result.get("prices", {})
            if args.once:
                break
            if now >= market_close:
                dashboard.event("Reached market close. Today's paper run is complete.")
                break
            sleep_seconds = min(
                seconds_until_next_scan(now, args.scan_interval_minutes),
                max(1, (market_close - now).total_seconds()),
            )
            dashboard.wait(sleep_seconds, "Idle until next 15-minute scan")
        write_end_of_day_statistics(
            output_dir,
            accounts,
            final_prices,
            candidates_by_strategy,
            dt.datetime.now(tz),
            dashboard=dashboard,
        )
    finally:
        for account in accounts.values():
            account.close()
        dashboard.stop()


if __name__ == "__main__":
    run()
