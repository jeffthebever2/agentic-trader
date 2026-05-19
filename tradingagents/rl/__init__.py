"""TradingAgents RL module — TD3 deep reinforcement learning for portfolio allocation.

Components:
  - StockTradingEnv   : Gymnasium-compatible multi-stock continuous-action environment
  - TD3Agent          : Twin-Delayed DDPG policy (actor + twin critics + replay buffer)
  - RLSignalProvider  : Inference wrapper — loads checkpoint, returns allocation signals
"""

from .environment import StockTradingEnv, LOOKBACK, FEATURES_PER_TICKER
from .rl_signal import RLSignalProvider, RLSignal

try:
    from .td3_agent import TD3Agent, Actor, Critic, ReplayBuffer
    _TD3_AVAILABLE = True
except ImportError:
    _TD3_AVAILABLE = False

__all__ = [
    "StockTradingEnv",
    "LOOKBACK",
    "FEATURES_PER_TICKER",
    "RLSignalProvider",
    "RLSignal",
    "TD3Agent",
    "Actor",
    "Critic",
    "ReplayBuffer",
]
