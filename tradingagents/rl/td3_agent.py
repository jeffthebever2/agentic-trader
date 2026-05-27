"""Twin-Delayed DDPG (TD3) agent for multi-stock portfolio allocation.

Based on Fujimoto et al. (2018) "Addressing Function Approximation Error
in Actor-Critic Methods." Adapted from Deep-RL-Stocks to work with the
TradingAgents multi-stock environment.

Key TD3 features implemented:
  - Twin critic networks (Q1, Q2) — takes min to reduce overestimation bias
  - Delayed policy updates (actor updated every `policy_delay` critic steps)
  - Target policy smoothing (Gaussian noise on target actions during critic update)
  - Replay buffer with uniform sampling
  - Epsilon-greedy exploration decaying from `epsilon_start` to `epsilon_min`
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Optional PyTorch import (graceful degradation) ────────────────────────────
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False
    logger.warning(
        "PyTorch not installed. TD3 agent requires PyTorch: pip install torch"
    )


# ── Neural network modules ─────────────────────────────────────────────────────

def _mlp(in_dim: int, out_dim: int, hidden: List[int], activation=None) -> "nn.Sequential":
    """Build a multi-layer perceptron."""
    if not _TORCH_AVAILABLE:
        raise ImportError("PyTorch required for TD3 networks.")
    layers: List[nn.Module] = []
    dims = [in_dim] + hidden
    for a, b in zip(dims, dims[1:]):
        layers.append(nn.Linear(a, b))
        layers.append(nn.ReLU())
    layers.append(nn.Linear(dims[-1], out_dim))
    if activation:
        layers.append(activation)
    return nn.Sequential(*layers)


class Actor(nn.Module if _TORCH_AVAILABLE else object):
    """Deterministic policy network: state → action in [-1, 1]^n."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: List[int] = (256, 256)):
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch required.")
        super().__init__()
        self.net = _mlp(obs_dim, act_dim, list(hidden), nn.Tanh())

    def forward(self, state: "torch.Tensor") -> "torch.Tensor":
        return self.net(state)


class Critic(nn.Module if _TORCH_AVAILABLE else object):
    """Twin Q-value networks: (state, action) → (Q1, Q2)."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: List[int] = (256, 256)):
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch required.")
        super().__init__()
        self.q1 = _mlp(obs_dim + act_dim, 1, list(hidden))
        self.q2 = _mlp(obs_dim + act_dim, 1, list(hidden))

    def forward(
        self, state: "torch.Tensor", action: "torch.Tensor"
    ) -> Tuple["torch.Tensor", "torch.Tensor"]:
        sa = torch.cat([state, action], dim=-1)
        return self.q1(sa), self.q2(sa)

    def q1_only(
        self, state: "torch.Tensor", action: "torch.Tensor"
    ) -> "torch.Tensor":
        sa = torch.cat([state, action], dim=-1)
        return self.q1(sa)


# ── Replay Buffer ─────────────────────────────────────────────────────────────

class ReplayBuffer:
    """Fixed-size experience replay buffer with uniform sampling."""

    def __init__(self, obs_dim: int, act_dim: int, capacity: int = 1_000_000):
        self.capacity = capacity
        self.size = 0
        self.ptr = 0

        self.states = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.actions = np.zeros((capacity, act_dim), dtype=np.float32)
        self.rewards = np.zeros((capacity, 1), dtype=np.float32)
        self.next_states = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.dones = np.zeros((capacity, 1), dtype=np.float32)

    def add(
        self,
        state: np.ndarray,
        action: np.ndarray,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self.states[self.ptr] = state
        self.actions[self.ptr] = action
        self.rewards[self.ptr] = reward
        self.next_states[self.ptr] = next_state
        self.dones[self.ptr] = float(done)
        self.ptr = (self.ptr + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def sample(self, batch_size: int) -> Tuple[np.ndarray, ...]:
        idx = np.random.randint(0, self.size, size=batch_size)
        return (
            self.states[idx],
            self.actions[idx],
            self.rewards[idx],
            self.next_states[idx],
            self.dones[idx],
        )

    def __len__(self) -> int:
        return self.size


# ── TD3 Agent ─────────────────────────────────────────────────────────────────

class TD3Agent:
    """Twin-Delayed DDPG agent for continuous portfolio allocation.

    Parameters
    ----------
    obs_dim:
        Dimension of the observation/state vector.
    act_dim:
        Number of assets (action dimension).
    hidden:
        Hidden layer sizes for actor and critic networks.
    lr_actor / lr_critic:
        Learning rates.
    gamma:
        Discount factor.
    tau:
        Soft target update coefficient.
    policy_delay:
        Critic updates per actor update (TD3 "delayed" part).
    policy_noise:
        Std of Gaussian noise added to target policy actions.
    noise_clip:
        Clipping range for target policy noise.
    epsilon_start / epsilon_min / epsilon_decay:
        Epsilon-greedy exploration schedule (decays linearly over episodes).
    buffer_capacity:
        Maximum replay buffer size.
    batch_size:
        Training batch size.
    device:
        PyTorch device string ("cpu", "cuda", "mps").
    """

    def __init__(
        self,
        obs_dim: int,
        act_dim: int,
        hidden: List[int] = (256, 256),
        lr_actor: float = 3e-4,
        lr_critic: float = 3e-4,
        gamma: float = 0.99,
        tau: float = 0.005,
        policy_delay: int = 2,
        policy_noise: float = 0.2,
        noise_clip: float = 0.5,
        epsilon_start: float = 1.0,
        epsilon_min: float = 0.025,
        epsilon_decay: int = 100_000,
        buffer_capacity: int = 1_000_000,
        batch_size: int = 128,
        device: str = "cpu",
    ):
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch required for TD3Agent: pip install torch")

        self.obs_dim = obs_dim
        self.act_dim = act_dim
        self.gamma = gamma
        self.tau = tau
        self.policy_delay = policy_delay
        self.policy_noise = policy_noise
        self.noise_clip = noise_clip
        self.epsilon_start = epsilon_start
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.batch_size = batch_size
        self.device = torch.device(device)

        # Networks
        self.actor = Actor(obs_dim, act_dim, hidden).to(self.device)
        self.actor_target = copy.deepcopy(self.actor)
        self.critic = Critic(obs_dim, act_dim, hidden).to(self.device)
        self.critic_target = copy.deepcopy(self.critic)

        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=lr_actor)
        self.critic_optimizer = torch.optim.Adam(self.critic.parameters(), lr=lr_critic)

        self.replay_buffer = ReplayBuffer(obs_dim, act_dim, buffer_capacity)

        self.total_steps = 0
        self.critic_updates = 0

    # ── Exploration ────────────────────────────────────────────────────────────

    @property
    def epsilon(self) -> float:
        return max(
            self.epsilon_min,
            self.epsilon_start - (self.epsilon_start - self.epsilon_min)
            * self.total_steps / self.epsilon_decay,
        )

    def select_action(self, state: np.ndarray, explore: bool = True) -> np.ndarray:
        """Return action in [-1, 1]^n. Applies epsilon-greedy exploration."""
        if explore and np.random.rand() < self.epsilon:
            return np.random.uniform(-1.0, 1.0, size=self.act_dim).astype(np.float32)

        with torch.no_grad():
            s = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            action = self.actor(s).squeeze(0).cpu().numpy()
        return action.astype(np.float32)

    # ── Training step ──────────────────────────────────────────────────────────

    def train_step(self) -> Optional[Dict[str, float]]:
        """Sample a batch and perform one TD3 gradient update.

        Returns dict of training metrics, or None if buffer too small.
        """
        if len(self.replay_buffer) < self.batch_size:
            return None

        states, actions, rewards, next_states, dones = self.replay_buffer.sample(
            self.batch_size
        )
        states = torch.FloatTensor(states).to(self.device)
        actions = torch.FloatTensor(actions).to(self.device)
        rewards = torch.FloatTensor(rewards).to(self.device)
        next_states = torch.FloatTensor(next_states).to(self.device)
        dones = torch.FloatTensor(dones).to(self.device)

        with torch.no_grad():
            noise = (
                torch.randn_like(actions) * self.policy_noise
            ).clamp(-self.noise_clip, self.noise_clip)
            next_actions = (self.actor_target(next_states) + noise).clamp(-1.0, 1.0)
            q1_next, q2_next = self.critic_target(next_states, next_actions)
            q_target = rewards + self.gamma * (1.0 - dones) * torch.min(q1_next, q2_next)

        q1, q2 = self.critic(states, actions)
        critic_loss = F.mse_loss(q1, q_target) + F.mse_loss(q2, q_target)

        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()
        self.critic_updates += 1

        metrics: Dict[str, float] = {"critic_loss": float(critic_loss.item())}

        # Delayed actor update
        if self.critic_updates % self.policy_delay == 0:
            actor_loss = -self.critic.q1_only(states, self.actor(states)).mean()
            self.actor_optimizer.zero_grad()
            actor_loss.backward()
            self.actor_optimizer.step()

            # Soft target updates (Polyak averaging)
            _soft_update(self.actor, self.actor_target, self.tau)
            _soft_update(self.critic, self.critic_target, self.tau)
            metrics["actor_loss"] = float(actor_loss.item())

        return metrics

    # ── Store transition ───────────────────────────────────────────────────────

    def store(
        self,
        state: np.ndarray,
        action: np.ndarray,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self.replay_buffer.add(state, action, reward, next_state, done)
        self.total_steps += 1

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.actor.state_dict(), path / "actor.pt")
        torch.save(self.critic.state_dict(), path / "critic.pt")
        torch.save(self.actor_target.state_dict(), path / "actor_target.pt")
        torch.save(self.critic_target.state_dict(), path / "critic_target.pt")
        np.save(str(path / "meta.npy"), np.array([self.total_steps, self.critic_updates]))
        logger.info("TD3 checkpoint saved → %s", path)

    def load(self, path: str | Path) -> None:
        path = Path(path)
        # weights_only=True prevents arbitrary code execution from a malicious
        # checkpoint (torch.load unpickles by default). These are pure state_dicts.
        self.actor.load_state_dict(torch.load(path / "actor.pt", map_location=self.device, weights_only=True))
        self.critic.load_state_dict(torch.load(path / "critic.pt", map_location=self.device, weights_only=True))
        self.actor_target.load_state_dict(
            torch.load(path / "actor_target.pt", map_location=self.device, weights_only=True)
        )
        self.critic_target.load_state_dict(
            torch.load(path / "critic_target.pt", map_location=self.device, weights_only=True)
        )
        meta = np.load(str(path / "meta.npy"))
        self.total_steps = int(meta[0])
        self.critic_updates = int(meta[1])
        logger.info("TD3 checkpoint loaded ← %s (step %d)", path, self.total_steps)

    def eval_mode(self) -> None:
        self.actor.eval()
        self.critic.eval()

    def train_mode(self) -> None:
        self.actor.train()
        self.critic.train()


# ── Utility ───────────────────────────────────────────────────────────────────

def _soft_update(source: "nn.Module", target: "nn.Module", tau: float) -> None:
    for s_param, t_param in zip(source.parameters(), target.parameters()):
        t_param.data.copy_(tau * s_param.data + (1.0 - tau) * t_param.data)
