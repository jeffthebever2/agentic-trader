"""RL allocation signal — load trained TD3 policy and query it for a signal.

This bridges the Deep-RL-Stocks TD3 policy with the TradingAgents LLM pipeline:
  1. Load a trained TD3 checkpoint from rl_models/td3_checkpoint/
  2. Build the current state vector from live OHLCV data
  3. Return a normalized allocation score in [-1, 1] per ticker
  4. Format it as a markdown context block for injection into agent prompts

Usage (standalone):
    from tradingagents.rl.rl_signal import RLSignalProvider
    provider = RLSignalProvider.from_checkpoint("rl_models/td3_checkpoint")
    signal = provider.get_signal("NVDA", as_of_date="2024-06-01")
    print(signal.context_markdown)

Usage (in trading graph — see wiring in trading_graph.py):
    rl_context = rl_provider.get_signal_context(ticker, trade_date)
    # Inject rl_context into AgentState.execution_context or portfolio_context
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RLSignal:
    """Output from the TD3 policy for one ticker."""
    ticker: str
    allocation_score: float    # raw TD3 output in [-1, 1]
    allocation_pct: float      # scaled to [0%, max_position_size%]
    confidence: str            # "STRONG_BUY" / "BUY" / "HOLD" / "REDUCE" / "AVOID"
    all_scores: Dict[str, float]   # scores for every ticker in the universe
    max_position_size: float
    context_markdown: str

    @property
    def is_buy_signal(self) -> bool:
        return self.allocation_score > 0.1

    @property
    def is_strong_signal(self) -> bool:
        return abs(self.allocation_score) > 0.5


class RLSignalProvider:
    """Wrap a trained TD3 agent for inference-time signal generation.

    Parameters
    ----------
    checkpoint_dir:
        Path to directory produced by scripts/train_rl_agent.py.
        Must contain: actor.pt, meta.json, meta.npy
    device:
        PyTorch device string.
    """

    def __init__(self, checkpoint_dir: str | Path, device: str = "cpu"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.device = device
        self._agent = None
        self._tickers: List[str] = []
        self._obs_dim: int = 0
        self._act_dim: int = 0
        self._max_position_size: float = 0.10
        self._loaded = False

    @classmethod
    def from_checkpoint(
        cls, checkpoint_dir: str | Path, device: str = "cpu"
    ) -> "RLSignalProvider":
        provider = cls(checkpoint_dir, device)
        provider._load()
        return provider

    def _load(self) -> None:
        meta_path = self.checkpoint_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(
                f"No meta.json in {self.checkpoint_dir}. "
                "Run scripts/train_rl_agent.py first."
            )
        meta = json.loads(meta_path.read_text())
        self._tickers = meta["tickers"]
        self._obs_dim = meta["obs_dim"]
        self._act_dim = meta["act_dim"]
        self._max_position_size = meta.get("max_position_size", 0.10)
        hidden = meta.get("hidden", [256, 256])

        try:
            from tradingagents.rl.td3_agent import TD3Agent
            self._agent = TD3Agent(
                obs_dim=self._obs_dim,
                act_dim=self._act_dim,
                hidden=hidden,
                device=self.device,
            )
            self._agent.load(self.checkpoint_dir)
            self._agent.eval_mode()
            self._loaded = True
            logger.info(
                "RLSignalProvider loaded from %s (tickers: %s)",
                self.checkpoint_dir,
                ", ".join(self._tickers),
            )
        except Exception as exc:
            logger.error("Failed to load TD3 agent: %s", exc)
            self._loaded = False

    def is_available(self) -> bool:
        return self._loaded and self._agent is not None

    def get_signal(
        self,
        ticker: str,
        as_of_date: str,
        price_data: Optional[Dict[str, "pd.DataFrame"]] = None,
    ) -> Optional[RLSignal]:
        """Get RL allocation signal for a ticker.

        Parameters
        ----------
        ticker:
            Ticker to get signal for. Must be in the trained universe.
        as_of_date:
            Date string "YYYY-MM-DD" to use as the current timestep.
        price_data:
            Optional pre-loaded OHLCV data dict (avoids redundant downloads).
            If None, downloads automatically from yfinance.

        Returns None if the RL model is not available or ticker not in universe.
        """
        if not self.is_available():
            return None

        ticker = ticker.upper()
        if ticker not in self._tickers:
            logger.debug("Ticker %s not in RL universe: %s", ticker, self._tickers)
            return None

        if price_data is None:
            price_data = _download_for_signal(self._tickers, as_of_date)
            if not price_data:
                return None

        try:
            obs = self._build_obs(price_data, as_of_date)
        except Exception as exc:
            logger.warning("Failed to build RL obs for %s: %s", ticker, exc)
            return None

        try:
            action = self._agent.select_action(obs, explore=False)  # shape: (n_tickers,)
        except Exception as exc:
            logger.warning("TD3 inference failed: %s", exc)
            return None

        all_scores = {t: float(a) for t, a in zip(self._tickers, action)}
        raw_score = all_scores.get(ticker, 0.0)
        alloc_pct = float(np.clip(raw_score, 0.0, 1.0) * self._max_position_size)
        confidence = _score_to_confidence(raw_score)
        context_md = _format_context(ticker, raw_score, alloc_pct, confidence, all_scores, self._max_position_size)

        return RLSignal(
            ticker=ticker,
            allocation_score=raw_score,
            allocation_pct=alloc_pct,
            confidence=confidence,
            all_scores=all_scores,
            max_position_size=self._max_position_size,
            context_markdown=context_md,
        )

    def get_signal_context(
        self,
        ticker: str,
        as_of_date: str,
        price_data: Optional[Dict] = None,
    ) -> str:
        """Return a markdown context string for prompt injection.

        Returns empty string if RL model unavailable (graceful degradation).
        """
        signal = self.get_signal(ticker, as_of_date, price_data)
        if signal is None:
            return ""
        return signal.context_markdown

    def _build_obs(self, price_data: Dict, as_of_date: str) -> np.ndarray:
        """Build the TD3 state vector as of a specific date."""
        from tradingagents.rl.environment import (
            LOOKBACK,
            _compute_rsi,
            _compute_macd_hist,
        )
        import pandas as pd

        # Filter to data up to as_of_date
        cutoff = pd.Timestamp(as_of_date)
        obs_parts = []

        for ticker in self._tickers:
            df = price_data.get(ticker)
            if df is None or df.empty:
                obs_parts.append(np.zeros(LOOKBACK + 5, dtype=np.float32))
                continue

            df = df.sort_index()
            df = df[df.index <= cutoff]
            if len(df) < LOOKBACK + 1:
                obs_parts.append(np.zeros(LOOKBACK + 5, dtype=np.float32))
                continue

            closes = df["Close"].values.astype(np.float64)
            volumes = df["Volume"].values.astype(np.float64)

            # Log-returns (last LOOKBACK)
            window = closes[-LOOKBACK - 1:]
            log_rets = np.log(window[1:] / np.where(window[:-1] > 0, window[:-1], 1.0))
            std = log_rets.std() + 1e-8
            log_rets = np.clip(log_rets / std, -5.0, 5.0)

            rsi = _compute_rsi(closes[-29:]) / 100.0
            macd_hist = _compute_macd_hist(closes[-61:])
            hist_std = max(abs(macd_hist) * 2, 1e-8)
            macd_norm = float(np.clip(macd_hist / hist_std, -5.0, 5.0))

            if len(volumes) >= 30:
                vol_ratio = float(np.clip(volumes[-10:].mean() / (volumes[-30:].mean() + 1e-8) - 1.0, -2.0, 2.0))
            else:
                vol_ratio = 0.0

            # No live position info at inference; use 0 for weight and pnl
            ticker_features = np.concatenate([log_rets, [rsi, macd_norm, vol_ratio, 0.0, 0.0]])
            obs_parts.append(ticker_features.astype(np.float32))

        return np.concatenate(obs_parts).astype(np.float32)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _download_for_signal(tickers: List[str], as_of_date: str) -> Optional[Dict]:
    """Download ~3 months of history ending at as_of_date."""
    import yfinance as yf
    import pandas as pd

    end = pd.Timestamp(as_of_date) + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=120)
    try:
        raw = yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception as exc:
        logger.warning("yfinance download failed for RL signal: %s", exc)
        return None

    result = {}
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna()
            else:
                df = raw.dropna()
            if not df.empty:
                result[ticker] = df
        except Exception:
            pass
    return result


def _score_to_confidence(score: float) -> str:
    if score >= 0.7:
        return "STRONG_BUY"
    elif score >= 0.3:
        return "BUY"
    elif score >= -0.1:
        return "HOLD"
    elif score >= -0.5:
        return "REDUCE"
    return "AVOID"


def _format_context(
    ticker: str,
    raw_score: float,
    alloc_pct: float,
    confidence: str,
    all_scores: Dict[str, float],
    max_position_size: float,
) -> str:
    top3 = sorted(all_scores.items(), key=lambda x: -x[1])[:3]
    top3_str = ", ".join(f"{t} ({s:+.2f})" for t, s in top3)
    lines = [
        "## RL Policy Signal (TD3 Deep Reinforcement Learning)",
        "",
        "The TD3 agent was trained on historical price data using deep reinforcement learning.",
        "It outputs a continuous portfolio allocation score independent of fundamental/news analysis.",
        "",
        f"- **Ticker**: {ticker}",
        f"- **Allocation score**: {raw_score:+.3f} (range: -1 to +1)",
        f"- **Suggested allocation**: {alloc_pct:.1%} of portfolio (max allowed: {max_position_size:.1%})",
        f"- **RL signal**: {confidence}",
        f"- **Top RL picks today**: {top3_str}",
        "",
        "**Interpretation guide**:",
        "  - Score > 0.7: RL model strongly favors allocation (technical momentum + pattern)",
        "  - Score 0.3–0.7: Moderate positive signal",
        "  - Score -0.1–0.3: Neutral / hold",
        "  - Score < -0.1: RL model disfavors this ticker vs alternatives",
        "",
        "Use this signal as *one additional data point* alongside the fundamental/news analysis above.",
        "The RL model sees only price/volume patterns — it has no knowledge of fundamentals or news.",
    ]
    return "\n".join(lines)
