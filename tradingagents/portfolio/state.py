"""Persistent paper-portfolio state for TradingAgents.

This module deliberately records paper decisions and holdings only. It does
not talk to a live broker or place orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import yfinance as yf


PriceLookup = Callable[[str], Optional[float]]
SectorLookup = Callable[[str], str]


def get_current_price(ticker: str) -> Optional[float]:
    """Return the latest close/regular-market price for a ticker, if available."""
    try:
        info = yf.Ticker(ticker).fast_info
        for key in ("last_price", "lastPrice", "regular_market_price"):
            value = getattr(info, key, None) if not isinstance(info, dict) else info.get(key)
            if value:
                return float(value)
    except Exception:
        pass

    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        return None
    return None


def get_sector(ticker: str) -> str:
    """Return a ticker sector using yfinance metadata."""
    try:
        sector = yf.Ticker(ticker).info.get("sector")
        return sector or "Unknown"
    except Exception:
        return "Unknown"


@dataclass
class Position:
    ticker: str
    entry_price: float
    shares: float
    entry_date: datetime
    stop_loss: float
    take_profit: float
    thesis: str
    sector: str = "Unknown"

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        raw_date = data.get("entry_date") or datetime.now().isoformat()
        entry_date = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(raw_date)
        return cls(
            ticker=str(data["ticker"]).upper(),
            entry_price=float(data["entry_price"]),
            shares=float(data["shares"]),
            entry_date=entry_date,
            stop_loss=float(data["stop_loss"]),
            take_profit=float(data["take_profit"]),
            thesis=str(data.get("thesis", "")),
            sector=str(data.get("sector", "Unknown")),
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["entry_date"] = self.entry_date.isoformat()
        return data

    def cost_basis(self) -> float:
        return self.shares * self.entry_price

    def current_value(self, price_lookup: PriceLookup = get_current_price) -> float:
        price = price_lookup(self.ticker) or self.entry_price
        return self.shares * price

    def unrealized_pnl(self, price_lookup: PriceLookup = get_current_price) -> float:
        return self.current_value(price_lookup) - self.cost_basis()

    def unrealized_pnl_pct(self, price_lookup: PriceLookup = get_current_price) -> float:
        basis = self.cost_basis()
        return self.unrealized_pnl(price_lookup) / basis if basis else 0.0


class PortfolioState:
    """Tracks paper holdings, cash, and portfolio-level risk constraints."""

    def __init__(
        self,
        config: Optional[dict] = None,
        price_lookup: PriceLookup = get_current_price,
        sector_lookup: SectorLookup = get_sector,
    ):
        cfg = config or {}
        self.state_file = Path(
            cfg.get("portfolio_state_path", "~/.tradingagents/portfolio/positions.json")
        ).expanduser()
        self.trade_log_path = Path(
            cfg.get("trade_log_path", "~/.tradingagents/logs/trade_results.jsonl")
        ).expanduser()
        self.paper_decision_log_path = Path(
            cfg.get("paper_decision_log_path", "~/.tradingagents/logs/paper_decisions.jsonl")
        ).expanduser()
        self.starting_cash = float(cfg.get("starting_cash", 100000.0))
        self.max_positions = int(cfg.get("max_positions", 10))
        self.max_sector_exposure = float(cfg.get("max_sector_exposure", 0.30))
        self.max_position_size = float(cfg.get("max_position_size", 0.05))
        self.price_lookup = price_lookup
        self.sector_lookup = sector_lookup

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.paper_decision_log_path.parent.mkdir(parents=True, exist_ok=True)

        self.cash, self.positions = self._load()

    def _load(self) -> Tuple[float, Dict[str, Position]]:
        if not self.state_file.exists():
            return self.starting_cash, {}

        data = json.loads(self.state_file.read_text(encoding="utf-8") or "{}")
        if "positions" in data:
            positions_data = data.get("positions", {})
            cash = float(data.get("cash", self.starting_cash))
        else:
            # Compatibility with a plain {ticker: position} shape.
            positions_data = data
            cash = self.starting_cash

        positions = {
            ticker.upper(): Position.from_dict(pos)
            for ticker, pos in positions_data.items()
        }
        return cash, positions

    def save(self) -> None:
        payload = {
            "cash": self.cash,
            "positions": {
                ticker: position.to_dict()
                for ticker, position in sorted(self.positions.items())
            },
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    @property
    def invested_value(self) -> float:
        return sum(pos.current_value(self.price_lookup) for pos in self.positions.values())

    @property
    def total_value(self) -> float:
        return self.cash + self.invested_value

    @property
    def portfolio_delta(self) -> float:
        total = self.total_value
        return self.invested_value / total if total else 0.0

    def get_exposure(self, ticker: str) -> float:
        ticker = ticker.upper()
        if ticker not in self.positions or self.total_value <= 0:
            return 0.0
        return self.positions[ticker].current_value(self.price_lookup) / self.total_value

    def get_sector_exposure(self, sector: str) -> float:
        total = self.total_value
        if total <= 0:
            return 0.0
        sector_value = sum(
            pos.current_value(self.price_lookup)
            for pos in self.positions.values()
            if pos.sector == sector
        )
        return sector_value / total

    def can_buy(self, ticker: str, shares: float, price: Optional[float] = None) -> Tuple[bool, str]:
        ticker = ticker.upper()
        price = price if price is not None else self.price_lookup(ticker)
        if price is None or price <= 0:
            return False, f"No current price available for {ticker}"

        if ticker in self.positions:
            return False, f"Already long {ticker}"
        if len(self.positions) >= self.max_positions:
            return False, f"Already holding {self.max_positions} positions (max)"

        position_cost = shares * price
        total = self.total_value
        position_pct = position_cost / total if total else 0.0
        if position_pct > self.max_position_size:
            return False, (
                f"Position would be {position_pct:.1%} "
                f"(max {self.max_position_size:.1%})"
            )

        sector = self.sector_lookup(ticker)
        new_sector_exposure = self.get_sector_exposure(sector) + position_pct
        if new_sector_exposure > self.max_sector_exposure:
            return False, (
                f"{sector} exposure would be {new_sector_exposure:.1%} "
                f"(max {self.max_sector_exposure:.1%})"
            )
        if position_cost > self.cash:
            return False, f"Insufficient cash (${self.cash:,.0f} available, need ${position_cost:,.0f})"
        return True, "OK to buy"

    def execute_buy(
        self,
        ticker: str,
        shares: float,
        entry_price: float,
        thesis: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        atr: Optional[float] = None,
    ) -> None:
        ticker = ticker.upper()
        ok, reason = self.can_buy(ticker, shares, entry_price)
        if not ok:
            raise ValueError(reason)

        # Cycle 44 V-24: derive fallback levels from ATR (1.0 stop / 1.2 target,
        # matching the system geometry) when explicit levels are absent, instead of
        # a fixed 2%/10% that ignores volatility.
        if stop_loss is None:
            stop_loss = (entry_price - 1.0 * atr) if atr and atr > 0 else entry_price * 0.98
        if take_profit is None:
            take_profit = (entry_price + 1.2 * atr) if atr and atr > 0 else entry_price * 1.10

        self.cash -= shares * entry_price
        self.positions[ticker] = Position(
            ticker=ticker,
            entry_price=entry_price,
            shares=shares,
            entry_date=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            thesis=thesis,
            sector=self.sector_lookup(ticker),
        )
        self.save()

    def execute_sell(self, ticker: str, reason: str) -> None:
        ticker = ticker.upper()
        if ticker not in self.positions:
            raise ValueError(f"Not holding {ticker}")
        pos = self.positions[ticker]
        # Cycle 44 V-26: gap-aware fill. A stop must not fill ABOVE its level and a
        # target must not fill BELOW its level — otherwise realized PnL is
        # systematically optimistic vs the level that actually triggered the exit.
        live = self.price_lookup(ticker) or pos.entry_price
        reason_u = (reason or "").upper()
        if "STOP" in reason_u:
            exit_price = min(live, pos.stop_loss)
        elif "TAKE_PROFIT" in reason_u or "TARGET" in reason_u:
            exit_price = max(live, pos.take_profit)
        else:
            exit_price = live
        self.cash += pos.shares * exit_price
        self._log_trade_result(ticker, pos, exit_price, reason)
        del self.positions[ticker]
        self.save()

    def _log_trade_result(self, ticker: str, pos: Position, exit_price: float, reason: str) -> None:
        pnl = (exit_price - pos.entry_price) * pos.shares
        pnl_pct = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0.0
        record = {
            "ticker": ticker,
            "entry_date": pos.entry_date.isoformat(),
            "exit_date": datetime.now().isoformat(),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "shares": pos.shares,
            "thesis": pos.thesis,
            "exit_reason": reason,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        }
        with open(self.trade_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def record_paper_decision(self, ticker: str, decision: str, rating: str, trade_date: str) -> None:
        record = {
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker.upper(),
            "trade_date": trade_date,
            "rating": rating,
            "decision": decision,
            "cash": self.cash,
            "total_value": self.total_value,
            "positions": sorted(self.positions.keys()),
        }
        with open(self.paper_decision_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def check_stops_and_limits(self) -> list[tuple[str, str, str]]:
        actions = []
        for ticker, pos in self.positions.items():
            current_price = self.price_lookup(ticker)
            if current_price is None:
                continue
            if current_price <= pos.stop_loss:
                actions.append(("SELL", ticker, "STOP_LOSS"))
            elif current_price >= pos.take_profit:
                actions.append(("SELL", ticker, "TAKE_PROFIT"))
        return actions

    def context_for(self, ticker: str) -> str:
        """Return compact markdown portfolio context for prompt injection."""
        ticker = ticker.upper()
        price = self.price_lookup(ticker)
        suggested_value = self.total_value * self.max_position_size
        suggested_shares = int(suggested_value / price) if price else 0
        buy_ok, buy_reason = self.can_buy(ticker, suggested_shares, price) if suggested_shares else (
            False,
            f"No current price available for {ticker}",
        )

        lines = [
            "## Paper Portfolio Context",
            f"- Total value: ${self.total_value:,.2f}",
            f"- Cash: ${self.cash:,.2f}",
            f"- Invested: {self.portfolio_delta:.1%}",
            f"- Open positions: {len(self.positions)}/{self.max_positions}",
            f"- Current {ticker} exposure: {self.get_exposure(ticker):.1%}",
            f"- Buy capacity check: {'PASS' if buy_ok else 'BLOCK'} - {buy_reason}",
            "  - Dynamic sizing guidelines for this trade:",
            "    - 0.50 Confidence: Max 1% allocation",
            "    - 0.70 Confidence: Max 3% allocation",
            "    - 0.85+ Confidence: Max 10% allocation",
        ]

        stops = self.check_stops_and_limits()
        if stops:
            rendered = "; ".join(f"{action} {sym} ({reason})" for action, sym, reason in stops)
            lines.append(f"- Stop/take-profit alerts: {rendered}")

        if self.positions:
            lines.append("- Holdings:")
            for sym, pos in sorted(self.positions.items()):
                value = pos.current_value(self.price_lookup)
                pnl = pos.unrealized_pnl_pct(self.price_lookup)
                lines.append(
                    f"  - {sym}: {pos.shares:g} shares, {pos.sector}, "
                    f"value ${value:,.2f}, PnL {pnl:+.1%}, thesis: {pos.thesis[:120]}"
                )
        return "\n".join(lines)
