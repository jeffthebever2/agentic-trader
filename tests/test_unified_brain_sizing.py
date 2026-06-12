"""B11/B12/B14/B15 sizing upgrades in UnifiedBrain.allocate."""
import pytest

from tradingagents.portfolio.unified_brain import (
    UnifiedBrain,
    UnifiedCandidate,
    account_drawdown_frac,
    rolling_trade_stats,
)


# stop 10 wide so risk-based share count stays below the 20% position cap —
# otherwise cap_shares binds and risk_dollars stops reflecting the size factors.
def make_candidate(ticker="TEST", alpha=0.60, tier="A", entry=100.0, stop=90.0,
                   target=120.0, atr=2.0):
    return UnifiedCandidate(
        ticker=ticker,
        strategy_sources=["algorithm"],
        primary_source="algorithm",
        direction="long",
        entry=entry,
        stop=stop,
        take_profit=target,
        horizon_days=10,
        atr=atr,
        confidence=0.6,
        expected_return=0.01,
        large_loss_probability=0.2,
        target_before_stop_probability=0.5,
        timeout_probability=0.4,
        breakout_score=100.0,
        regime_score=0.9,
        ticker_reliability=0.6,
        liquidity_score=0.8,
        volatility_score=0.8,
        alpha_score=alpha,
        tier=tier,
    )


def allocate(brain, cands, rolling_stats=None, drawdown_frac=None, account_value=100_000.0):
    return brain.allocate(
        candidates=cands,
        account_value=account_value,
        settled_cash=account_value,
        current_heat_pct=0.0,
        current_positions={},
        vix_level=20.0,
        regime_state=None,
        spy_regime="bull",
        rolling_stats=rolling_stats,
        drawdown_frac=drawdown_frac,
    )


def trades(pnls):
    return [{"pnl": p} for p in pnls]


class TestKellySizing:  # B11
    def test_no_stats_uses_flat_risk(self):
        brain = UnifiedBrain()
        sized = allocate(brain, [make_candidate()])
        assert len(sized) == 1
        # flat 1% of 100k = $1000 risk (± conviction/regime factors applied to dollars)
        assert sized[0].risk_dollars > 0

    def test_kelly_scales_risk_up_on_strong_record(self):
        brain = UnifiedBrain()
        flat = allocate(brain, [make_candidate()])[0].risk_dollars
        # 80% WR, wins 2x losses → strong half-Kelly
        stats = rolling_trade_stats(trades([200, 200, 200, 200, -100] * 4))
        assert stats["n"] == 20
        kelly = allocate(brain, [make_candidate()], rolling_stats=stats)[0].risk_dollars
        assert kelly > flat

    def test_kelly_clamped_at_max(self):
        brain = UnifiedBrain()
        stats = {"kelly": 0.50, "n": 20, "loss_streak": 0, "win_streak": 0}
        sized = allocate(brain, [make_candidate(tier="A", alpha=0.60)], rolling_stats=stats)[0]
        # cap = 2.5% of 100k = $2500 base; conviction*regime ≈ ~ amplifies/shrinks,
        # but the risk_pct itself must be clamped — verify via cfg ceiling:
        max_base = 100_000 * 0.025 * 1.3 * 1.25  # rr_factor cap × tier cap
        assert sized.risk_dollars <= max_base

    def test_too_few_trades_falls_back_to_flat(self):
        brain = UnifiedBrain()
        flat = allocate(brain, [make_candidate()])[0].risk_dollars
        stats = {"kelly": 0.50, "n": 3, "loss_streak": 0, "win_streak": 0}
        sized = allocate(brain, [make_candidate()], rolling_stats=stats)[0]
        assert sized.risk_dollars == pytest.approx(flat, rel=0.01)

    def test_kelly_disabled_by_config(self):
        brain = UnifiedBrain(config={"kelly_sizing": False})
        flat = allocate(brain, [make_candidate()])[0].risk_dollars
        stats = {"kelly": 0.50, "n": 20, "loss_streak": 0, "win_streak": 0}
        sized = allocate(brain, [make_candidate()], rolling_stats=stats)[0]
        assert sized.risk_dollars == pytest.approx(flat, rel=0.01)


class TestDrawdownThrottle:  # B14
    @pytest.mark.parametrize("dd,factor", [(0.0, 1.0), (0.06, 0.75), (0.12, 0.5), (0.20, 0.25)])
    def test_dd_tiers(self, dd, factor):
        brain = UnifiedBrain()
        base = allocate(brain, [make_candidate()])[0].risk_dollars
        throttled = allocate(brain, [make_candidate()], drawdown_frac=dd)[0].risk_dollars
        assert throttled == pytest.approx(base * factor, rel=0.05)

    def test_throttle_disabled_by_config(self):
        brain = UnifiedBrain(config={"dd_throttle": False})
        base = allocate(brain, [make_candidate()])[0].risk_dollars
        throttled = allocate(brain, [make_candidate()], drawdown_frac=0.20)[0].risk_dollars
        assert throttled == pytest.approx(base, rel=0.01)


class TestStreakScaling:  # B15
    def test_loss_streak_shrinks(self):
        brain = UnifiedBrain()
        base = allocate(brain, [make_candidate()])[0].risk_dollars
        stats = {"kelly": 0.0, "n": 5, "loss_streak": 3, "win_streak": 0}
        sized = allocate(brain, [make_candidate()], rolling_stats=stats)[0].risk_dollars
        assert sized == pytest.approx(base * 0.5, rel=0.05)

    def test_win_streak_grows(self):
        brain = UnifiedBrain()
        base = allocate(brain, [make_candidate()])[0].risk_dollars
        stats = {"kelly": 0.0, "n": 5, "loss_streak": 0, "win_streak": 4}
        sized = allocate(brain, [make_candidate()], rolling_stats=stats)[0].risk_dollars
        assert sized == pytest.approx(base * 1.2, rel=0.05)


class TestContinuousConviction:  # B12
    def test_alpha_gradient_changes_size(self):
        brain = UnifiedBrain()
        low = allocate(brain, [make_candidate(alpha=0.56, tier="A")])[0].risk_dollars
        high = allocate(brain, [make_candidate(alpha=0.70, tier="A")])[0].risk_dollars
        assert high > low  # same tier, higher alpha → more size

    def test_clamped_to_tier_mult_range(self):
        brain = UnifiedBrain()
        floor_c = allocate(brain, [make_candidate(alpha=0.38, tier="B")])[0]
        ceil_c = allocate(brain, [make_candidate(alpha=0.95, tier="A+")])[0]
        # B floor candidate sized at tier_mult_b, A+ super-alpha capped at tier_mult_aplus
        assert floor_c.size_factor < ceil_c.size_factor
        assert ceil_c.size_factor <= 1.25 * 1.3  # mult cap × any rr headroom

    def test_discrete_when_disabled(self):
        brain = UnifiedBrain(config={"continuous_conviction": False})
        a1 = allocate(brain, [make_candidate(alpha=0.56, tier="A")])[0].risk_dollars
        a2 = allocate(brain, [make_candidate(alpha=0.70, tier="A")])[0].risk_dollars
        assert a1 == pytest.approx(a2, rel=0.01)


class TestRollingStats:
    def test_streak_detection(self):
        stats = rolling_trade_stats(trades([100, 100, -50, -50, -50]))
        assert stats["loss_streak"] == 3
        assert stats["win_streak"] == 0

    def test_kelly_zero_on_losing_record(self):
        stats = rolling_trade_stats(trades([-100] * 20))
        assert stats["kelly"] == 0.0

    def test_open_trades_ignored(self):
        stats = rolling_trade_stats([{"pnl": None}, {"pnl": 100.0}])
        assert stats["n"] == 1


class TestDrawdownFromTrades:
    def test_no_drawdown_at_peak(self):
        class Acct:
            starting_cash = 10_000.0
            trades = trades([500, 500])
        assert account_drawdown_frac(Acct(), 11_000.0) == pytest.approx(0.0)

    def test_drawdown_after_losses(self):
        class Acct:
            starting_cash = 10_000.0
            trades = trades([2000, -1000, -1000])  # peak 12k, now 10k value
        dd = account_drawdown_frac(Acct(), 10_000.0)
        assert dd == pytest.approx(1 - 10_000 / 12_000, rel=0.01)

    def test_empty_account(self):
        class Acct:
            starting_cash = 0.0
            trades = []
        assert account_drawdown_frac(Acct(), 0.0) == 0.0
