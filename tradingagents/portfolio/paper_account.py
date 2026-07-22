"""Paper-only portfolio account state and models.

Tracks separate cash, settled/unsettled, positions, trades, and compliance for each of 15 portfolios.
All portfolios start with $10,000 and are completely isolated.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from tradingagents.portfolio.paper_configs import PaperPortfolioConfig, get_portfolio


# ── Live-broker safety (spec Part 10) ────────────────────────────────────────
# Named execution targets a paper portfolio must never reach.
LIVE_BROKER_ROUTES = {
    "live_order",
    "broker_buy",
    "broker_sell",
    "real_money_execution",
    # Real integrations in this codebase:
    "fidelity_thematic_trade",
    "fidelity_thematic_exit",
    "webull_place_order",
}


def assert_paper_only(portfolio: "PaperPortfolioAccount", route_or_order_target: str | None = None) -> None:
    """Raise if a paper portfolio would touch a live-broker execution path.

    Call this at every point a paper portfolio *could* reach real execution.
    It only ever denies — it can never widen access.
    """
    if not getattr(portfolio, "paper_only", True) or not portfolio.config.paper_only:
        raise RuntimeError(f"Paper portfolio {portfolio.portfolio_id} attempted non-paper execution")
    if route_or_order_target is not None and route_or_order_target in LIVE_BROKER_ROUTES:
        raise RuntimeError(
            f"Paper portfolio {portfolio.portfolio_id} cannot access live broker route "
            f"'{route_or_order_target}'"
        )


@dataclass
class PaperPosition:
    """Open position in a paper portfolio."""

    portfolio_id: str
    ticker: str
    shares: float
    entry_price: float
    entry_date: str
    entry_timestamp: str

    stop: float
    target: float
    trailing_stop: Optional[float] = None
    trailing_activated_at: Optional[str] = None

    max_hold_days: int = 10
    source_strategy: str = "algorithm"

    ml_probability: Optional[float] = None
    ml_threshold: Optional[float] = None
    used_ml: bool = False
    used_unified_brain: bool = False
    used_ai: bool = False

    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pct: float = 0.0

    peak_price: float = 0.0            # high-water mark, for trailing stop
    bought_with_unsettled: bool = False  # GFV tracking (paper buys use settled only → False)
    entry_reason: str = ""


@dataclass
class PaperTrade:
    """Closed trade history."""

    portfolio_id: str
    ticker: str
    side: str  # BUY
    shares: float
    entry_price: float
    entry_date: str
    entry_timestamp: str

    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_timestamp: Optional[str] = None
    exit_reason: Optional[str] = None  # TARGET | STOP | MAX_HOLD | MANUAL | ...

    realized_pnl: float = 0.0
    realized_pct: float = 0.0

    source_strategy: str = "algorithm"
    ml_probability: Optional[float] = None
    ml_threshold: Optional[float] = None
    compliance_flags: list[str] = field(default_factory=list)


@dataclass
class PaperComplianceEvent:
    """Skipped trade or compliance event."""

    timestamp: str
    portfolio_id: str
    ticker: str
    action: str  # SKIPPED | WARNING | ...
    reason: str  # PDT_LIMIT_REACHED | GFV_RISK_UNSETTLED_FUNDS | ...
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperEquitySnapshot:
    """Daily or point-in-time snapshot."""

    timestamp: str
    portfolio_id: str

    cash: float
    settled_cash: float
    unsettled_cash: float

    equity: float  # cash + unrealized positions value
    realized_pnl: float
    unrealized_pnl: float

    all_time_ror: float  # (equity - initial_cash) / initial_cash * 100
    daily_ror: float

    open_positions: int
    closed_trades: int
    day_trades_last_5_days: int


@dataclass
class PaperPortfolioAccount:
    """Complete account state for one paper portfolio."""

    portfolio_id: str
    name: str
    config: PaperPortfolioConfig

    # Cash accounting
    initial_cash: float = 10000.0
    cash: float = 10000.0
    settled_cash: float = 10000.0
    unsettled_cash: float = 0.0

    # Positions and trades
    positions: list[PaperPosition] = field(default_factory=list)
    trades: list[PaperTrade] = field(default_factory=list)

    # Compliance and logs
    compliance_log: list[PaperComplianceEvent] = field(default_factory=list)
    equity_snapshots: list[PaperEquitySnapshot] = field(default_factory=list)

    # Day trade tracking (rolling 5 business days)
    day_trades_rolling_5: list[str] = field(default_factory=list)  # timestamps of day trades

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    paper_only: bool = True

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(ZoneInfo("US/Eastern")).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def realized_pnl(self) -> float:
        """Total realized P&L from closed trades."""
        return sum(t.realized_pnl for t in self.trades)

    def unrealized_pnl(self) -> float:
        """Total unrealized P&L from open positions."""
        return sum(p.unrealized_pnl for p in self.positions)

    def positions_market_value(self) -> float:
        """Mark-to-market value of all open positions."""
        return sum(p.shares * (p.current_price or p.entry_price) for p in self.positions)

    def current_equity(self) -> float:
        """Total equity = free cash + positions at market value.

        `cash` already excludes the cost basis of open positions (deducted at buy),
        so we add positions back at *market* value — not unrealized P&L.
        """
        return self.cash + self.positions_market_value()

    def sync_cash(self) -> None:
        """Keep `cash` == settled + unsettled (single source of truth)."""
        self.cash = round(self.settled_cash + self.unsettled_cash, 6)

    def all_time_ror(self) -> float:
        """All-time rate of return as percentage."""
        if self.initial_cash <= 0:
            return 0.0
        return (self.current_equity() - self.initial_cash) / self.initial_cash * 100.0

    def total_trades(self) -> int:
        """Number of *closed* trades (open positions live in .positions)."""
        return sum(1 for t in self.trades if t.exit_date is not None)

    def open_position_count(self) -> int:
        """Number of open positions."""
        return len(self.positions)

    def assert_paper_only(self, route_or_order_target: str | None = None):
        """Enforce paper-only invariant (optionally guarding a named live route)."""
        assert_paper_only(self, route_or_order_target)

    def snapshot(self) -> "PaperEquitySnapshot":
        """Build a point-in-time equity snapshot from current state."""
        from tradingagents.portfolio.paper_compliance import count_day_trades_last_5_business_days
        equity = self.current_equity()
        # daily ROR vs the most recent prior snapshot's equity
        daily = 0.0
        if self.equity_snapshots:
            prev = self.equity_snapshots[-1].equity
            if prev > 0:
                daily = (equity - prev) / prev * 100.0
        return PaperEquitySnapshot(
            timestamp=datetime.now(ZoneInfo("US/Eastern")).isoformat(),
            portfolio_id=self.portfolio_id,
            cash=round(self.cash, 2),
            settled_cash=round(self.settled_cash, 2),
            unsettled_cash=round(self.unsettled_cash, 2),
            equity=round(equity, 2),
            realized_pnl=round(self.realized_pnl(), 2),
            unrealized_pnl=round(self.unrealized_pnl(), 2),
            all_time_ror=round(self.all_time_ror(), 4),
            daily_ror=round(daily, 4),
            open_positions=self.open_position_count(),
            closed_trades=self.total_trades(),
            day_trades_last_5_days=count_day_trades_last_5_business_days(self),
        )

    def record_snapshot(self) -> "PaperEquitySnapshot":
        """Append a snapshot, replacing any earlier one from the same calendar day."""
        snap = self.snapshot()
        today = snap.timestamp[:10]
        self.equity_snapshots = [s for s in self.equity_snapshots if s.timestamp[:10] != today]
        self.equity_snapshots.append(snap)
        return snap

    def write_daily_file(self, snapshots_base: Path) -> Path:
        """Write the equity-snapshot history to paper_accounts/account_{id}.json."""
        snapshots_base.mkdir(parents=True, exist_ok=True)
        path = snapshots_base / f"account_{self.portfolio_id}.json"
        payload = {
            "portfolio_id": self.portfolio_id,
            "name": self.name,
            "initial_cash": self.initial_cash,
            "current_equity": round(self.current_equity(), 2),
            "all_time_ror": round(self.all_time_ror(), 4),
            "snapshots": [asdict(s) for s in self.equity_snapshots],
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        return path

    def export_csv(self, csv_base: Path) -> Path:
        """Export closed-trade history to CSV for backtest/analysis."""
        csv_base.mkdir(parents=True, exist_ok=True)
        path = csv_base / f"account_{self.portfolio_id}_trades.csv"
        cols = [
            "portfolio_id", "ticker", "side", "shares", "entry_price", "entry_date",
            "exit_price", "exit_date", "exit_reason", "realized_pnl", "realized_pct",
            "source_strategy", "ml_probability", "ml_threshold",
        ]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for t in self.trades:
                w.writerow(asdict(t))
        return path

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "portfolio_id": self.portfolio_id,
            "name": self.name,
            "config": asdict(self.config),
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "settled_cash": self.settled_cash,
            "unsettled_cash": self.unsettled_cash,
            "positions": [asdict(p) for p in self.positions],
            "trades": [asdict(t) for t in self.trades],
            "compliance_log": [asdict(c) for c in self.compliance_log],
            "equity_snapshots": [asdict(s) for s in self.equity_snapshots],
            "day_trades_rolling_5": self.day_trades_rolling_5,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "paper_only": self.paper_only,
        }

    def save(self, base_path: Path):
        """Save account state to JSON file (atomic write, fresh updated_at)."""
        base_path.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(ZoneInfo("US/Eastern")).isoformat()
        file_path = base_path / f"{self.portfolio_id}.json"
        tmp_path = file_path.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        tmp_path.replace(file_path)  # atomic — no torn JSON if interrupted

    @staticmethod
    def load(portfolio_id: str, base_path: Path) -> PaperPortfolioAccount:
        """Load account state from JSON file."""
        file_path = base_path / f"{portfolio_id}.json"
        if not file_path.exists():
            # Return fresh account
            cfg = replace(get_portfolio(portfolio_id))  # per-account copy — never mutate the shared registry singleton
            return PaperPortfolioAccount(
                portfolio_id=portfolio_id,
                name=cfg.name,
                config=cfg,
                initial_cash=cfg.initial_cash,
                cash=cfg.initial_cash,
                settled_cash=cfg.initial_cash,
            )

        with open(file_path) as f:
            data = json.load(f)

        cfg = replace(get_portfolio(portfolio_id))  # per-account copy — never mutate the shared registry singleton
        acc = PaperPortfolioAccount(
            portfolio_id=portfolio_id,
            name=data.get("name", cfg.name),
            config=cfg,
            initial_cash=data.get("initial_cash", 10000.0),
            cash=data.get("cash", 10000.0),
            settled_cash=data.get("settled_cash", 10000.0),
            unsettled_cash=data.get("unsettled_cash", 0.0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            paper_only=data.get("paper_only", True),
        )

        # Reconstruct positions, trades, compliance log
        if "positions" in data and data["positions"]:
            acc.positions = [PaperPosition(**p) for p in data["positions"]]
        if "trades" in data and data["trades"]:
            acc.trades = [PaperTrade(**t) for t in data["trades"]]
        if "compliance_log" in data and data["compliance_log"]:
            acc.compliance_log = [PaperComplianceEvent(**c) for c in data["compliance_log"]]
        if "equity_snapshots" in data and data["equity_snapshots"]:
            acc.equity_snapshots = [PaperEquitySnapshot(**s) for s in data["equity_snapshots"]]
        if "day_trades_rolling_5" in data:
            acc.day_trades_rolling_5 = data["day_trades_rolling_5"]

        return acc

    @staticmethod
    def reset(portfolio_id: str, base_path: Path):
        """Reset account to fresh $10,000."""
        cfg = replace(get_portfolio(portfolio_id))  # per-account copy — never mutate the shared registry singleton
        fresh = PaperPortfolioAccount(
            portfolio_id=portfolio_id,
            name=cfg.name,
            config=cfg,
            initial_cash=cfg.initial_cash,
            cash=cfg.initial_cash,
            settled_cash=cfg.initial_cash,
        )
        fresh.save(base_path)
        return fresh
