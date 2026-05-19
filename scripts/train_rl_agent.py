#!/usr/bin/env python3
"""Train a TD3 RL agent on historical stock price data.

The agent learns a continuous portfolio allocation policy directly from
OHLCV data — no LLMs required. After training, the saved weights can be
loaded by rl_signal.py to inject RL-derived allocation signals into the
TradingAgents LLM pipeline.

Algorithm: Twin-Delayed DDPG (TD3) — Fujimoto et al. (2018)
  - Twin critics reduce overestimation bias
  - Delayed actor updates for stability
  - Gaussian target policy smoothing
  - Epsilon-greedy exploration (1.0 → 0.025 over epsilon_decay steps)
  - TensorBoard logging for all metrics

Examples:
    # Train on NVDA, AAPL, MSFT from 2018-2023:
    python scripts/train_rl_agent.py --tickers NVDA AAPL MSFT \\
        --start 2018-01-01 --end 2023-12-31 --iterations 500000

    # Resume from checkpoint:
    python scripts/train_rl_agent.py --tickers NVDA AAPL MSFT \\
        --checkpoint rl_models/td3_checkpoint

    # Full S&P 100 universe (slow, needs GPU):
    python scripts/train_rl_agent.py \\
        --tickers-file tickers/sp100.txt \\
        --start 2015-01-01 --end 2023-12-31 \\
        --iterations 1000000 --device cuda
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("train_rl")


def parse_args():
    p = argparse.ArgumentParser(description="Train TD3 RL agent on stocks.")
    # Data
    p.add_argument("--tickers", nargs="+", default=["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"])
    p.add_argument("--tickers-file", help="Newline-separated file of tickers (overrides --tickers)")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default="2023-12-31")
    p.add_argument("--test-start", default="2024-01-01", help="Out-of-sample eval start")
    p.add_argument("--test-end", default="2024-12-31")
    # Environment
    p.add_argument("--starting-cash", type=float, default=100_000.0)
    p.add_argument("--max-position-size", type=float, default=0.10)
    p.add_argument("--transaction-cost", type=float, default=0.001)
    # Agent hyperparameters
    p.add_argument("--iterations", type=int, default=500_000)
    p.add_argument("--warm-up-steps", type=int, default=5_000,
                   help="Random exploration steps before training begins")
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--buffer-capacity", type=int, default=1_000_000)
    p.add_argument("--gamma", type=float, default=0.99)
    p.add_argument("--tau", type=float, default=0.005)
    p.add_argument("--lr-actor", type=float, default=3e-4)
    p.add_argument("--lr-critic", type=float, default=3e-4)
    p.add_argument("--policy-delay", type=int, default=2)
    p.add_argument("--policy-noise", type=float, default=0.2)
    p.add_argument("--noise-clip", type=float, default=0.5)
    p.add_argument("--epsilon-start", type=float, default=1.0)
    p.add_argument("--epsilon-min", type=float, default=0.025)
    p.add_argument("--epsilon-decay", type=int, default=100_000)
    p.add_argument("--hidden", nargs="+", type=int, default=[256, 256])
    p.add_argument("--device", default="cpu", help="PyTorch device: cpu/cuda/mps")
    # Checkpointing
    p.add_argument("--checkpoint-dir", default="rl_models/td3_checkpoint")
    p.add_argument("--checkpoint", help="Load existing checkpoint before training")
    p.add_argument("--checkpoint-interval", type=int, default=10_000,
                   help="Save checkpoint every N steps")
    p.add_argument("--eval-interval", type=int, default=10_000,
                   help="Run out-of-sample eval every N steps")
    # Logging
    p.add_argument("--log-dir", default="rl_models/runs")
    p.add_argument("--log-interval", type=int, default=1_000)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_tickers(args) -> list:
    if args.tickers_file:
        lines = Path(args.tickers_file).read_text().strip().splitlines()
        return [t.strip().upper() for t in lines if t.strip()]
    return [t.upper() for t in args.tickers]


def download_price_data(tickers: list, start: str, end: str) -> dict:
    """Download OHLCV for all tickers; drop any with insufficient history."""
    import yfinance as yf
    import pandas as pd

    logger.info("Downloading price data for %d tickers (%s → %s)...", len(tickers), start, end)
    raw = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)

    price_data = {}
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna()
            else:
                df = raw.dropna()
            if len(df) < 60:
                logger.warning("Skipping %s — only %d rows", ticker, len(df))
                continue
            price_data[ticker] = df
        except Exception as exc:
            logger.warning("Skipping %s: %s", ticker, exc)

    logger.info("Loaded %d / %d tickers", len(price_data), len(tickers))
    return price_data


def evaluate_agent(agent, price_data: dict, tickers: list, args) -> dict:
    """Run a single episode on the test period without exploration."""
    from tradingagents.rl.environment import StockTradingEnv

    test_data = download_price_data(tickers, args.test_start, args.test_end)
    if not test_data or not all(t in test_data for t in tickers):
        logger.warning("Test data missing for some tickers; skipping eval.")
        return {}

    try:
        env = StockTradingEnv(
            price_data=test_data,
            tickers=tickers,
            starting_cash=args.starting_cash,
            max_position_size=args.max_position_size,
            transaction_cost=args.transaction_cost,
        )
    except Exception as exc:
        logger.warning("Eval env init failed: %s", exc)
        return {}

    agent.eval_mode()
    obs, _ = env.reset()
    total_reward = 0.0
    episode_steps = 0
    done = False

    while not done:
        action = agent.select_action(obs, explore=False)
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        episode_steps += 1
        done = terminated or truncated

    agent.train_mode()
    final_value = info.get("portfolio_value", args.starting_cash)
    total_return = (final_value - args.starting_cash) / args.starting_cash

    return {
        "eval_total_return": total_return,
        "eval_final_value": final_value,
        "eval_total_reward": total_reward,
        "eval_steps": episode_steps,
    }


def main():
    args = parse_args()
    np.random.seed(args.seed)

    # ── Imports ────────────────────────────────────────────────────────────────
    try:
        import torch
        torch.manual_seed(args.seed)
    except ImportError:
        logger.error("PyTorch not installed. Run: pip install torch")
        sys.exit(1)

    try:
        from torch.utils.tensorboard import SummaryWriter
        writer = SummaryWriter(log_dir=args.log_dir)
        logger.info("TensorBoard logs → %s", args.log_dir)
    except ImportError:
        writer = None
        logger.warning("tensorboard not installed; no TensorBoard logging. pip install tensorboard")

    from tradingagents.rl.environment import StockTradingEnv
    from tradingagents.rl.td3_agent import TD3Agent

    # ── Load data ─────────────────────────────────────────────────────────────
    tickers = load_tickers(args)
    price_data = download_price_data(tickers, args.start, args.end)
    tickers = [t for t in tickers if t in price_data]
    if len(tickers) < 1:
        logger.error("No usable tickers after download. Aborting.")
        sys.exit(1)

    logger.info("Training tickers (%d): %s", len(tickers), ", ".join(tickers))

    # ── Build environment ──────────────────────────────────────────────────────
    env = StockTradingEnv(
        price_data=price_data,
        tickers=tickers,
        starting_cash=args.starting_cash,
        max_position_size=args.max_position_size,
        transaction_cost=args.transaction_cost,
    )
    obs_dim = env.observation_space.shape[0]
    act_dim = env.action_space.shape[0]
    logger.info("Env: obs_dim=%d  act_dim=%d  steps=%d", obs_dim, act_dim, env.T)

    # ── Build agent ───────────────────────────────────────────────────────────
    agent = TD3Agent(
        obs_dim=obs_dim,
        act_dim=act_dim,
        hidden=args.hidden,
        lr_actor=args.lr_actor,
        lr_critic=args.lr_critic,
        gamma=args.gamma,
        tau=args.tau,
        policy_delay=args.policy_delay,
        policy_noise=args.policy_noise,
        noise_clip=args.noise_clip,
        epsilon_start=args.epsilon_start,
        epsilon_min=args.epsilon_min,
        epsilon_decay=args.epsilon_decay,
        buffer_capacity=args.buffer_capacity,
        batch_size=args.batch_size,
        device=args.device,
    )

    if args.checkpoint:
        agent.load(args.checkpoint)
        logger.info("Resumed from checkpoint at step %d", agent.total_steps)

    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # Save tickers + config alongside model for inference
    meta = {
        "tickers": tickers,
        "obs_dim": obs_dim,
        "act_dim": act_dim,
        "hidden": args.hidden,
        "max_position_size": args.max_position_size,
        "starting_cash": args.starting_cash,
    }
    (checkpoint_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # ── Training loop ─────────────────────────────────────────────────────────
    logger.info(
        "Training TD3 for %d iterations (warm-up: %d)...",
        args.iterations, args.warm_up_steps,
    )

    global_step = agent.total_steps
    episode_rewards: list = []
    episode_reward = 0.0
    episode_num = 0
    t0 = time.time()

    obs, _ = env.reset(seed=args.seed)

    while global_step < args.iterations:
        explore = global_step < args.warm_up_steps or np.random.rand() < agent.epsilon
        if global_step < args.warm_up_steps:
            action = env.action_space.sample()
        else:
            action = agent.select_action(obs, explore=True)

        next_obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        agent.store(obs, action, reward, next_obs, done)
        episode_reward += reward
        global_step += 1

        obs = next_obs

        # Train
        if global_step >= args.warm_up_steps:
            metrics = agent.train_step()
            if metrics and writer and global_step % args.log_interval == 0:
                for k, v in metrics.items():
                    writer.add_scalar(f"train/{k}", v, global_step)
                writer.add_scalar("train/epsilon", agent.epsilon, global_step)
                writer.add_scalar(
                    "train/portfolio_value", info.get("portfolio_value", 0), global_step
                )

        if done:
            episode_rewards.append(episode_reward)
            episode_num += 1
            elapsed = time.time() - t0
            steps_per_sec = global_step / max(elapsed, 1.0)

            if writer:
                writer.add_scalar("episode/reward", episode_reward, episode_num)
                writer.add_scalar(
                    "episode/portfolio_value",
                    info.get("portfolio_value", args.starting_cash),
                    episode_num,
                )

            if episode_num % 10 == 0:
                mean_r = np.mean(episode_rewards[-10:])
                logger.info(
                    "Ep %d | step %d/%d | ep_reward %.4f | mean10 %.4f | ε %.3f | %.0f stp/s",
                    episode_num, global_step, args.iterations,
                    episode_reward, mean_r, agent.epsilon, steps_per_sec,
                )

            episode_reward = 0.0
            obs, _ = env.reset()

        # Eval
        if global_step % args.eval_interval == 0 and global_step >= args.warm_up_steps:
            eval_metrics = evaluate_agent(agent, price_data, tickers, args)
            if eval_metrics:
                logger.info(
                    "EVAL step %d | return %.2f%% | value $%.0f",
                    global_step,
                    eval_metrics["eval_total_return"] * 100,
                    eval_metrics["eval_final_value"],
                )
                if writer:
                    for k, v in eval_metrics.items():
                        writer.add_scalar(f"eval/{k}", v, global_step)

        # Checkpoint
        if global_step % args.checkpoint_interval == 0:
            agent.save(checkpoint_dir)
            logger.info("Checkpoint saved at step %d", global_step)

    # Final save
    agent.save(checkpoint_dir)
    if writer:
        writer.close()

    logger.info("Training complete. Model saved → %s", checkpoint_dir)
    if episode_rewards:
        logger.info(
            "Final stats: %d episodes | mean episode reward %.4f",
            episode_num, np.mean(episode_rewards),
        )


if __name__ == "__main__":
    main()
