#!/usr/bin/env python3
"""Unified Short-Hold Paper Trading Runner.

Runs the UnifiedBrain decision layer as a parallel paper account.
Does NOT replace or modify paper_trade_today.py — the existing strategies
(algorithm, combined, ML, ML_new, long_hold, pure_ai) are untouched.

This runner:
  1. Downloads the same ticker universe and price data as paper_trade_today.py
  2. Builds candidates via build_candidates() (unchanged pipeline)
  3. Passes ALL candidates through UnifiedBrain.process()
  4. Opens/manages positions in a SEPARATE paper account (unified_paper_account.json)
  5. Enforces short-hold (max 10 trading days, no overnight by default is configurable)
  6. Writes unified_brain_audit_{YYYYMMDD}.jsonl for every decision
  7. Compatible with ProductionSafetyMonitor (same kill-switch, model health checks)

Safety rules (hard):
  - DO NOT touch live broker execution
  - DO NOT touch web/api/fidelity.py or web/api/webull_portfolio.py
  - DO NOT weaken risk controls
  - DO NOT train or tune on holdout (2026-05-08 → 2026-05-26)
  - All positions are paper only — no real-money orders

Example usage:
    python scripts/paper_trade_unified.py --reset
    python scripts/paper_trade_unified.py --once --max-tickers 100 --force
    python scripts/paper_trade_unified.py --starting-cash 25000 --max-hold-days 7
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=ResourceWarning)

import numpy as np
import pandas as pd
import yfinance as yf

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Imports from paper_trade_today (shared pipeline — read-only)
# ---------------------------------------------------------------------------
from scripts.paper_trade_today import (   # noqa: E402
    PaperAccount,
    Position,
    Candidate,
    build_candidates,
    parse_args as _parse_args_today,
)

# ---------------------------------------------------------------------------
# Portfolio / brain imports
# ---------------------------------------------------------------------------
from tradingagents.portfolio.unified_brain import (   # noqa: E402
    UnifiedBrain,
    UnifiedCandidate,
    BrainResult,
    SHORT_HOLD_CONFIG,
)
from tradingagents.portfolio.short_hold_exits import (  # noqa: E402
    ShortHoldExitManager,
    ShortHoldExitPlan,
    ExitSignal,
)
from tradingagents.portfolio.production_safety import ProductionSafetyMonitor  # noqa: E402
from tradingagents.portfolio.ticker_reliability import TickerReliabilityTracker  # noqa: E402
from tradingagents.portfolio.alpha_engine import PaperFeedbackTracker  # noqa: E402
from tradingagents.screening.market_regime import MarketRegimeEngine, MarketRegimeState  # noqa: E402

ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
UNIFIED_OUTPUT_SUBDIR = "unified_brain"   # inside --output-dir
# Filenames match the pattern expected by the web dashboard:
#   <data_dir>/<strategy>/state.json   (PaperAccount saves as state_path)
#   <data_dir>/<strategy>/events.jsonl (PaperAccount event_log_path)
ACCOUNT_FILENAME      = "state.json"
EVENTS_FILENAME       = "events.jsonl"
EXIT_PLANS_FILENAME   = "unified_exit_plans.json"


# ---------------------------------------------------------------------------
# Argument parser (extends paper_trade_today args + unified-specific flags)
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified short-hold paper trading runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ── Shared with paper_trade_today ──────────────────────────────────────
    parser.add_argument("--tickers",         default="all_tickers.txt")
    parser.add_argument("--starting-cash",   type=float,  default=10_000.0)
    parser.add_argument("--scan-interval-minutes", type=float, default=15.0)
    parser.add_argument("--output-dir",      default="tmp/paper_trading_today",
                        help="Base output dir (unified data written to <output-dir>/unified_brain/)")
    parser.add_argument("--model-bundle",    default="ml_models/latest/model_bundle.joblib")
    parser.add_argument("--new-model-bundle",default=None)
    parser.add_argument("--no-ml",           action="store_true")
    parser.add_argument("--ml-probability-threshold", type=float, default=0.0,
                        help="Win_prob threshold. Default 0.0 (disabled, ROC<0.5). Re-enable after Cycle 17.")
    parser.add_argument("--max-tickers",     type=int,    default=0)
    parser.add_argument("--batch-size",      type=int,    default=100)
    parser.add_argument("--price-batch-size",type=int,    default=200)
    parser.add_argument("--lookback-days",   type=int,    default=620)
    parser.add_argument("--benchmark",       default="SPY")
    parser.add_argument("--min-price",       type=float,  default=15.0)
    parser.add_argument("--max-price",       type=float,  default=100.0)
    parser.add_argument("--threshold",       type=float,  default=100.0)
    parser.add_argument("--target-mult",     type=float,  default=1.2)
    parser.add_argument("--stop-mult",       type=float,  default=1.0)
    parser.add_argument("--once",            action="store_true",
                        help="Run one scan cycle and exit.")
    parser.add_argument("--force",           action="store_true",
                        help="Allow scan outside regular market hours.")
    parser.add_argument("--reset",           action="store_true",
                        help="Reset unified paper account to starting cash.")
    parser.add_argument("--allow-near-miss-rule-candidates",
                        action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--near-miss-max-soft-failures", type=int, default=3)

    # ── Unified-specific ───────────────────────────────────────────────────
    parser.add_argument("--max-hold-days",   type=int,    default=10,
                        help="Hard max hold days for short-hold mode.")
    parser.add_argument("--min-rr",          type=float,  default=0.8,
                        help="Minimum reward:risk to accept a candidate. "
                             "Default 0.8 (1.2 was blocking all confirmed_pullback signals, median R:R=0.79).")
    parser.add_argument("--risk-pct",        type=float,  default=1.0,
                        help="Percent of account to risk per trade.")
    parser.add_argument("--position-cap-pct",type=float,  default=20.0,
                        help="Max account %% per position.")
    parser.add_argument("--max-heat-pct",    type=float,  default=75.0,
                        help="Max account %% deployed at once.")
    parser.add_argument("--max-open-positions", type=int, default=5)
    parser.add_argument("--min-confidence",  type=float,  default=0.0,
                        help="Minimum ML confidence. Default 0.0 (disabled, win_prob ROC<0.5). Re-enable after Cycle 17.")
    parser.add_argument("--vix-crisis-threshold",   type=float, default=35.0)
    parser.add_argument("--vix-elevated-threshold", type=float, default=25.0)
    parser.add_argument("--hold-overnight",  action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-long-hold", action=argparse.BooleanOptionalAction, default=True,
                        help="Exclude long_hold and pure_ai strategies from unified brain.")
    parser.add_argument("--commission",      type=float,  default=0.0)
    parser.add_argument("--skip-vix-low-vol", action=argparse.BooleanOptionalAction, default=True,
                        help="Skip trades when VIX regime is low_vol. Evidence: E=-0.094%%/trade.")
    parser.add_argument("--skip-extended-bounce", action=argparse.BooleanOptionalAction, default=True,
                        help="Skip when consec_up>=2. Evidence: consec_up<=1 E=+0.530%% vs 0.157%%.")
    parser.add_argument("--skip-thursday", action=argparse.BooleanOptionalAction, default=True,
                        help="Skip Thursday scans (→ Friday opens). Evidence: Thu WR=50.4%% vs 57.4%% non-Thu, z=-3.5.")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _market_is_open(now: dt.datetime, force: bool) -> bool:
    if force:
        return True
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close


def _load_exit_plans(path: Path) -> Dict[str, ShortHoldExitPlan]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {ticker: ShortHoldExitPlan.from_dict(d) for ticker, d in raw.items()}
    except Exception as e:
        print(f"[unified] Warning: could not load exit plans: {e}")
        return {}


def _save_exit_plans(plans: Dict[str, ShortHoldExitPlan], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({t: p.to_dict() for t, p in plans.items()}, indent=2),
        encoding="utf-8",
    )


def _open_days(account: PaperAccount, ticker: str, now: dt.datetime) -> int:
    """Rough count of trading days position has been open."""
    pos = account.positions.get(ticker)
    if pos is None:
        return 0
    try:
        opened = dt.datetime.fromisoformat(pos.entry_time)
        delta  = now - opened
        # Approx: 5/7 trading days per calendar day
        return max(0, int(delta.days * 5 / 7))
    except Exception:
        return 0


def _fetch_live_prices(tickers: List[str], batch_size: int = 200) -> Dict[str, float]:
    """Fetch latest prices via yfinance fast_info."""
    prices: Dict[str, float] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            data = yf.download(
                " ".join(batch),
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=True,
            )
            if data.empty:
                continue
            close = data["Close"] if "Close" in data.columns else data.iloc[:, 0:1]
            if hasattr(close, "columns"):
                for t in close.columns:
                    val = close[t].dropna()
                    if len(val) > 0:
                        prices[t] = float(val.iloc[-1])
            else:
                val = close.dropna()
                if len(val) > 0 and len(batch) == 1:
                    prices[batch[0]] = float(val.iloc[-1])
        except Exception as e:
            print(f"[unified] Price fetch error (batch {i}): {e}")
    return prices


def _fetch_vix(fallback: float = 18.0) -> float:
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="2d", interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return fallback


# ---------------------------------------------------------------------------
# Exit processing for open unified positions
# ---------------------------------------------------------------------------

def _process_exits(
    account:     PaperAccount,
    exit_plans:  Dict[str, ShortHoldExitPlan],
    prices:      Dict[str, float],
    now:         dt.datetime,
    args:        argparse.Namespace,
    output_dir:  Path,
) -> None:
    """Check exit conditions for all open unified positions; execute sells."""
    manager = ShortHoldExitManager()
    to_delete: List[str] = []

    for ticker, plan in list(exit_plans.items()):
        if ticker not in account.positions:
            to_delete.append(ticker)
            continue

        price = prices.get(ticker)
        if price is None or price <= 0:
            continue

        days = _open_days(account, ticker, now)
        result = manager.check(plan, price, now.date(), days)

        # Update mutable plan state from result
        exit_plans[ticker] = result.updated_plan

        if result.signal == ExitSignal.HOLD:
            continue

        pos = account.positions[ticker]
        shares_to_sell = pos.shares  # full close by default

        if result.signal == ExitSignal.PARTIAL:
            shares_to_sell = max(1, math.floor(pos.shares * result.close_fraction))

        if shares_to_sell <= 0:
            continue

        reason_tag = result.signal.value
        print(
            f"[unified] EXIT {ticker}: {result.signal.value} "
            f"price={price:.2f} shares={shares_to_sell} reason={result.reason}"
        )
        account.sell(ticker, price, reason_tag, now)

        # Log exit audit
        audit_path = output_dir / f"unified_brain_audit_{now.strftime('%Y%m%d')}.jsonl"
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts":        now.isoformat(timespec="seconds"),
                "ticker":    ticker,
                "decision":  "EXIT",
                "exit_type": result.signal.value,
                "price":     price,
                "shares":    shares_to_sell,
                "reason":    result.reason,
                "open_days": days,
            }) + "\n")

        if result.close_fraction >= 1.0:
            to_delete.append(ticker)

    for t in to_delete:
        exit_plans.pop(t, None)


# ---------------------------------------------------------------------------
# EOD flatten
# ---------------------------------------------------------------------------

def _eod_flatten(
    account: PaperAccount,
    exit_plans: Dict[str, ShortHoldExitPlan],
    prices: Dict[str, float],
    now: dt.datetime,
    output_dir: Path,
) -> None:
    """Flatten all positions at end of day (if hold_overnight=False)."""
    for ticker in list(account.positions.keys()):
        price = prices.get(ticker, 0.0)
        if price <= 0:
            continue
        print(f"[unified] EOD_FLATTEN {ticker} price={price:.2f}")
        account.sell(ticker, price, "EOD_FLATTEN", now)
        exit_plans.pop(ticker, None)
        audit_path = output_dir / f"unified_brain_audit_{now.strftime('%Y%m%d')}.jsonl"
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts":        now.isoformat(timespec="seconds"),
                "ticker":    ticker,
                "decision":  "EXIT",
                "exit_type": "EOD_FLATTEN",
                "price":     price,
                "reason":    "end-of-day flatten",
            }) + "\n")


# ---------------------------------------------------------------------------
# Entry: process unified brain output and open new positions
# ---------------------------------------------------------------------------

def _process_entries(
    account:     PaperAccount,
    brain_result: BrainResult,
    exit_plans:  Dict[str, ShortHoldExitPlan],
    prices:      Dict[str, float],
    now:         dt.datetime,
    config:      Dict[str, Any],
    output_dir:  Path,
    skip_entries: bool,
) -> None:
    """Open new positions for accepted unified candidates."""
    if skip_entries:
        return

    for uc in brain_result.accepted:
        ticker = uc.ticker
        if ticker in account.positions:
            continue  # already open
        if uc.shares <= 0:
            continue
        price = prices.get(ticker, uc.entry)
        if price <= 0:
            continue

        cost = price * uc.shares
        if cost > account.settled_cash:
            print(f"[unified] SKIP {ticker}: insufficient cash ({cost:.0f} > {account.settled_cash:.0f})")
            continue

        print(
            f"[unified] BUY {ticker}: tier={uc.tier} alpha={uc.alpha_score:.3f} "
            f"shares={uc.shares} price={price:.2f} stop={uc.stop:.2f} tp={uc.take_profit:.2f}"
        )
        account.buy(ticker, price, uc.shares, now)

        # Create exit plan
        plan = ShortHoldExitPlan.from_candidate(uc, config=config)
        exit_plans[ticker] = plan

    account.save()


# ---------------------------------------------------------------------------
# Single scan cycle
# ---------------------------------------------------------------------------

def scan_once(
    account:           PaperAccount,
    exit_plans:        Dict[str, ShortHoldExitPlan],
    args:              argparse.Namespace,
    now:               dt.datetime,
    output_dir:        Path,
    reliability:       TickerReliabilityTracker,
    feedback:          PaperFeedbackTracker,
    regime_engine:     MarketRegimeEngine,
    config:            Dict[str, Any],
    candidates_by_strategy: Dict[str, List[Candidate]],
    prices:            Dict[str, float],
    vix_level:         float,
    spy_regime:        str,
    regime_state:      Optional[MarketRegimeState],
) -> None:
    """One full scan: exits → safety check → brain → entries."""

    # ── Safety monitor ────────────────────────────────────────────────────
    bundle_path = args.model_bundle
    bundle: Optional[Dict] = getattr(args, "_bundle_ref", None)
    safety_monitor = ProductionSafetyMonitor(output_dir=str(output_dir))
    safety_report = safety_monitor.check_all(
        account=account,
        prices=prices,
        bundle=bundle,
        bundle_path=bundle_path,
        vix_level=vix_level,
        spy_regime=spy_regime,
        regime_state=regime_state,
        output_dir=str(output_dir),
        now=now,
    )
    skip_entries = not safety_report.safe_to_trade
    for reason in safety_report.halt_reasons:
        print(f"[unified] SAFETY_HALT: {reason}")
        account.log_event({"type": "SAFETY_HALT", "reason": reason, "ts": now.isoformat()})

    # ── Exits (always run, even on safety halt) ───────────────────────────
    _process_exits(account, exit_plans, prices, now, args, output_dir)

    # ── EOD flatten ───────────────────────────────────────────────────────
    market_close = now.replace(hour=15, minute=55, second=0, microsecond=0)
    if not args.hold_overnight and now >= market_close:
        _eod_flatten(account, exit_plans, prices, now, output_dir)
        return

    if skip_entries:
        return

    # ── UnifiedBrain ──────────────────────────────────────────────────────
    brain = UnifiedBrain(config=config)
    exclude = ["long_hold", "pure_ai"] if args.exclude_long_hold else []

    account_value = account.cash + sum(
        account.positions[t].shares * prices.get(t, account.positions[t].avg_price)
        for t in account.positions
    )
    current_heat = sum(
        account.positions[t].shares * prices.get(t, account.positions[t].avg_price)
        for t in account.positions
    ) / account_value * 100 if account_value > 0 else 0.0

    brain_result = brain.process(
        candidates_by_strategy=candidates_by_strategy,
        account=account,
        account_value=account_value,
        prices=prices,
        regime_state=regime_state,
        spy_regime=spy_regime,
        vix_level=vix_level,
        reliability_tracker=reliability,
        feedback_tracker=feedback,
        output_dir=output_dir,
        exclude_strategies=exclude,
    )

    print(
        f"[unified] Brain: {len(brain_result.accepted)} accepted, "
        f"{len(brain_result.rejected)} rejected, "
        f"{len(brain_result.watchlist)} watchlist"
    )

    # ── Entries ───────────────────────────────────────────────────────────
    _process_entries(
        account=account,
        brain_result=brain_result,
        exit_plans=exit_plans,
        prices=prices,
        now=now,
        config=config,
        output_dir=output_dir,
        skip_entries=skip_entries,
    )

    # Save exit plans after potential updates
    _save_exit_plans(exit_plans, output_dir / EXIT_PLANS_FILENAME)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # ── Directories ───────────────────────────────────────────────────────
    base_dir   = ROOT / args.output_dir
    output_dir = base_dir / UNIFIED_OUTPUT_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)

    account_path = output_dir / ACCOUNT_FILENAME
    events_path  = output_dir / EVENTS_FILENAME
    exit_plans_path = output_dir / EXIT_PLANS_FILENAME

    # ── Build unified brain config ────────────────────────────────────────
    config = {
        **SHORT_HOLD_CONFIG,
        "max_hold_days":         args.max_hold_days,
        "min_rr":                args.min_rr,
        "risk_pct_per_trade":    args.risk_pct,
        "position_cap_pct":      args.position_cap_pct,
        "max_heat_pct":          args.max_heat_pct,
        "max_open_positions":    args.max_open_positions,
        "min_confidence":        args.min_confidence,
        "vix_crisis_threshold":  args.vix_crisis_threshold,
        "vix_elevated_threshold":args.vix_elevated_threshold,
    }

    # ── Paper account ────────────────────────────────────────────────────
    account = PaperAccount(
        state_path=account_path,
        event_log_path=events_path,
        starting_cash=args.starting_cash,
        commission=args.commission,
        reset=args.reset,
        strategy="unified_brain",
    )
    print(
        f"[unified] Account: cash={account.cash:.2f} "
        f"positions={len(account.positions)} "
        f"pnl={account.realized_pnl:.2f}"
    )

    # ── Exit plans ───────────────────────────────────────────────────────
    exit_plans = _load_exit_plans(exit_plans_path)
    # Prune plans for positions no longer open
    for t in list(exit_plans.keys()):
        if t not in account.positions:
            del exit_plans[t]

    # ── Tracking objects ─────────────────────────────────────────────────
    reliability = TickerReliabilityTracker(
        data_path=str(output_dir / "ticker_reliability.json")
    )
    feedback    = PaperFeedbackTracker(
        state_path=str(output_dir / "paper_feedback.json")
    )
    regime_engine = MarketRegimeEngine()

    # ── Stub args for build_candidates ───────────────────────────────────
    # build_candidates expects argparse.Namespace with many fields from
    # paper_trade_today.parse_args(). We pass a compatible namespace.
    _today_args = argparse.Namespace(
        tickers                        = args.tickers,
        starting_cash                  = args.starting_cash,
        scan_interval_minutes          = args.scan_interval_minutes,
        output_dir                     = str(args.output_dir),
        model_bundle                   = args.model_bundle,
        new_model_bundle               = args.new_model_bundle,
        no_ml                          = args.no_ml,
        ml_probability_threshold       = args.ml_probability_threshold,
        ml_large_loss_max              = None,
        ml_expected_return_min         = None,
        max_tickers                    = args.max_tickers,
        batch_size                     = args.batch_size,
        price_batch_size               = args.price_batch_size,
        lookback_days                  = args.lookback_days,
        benchmark                      = args.benchmark,
        min_price                      = args.min_price,
        max_price                      = args.max_price,
        threshold                      = args.threshold,
        target_mult                    = args.target_mult,
        stop_mult                      = args.stop_mult,
        max_hold_days                  = args.max_hold_days,
        long_hold_days                 = 20,
        hold_overnight                 = args.hold_overnight,
        position_cap_pct               = args.position_cap_pct,
        position_cap_min_pct           = 10.0,
        position_high_confidence_threshold = 0.80,
        risk_per_trade_pct             = args.risk_pct,
        min_risk_reward                = args.min_rr,
        bear_regime_size_factor        = 0.5,
        neutral_regime_size_factor     = 0.75,
        crisis_vix_threshold           = args.vix_crisis_threshold,
        elevated_vix_threshold         = args.vix_elevated_threshold,
        ml_drift_halt_threshold        = 0.20,
        rolling_wr_floor               = 0.30,
        take_profit_pct                = 0.0,
        allow_near_miss_rule_candidates= getattr(args, "allow_near_miss_rule_candidates", True),
        near_miss_max_soft_failures    = getattr(args, "near_miss_max_soft_failures", 3),
        skip_vix_low_vol               = getattr(args, "skip_vix_low_vol", True),
        skip_extended_bounce           = getattr(args, "skip_extended_bounce", True),
        skip_thursday                  = getattr(args, "skip_thursday", True),
        skip_monday                    = getattr(args, "skip_monday", True),
        max_ml_candidates              = 200,
        no_ai                          = True,  # disable Pure AI to avoid API calls
        openrouter_model               = "openai/gpt-4o-mini",
        ai_shortlist_size              = 30,
        ai_max_picks                   = 5,
        force                          = args.force,
        once                           = args.once,
        reset                          = args.reset,
        no_dashboard                   = True,
        benchmark_symbol               = "SPY",
        max_portfolio_drawdown         = 12.0,
    )

    # ── Main loop ─────────────────────────────────────────────────────────
    interval_secs = args.scan_interval_minutes * 60
    first_run     = True

    while True:
        now = dt.datetime.now(tz=ET)

        if not _market_is_open(now, args.force):
            if args.once:
                print("[unified] Market closed. Exiting (--once).")
                break
            print(f"[unified] Market closed. Sleeping {interval_secs:.0f}s…")
            time.sleep(interval_secs)
            continue

        print(f"\n[unified] ─── Scan at {now.strftime('%Y-%m-%d %H:%M:%S ET')} ───")

        try:
            # ── Build candidates (reuses existing pipeline unchanged) ─────
            print("[unified] Building candidates…")
            (
                candidates_by_strategy,
                prices,
                spy_regime,
                vix_level,
                regime_state,
            ) = build_candidates(args=_today_args)

            if vix_level is None:
                vix_level = _fetch_vix()

            # Cache refs on args for safety monitor
            _today_args._bundle_ref        = getattr(_today_args, "_bundle_ref", None)
            _today_args._regime_state_cache = regime_state
            _today_args.ml_model_bundle     = args.model_bundle

            total_candidates = sum(len(v) for v in candidates_by_strategy.values())
            print(
                f"[unified] Candidates: {total_candidates} across "
                f"{list(candidates_by_strategy.keys())} | "
                f"SPY regime={spy_regime} VIX={vix_level:.1f}"
            )

            # ── Run scan cycle ────────────────────────────────────────────
            scan_once(
                account=account,
                exit_plans=exit_plans,
                args=args,
                now=now,
                output_dir=output_dir,
                reliability=reliability,
                feedback=feedback,
                regime_engine=regime_engine,
                config=config,
                candidates_by_strategy=candidates_by_strategy,
                prices=prices,
                vix_level=float(vix_level),
                spy_regime=spy_regime,
                regime_state=regime_state,
            )

        except KeyboardInterrupt:
            print("\n[unified] Interrupted by user.")
            break
        except Exception as e:
            import traceback
            print(f"[unified] ERROR in scan cycle: {e}")
            traceback.print_exc()

        if args.once:
            print("[unified] --once flag set. Exiting.")
            break

        print(f"[unified] Sleeping {interval_secs:.0f}s until next scan…")
        time.sleep(interval_secs)

    # ── Final summary ─────────────────────────────────────────────────────
    account_value = account.cash + sum(
        account.positions[t].shares * account.positions[t].avg_price
        for t in account.positions
    )
    print("\n[unified] ══ Final Account Summary ══")
    print(f"  Cash          : ${account.cash:.2f}")
    print(f"  Open positions: {len(account.positions)}")
    print(f"  Realized P&L  : ${account.realized_pnl:.2f}")
    print(f"  Account value : ${account_value:.2f}")
    print(f"  Total trades  : {len(account.trades)}")
    print(f"  Audit log     : {output_dir}/unified_brain_audit_*.jsonl")


if __name__ == "__main__":
    main()
