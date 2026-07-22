#!/usr/bin/env python3
"""Live candidate provider + runner for the 15 paper portfolios.

Feeds REAL candidates into the paper engine:
  - build_candidates()  → breakout/pullback screen + model_bundle.joblib ML
                          win-probability, bucketed by source strategy.
  - UnifiedBrain.process() → the `unified_brain` bucket (brainstorm_atlas), real.
  - latest_prices()     → yfinance 1-min last prices for MTM + fills.

Then paper_engine.run_all() gates each portfolio (ML threshold + compliance),
sizes, opens/manages positions, and persists state + snapshots + CSV.

Paper-only — never imports or calls any live broker route.

Usage:
    python scripts/run_paper_portfolios.py --once --max-tickers 40
    python scripts/run_paper_portfolios.py --loop-interval 15        # 15-min loop
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import joblib  # noqa: E402

from tradingagents.portfolio.paper_engine import Candidate, run_all, SIT_OUT_DOWS  # noqa: E402
from tradingagents.portfolio.paper_configs import all_portfolios  # noqa: E402
from tradingagents.portfolio.paper_account import PaperPortfolioAccount  # noqa: E402

ET = ZoneInfo("US/Eastern")
BASE = ROOT / "tmp" / "paper_portfolios"
SNAPS = ROOT / "paper_accounts"
BUNDLE = ROOT / "ml_models" / "latest" / "model_bundle.joblib"


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _default_args(max_tickers: int, tickers: str,
                  skip_monday: bool | None = None, skip_thursday: bool | None = None):
    """Reuse paper_trade_today's own parser for a fully-populated args Namespace.

    skip_monday / skip_thursday default None → keep paper_trade_today's own
    defaults (both True — the backtested Mon/Thu sit-out filters). Pass an
    explicit bool to override for this run (e.g. False to trade Mondays).
    """
    import paper_trade_today as ptt
    argv = sys.argv
    sys.argv = ["run_paper_portfolios"]
    try:
        args = ptt.parse_args()
    except SystemExit:
        # Parser had a required arg; fall back to a minimal namespace.
        args = argparse.Namespace(
            batch_size=50, benchmark="SPY", lookback_days=400, no_ai=False,
            stop_mult=1.5, target_mult=2.5, threshold=0.5,
        )
    finally:
        sys.argv = argv
    args.max_tickers = max_tickers
    args.tickers = tickers
    if skip_monday is not None:
        args.skip_monday = skip_monday
    if skip_thursday is not None:
        args.skip_thursday = skip_thursday
    return args


def _map_row(row, source: str) -> Candidate:
    return Candidate(
        ticker=getattr(row, "ticker"),
        price=float(getattr(row, "entry", 0) or 0),
        source=source,
        stop=_f(getattr(row, "stop", None)),
        target=_f(getattr(row, "target", None)),
        atr=_f(getattr(row, "atr", None)),
        ml_probability=_f(getattr(row, "ml_probability", None)),
        score=_f(getattr(row, "score", None)),
    )


def build_candidate_pool(max_tickers: int, tickers: str, trade_date: dt.date | None = None,
                         skip_monday: bool | None = None, skip_thursday: bool | None = None) -> tuple[dict[str, list[Candidate]], object]:
    """Run the real screen + ML + UnifiedBrain → engine candidates keyed by source."""
    import paper_trade_today as ptt
    args = _default_args(max_tickers, tickers, skip_monday, skip_thursday)
    bundle = joblib.load(BUNDLE) if BUNDLE.exists() else None
    # ml_new source needs a challenger model. Until one is trained, fall back to the
    # main bundle so the ML-New portfolios still trade on real ML (same model, distinct
    # thresholds/holds). Swap in a real new_model_bundle.joblib to differentiate.
    new_path = ROOT / "ml_models" / "latest" / "new_model_bundle.joblib"
    new_bundle = joblib.load(new_path) if new_path.exists() else bundle
    if not new_path.exists():
        print("[provider] no challenger bundle — ml_new falls back to the main model")
    trade_date = trade_date or dt.date.today()

    cands_by_strategy, _raw = ptt.build_candidates(args, trade_date, bundle, new_bundle)

    by_source: dict[str, list[Candidate]] = {
        src: [_map_row(r, src) for r in rows] for src, rows in cands_by_strategy.items()
    }

    # unified_brain (brainstorm_atlas) — real UnifiedBrain over the full pool.
    try:
        from tradingagents.portfolio.unified_brain import UnifiedBrain, SHORT_HOLD_CONFIG
        brain = UnifiedBrain(config=SHORT_HOLD_CONFIG)
        res = brain.process(candidates_by_strategy=cands_by_strategy, account_value=10_000.0)
        by_source["unified_brain"] = [
            Candidate(
                ticker=uc.ticker, price=float(uc.entry), source="unified_brain",
                stop=_f(getattr(uc, "stop", None)), target=_f(getattr(uc, "take_profit", None)),
                atr=_f(getattr(uc, "atr", None)), ml_probability=_f(getattr(uc, "confidence", None)),
                score=_f(getattr(uc, "alpha_score", None)),
            )
            for uc in res.accepted
        ]
    except Exception as e:  # brain unavailable → honest empty, never fake
        print(f"[provider] UnifiedBrain unavailable ({e}); brainstorm_atlas gets no candidates this cycle")
        by_source["unified_brain"] = []

    # thematic (thematic_momentum) — the social-momentum strategy competes with its
    # OWN pending picks (from the thematic scanner), not the price screen's candidates.
    by_source["thematic"] = _thematic_candidates()

    # Signal-bar weekday — the exact bar the screen keys its Mon/Thu skip off
    # (last completed daily bar strictly before trade_date; holiday-proof because
    # it reads real bars, not a naive calendar walk). Used per-portfolio downstream.
    signal_bar_dow = _signal_bar_dow(_raw, args, trade_date)
    return by_source, args, signal_bar_dow


def _thematic_candidates(max_signals: int = 25) -> "list[Candidate]":
    """Read the thematic system's current PENDING picks (social/news momentum, v2
    macro-cluster diversified) and convert them to engine candidates so the thematic
    strategy competes in the paper competition alongside the ML/algo/multi books.

    Each pending signal carries conviction + its own target_pct/stop_pct; we fetch a
    fresh price and set explicit stop/target levels off it. Empty (honest) when the
    scanner has produced nothing — the thematic_momentum book simply doesn't open.
    """
    try:
        import paper_trade_today as ptt
        sigs_path = ROOT / "tmp" / "thematic_signals.json"
        if not sigs_path.exists():
            return []
        data = json.loads(sigs_path.read_text())
        sigs = data.get("signals", []) if isinstance(data, dict) else data
        pending = [s for s in (sigs or []) if s.get("status") == "pending"][:max_signals]
        tickers = [str(s.get("ticker", "")).upper() for s in pending if s.get("ticker")]
        if not tickers:
            return []
        prices = ptt.latest_prices(list(dict.fromkeys(tickers)), 50)
        out: "list[Candidate]" = []
        for s in pending:
            t = str(s.get("ticker", "")).upper()
            px = _f(prices.get(t))
            if not px or px <= 0:
                continue
            conv = float(s.get("conviction", 7) or 7)
            tgt_pct = float(s.get("target_pct", 40) or 40)
            stop_pct = float(s.get("stop_pct", 8) or 8)
            out.append(Candidate(
                ticker=t, price=px, source="thematic",
                stop=round(px * (1.0 - stop_pct / 100.0), 4),
                target=round(px * (1.0 + tgt_pct / 100.0), 4),
                score=conv * 10.0,
            ))
        print(f"[provider] thematic bucket: {len(out)} pending picks")
        return out
    except Exception as e:
        print(f"[provider] thematic bucket unavailable: {e}")
        return []


def _signal_bar_dow(raw: dict, args, trade_date: dt.date) -> int | None:
    """Weekday (0=Mon..6=Sun) of the last daily bar before trade_date, from the
    benchmark (falls back to any ticker). None if it can't be determined."""
    import paper_trade_today as ptt
    try:
        bench = getattr(args, "benchmark", "SPY") or "SPY"
        df = raw.get(bench)
        if df is None or not len(df):
            df = next((v for v in raw.values() if v is not None and len(v)), None)
        if df is None:
            return None
        cleaned = ptt.clean_daily_frame(df, trade_date)
        if not len(cleaned):
            return None
        return int(cleaned.index[-1].dayofweek)
    except Exception:
        return None


def collect_prices(by_source: dict[str, list[Candidate]], args) -> dict[str, float]:
    """yfinance last prices for all candidate + open-position tickers."""
    import paper_trade_today as ptt
    tickers = {c.ticker for rows in by_source.values() for c in rows}
    for cfg in all_portfolios():
        acc = PaperPortfolioAccount.load(cfg.portfolio_id, BASE)
        tickers.update(p.ticker for p in acc.positions)
    if not tickers:
        return {}
    return ptt.latest_prices(list(tickers), getattr(args, "batch_size", 50))


def run_once(max_tickers: int, tickers: str, trade_date: dt.date | None = None,
             skip_monday: bool | None = None, skip_thursday: bool | None = None) -> list[dict]:
    # Always build the pool with the calendar filters OFF so skip-day-trading
    # portfolios have candidates on Mon/Thu. The sit-out is now a PER-PORTFOLIO
    # decision (cfg.trade_skip_days), applied in run_all via signal_bar_dow.
    # skip_monday/skip_thursday here are optional GLOBAL forces (from the CLI):
    # True → every portfolio sits that weekday out; False → every portfolio
    # trades it; None → defer to each portfolio's A/B flag.
    by_source, args, signal_bar_dow = build_candidate_pool(
        max_tickers, tickers, trade_date, skip_monday=False, skip_thursday=False)
    counts = {k: len(v) for k, v in by_source.items() if v}
    _dow_name = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    print(f"[provider] candidates by source: {counts or 'none'}")
    print(f"[provider] signal-bar weekday: {_dow_name.get(signal_bar_dow, signal_bar_dow)}"
          + (" (sit-out day → only trade_skip_days portfolios enter)" if signal_bar_dow in SIT_OUT_DOWS else ""))

    prices = collect_prices(by_source, args)
    print(f"[provider] prices for {len(prices)} tickers")

    now = dt.datetime.now(ET)
    summaries = run_all(BASE, by_source, prices, now=now, snapshots_base=SNAPS, csv_base=SNAPS,
                        signal_bar_dow=signal_bar_dow,
                        force_skip_monday=skip_monday, force_skip_thursday=skip_thursday)
    opened = sum(s["opened"] for s in summaries)
    closed = sum(s["closed"] for s in summaries)
    sat_out = sum(1 for s in summaries if s.get("sat_out"))
    print(f"[provider] ran {len(summaries)} portfolios — opened {opened}, closed {closed}, sat out {sat_out}")

    BASE.mkdir(parents=True, exist_ok=True)
    snap = {
        k: [{"ticker": c.ticker, "entry": c.price, "ml_probability": c.ml_probability,
             "stop": c.stop, "target": c.target} for c in v]
        for k, v in by_source.items()
    }
    (BASE / "candidates_latest.json").write_text(json.dumps(snap, indent=2, default=str))
    return summaries


def main():
    ap = argparse.ArgumentParser(description="Run the 15 paper portfolios on live candidates.")
    ap.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    ap.add_argument("--loop-interval", type=int, default=15, help="Minutes between cycles in loop mode.")
    ap.add_argument("--max-tickers", type=int, default=0, help="0 = all tickers in the file.")
    ap.add_argument("--tickers", default="all_tickers.txt", help="Ticker universe file.")
    ap.add_argument("--trade-date", default=None, help="YYYY-MM-DD to scan for (default: today). Signal bar = last bar before this date.")
    ap.add_argument("--skip-monday", action=argparse.BooleanOptionalAction, default=None,
                    help="GLOBAL force for Monday signal bars. Default (unset): each portfolio's "
                         "trade_skip_days A/B decides. --skip-monday → ALL sit out; --no-skip-monday → ALL trade.")
    ap.add_argument("--skip-thursday", action=argparse.BooleanOptionalAction, default=None,
                    help="GLOBAL force for Thursday signal bars. Default (unset): per-portfolio A/B decides. "
                         "--skip-thursday → ALL sit out; --no-skip-thursday → ALL trade.")
    a = ap.parse_args()
    td = dt.date.fromisoformat(a.trade_date) if a.trade_date else None

    if a.once:
        run_once(a.max_tickers, a.tickers, td, a.skip_monday, a.skip_thursday)
        return

    print(f"[provider] loop mode — every {a.loop_interval} min. Ctrl-C to stop.")
    while True:
        try:
            run_once(a.max_tickers, a.tickers, td, a.skip_monday, a.skip_thursday)
        except Exception as e:
            print(f"[provider] cycle error: {e}")
        time.sleep(max(60, a.loop_interval * 60))


if __name__ == "__main__":
    main()
