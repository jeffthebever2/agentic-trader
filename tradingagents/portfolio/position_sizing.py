"""Position sizing helpers, including conservative half-Kelly sizing."""

from __future__ import annotations

from typing import Dict, Tuple

from tradingagents.agents.utils.memory import TradingMemoryLog


class PositionSizer:
    """Size paper positions based on confidence and historical outcomes."""

    def calculate_kelly_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        portfolio_value: float,
        confidence: float = 0.5,
        max_fraction: float = 0.02,
    ) -> float:
        if not (0 < win_rate < 1) or avg_win <= 0 or avg_loss >= 0 or portfolio_value <= 0:
            return 0.0

        adjusted_win_rate = max(0.0, min(1.0, win_rate * confidence))
        b = abs(avg_win / avg_loss)
        p = adjusted_win_rate
        q = 1 - p
        kelly_fraction = (b * p - q) / b
        half_kelly = kelly_fraction / 2
        return max(0.0, min(half_kelly, max_fraction))

    def calculate_position_size(
        self,
        ticker: str,
        decision: Dict,
        portfolio_value: float,
        memory_log: TradingMemoryLog | None = None,
    ) -> Tuple[int, str]:
        confidence = float(decision.get("confidence", 0.5))
        entry_target = decision.get("entry_target")
        stop_loss = decision.get("stop_loss")
        take_profit = decision.get("take_profit")
        if entry_target is None or stop_loss is None or take_profit is None:
            return 0, "Missing entry/stop/target"

        entry_target = float(entry_target)
        stop_loss = float(stop_loss)
        take_profit = float(take_profit)
        risk_amount = abs(entry_target - stop_loss)
        reward_amount = abs(take_profit - entry_target)
        if risk_amount <= 0:
            return 0, "Invalid stop loss"

        risk_reward = reward_amount / risk_amount
        if risk_reward < 1:
            return 0, f"Risk/reward is {risk_reward:.2f} (need >= 1)"

        memory_log = memory_log or TradingMemoryLog()
        stats = memory_log.get_decision_accuracy(ticker)
        ticker_stats = stats.get(ticker.upper()) or stats.get(ticker)
        if ticker_stats:
            win_rate = float(ticker_stats["win_rate"])
            avg_win = float(ticker_stats["avg_win"]) / 100
            avg_loss = float(ticker_stats["avg_loss"]) / 100
        else:
            win_rate = 0.50
            avg_win = 0.10
            avg_loss = -0.02

        kelly_pct = self.calculate_kelly_size(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            portfolio_value=portfolio_value,
            confidence=confidence,
        )
        if kelly_pct <= 0:
            return 0, f"No positive expectancy (win_rate={win_rate:.1%})"

        dollars_to_risk = portfolio_value * kelly_pct
        shares = int(dollars_to_risk / risk_amount)
        return shares, f"Half-Kelly={kelly_pct:.1%}, Risk/Reward={risk_reward:.2f}"
