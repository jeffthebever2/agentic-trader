"""Paper trading engine for the 15 isolated portfolios.

Pure and deterministic: given candidates + prices + a clock, it opens/manages
paper positions for one account, enforcing ML gates, risk sizing, and compliance.
No network, no live broker — injectable so it is fully unit-testable.

Cash model (settlement-aware):
    free cash = settled_cash + unsettled_cash        (kept in `cash`)
    BUY  cost C  → settled_cash -= C                 (only settled cash is spendable)
    SELL proceeds P → unsettled_cash += P            (credited T+1)
    new business day → unsettled_cash rolls to settled_cash
    equity = free_cash + Σ(shares × market_price)
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from tradingagents.portfolio.paper_account import PaperPortfolioAccount, PaperPosition, PaperTrade
from tradingagents.portfolio.paper_configs import PaperPortfolioConfig
from tradingagents.portfolio.paper_compliance import (
    PaperComplianceConfig,
    DEFAULT_COMPLIANCE,
    can_enter_trade,
    log_compliance_event,
)

ET = ZoneInfo("US/Eastern")

# Which sources count as ML / brain / AI for provenance stamping.
_ML_SOURCES = {"machine_learning", "ml_new", "combined"}
_BRAIN_SOURCES = {"unified_brain"}
_AI_SOURCES = {"pure_ai"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── Smart skip-day entry policy ──────────────────────────────────────────────
# On a Mon/Thu SIGNAL BAR the day-of-week edge is negative (backtest: Thu WR
# 50.4% vs 57.4%, Mon 55.3% vs 66.9%). A portfolio that trades those days anyway
# (cfg.trade_skip_days, or a global force) applies this policy instead of eating
# the tax — take FEWER, HIGHER-QUALITY, SMALLER, SHORTER-LEASHED entries:
#   • raise conviction: ML threshold +BUMP, and require a minimum reward:risk
#   • halve the risk (SIZE_FACTOR) so a lower-WR day does less damage
#   • cap the hold (Thu→Fri entries straddle two weekends → shorten the leash)
# All env-tunable; defaults are deliberately conservative. Exits are untouched.
SKIP_DAY_SIZE_FACTOR = _env_float("PAPER_SKIP_DAY_SIZE_FACTOR", 0.5)   # ×risk on sit-out days
SKIP_DAY_ML_BUMP     = _env_float("PAPER_SKIP_DAY_ML_BUMP", 0.05)      # +ML win-prob required
SKIP_DAY_MIN_RR      = _env_float("PAPER_SKIP_DAY_MIN_RR", 2.0)        # min (target-entry)/(entry-stop)
SKIP_DAY_MAX_HOLD    = int(_env_float("PAPER_SKIP_DAY_MAX_HOLD", 5))   # cap on max_hold_days

# One-way execution friction in basis points, applied against the trade: buys fill
# above the reference, sells below. Commission is intentionally NOT modelled —
# Fidelity US equities are zero-commission, so that part of the model is accurate.
# The spread is not: these are small-caps where the round-trip is routinely
# 40-150bps, and the engine fills entries at the screen price and exits at the
# level. That flatters every strategy, and `leaderboard_sort_key` ranks the 15-30
# portfolios on the result — which `web/copytrade.py` then mirrors into REAL money.
# So the strategy most sensitive to unmodelled friction is the one most likely to
# win the competition and get funded.
#
# DEFAULT 0.0 = off, because turning it on rewrites every historical ROR and puts
# a discontinuity in the leaderboard. Set PAPER_SLIPPAGE_BPS=10-25 to make the
# competition honest going forward; the gap-through stop fill in `_exit_decision`
# is on by default because that one is a correctness bug (filling at a price the
# market never offered), not a tunable.
PAPER_SLIPPAGE_BPS = max(0.0, _env_float("PAPER_SLIPPAGE_BPS", 0.0))

# Hard ceiling on any single paper position, as % of equity. A backstop against
# the degenerate risk-parity case where a missing ATR yields a very tight stop
# and therefore a very large position — see size_shares. 0 disables.
POSITION_CAP_PCT = max(0.0, _env_float("PAPER_POSITION_CAP_PCT", 25.0))


def apply_slippage(price: float, side: str, bps: float | None = None) -> float:
    """Adjust a reference price for one-way execution friction, always against us.

    ``side`` is "buy" (fill higher) or "sell" (fill lower)."""
    rate = PAPER_SLIPPAGE_BPS if bps is None else max(0.0, bps)
    if rate <= 0 or not price or price <= 0:
        return price
    factor = 1.0 + (rate / 10_000.0) * (1.0 if str(side).lower() == "buy" else -1.0)
    return max(0.0, price * factor)


@dataclass
class Candidate:
    """One entry idea fed to the engine. Provenance travels with it."""

    ticker: str
    price: float                          # current / intended entry price
    source: str = "algorithm"
    sources: Optional[list] = None        # multi-tool: every tool that flagged it
    stop: Optional[float] = None          # precomputed level (preferred)
    target: Optional[float] = None
    atr: Optional[float] = None           # fallback for level computation
    ml_probability: Optional[float] = None
    score: Optional[float] = None
    ai_commentary: Optional[str] = None   # advisory only


# ── Level + sizing helpers ───────────────────────────────────────────────────

def resolve_levels(cand: Candidate, cfg: PaperPortfolioConfig) -> tuple[float, float]:
    """Return (stop, target) for a candidate, from explicit levels, ATR, or a % fallback."""
    entry = cand.price
    if cand.stop is not None and cand.target is not None and cand.stop < entry < cand.target:
        return cand.stop, cand.target
    if cand.atr is not None and cand.atr > 0:
        return entry - cand.atr * cfg.stop_mult, entry + cand.atr * cfg.target_mult
    # Percentage fallback: treat each ATR mult as ~1.5% of price.
    return entry * (1 - 0.015 * cfg.stop_mult), entry * (1 + 0.015 * cfg.target_mult)


def size_shares(account: PaperPortfolioAccount, cfg: PaperPortfolioConfig, entry: float, stop: float,
                risk_factor: float = 1.0) -> int:
    """Risk-based position size, capped by settled cash. Whole shares.

    risk_factor scales the dollar risk (e.g. 0.5 for a smaller skip-day entry).
    """
    equity = account.current_equity()
    risk_dollars = equity * (cfg.risk_per_trade_pct / 100.0) * risk_factor
    per_share_risk = max(entry - stop, 0.01 * entry)  # floor to avoid div-by-zero
    shares = int(risk_dollars / per_share_risk)
    affordable = int(account.settled_cash / entry) if entry > 0 else 0
    # Concentration backstop. Pure risk-parity sizing has NO position ceiling —
    # the only limit was settled cash — and the tighter the stop, the larger the
    # position. That inverts badly when ATR is missing: `resolve_levels` then
    # falls back to a ~1.5%*stop_mult stop, so the *most* uncertain candidate
    # gets the *largest* bet (a 1% risk budget against a 2.1% stop is 47.6% of
    # equity in one name; two such fills and the book is 95% in two tickers).
    # This cap binds only in that pathological regime — normal ATR-derived stops
    # size well under it.
    cap_shares = (
        int(equity * POSITION_CAP_PCT / 100.0 / entry)
        if entry > 0 and POSITION_CAP_PCT > 0 else shares
    )
    return max(0, min(shares, affordable, cap_shares))


def _provenance(cand: Candidate) -> dict:
    """Which tool families actually contributed to this candidate.

    For multi-tool (consensus/blend) candidates, `sources` lists every tool that
    flagged the ticker, so a portfolio that combined algorithm+ML stamps both.
    """
    srcs = cand.sources or [cand.source]
    return {
        "used_ml": any(s in _ML_SOURCES for s in srcs),
        "used_unified_brain": any(s in _BRAIN_SOURCES for s in srcs),
        "used_ai": any(s in _AI_SOURCES for s in srcs),
    }


def combine_candidates(candidates_by_source: dict[str, list[Candidate]],
                       source_strategies: list[str], mode: str) -> list[Candidate]:
    """Merge candidates across multiple tool buckets by agreement (portfolios 16–30).

    Groups by ticker across the listed source buckets, then keeps tickers that
    enough tools agree on:
      union        → ≥1 tool     consensus_2 → ≥2     consensus_3 → ≥3
      intersection → all listed tools
    The merged candidate carries every contributing tool in `sources`, the max
    ML probability across contributors, and a score summed across them (so broader
    agreement ranks higher). Levels come from the highest-scoring contributor.
    """
    by_ticker: dict[str, dict[str, Candidate]] = {}
    for src in source_strategies:
        for c in candidates_by_source.get(src, []):
            by_ticker.setdefault(c.ticker, {})[src] = c

    need = {
        "intersection": len(source_strategies),
        "consensus_3": 3,
        "consensus_2": 2,
        "union": 1,
    }.get(mode, 1)

    merged: list[Candidate] = []
    for ticker, per_src in by_ticker.items():
        if len(per_src) < need:
            continue
        contributors = sorted(per_src.keys())
        cands = list(per_src.values())
        base = max(cands, key=lambda c: (c.score or 0.0))
        ml_probs = [c.ml_probability for c in cands if c.ml_probability is not None]
        merged.append(Candidate(
            ticker=ticker,
            price=base.price,
            source="+".join(contributors),
            sources=contributors,
            stop=base.stop, target=base.target, atr=base.atr,
            ml_probability=max(ml_probs) if ml_probs else None,
            score=sum((c.score or 0.0) for c in cands),
        ))
    merged.sort(key=lambda c: (c.score or 0.0), reverse=True)
    return merged


def pool_for(cfg: PaperPortfolioConfig, candidates_by_source: dict[str, list[Candidate]]) -> list[Candidate]:
    """The candidate list a portfolio draws from — single bucket or a multi-tool combine."""
    if getattr(cfg, "source_strategies", None):
        return combine_candidates(candidates_by_source, cfg.source_strategies,
                                  getattr(cfg, "combine_mode", "union"))
    return list(candidates_by_source.get(cfg.source_strategy, []))


# ── Settlement ───────────────────────────────────────────────────────────────

def settle_if_new_day(account: PaperPortfolioAccount, now: datetime) -> None:
    """Roll unsettled cash to settled at the start of a new business day."""
    if account.unsettled_cash <= 0:
        return
    last_day = account.equity_snapshots[-1].timestamp[:10] if account.equity_snapshots else None
    if last_day is None or last_day != now.date().isoformat():
        account.settled_cash += account.unsettled_cash
        account.unsettled_cash = 0.0
    account.sync_cash()


# ── Position management (exits) ──────────────────────────────────────────────

def _days_held(pos: PaperPosition, now: datetime) -> float:
    try:
        entry = datetime.fromisoformat(pos.entry_timestamp)
        return (now - entry).total_seconds() / 86400.0
    except Exception:
        return 0.0


def _exit_decision(pos: PaperPosition, price: float, cfg: PaperPortfolioConfig, now: datetime) -> Optional[tuple[str, float]]:
    """Return (reason, fill_price) if the position should close, else None.

    Applies the system's documented edge (survival-to-drift: timeouts win ~98.7%,
    the edge is NOT target-hitting — SYSTEM_AUDIT E-9). Portfolios with a trailing
    stop configured are "let-winners-run": the fixed target does NOT cap them and
    the time-stop does NOT dump a green survivor — the ratcheted trail protects the
    downside while the winner rides. Portfolios without a trail keep the classic
    fixed-target + hard time-stop behaviour (the control group).
    """
    trails = cfg.trailing_stop_atr_mult is not None

    # Hard stop first, filled at the WORSE of the stop level and the observed
    # print. Filling at `pos.stop` when the print is already below it is the BEST
    # case, not the worst: marks are sampled on a 15-minute loop and never
    # overnight, so a name that closes at $10.20 against a $10.00 stop and opens
    # at $8.40 was being booked as a clean $10.00 fill. Gaps are exactly what a
    # stop cannot protect you from, and pretending otherwise understates drawdown
    # on precisely the trades that hurt. The leaderboard built on these fills is
    # what copytrade mirrors into real money.
    if price <= pos.stop:
        return "STOP", min(price, pos.stop)
    # Ratcheted trailing stop — same gap-through treatment as the hard stop.
    if pos.trailing_stop is not None and price <= pos.trailing_stop:
        return "TRAILING_STOP", min(price, pos.trailing_stop)
    # Target: on trailing portfolios, don't cap the winner — let the trail ride it.
    if price >= pos.target and not trails:
        return "TARGET", pos.target
    # Time stop: force-close losers/flat at the limit; green survivors with trail
    # protection keep riding (E-9 — don't liquidate slow winners mid-move).
    if _days_held(pos, now) >= pos.max_hold_days:
        if trails and price > pos.entry_price:
            return None
        return "MAX_HOLD", price
    return None


def _update_trailing(pos: PaperPosition, price: float, cfg: PaperPortfolioConfig, now: datetime) -> None:
    """Ratchet a trailing stop once configured and price makes new highs past breakeven."""
    if cfg.trailing_stop_atr_mult is None:
        return
    pos.peak_price = max(pos.peak_price or pos.entry_price, price)
    # Only trail once past breakeven (protect, never loosen).
    if pos.peak_price <= pos.entry_price:
        return
    atr_proxy = (pos.entry_price - pos.stop)  # distance used at entry ≈ 1 ATR unit
    new_trail = pos.peak_price - atr_proxy * cfg.trailing_stop_atr_mult
    new_trail = max(new_trail, pos.entry_price)  # never below breakeven once trailing
    if pos.trailing_stop is None or new_trail > pos.trailing_stop:
        pos.trailing_stop = round(new_trail, 4)
        pos.trailing_activated_at = now.isoformat()


def close_position(account: PaperPortfolioAccount, pos: PaperPosition, fill_price: float,
                   reason: str, now: datetime) -> PaperTrade:
    """Close a position: credit proceeds to unsettled cash, record the trade."""
    # Sells fill below the reference (see PAPER_SLIPPAGE_BPS).
    fill_price = apply_slippage(fill_price, "sell")
    proceeds = pos.shares * fill_price
    account.unsettled_cash += proceeds
    account.sync_cash()

    realized = pos.shares * (fill_price - pos.entry_price)
    realized_pct = (fill_price - pos.entry_price) / pos.entry_price * 100.0 if pos.entry_price else 0.0

    trade = PaperTrade(
        portfolio_id=account.portfolio_id,
        ticker=pos.ticker,
        side="BUY",
        shares=pos.shares,
        entry_price=pos.entry_price,
        entry_date=pos.entry_date,
        entry_timestamp=pos.entry_timestamp,
        exit_price=round(fill_price, 4),
        exit_date=now.date().isoformat(),
        exit_timestamp=now.isoformat(),
        exit_reason=reason,
        realized_pnl=round(realized, 2),
        realized_pct=round(realized_pct, 4),
        source_strategy=pos.source_strategy,
        ml_probability=pos.ml_probability,
        ml_threshold=pos.ml_threshold,
    )
    account.trades.append(trade)
    account.positions = [p for p in account.positions if p is not pos]
    return trade


def manage_open_positions(account: PaperPortfolioAccount, prices: dict[str, float],
                          cfg: PaperPortfolioConfig, now: datetime) -> list[PaperTrade]:
    """Mark-to-market every open position and close any that hit an exit rule."""
    closed: list[PaperTrade] = []
    for pos in list(account.positions):
        price = prices.get(pos.ticker)
        if price is None or not math.isfinite(price) or price <= 0:
            continue  # no fresh quote → hold
        pos.current_price = round(price, 4)
        pos.unrealized_pnl = round(pos.shares * (price - pos.entry_price), 2)
        pos.unrealized_pct = round((price - pos.entry_price) / pos.entry_price * 100.0, 4) if pos.entry_price else 0.0

        _update_trailing(pos, price, cfg, now)
        decision = _exit_decision(pos, price, cfg, now)
        if decision is not None:
            reason, fill = decision
            closed.append(close_position(account, pos, fill, reason, now))
    return closed


# ── Entries ──────────────────────────────────────────────────────────────────

def open_new_positions(account: PaperPortfolioAccount, candidates: list[Candidate],
                       cfg: PaperPortfolioConfig, now: datetime,
                       compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
                       skip_day: bool = False) -> list[PaperPosition]:
    """Gate candidates through ML threshold + compliance, size, and open positions.

    Every rejection is logged to the account's compliance_log with a clear reason.

    skip_day=True → this scan's signal bar is a Mon/Thu the portfolio would
    normally sit out but is trading anyway. Apply the smart skip-day policy so
    it doesn't eat the day-of-week tax: a higher ML bar (+SKIP_DAY_ML_BUMP), a
    minimum reward:risk (SKIP_DAY_MIN_RR), reduced size (×SKIP_DAY_SIZE_FACTOR),
    and a capped hold (SKIP_DAY_MAX_HOLD). Exits are unaffected.
    """
    opened: list[PaperPosition] = []

    # Effective ML bar / risk / hold under the skip-day policy.
    ml_threshold = cfg.ml_probability_threshold
    if skip_day and ml_threshold is not None:
        ml_threshold = min(0.99, ml_threshold + SKIP_DAY_ML_BUMP)
    risk_factor = SKIP_DAY_SIZE_FACTOR if skip_day else 1.0
    max_hold = min(cfg.max_hold_days, SKIP_DAY_MAX_HOLD) if skip_day else cfg.max_hold_days

    for cand in candidates:
        ticker = cand.ticker
        entry = cand.price

        if entry is None or not math.isfinite(entry) or entry <= 0:
            log_compliance_event(account, ticker, "SKIPPED", "INVALID_PRICE", {"price": entry})
            continue
        # Buys fill above the screen price (see PAPER_SLIPPAGE_BPS). Applied
        # before sizing so share count reflects the real cost, not the quote.
        entry = apply_slippage(entry, "buy")

        # ML gate — ML portfolios must clear their probability threshold
        # (raised by SKIP_DAY_ML_BUMP when trading a taxed weekday).
        if ml_threshold is not None:
            if cand.ml_probability is None:
                log_compliance_event(account, ticker, "SKIPPED", "ML_THRESHOLD_FAILED",
                                     {"reason": "no ml_probability on candidate",
                                      "threshold": ml_threshold})
                continue
            if cand.ml_probability < ml_threshold:
                log_compliance_event(account, ticker, "SKIPPED", "ML_THRESHOLD_FAILED",
                                     {"ml_probability": cand.ml_probability,
                                      "threshold": ml_threshold, "skip_day": skip_day})
                continue

        stop, target = resolve_levels(cand, cfg)

        # Skip-day quality gate: only strong-payoff setups clear on a taxed day.
        if skip_day:
            rr = (target - entry) / (entry - stop) if entry > stop else 0.0
            if rr < SKIP_DAY_MIN_RR:
                log_compliance_event(account, ticker, "SKIPPED", "SKIP_DAY_LOW_RR",
                                     {"reward_risk": round(rr, 2), "min": SKIP_DAY_MIN_RR})
                continue

        shares = size_shares(account, cfg, entry, stop, risk_factor=risk_factor)
        if shares <= 0:
            log_compliance_event(account, ticker, "SKIPPED", "RISK_LIMIT_FAILED",
                                 {"settled_cash": round(account.settled_cash, 2), "entry": entry,
                                  "risk_factor": risk_factor})
            continue

        allowed, reason = can_enter_trade(account, ticker, "BUY", shares, entry, compliance)
        if not allowed:
            code = reason.split(" ")[0]
            log_compliance_event(account, ticker, "SKIPPED", code,
                                 {"detail": reason, "settled_cash": round(account.settled_cash, 2),
                                  "unsettled_cash": round(account.unsettled_cash, 2)})
            continue

        # Open — spend settled cash only.
        prov = _provenance(cand)
        cost = shares * entry
        account.settled_cash -= cost
        account.sync_cash()

        entry_reason = (
            f"source={cand.source}"
            + (f", ml={cand.ml_probability:.2f}>={ml_threshold:.2f}"
               if prov["used_ml"] and cand.ml_probability is not None and ml_threshold is not None else "")
            + (", unified_brain scored" if prov["used_unified_brain"] else "")
            + (", ai_commentary" if prov["used_ai"] else "")
            + (f", skip-day(rr>={SKIP_DAY_MIN_RR:g},x{SKIP_DAY_SIZE_FACTOR:g},hold<={SKIP_DAY_MAX_HOLD})"
               if skip_day else "")
        )

        pos = PaperPosition(
            portfolio_id=account.portfolio_id,
            ticker=ticker,
            shares=shares,
            entry_price=round(entry, 4),
            entry_date=now.date().isoformat(),
            entry_timestamp=now.isoformat(),
            stop=round(stop, 4),
            target=round(target, 4),
            max_hold_days=max_hold,
            source_strategy=cand.source,
            ml_probability=cand.ml_probability,
            ml_threshold=ml_threshold,
            current_price=round(entry, 4),
            peak_price=round(entry, 4),
            entry_reason=entry_reason,
            **prov,
        )
        account.positions.append(pos)
        opened.append(pos)

    return opened


# ── Full cycle ───────────────────────────────────────────────────────────────

def run_portfolio(account: PaperPortfolioAccount, candidates: list[Candidate],
                  prices: dict[str, float], now: Optional[datetime] = None,
                  compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
                  skip_day: bool = False) -> dict:
    """Run one full cycle for one account: settle → manage → open → snapshot.

    skip_day=True applies the smart skip-day entry policy (see open_new_positions).
    Returns a small summary dict. Does NOT persist — caller decides when to save.
    """
    account.assert_paper_only()  # hard guard: never live
    now = now or datetime.now(ET)

    settle_if_new_day(account, now)
    closed = manage_open_positions(account, prices, account.config, now)
    # Refresh MTM for still-open positions so equity/snapshot are current.
    for pos in account.positions:
        p = prices.get(pos.ticker)
        if p and math.isfinite(p) and p > 0:
            pos.current_price = round(p, 4)
            pos.unrealized_pnl = round(pos.shares * (p - pos.entry_price), 2)
    opened = open_new_positions(account, candidates, account.config, now, compliance, skip_day=skip_day)
    account.record_snapshot()

    return {
        "portfolio_id": account.portfolio_id,
        "opened": len(opened),
        "closed": len(closed),
        "open_positions": account.open_position_count(),
        "equity": round(account.current_equity(), 2),
        "all_time_ror": round(account.all_time_ror(), 4),
    }


# ── Calendar sit-out (per-portfolio) ─────────────────────────────────────────
# Weekdays the screen sits out by default (paper_trade_today `_score_ticker`),
# keyed off the SIGNAL BAR: Monday=0, Thursday=3. A portfolio with
# cfg.trade_skip_days=True trades INTO these; the rest sit them out. This gates
# ENTRIES only — exits/position-management always run (handled in run_portfolio).
SIT_OUT_DOWS = frozenset({0, 3})  # Mon, Thu


def sits_out_entries(cfg: PaperPortfolioConfig, signal_bar_dow: Optional[int],
                     force_skip_monday: Optional[bool] = None,
                     force_skip_thursday: Optional[bool] = None) -> bool:
    """Whether `cfg` should open NO new entries this scan.

    signal_bar_dow — weekday (0=Mon..6=Sun) of the signal bar (last completed
      daily bar before the scan). None → unknown → never sit out.
    force_skip_* — global override (from the provider CLI): True forces every
      portfolio to sit out that weekday, False forces every portfolio to trade
      it; None (default) defers to the per-portfolio cfg.trade_skip_days A/B.
    """
    if signal_bar_dow is None or signal_bar_dow not in SIT_OUT_DOWS:
        return False
    force = force_skip_monday if signal_bar_dow == 0 else force_skip_thursday
    if force is not None:
        return force
    return not cfg.trade_skip_days


def run_all(base_path, candidates_by_source: dict[str, list[Candidate]],
            prices: dict[str, float], now: Optional[datetime] = None,
            snapshots_base=None, csv_base=None,
            compliance: PaperComplianceConfig = DEFAULT_COMPLIANCE,
            signal_bar_dow: Optional[int] = None,
            force_skip_monday: Optional[bool] = None,
            force_skip_thursday: Optional[bool] = None) -> list[dict]:
    """Run every registered portfolio, persisting state, snapshots and CSV.

    `candidates_by_source` maps a source_strategy name to its candidate list; each
    portfolio pulls the bucket matching its config.source_strategy.

    On a Mon/Thu signal bar (`signal_bar_dow` 0/3), portfolios that sit the day
    out (see `sits_out_entries`) get an EMPTY candidate list — they still settle
    cash and manage/exit open positions, they just open nothing new.
    """
    from pathlib import Path
    from tradingagents.portfolio.paper_configs import all_portfolios

    base_path = Path(base_path)
    now = now or datetime.now(ET)
    summaries: list[dict] = []

    on_sit_out_weekday = signal_bar_dow in SIT_OUT_DOWS
    for cfg in all_portfolios():
        account = PaperPortfolioAccount.load(cfg.portfolio_id, base_path)
        sat_out = sits_out_entries(cfg, signal_bar_dow, force_skip_monday, force_skip_thursday)
        cands = [] if sat_out else pool_for(cfg, candidates_by_source)
        # Trading a Mon/Thu the portfolio would otherwise sit out → smart policy.
        skip_day = on_sit_out_weekday and not sat_out
        summary = run_portfolio(account, cands, prices, now, compliance, skip_day=skip_day)
        summary["sat_out"] = sat_out
        summary["skip_day_policy"] = skip_day
        account.save(base_path)
        if snapshots_base is not None:
            account.write_daily_file(Path(snapshots_base))
        if csv_base is not None:
            account.export_csv(Path(csv_base))
        summaries.append(summary)

    return summaries
