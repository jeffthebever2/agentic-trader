"""Position sizing helpers, including continuous Kelly sizing and liquidity caps."""

from __future__ import annotations

import math
import datetime as dt
from typing import Dict, Tuple, Any

from tradingagents.agents.utils.memory import TradingMemoryLog


class PositionSizer:
    """Size paper positions based on confidence, historical outcomes, and risk metrics."""

    def calculate_kelly_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        portfolio_value: float,
        confidence: float = 0.5,
        max_fraction: float = 0.02,
        kelly_fraction_multiplier: float = 0.5, # default Half-Kelly, can pass 0.25 for Quarter
    ) -> float:
        if not (0 < win_rate < 1) or avg_win <= 0 or avg_loss >= 0 or portfolio_value <= 0:
            return 0.0

        b = abs(avg_win / avg_loss)
        p = win_rate
        q = 1 - p

        # Standard Discrete Kelly: f* = (bp - q) / b
        kelly_fraction = (b * p - q) / b

        # Cycle 44 SR-8: confidence is a fractional-Kelly multiplier on the OUTPUT,
        # not a discount on the win probability. Multiplying p by confidence biased
        # p downward (e.g. p=0.55, conf=0.5 → 0.275 → negative Kelly → size 0 for
        # most setups). Now it scales the final bet, which is the correct use.
        conf_mult = max(0.0, min(1.0, confidence))
        adjusted_kelly = kelly_fraction * kelly_fraction_multiplier * conf_mult
        return max(0.0, min(adjusted_kelly, max_fraction))

    def calculate_position_size(
        self,
        ticker: str,
        decision: Dict,
        portfolio_value: float,
        memory_log: TradingMemoryLog | None = None,
        adv: float | None = None,
        adv_cap_pct: float = 0.01,
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
            # Cycle 44 SR-8: conservative no-history prior (b≈1.5, p=0.50 → Kelly≈0)
            # so unproven tickers are not over-bet on an optimistic 5:1 default.
            win_rate = 0.50
            avg_win = 0.03
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
        
        # ADV Liquidity Cap
        if adv is not None and adv > 0:
            max_shares_adv = int(adv * adv_cap_pct)
            if shares > max_shares_adv:
                return max_shares_adv, f"Capped by ADV limit ({adv_cap_pct:.1%})"
                
        return shares, f"Half-Kelly={kelly_pct:.1%}, Risk/Reward={risk_reward:.2f}"
        
    def calculate_dynamic_size(
        self,
        account: Any,  # PaperAccount
        price: float,
        account_value: float,
        args: Any,     # argparse.Namespace
        ml_probability: float | None = None,
        atr: float = 0.0,
        stop: float = 0.0,
        regime_factor: float = 1.0,
        now: dt.datetime | None = None,
        adv: float | None = None,
        adv_cap_pct: float = 0.01,
        kelly_fraction_multiplier: float = 0.5, # 0.5 for Half-Kelly, 0.25 for Quarter-Kelly
        rolling_stats: Dict[str, Any] | None = None,
        expected_return: float = 0.0,
        large_loss_probability: float = 0.0,
        tier_factor: float = 1.0,
    ) -> int:
        """Dynamic position sizer. Layers: Kelly → ML confidence → streak → time-of-day →
        daily-profit lock-in → drawdown → regime → tier → ATR risk → heat cap → cash floor → liquidity cap.

        Parameters
        ----------
        tier_factor : float
            Multiplier from AlphaEngine tier: A+=1.25, A=1.0, B=0.5.
            Applied after regime_factor. Hard cap still respected.

        Parameters
        ----------
        expected_return : float
            Model expected return (e.g. 0.032 = 3.2%). Boosts cap_max slightly for
            high-expected-return candidates (within hard cap).
        large_loss_probability : float
            Model large-loss probability [0, 1]. Reduces final size proportionally.
            A hard cap should be applied upstream (in CandidateRanker / scan_account_once)
            before reaching here; this is a secondary safety valve only.
        """
        if price <= 0 or account_value <= 0:
            return 0

        cap_max = args.position_cap_pct / 100.0
        cap_min = getattr(args, "position_cap_min_pct", 10.0) / 100.0
        risk_per_trade_pct = getattr(args, "risk_per_trade_pct", 0.0)

        # ── Expected return cap boost ──────────────────────────────────────────────
        # High expected return → allow up to cap_max; low → lean toward cap_min
        # Clip to [-0.5, 2.0] to prevent extreme signals from dominating
        er_clipped = max(-0.5, min(2.0, expected_return))
        er_alloc_boost = er_clipped / 4.0  # +0% at er=0, +50% at er=2.0 (as fraction of cap range)
        # Adjust cap_max modestly — never exceed the hard cap_max
        cap_max_effective = min(cap_max, cap_min + (cap_max - cap_min) * (0.5 + er_alloc_boost))
        cap_max_effective = max(cap_min, cap_max_effective)  # never go below cap_min

        # ── 1. Kelly-fraction base pct ─────────────────────────────────────────────
        if rolling_stats is None:
            kelly_pct = 0.0
            n_trades = 0
            loss_streak = 0
            win_streak = 0
        else:
            kelly_pct = rolling_stats.get("kelly", 0.0)
            n_trades = rolling_stats.get("n", 0)
            loss_streak = rolling_stats.get("loss_streak", 0)
            win_streak = rolling_stats.get("win_streak", 0)

        if n_trades < 5:
            # Too few trades — start at midpoint, let system learn
            base_pct = (cap_min + cap_max_effective) / 2.0
        else:
            # Apply fractional Kelly multiplier
            adjusted_kelly = kelly_pct * (kelly_fraction_multiplier / 0.5) if kelly_pct > 0 else 0
            # Kelly gives optimal bet size; clamp between cap_min and cap_max_effective
            base_pct = max(cap_min, min(cap_max_effective, adjusted_kelly))

        # ── 2. ML confidence scalar (min→max within Kelly range) ──────────────────
        if ml_probability is not None and ml_probability > 0:
            ml_threshold = getattr(args, "ml_probability_threshold", None) or 0.58
            high_conf = getattr(args, "position_high_confidence_threshold", 0.80)
            t = (ml_probability - ml_threshold) / max(high_conf - ml_threshold, 0.01)
            t = max(0.0, min(1.0, t))
            # Scale base_pct up toward cap_max_effective based on ML conviction
            base_pct = base_pct + t * (cap_max_effective - base_pct) * 0.6
        else:
            base_pct = cap_min

        # ── 3. Streak adjustment ───────────────────────────────────────────────────
        # Cycle 44 DL-1: captured as an explicit factor so it also applies to the
        # ATR (primary) path, which previously discarded base_pct entirely.
        if loss_streak >= 3:
            streak_factor = 0.50   # 3+ losses: cut in half — protect from hole-digging
        elif loss_streak == 2:
            streak_factor = 0.70   # 2 losses: trim significantly
        elif loss_streak == 1:
            streak_factor = 0.85   # 1 loss: slight caution
        elif win_streak >= 4:
            streak_factor = 1.20   # hot streak: press a bit
        elif win_streak >= 2:
            streak_factor = 1.10   # 2+ wins: mild press
        else:
            streak_factor = 1.0
        base_pct = min(cap_max, base_pct * streak_factor)

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
            float(t.get("pnl", 0)) for t in getattr(account, "trades", [])
            if str(t.get("exit_time", ""))[:10] == today_str
        ) if today_str else 0.0
        
        starting_cash = getattr(account, "starting_cash", account_value)
        daily_profit_target = starting_cash * 0.01  # 1% daily profit target
        # Cycle 44 DL-1: captured as a factor so it also applies to the ATR path.
        if today_pnl >= daily_profit_target * 2:
            daily_factor = 0.50  # up 2%+ today: half size to protect
        elif today_pnl >= daily_profit_target:
            daily_factor = 0.75  # hit daily target: reduce slightly
        else:
            daily_factor = 1.0
        base_pct *= daily_factor

        # ── 6. Apply regime factor (already includes drawdown from caller) ─────────
        base_pct *= regime_factor
        base_pct = max(cap_min * 0.5, min(cap_max_effective, base_pct))  # soft clamp

        # ── 6b. Tier-factor gate ─────────────────────────────────────────────────────
        # PS-3: tier_factor must be applied exactly ONCE per sizing path. Previously it was
        # applied here (to base_pct, used by fallback path) AND again inside the ATR path
        # (to risk_dollars), making the ATR path tier-scale twice and sizes inconsistent.
        # Solution: only gate here (tier C → no trade); actual scaling happens inside each path.
        tier_factor = max(0.0, min(2.0, tier_factor))  # safety clamp
        if tier_factor <= 0:
            return 0  # tier C → no trade
        # Do NOT multiply base_pct here — fallback path applies tier_factor below.

        # ── 7. ATR dollar-risk sizing (PRIMARY path when stop/atr available) ────────
        commission = getattr(args, "commission", 0.0)
        settled_cash = getattr(account, "settled_cash", account_value)

        if risk_per_trade_pct > 0 and (atr > 0 or stop > 0):
            # Primary: dollar-risk / stop-distance
            risk_dollars = account_value * (risk_per_trade_pct / 100.0)
            # Apply tier_factor to risk_dollars so A+ risks more, B risks less
            risk_dollars = risk_dollars * tier_factor
            # Cycle 44 DL-1: also apply the risk-management safety layers that
            # previously only affected the (unused) base_pct on this path — regime,
            # loss/win streak, time-of-day, and daily profit lock-in. Without this the
            # ATR path was a pure vol-target × tier sizer and these controls were dead.
            # (ML-confidence UP-scaling is intentionally NOT applied here — it increases
            # size and needs walk-forward validation before going live.)
            risk_dollars *= regime_factor * streak_factor * tod_factor * daily_factor
            stop_dist = (price - stop) if stop > 0 and price > stop else max(atr, price * 0.01)
            atr_shares = int(math.floor(risk_dollars / stop_dist)) if stop_dist > 0 else 0

            # Hard cap scaled by tier_factor so B-tier (0.5×) caps at cap_max×0.5 not cap_max.
            # Without this: B-tier gets same 20% cap as A+ for low-ATR stocks (tier system bypassed).
            tier_cap = cap_max * min(1.0, tier_factor) if tier_factor > 0 else cap_max
            cap_shares = int(math.floor(account_value * tier_cap / price))
            final_shares = min(atr_shares, cap_shares)

            # Cash ceiling
            budget = max(0.0, settled_cash - commission)
            final_shares = max(0, min(final_shares, int(math.floor(budget / price))))
        else:
            # ── 8. Percentage-of-account fallback ─────────────────────────────────────
            # PS-3: apply tier_factor exactly once here (ATR path already applied it above)
            scaled_pct = min(cap_max, base_pct * tier_factor)
            max_position_value = account_value * scaled_pct
            # Use settled_cash as ceiling — never size into unsettled funds
            budget = max(0.0, min(settled_cash - commission, max_position_value))
            final_shares = max(0, int(math.floor(budget / price)))

        # ── 9. ADV Liquidity Cap ──────────────────────────────────────────────────
        if adv is not None and adv > 0:
            max_shares_adv = int(math.floor(adv * adv_cap_pct))
            final_shares = min(final_shares, max_shares_adv)

        # ── 10. Large-loss probability safety reduction ───────────────────────────
        # Hard cap must happen upstream; this is a soft scaling valve only.
        # Reduces size linearly: ll=0 → 1.0×, ll=0.35 → 0.65×, ll≥0.50 → 0.50×
        if large_loss_probability > 0:
            ll_scale = max(0.50, 1.0 - large_loss_probability)
            final_shares = int(math.floor(final_shares * ll_scale))

        return final_shares
