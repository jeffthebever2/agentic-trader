"""Simple HTML performance reports."""

from __future__ import annotations

from datetime import datetime, timedelta
import json

from tradingagents.portfolio import PortfolioState


class ReportGenerator:
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def generate_weekly_report(self) -> str:
        portfolio = PortfolioState(self.config)
        rows = []
        for trade in self._get_trades_since(days=7):
            rows.append(
                f"<tr><td>{trade['ticker']}</td><td>{trade.get('entry_price', 0):.2f}</td>"
                f"<td>{trade.get('exit_price', 0):.2f}</td><td>{trade.get('pnl_pct', 0):+.1%}</td>"
                f"<td>{trade.get('exit_reason', '')}</td></tr>"
            )
        return f"""
<h1>Trading Report: Week of {datetime.now().strftime('%Y-%m-%d')}</h1>
<h2>Portfolio Summary</h2>
<ul>
  <li>Total Value: ${portfolio.total_value:,.0f}</li>
  <li>Positions: {len(portfolio.positions)}</li>
  <li>Invested: {portfolio.portfolio_delta:.1%}</li>
  <li>Cash: ${portfolio.cash:,.0f}</li>
</ul>
<h2>Trades This Week</h2>
<table border="1"><tr><th>Ticker</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th></tr>
{''.join(rows)}
</table>
"""

    def _get_trades_since(self, days: int) -> list[dict]:
        portfolio = PortfolioState(self.config)
        cutoff = datetime.now() - timedelta(days=days)
        if not portfolio.trade_log_path.exists():
            return []
        trades = []
        with open(portfolio.trade_log_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                trade = json.loads(line)
                exit_dt = self._trade_exit_dt(trade)
                if exit_dt and exit_dt >= cutoff:
                    trades.append(trade)
        return trades

    @staticmethod
    def _trade_exit_dt(trade: dict) -> datetime | None:
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
