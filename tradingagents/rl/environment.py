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
        Round-trip cost fraction per trade (default 0.001 = 10bps).
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
        longs_only: bool = True,
    ):
        self.tickers = [t.upper() for t in tickers]
        self.n = len(self.tickers)
        self.starting_cash = starting_cash
        self.max_position_size = max_position_size
        self.transaction_cost = transaction_cost
        self.longs_only = longs_only

        # Align and store price data
        self._closes: Dict[str, np.ndarray] = {}
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
        if seed is not None:
            np.random.seed(seed)
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

        portfolio_value = self._portfolio_value(prices)
        cost = self._rebalance(prices, target_fractions, portfolio_value)

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
            self._volumes[ticker] = df["Volume"].values.astype(np.float64)

    def _get_prices(self, t: int) -> np.ndarray:
        return np.array([self._closes[tk][t] for tk in self.tickers], dtype=np.float64)

    def _portfolio_value(self, prices: np.ndarray) -> float:
        return float(self._cash + np.dot(self._shares, prices))

    def _rebalance(
        self,
        prices: np.ndarray,
        target_fractions: np.ndarray,
        portfolio_value: float,
    ) -> float:
        """Execute trades to reach target_fractions; return total transaction cost."""
        target_values = target_fractions * portfolio_value
        target_shares = target_values / np.where(prices > 0, prices, 1.0)

        delta_shares = target_shares - self._shares
        trade_values = np.abs(delta_shares * prices)
        total_cost_fraction = float(np.sum(trade_values)) * self.transaction_cost / portfolio_value

        for i, ticker in enumerate(self.tickers):
            price = prices[i]
            if price <= 0:
                continue
            delta = delta_shares[i]
            if delta > 0:  # buy
                cost = delta * price
                if cost <= self._cash:
                    self._cash -= cost
                    self._shares[i] += delta
                else:
                    # Buy as many as cash allows
                    affordable = self._cash / price
                    self._cash -= affordable * price
                    self._shares[i] += affordable
            elif delta < 0:  # sell
                sell_shares = min(abs(delta), self._shares[i])
                self._cash += sell_shares * price
                self._shares[i] -= sell_shares

        return total_cost_fraction

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
    if len(closes) < 26:
        return 0.0
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean().iloc[-1]
    ema26 = s.ewm(span=26, adjust=False).mean().iloc[-1]
    macd = ema12 - ema26
    signal = pd.Series([macd]).ewm(span=9, adjust=False).mean().iloc[-1]
    return float(macd - signal)


class _BoxSpace:
    """Minimal stand-in for gymnasium.spaces.Box (avoids hard dep at import time)."""

    def __init__(self, low: float, high: float, shape: tuple):
        self.low = np.full(shape, low, dtype=np.float32)
        self.high = np.full(shape, high, dtype=np.float32)
        self.shape = shape
        self.dtype = np.float32

    def sample(self) -> np.ndarray:
        return np.random.uniform(self.low, self.high).astype(np.float32)
