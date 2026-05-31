"""Drawdown circuit breakers for paper-trading runs."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Tuple


class DrawdownMonitor:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.max_daily_loss = float(cfg.get("max_daily_loss", -0.05))
        self.max_monthly_loss = float(cfg.get("max_monthly_loss", -0.15))
        self.trade_log_path = Path(
            cfg.get("trade_log_path", "~/.tradingagents/logs/trade_results.jsonl")
        ).expanduser()

    def should_keep_trading(self, unrealized_pnl_pct: float = 0.0) -> Tuple[bool, str]:
        """Circuit-breaker check.

        Cycle 44: ``unrealized_pnl_pct`` lets the caller fold in open-position
        mark-to-market (account-fraction terms). Without it the breaker was blind
        to open losers and could be bypassed by holding losing positions open
        during a selloff — exactly the scenario it exists to catch.
        """
        today_pnl = self._calculate_daily_pnl() + unrealized_pnl_pct
        if today_pnl < self.max_daily_loss:
            return False, (
                f"Daily loss {today_pnl:.1%} (incl. open MTM) exceeds limit "
                f"{self.max_daily_loss:.1%}. STOP TRADING."
            )

        month_pnl = self._calculate_monthly_pnl() + unrealized_pnl_pct
        if month_pnl < self.max_monthly_loss:
            return False, (
                f"Monthly loss {month_pnl:.1%} (incl. open MTM) exceeds limit "
                f"{self.max_monthly_loss:.1%}. STOP TRADING."
            )
        return True, "OK to trade"

    def _iter_trades(self):
        if not self.trade_log_path.exists():
            return
        with open(self.trade_log_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    @staticmethod
    def _pnl_pct(trade: dict) -> float:
        """Per-trade return as an account fraction.

        Cycle 44: when a trade carries ``capital_fraction`` (notional / account
        equity at entry) the per-trade return is weighted by it, so summing
        across trades approximates the true account-level daily/monthly move
        rather than naively adding position-level returns of unequal size.
        """
        if "pnl_pct" in trade:
            ret = float(trade["pnl_pct"])
        else:
            pnl = float(trade.get("pnl", 0.0))
            ret = pnl / 100 if abs(pnl) > 1 else pnl
        cf = trade.get("capital_fraction")
        if cf is not None:
            try:
                return ret * float(cf)
            except (TypeError, ValueError):
                return ret
        return ret

    @staticmethod
    def _exit_dt(trade: dict) -> datetime | None:
        raw = trade.get("exit_date") or trade.get("exit_time")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _calculate_daily_pnl(self) -> float:
        today = datetime.now().date()
        total = 0.0
        for trade in self._iter_trades():
            exit_dt = self._exit_dt(trade)
            if exit_dt and exit_dt.date() == today:
                total += self._pnl_pct(trade)
        return total

    def _calculate_monthly_pnl(self) -> float:
        now = datetime.now()
        total = 0.0
        for trade in self._iter_trades():
            exit_dt = self._exit_dt(trade)
            if exit_dt and exit_dt.year == now.year and exit_dt.month == now.month:
                total += self._pnl_pct(trade)
        return total
