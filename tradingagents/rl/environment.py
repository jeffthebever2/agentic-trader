"""Stock trading Gymnasium environment for TD3 training.

State vector (per ticker, stacked):
  - 20 normalized log-returns (lookback window)
  - RSI-14 / 100
  - MACD histogram normalized by 10-day std
  - Volume ratio (10d / 30d)
  - Current portfolio weight for this ticker
  - Unrealized PnL (as fraction of portfolio value)

Action space: continuous allocation weights in [-1, 1] per ticker.
  Positive = long fraction of free cash, negative = short (skipped if no margin).
  Values are re-scaled and clipped to [0, max_position_size] for longs-only mode.

Reward: portfolio log-return at each step minus a transaction cost penalty.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LOOKBACK = 20          # price history steps in state
FEATURES_PER_TICKER = LOOKBACK + 5   # returns + RSI + MACD + VolRatio + weight + pnl


class StockTradingEnv:
    """Multi-stock continuous-action trading environment (Gymnasium-style API).

    Designed to work with the TradingAgents data pipeline: pass pre-downloaded
    OHLCV DataFrames (one per ticker) as ``price_data``.

    Parameters
    ----------
    price_data:
        Dict mapping ticker -> DataFrame with columns [Open, High, Low, Close, Volume].
        All DataFrames must be aligned on the same date index.
    tickers:
        Ordered list of tickers. Defines action/state ordering.
    starting_cash:
        Initial portfolio cash.
    max_position_size:
        Max fraction of portfolio per position (default 0.10).
    transaction_cost:
        Round-trip commission cost fraction per trade (default 0.001 = 10bps).
        Models flat/percentage commission (e.g. IB tiered, Webull zero-fee spread).
    slippage_bps:
        One-way slippage in basis points applied to each buy (adds cost) and
        sell (reduces proceeds). Default 5bps = 0.05% per side, 10bps round-trip.
        Models bid/ask spread and imperfect market-order fill.
    longs_only:
        When True, negative actions are treated as 0 (no shorts).
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        price_data: Dict[str, pd.DataFrame],
        tickers: List[str],
        starting_cash: float = 100_000.0,
        max_position_size: float = 0.10,
        transaction_cost: float = 0.001,
        slippage_bps: float = 5.0,
        longs_only: bool = True,
    ):
        self.tickers = [t.upper() for t in tickers]
        self.n = len(self.tickers)
        self.starting_cash = starting_cash
        self.max_position_size = max_position_size
        self.transaction_cost = transaction_cost
        self.slippage_bps = float(slippage_bps)
        self.longs_only = longs_only

        # Align and store price data
        self._closes: Dict[str, np.ndarray] = {}
        self._opens: Dict[str, np.ndarray] = {}   # RL-1: next-open fills
        self._volumes: Dict[str, np.ndarray] = {}
        self._dates: Optional[pd.DatetimeIndex] = None

        self._prepare_data(price_data)

        self.T = len(self._dates) - 1       # number of tradeable steps
        self._obs_dim = self.n * FEATURES_PER_TICKER
        self._act_dim = self.n

        # Gymnasium-compatible space descriptors
        self.observation_space = _BoxSpace(low=-10.0, high=10.0, shape=(self._obs_dim,))
        self.action_space = _BoxSpace(low=-1.0, high=1.0, shape=(self._act_dim,))

        # Episode state
        self._t: int = LOOKBACK
        self._cash: float = starting_cash
        self._shares: np.ndarray = np.zeros(self.n)
        self._prev_portfolio_value: float = starting_cash
        self._done: bool = False

    # ------------------------------------------------------------------ #
    # Gymnasium API                                                         #
    # ------------------------------------------------------------------ #

    def reset(self, seed: Optional[int] = None) -> Tuple[np.ndarray, dict]:
        # RL-8: global np.random.seed inside reset() pollutes all downstream NumPy randomness.
        # Use a local Generator instead (doesn't affect global state).
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._t = LOOKBACK
        self._cash = self.starting_cash
        self._shares = np.zeros(self.n)
        self._prev_portfolio_value = self.starting_cash
        self._done = False
        return self._get_obs(), {}

    def step(self, action: np.ndarray) -> Tuple[np.ndarray, float, bool, bool, dict]:
        """Execute one trading step.

        Returns (obs, reward, terminated, truncated, info).
        """
        if self._done:
            raise RuntimeError("Call reset() before step() after episode ends.")

        prices = self._get_prices(self._t)
        action = np.clip(action, -1.0, 1.0)
        if self.longs_only:
            action = np.maximum(action, 0.0)

        # Scale actions to target allocation fractions
        target_fractions = action * self.max_position_size  # [0, max_position_size]

        # RL-1: fill at next-bar open prices, not today's close.
        # EOD decisions execute at the next day's open; filling at today's close
        # gave the agent intrabar look-ahead that inflated training returns.
        next_open_prices = self._get_opens(self._t + 1)
        portfolio_value = self._portfolio_value(prices)
        cost = self._rebalance(next_open_prices, target_fractions, portfolio_value)

        self._t += 1
        new_prices = self._get_prices(self._t)
        new_portfolio_value = self._portfolio_value(new_prices)

        if self._prev_portfolio_value > 0:
            log_return = np.log(new_portfolio_value / self._prev_portfolio_value)
        else:
            log_return = 0.0

        reward = float(log_return - cost)
        self._prev_portfolio_value = new_portfolio_value

        terminated = self._t >= self.T
        self._done = terminated

        info = {
            "portfolio_value": new_portfolio_value,
            "cash": self._cash,
            "log_return": log_return,
            "transaction_cost": cost,
            "shares": self._shares.copy(),
            "prices": new_prices.copy(),
            "step": self._t,
        }
        return self._get_obs(), reward, terminated, False, info

    def render(self) -> None:
        prices = self._get_prices(self._t)
        pv = self._portfolio_value(prices)
        print(f"Step {self._t}/{self.T}  Portfolio: ${pv:,.2f}  Cash: ${self._cash:,.2f}")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _prepare_data(self, price_data: Dict[str, pd.DataFrame]) -> None:
        """Align all tickers to a common date index."""
        frames = {}
        for ticker in self.tickers:
            df = price_data.get(ticker)
            if df is None or df.empty:
                raise ValueError(f"No price data for ticker {ticker}")
            df = df.sort_index()
            frames[ticker] = df

        # Common index = intersection of all date ranges
        common_idx = None
        for df in frames.values():
            idx = df.index
            common_idx = idx if common_idx is None else common_idx.intersection(idx)

        if common_idx is None or len(common_idx) < LOOKBACK + 10:
            raise ValueError(
                f"Insufficient overlapping price history (need >{LOOKBACK + 10} days, "
                f"got {0 if common_idx is None else len(common_idx)})"
            )

        self._dates = common_idx
        for ticker in self.tickers:
            df = frames[ticker].loc[common_idx]
            self._closes[ticker] = df["Close"].values.astype(np.float64)
            # RL-1: store Open for next-bar fill prices (EOD decisions fill at next open)
            if "Open" in df.columns:
                self._opens[ticker] = df["Open"].values.astype(np.float64)
            else:
                self._opens[ticker] = self._closes[ticker]  # fallback: use Close
            self._volumes[ticker] = df["Volume"].values.astype(np.float64)

    def _get_prices(self, t: int) -> np.ndarray:
        return np.array([self._closes[tk][t] for tk in self.tickers], dtype=np.float64)

    def _get_opens(self, t: int) -> np.ndarray:
        """RL-1: next-bar open prices for realistic EOD fill simulation."""
        t_clamped = min(t, len(self._dates) - 1)
        return np.array([self._opens[tk][t_clamped] for tk in self.tickers], dtype=np.float64)

    def _portfolio_value(self, prices: np.ndarray) -> float:
        return float(self._cash + np.dot(self._shares, prices))

    def _rebalance(
        self,
        prices: np.ndarray,
        target_fractions: np.ndarray,
        portfolio_value: float,
    ) -> float:
        """Execute trades to reach target_fractions; return COMMISSION cost fraction only.

        IMPORTANT — slippage accounting:
          Slippage is embedded directly in fill prices (buys at ask, sells at bid),
          which changes `self._cash` and thus `self._shares` values. Because the
          portfolio value already reflects these fill-price losses, slippage must NOT
          also be subtracted from the reward — that would double-count it.

          Only the FLAT COMMISSION (transaction_cost) is returned as a separate reward
          penalty because it represents a pure out-of-pocket cost not already embedded
          in mark-to-market portfolio value at mid prices.

        Known limitation: trades execute at today's Close price. In practice,
        EOD decisions execute at the next day's Open. This gives the agent
        unrealistic intraday precision. The effect is small for daily strategies
        but should be fixed for intraday RL by using next_open prices.
        TODO: add `next_open_prices` parameter to step() for more realistic fills.
        """
        slip_frac = self.slippage_bps / 10_000.0  # per-side fraction

        # Enforce max_position_size: clip target fractions
        target_fractions = np.clip(target_fractions, 0.0, self.max_position_size)

        target_values = target_fractions * portfolio_value
        target_shares = target_values / np.where(prices > 0, prices, 1.0)

        delta_shares = target_shares - self._shares
        trade_values = np.abs(delta_shares * prices)

        # Commission cost fraction (returned for reward penalty — NOT double-counted)
        commission_cost = float(np.sum(trade_values)) * self.transaction_cost / max(portfolio_value, 1.0)

        for i, ticker in enumerate(self.tickers):
            price = prices[i]
            if price <= 0:
                continue
            delta = delta_shares[i]
            if delta > 0:  # buy at ask (price + slip) — slippage already in cash deduction
                fill_price = price * (1.0 + slip_frac)
                cost = delta * fill_price
                if cost <= self._cash:
                    self._cash -= cost
                    self._shares[i] += delta
                else:
                    # Buy as many as cash allows at ask price
                    affordable = self._cash / fill_price
                    self._cash -= affordable * fill_price
                    self._shares[i] += affordable
            elif delta < 0:  # sell at bid (price - slip) — slippage already in proceeds
                fill_price = price * (1.0 - slip_frac)
                sell_shares = min(abs(delta), self._shares[i])
                self._cash += sell_shares * fill_price
                self._shares[i] -= sell_shares

        # Return only commission (not slippage — already in fill prices above)
        return commission_cost

    def _get_obs(self) -> np.ndarray:
        """Build state vector for the current timestep."""
        obs_parts = []
        prices = self._get_prices(self._t)
        portfolio_value = self._portfolio_value(prices)

        for i, ticker in enumerate(self.tickers):
            closes = self._closes[ticker]
            volumes = self._volumes[ticker]

            # --- 20 log-returns (normalized) ---
            window = closes[max(0, self._t - LOOKBACK): self._t + 1]
            if len(window) < 2:
                log_rets = np.zeros(LOOKBACK)
            else:
                log_rets = np.log(window[1:] / np.where(window[:-1] > 0, window[:-1], 1.0))
                if len(log_rets) < LOOKBACK:
                    log_rets = np.pad(log_rets, (LOOKBACK - len(log_rets), 0))
                std = log_rets.std() + 1e-8
                log_rets = np.clip(log_rets / std, -5.0, 5.0)

            # --- RSI-14 (normalized to [0,1]) ---
            rsi_window = closes[max(0, self._t - 28): self._t + 1]
            rsi = _compute_rsi(rsi_window) / 100.0

            # --- MACD histogram (normalized) ---
            macd_window = closes[max(0, self._t - 60): self._t + 1]
            macd_hist = _compute_macd_hist(macd_window)
            hist_std = max(abs(macd_hist) * 2, 1e-8)
            macd_norm = float(np.clip(macd_hist / hist_std, -5.0, 5.0))

            # --- Volume ratio (10d / 30d) ---
            vol_window = volumes[max(0, self._t - 30): self._t + 1]
            if len(vol_window) >= 10:
                v10 = float(vol_window[-10:].mean())
                v30 = float(vol_window.mean())
                vol_ratio = float(np.clip((v10 / v30 if v30 > 0 else 1.0) - 1.0, -2.0, 2.0))
            else:
                vol_ratio = 0.0

            # --- Current portfolio weight ---
            pos_value = self._shares[i] * prices[i]
            weight = pos_value / portfolio_value if portfolio_value > 0 else 0.0
            weight = float(np.clip(weight, 0.0, 1.0))

            # --- Unrealized PnL (as fraction of portfolio value) ---
            # Approximate entry price not tracked per-share; use weight as proxy
            pnl_proxy = weight - target_fractions_proxy(self._shares[i], prices[i], self.starting_cash)
            pnl_proxy = float(np.clip(pnl_proxy, -1.0, 1.0))

            ticker_features = np.concatenate([
                log_rets,
                [rsi, macd_norm, vol_ratio, weight, pnl_proxy],
            ])
            obs_parts.append(ticker_features)

        return np.concatenate(obs_parts).astype(np.float32)


def target_fractions_proxy(shares: float, price: float, starting_cash: float) -> float:
    """Rough unrealized-PnL proxy: current position value / starting cash."""
    return float(np.clip(shares * price / max(starting_cash, 1.0), 0.0, 1.0))


# ── Technical indicator helpers ──────────────────────────────────────────────

def _compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _compute_macd_hist(closes: np.ndarray) -> float:
    # RL-3/BO-1: same bug as breakout_scanner — signal computed on a one-element Series
    # so signal==macd → hist always 0. Fix: compute signal on the full MACD series.
    if len(closes) < 26:
        return 0.0
    s = pd.Series(closes)
    macd_series = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    signal_series = macd_series.ewm(span=9, adjust=False).mean()
    return float((macd_series - signal_series).iloc[-1])


class _BoxSpace:
    """Minimal stand-in for gymnasium.spaces.Box (avoids hard dep at import time)."""

    def __init__(self, low: float, high: float, shape: tuple):
        self.low = np.full(shape, low, dtype=np.float32)
        self.high = np.full(shape, high, dtype=np.float32)
        self.shape = shape
        self.dtype = np.float32

    def sample(self) -> np.ndarray:
        return np.random.uniform(self.low, self.high).astype(np.float32)
