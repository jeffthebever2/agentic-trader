"""Production Safety and Monitoring Layer for TradingAgents.

Consolidates all runtime safety checks into one place:
  - Kill-switch (file-based, no restart required)
  - Model health (age, drift, calibration, validation)
  - Data health (freshness, NaN rate, duplicates, abnormal moves)
  - Account health (drawdown, daily/weekly loss, streak, trade count)
  - Market conditions (VIX, regime — delegates to SafeTradeGuard)
  - Exposure (heat, positions)

Usage in paper_trade_today.py::

    from tradingagents.portfolio.production_safety import ProductionSafetyMonitor
    monitor = ProductionSafetyMonitor(config_path="paper_accounts/alg/safety_config.json")
    report = monitor.check_all(
        account=account,
        prices=prices,
        bundle=bundle,
        candidates=candidates,
        vix_level=vix_level,
        spy_regime=spy_regime,
        regime_state=regime_state,
    )
    if not report.safe_to_trade:
        for r in report.halt_reasons:
            account.log_event({"type": "SAFETY_HALT", "reason": r})
        report.save(output_dir)
        return no_trade_result

Rules:
  - If ANY critical check fails → safe_to_trade = False
  - All failures are logged — none are hidden
  - Does not trade on stale/uncertain data
  - Does NOT increase trade size
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── Default safety config ─────────────────────────────────────────────────────
DEFAULT_SAFETY_CONFIG: Dict[str, Any] = {
    "kill_switch": False,
    "kill_switch_reason": "",
    "max_daily_loss_pct": 2.0,          # halt if today's PnL < -(account × 2%)
    "max_weekly_loss_pct": 5.0,         # halt if week's PnL < -(account × 5%)
    "max_consecutive_losses": 4,        # halt after 4+ consecutive losses
    "max_trades_per_day": 8,            # halt when 8+ new entries opened today
    "min_model_confidence_floor": 0.52, # warn if no candidate exceeds this
    "max_model_age_days": 45,           # halt if model older than this
    "model_age_warn_days": 30,          # warn if model older than this (below halt)
    "max_nan_rate": 0.30,               # halt if NaN rate in features exceeds 30%
    "max_stale_data_hours": 6.0,        # halt if market data older than 6h
    "max_abnormal_move_pct": 0.25,      # warn if any single-day move > 25%
    "crisis_vix": 35.0,
    "elevated_vix": 25.0,
    "max_portfolio_drawdown": -0.12,
    "ml_drift_halt_threshold": 0.20,
    "rolling_wr_floor": 0.30,
    "drift_min_trades": 15,
    "wr_floor_min_trades": 10,
}


# ── SafetyReport ──────────────────────────────────────────────────────────────

@dataclass
class SafetyReport:
    """Output of ProductionSafetyMonitor.check_all()."""
    safe_to_trade: bool
    halt_reasons: List[str]              # critical → blocks all new entries
    warn_reasons: List[str]              # non-critical → log + continue
    gates_active: List[str]              # every gate that triggered (halt + warn)
    model_health: Dict[str, Any] = field(default_factory=dict)
    data_health: Dict[str, Any] = field(default_factory=dict)
    account_health: Dict[str, Any] = field(default_factory=dict)
    exposure: Dict[str, Any] = field(default_factory=dict)
    market: Dict[str, Any] = field(default_factory=dict)
    checked_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safe_to_trade": self.safe_to_trade,
            "halt_reasons": self.halt_reasons,
            "warn_reasons": self.warn_reasons,
            "gates_active": self.gates_active,
            "model_health": self.model_health,
            "data_health": self.data_health,
            "account_health": self.account_health,
            "exposure": self.exposure,
            "market": self.market,
            "checked_at": self.checked_at,
        }

    def save(self, output_dir: str | Path) -> None:
        """Write safety_report.json to output_dir."""
        try:
            p = Path(output_dir) / "safety_report.json"
            p.write_text(json.dumps(self.to_dict(), indent=2))
        except Exception:
            pass

    def summary_str(self) -> str:
        """One-line summary for dashboard."""
        status = "✅ SAFE" if self.safe_to_trade else "🚫 HALT"
        if self.halt_reasons:
            first = self.halt_reasons[0][:60]
            return f"{status} | {first}"
        if self.warn_reasons:
            return f"{status} | {len(self.warn_reasons)} warning(s)"
        return f"{status} | all checks passed"


# ── DataHealthChecker ─────────────────────────────────────────────────────────

class DataHealthChecker:
    """Check market data quality for a batch of OHLCV DataFrames.

    Parameters
    ----------
    max_stale_hours : float
        Maximum acceptable age of last bar vs now.
    max_nan_rate : float
        Maximum NaN rate across key price/volume columns.
    max_abnormal_move_pct : float
        Single-day absolute return above this → flag as abnormal.
    """

    KEY_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

    def __init__(
        self,
        max_stale_hours: float = 6.0,
        max_nan_rate: float = 0.30,
        max_abnormal_move_pct: float = 0.25,
    ):
        self.max_stale_hours = max_stale_hours
        self.max_nan_rate = max_nan_rate
        self.max_abnormal_move_pct = max_abnormal_move_pct

    def check_dataframe(
        self,
        df: Any,  # pd.DataFrame
        ticker: str = "?",
        now: Optional[dt.datetime] = None,
    ) -> Dict[str, Any]:
        """Run all checks on one ticker DataFrame. Returns health dict."""
        result: Dict[str, Any] = {
            "ticker": ticker,
            "ok": True,
            "issues": [],
        }
        if df is None or len(df) == 0:
            result["ok"] = False
            result["issues"].append("empty_dataframe")
            return result

        try:
            import pandas as pd
            import numpy as np

            # ── Freshness ────────────────────────────────────────────────
            last_ts = df.index[-1]
            if hasattr(last_ts, "to_pydatetime"):
                last_ts = last_ts.to_pydatetime()
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=dt.timezone.utc)
            _now = (now or dt.datetime.now(dt.timezone.utc))
            if _now.tzinfo is None:
                _now = _now.replace(tzinfo=dt.timezone.utc)
            freshness_hours = (_now - last_ts).total_seconds() / 3600.0
            result["freshness_hours"] = round(freshness_hours, 2)
            if freshness_hours > self.max_stale_hours:
                result["ok"] = False
                result["issues"].append(
                    f"stale_data: last_bar={str(last_ts.date())} "
                    f"({freshness_hours:.1f}h ago > {self.max_stale_hours}h)"
                )

            # ── NaN rate ─────────────────────────────────────────────────
            cols_present = [c for c in self.KEY_COLUMNS if c in df.columns]
            if cols_present:
                nan_rate = float(df[cols_present].isna().mean().mean())
                result["nan_rate"] = round(nan_rate, 4)
                if nan_rate > self.max_nan_rate:
                    result["ok"] = False
                    result["issues"].append(
                        f"high_nan_rate: {nan_rate:.1%} > {self.max_nan_rate:.1%}"
                    )

            # ── Duplicate rows ───────────────────────────────────────────
            dup_count = int(df.index.duplicated().sum())
            result["duplicate_rows"] = dup_count
            if dup_count > 0:
                result["issues"].append(f"duplicate_rows: {dup_count}")

            # ── Abnormal moves ───────────────────────────────────────────
            if "Close" in df.columns and len(df) >= 2:
                ret = df["Close"].pct_change(fill_method=None).dropna()
                bad = ret[ret.abs() > self.max_abnormal_move_pct]
                if len(bad) > 0:
                    worst = float(bad.abs().max())
                    result["max_daily_move"] = round(worst, 4)
                    result["issues"].append(
                        f"abnormal_move: {worst:.1%} on "
                        f"{str(bad.abs().idxmax().date()) if hasattr(bad.abs().idxmax(), 'date') else bad.abs().idxmax()}"
                    )
                else:
                    result["max_daily_move"] = round(float(ret.abs().max()), 4) if len(ret) else 0.0

            # ── Zero-volume days ─────────────────────────────────────────
            if "Volume" in df.columns:
                zero_vol = int((df["Volume"].fillna(0) == 0).sum())
                result["zero_volume_days"] = zero_vol

        except Exception as exc:
            result["issues"].append(f"check_error: {exc}")

        return result

    def check_batch(
        self,
        data: Dict[str, Any],   # {ticker: (df, pc)} or {ticker: df}
        now: Optional[dt.datetime] = None,
        sample_size: int = 20,
    ) -> Dict[str, Any]:
        """Check a sample of tickers from precomputed dict. Returns summary."""
        # Cycle 44 V-20: deterministic sample (sorted, not random) so the data-health
        # verdict is reproducible across runs instead of intermittently halting.
        tickers = sorted(data.keys())
        if len(tickers) > sample_size:
            tickers = tickers[:sample_size]

        results = []
        stale_count = 0
        high_nan_count = 0
        abnormal_count = 0
        total_nan_rate = 0.0

        for t in tickers:
            val = data[t]
            df = val[0] if isinstance(val, (tuple, list)) else val
            r = self.check_dataframe(df, ticker=t, now=now)
            results.append(r)
            if any("stale_data" in i for i in r.get("issues", [])):
                stale_count += 1
            if any("high_nan_rate" in i for i in r.get("issues", [])):
                high_nan_count += 1
            if any("abnormal_move" in i for i in r.get("issues", [])):
                abnormal_count += 1
            total_nan_rate += r.get("nan_rate", 0.0)

        n = len(results) or 1
        avg_nan = total_nan_rate / n
        return {
            "n_checked": len(results),
            "stale_count": stale_count,
            "high_nan_count": high_nan_count,
            "abnormal_count": abnormal_count,
            "avg_nan_rate": round(avg_nan, 4),
            # Cycle 44 V-20: halt only when a FRACTION of the book is stale (>20%),
            # not on a single stale ticker (commonly one halted/holiday name).
            "critical": stale_count > max(1, int(0.20 * n)) or high_nan_count > n * 0.5,
            "ticker_results": results[:5],  # store first 5 for report
        }


# ── ModelHealthChecker ────────────────────────────────────────────────────────

class ModelHealthChecker:
    """Inspect ML model bundle health without loading it fully.

    Parameters
    ----------
    max_age_days : int
        Model older than this → HALT.
    warn_age_days : int
        Model older than this → WARN.
    max_drift : float
        |pred_wr - actual_wr| above this → HALT.
    """

    def __init__(
        self,
        max_age_days: int = 45,
        warn_age_days: int = 30,
        max_drift: float = 0.20,
    ):
        self.max_age_days = max_age_days
        self.warn_age_days = warn_age_days
        self.max_drift = max_drift

    def check(
        self,
        bundle_path: Optional[str | Path] = None,
        bundle: Optional[Dict] = None,
        drift_log_path: Optional[str | Path] = None,
        validation_summary_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Return model health dict with halt/warn flags."""
        result: Dict[str, Any] = {
            "load_status": "ok",
            "age_days": None,
            "created_at": None,
            "calibration_age_days": None,
            "drift": None,
            "roc_auc": None,
            "brier": None,
            "n_features": None,
            "halt_reasons": [],
            "warn_reasons": [],
        }

        # ── Load or use existing bundle ──────────────────────────────────
        _bundle = bundle
        if _bundle is None and bundle_path is not None:
            try:
                import joblib
                _bundle = joblib.load(bundle_path)
            except Exception as exc:
                result["load_status"] = f"load_failed: {exc}"
                result["halt_reasons"].append(f"model_load_failure: {exc}")
                return result

        if _bundle is None:
            result["load_status"] = "not_loaded"
            return result

        # ── Model age ────────────────────────────────────────────────────
        created_at = _bundle.get("created_at")
        result["created_at"] = created_at
        if created_at:
            try:
                # Cycle 44 V-16: tz-aware compare in UTC and clamp age>=0 so a
                # UTC-stamped or future-dated model can't skew/under-report age.
                created_dt = dt.datetime.fromisoformat(str(created_at)[:19])
                now_dt = dt.datetime.now()
                if created_dt.tzinfo is not None:
                    created_dt = created_dt.astimezone().replace(tzinfo=None)
                age = max(0, (now_dt - created_dt).days)
                result["age_days"] = age
                if age > self.max_age_days:
                    result["halt_reasons"].append(
                        f"model_too_old: {age}d > {self.max_age_days}d (retrain required)"
                    )
                elif age > self.warn_age_days:
                    result["warn_reasons"].append(
                        f"WARN_model_stale: {age}d > {self.warn_age_days}d (retrain recommended)"
                    )
            except Exception as exc:
                # E2-PS6: parse failure → warn, never silently pass as "healthy"
                result["warn_reasons"].append(f"WARN_model_age_parse_error: {exc}")

        # ── Feature count ────────────────────────────────────────────────
        feats = _bundle.get("numeric_features") or _bundle.get("feature_names", [])
        result["n_features"] = len(feats)

        # ── Calibration age ──────────────────────────────────────────────
        cal_date = _bundle.get("calibration_date") or _bundle.get("calibrated_at")
        if cal_date:
            try:
                cal_dt = dt.datetime.fromisoformat(str(cal_date)[:19])
                cal_age = (dt.datetime.now() - cal_dt).days
                result["calibration_age_days"] = cal_age
                cal_stale_days = int(self.cfg.get("max_calibration_age_days", 25)) if hasattr(self, "cfg") else 25
                if cal_age > cal_stale_days:
                    result["warn_reasons"].append(
                        f"WARN_calibration_stale: {cal_age}d > {cal_stale_days}d since last calibration"
                    )
            except Exception as exc:
                result["warn_reasons"].append(f"WARN_calibration_age_parse_error: {exc}")

        # ── Drift log ────────────────────────────────────────────────────
        _drift_path = drift_log_path
        if _drift_path and Path(_drift_path).exists():
            try:
                with open(_drift_path) as f:
                    drift_data = json.load(f)
                drift = drift_data.get("drift")
                if drift is not None:
                    result["drift"] = float(drift)
                    if float(drift) > self.max_drift:
                        # E2-PS6: build halt reason after threshold check; format defensively
                        pred_wr = drift_data.get("predicted_win_rate")
                        act_wr = drift_data.get("actual_win_rate")
                        result["halt_reasons"].append(
                            f"model_drift: {float(drift):.3f} > {self.max_drift} "
                            f"(predicted_wr={pred_wr:.3f if isinstance(pred_wr, float) else '?'}, "
                            f"actual_wr={act_wr:.3f if isinstance(act_wr, float) else '?'})"
                        )
            except Exception as exc:
                result["warn_reasons"].append(f"WARN_drift_log_parse_error: {exc}")

        # ── Validation summary ───────────────────────────────────────────
        _val_path = validation_summary_path
        if _val_path and Path(_val_path).exists():
            try:
                with open(_val_path) as f:
                    val_data = json.load(f)
                roc = val_data.get("roc_auc") or val_data.get("model_roc_auc")
                brier = val_data.get("brier_score") or val_data.get("model_brier")
                result["roc_auc"] = roc
                result["brier"] = brier
                if roc is not None:
                    roc_f = float(roc)
                    if roc_f < 0.45:
                        result["halt_reasons"].append(
                            f"model_roc_broken: roc_auc={roc_f:.3f} < 0.45 (anti-predictive)"
                        )
                    elif roc_f < 0.52:
                        result["warn_reasons"].append(
                            f"WARN_low_roc: roc_auc={roc_f:.3f} < 0.52 (weak discrimination)"
                        )
            except Exception as exc:
                # E2-PS7: parse failure → warn, not silent pass
                result["warn_reasons"].append(f"WARN_validation_summary_parse_error: {exc}")

        return result


# ── ProductionSafetyMonitor ───────────────────────────────────────────────────

class ProductionSafetyMonitor:
    """Consolidated runtime safety monitor.

    Parameters
    ----------
    config_path : str or Path, optional
        Path to safety_config.json. Loaded fresh on every check_all() call
        so kill-switch can be toggled without restarting.
    output_dir : str or Path, optional
        Directory to write safety_report.json.
    """

    def __init__(
        self,
        config_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
    ):
        self.config_path = Path(config_path) if config_path else None
        self.output_dir = Path(output_dir) if output_dir else None

    def _load_config(self) -> Dict[str, Any]:
        """Load safety config, falling back to defaults."""
        cfg = dict(DEFAULT_SAFETY_CONFIG)
        if self.config_path and self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    overrides = json.load(f)
                cfg.update(overrides)
            except Exception:
                pass
        return cfg

    def _write_config_defaults(self) -> None:
        """Write default config if file doesn't exist yet."""
        if self.config_path and not self.config_path.exists():
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.config_path, "w") as f:
                    json.dump(DEFAULT_SAFETY_CONFIG, f, indent=2)
            except Exception:
                pass

    # ── Kill-switch ───────────────────────────────────────────────────────────

    def check_kill_switch(self, cfg: Dict) -> Optional[str]:
        """Return reason string if kill-switch active, else None."""
        if cfg.get("kill_switch", False):
            reason = cfg.get("kill_switch_reason", "kill_switch=true in safety_config.json")
            return f"kill_switch: {reason}"
        return None

    # ── Account health ────────────────────────────────────────────────────────

    def check_account_health(
        self,
        account: Any,
        account_value: float,
        prices: Dict[str, float],
        cfg: Dict,
        now: Optional[dt.datetime] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """Check drawdown, daily loss, weekly loss, streak, trades today."""
        halt: List[str] = []
        warn: List[str] = []
        now = now or dt.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        week_start = (now - dt.timedelta(days=now.weekday())).strftime("%Y-%m-%d")

        trades = getattr(account, "trades", [])
        starting_cash = getattr(account, "starting_cash", account_value) or account_value

        # Open-position mark-to-market (Cycle 44 V-18): loss limits were blind to
        # open losers, so the daily/weekly breaker could be bypassed by holding
        # losing positions open during a selloff. Fold unrealized PnL in.
        open_unrealized = 0.0
        for pos in getattr(account, "positions", {}).values():
            try:
                shares = float(getattr(pos, "shares", 0) or 0)
                entry_px = float(getattr(pos, "entry_price", 0) or 0)
                ticker = getattr(pos, "ticker", "")
                # E2-PS1: on price fetch failure, count MTM as 0 (not entry_price) so
                # the breaker conservatively sees no unrealized gain — never fall back to
                # cost because that zeros out any actual loss and disables the breaker.
                raw_px = prices.get(ticker)
                if raw_px is None:
                    # price unknown — don't assume 0 PnL; leave position out of MTM
                    continue
                px = float(raw_px)
                open_unrealized += shares * (px - entry_px)
            except (TypeError, ValueError):
                continue

        # Daily PnL (realized today + all open MTM, conservative for a breaker)
        today_pnl = sum(
            float(t.get("pnl", 0)) for t in trades
            if str(t.get("exit_time", ""))[:10] == today_str
        ) + open_unrealized

        # Weekly PnL
        weekly_pnl = sum(
            float(t.get("pnl", 0)) for t in trades
            if str(t.get("exit_time", ""))[:10] >= week_start
        ) + open_unrealized

        # Account drawdown from a persisted running high-water mark (Cycle 44 V-17).
        # Previously peak=max(start, current), so any profitable run reset the peak
        # and the drawdown halt under-reported (or never fired) after a pullback.
        peak = max(
            float(getattr(account, "peak_equity", 0.0) or 0.0),
            starting_cash,
            account_value,
        )
        try:
            account.peak_equity = peak  # persist HWM forward (account is saved each run)
        except Exception:
            pass
        drawdown = (account_value - peak) / max(peak, 1.0)

        # Consecutive losses (most recent trades)
        recent_closed = [t for t in trades if "pnl" in t][-20:]
        consec_losses = 0
        for t in reversed(recent_closed):
            if t["pnl"] <= 0:
                consec_losses += 1
            else:
                break

        # Trades opened today
        trades_today = sum(
            1 for t in trades
            if str(t.get("entry_time", ""))[:10] == today_str
        )
        # Also count open positions entered today
        open_today = sum(
            1 for pos in getattr(account, "positions", {}).values()
            if getattr(pos, "entry_date", "") == today_str
        )
        total_entries_today = trades_today + open_today

        health = {
            "daily_pnl": round(today_pnl, 2),
            "weekly_pnl": round(weekly_pnl, 2),
            "drawdown": round(drawdown, 4),
            "consecutive_losses": consec_losses,
            "trades_today": total_entries_today,
            "account_value": round(account_value, 2),
            "starting_cash": round(starting_cash, 2),
        }

        # Daily loss limit
        max_daily_pct = float(cfg.get("max_daily_loss_pct", 2.0))
        daily_limit = starting_cash * max_daily_pct / 100.0
        if today_pnl < -daily_limit:
            halt.append(
                f"daily_loss_limit: today_pnl=${today_pnl:.2f} < "
                f"-${daily_limit:.2f} ({max_daily_pct}% of ${starting_cash:.0f})"
            )

        # Weekly loss limit
        max_weekly_pct = float(cfg.get("max_weekly_loss_pct", 5.0))
        weekly_limit = starting_cash * max_weekly_pct / 100.0
        if weekly_pnl < -weekly_limit:
            halt.append(
                f"weekly_loss_limit: week_pnl=${weekly_pnl:.2f} < "
                f"-${weekly_limit:.2f} ({max_weekly_pct}% of ${starting_cash:.0f})"
            )

        # Consecutive loss shutdown
        max_consec = int(cfg.get("max_consecutive_losses", 4))
        if consec_losses >= max_consec:
            halt.append(
                f"consecutive_loss_shutdown: {consec_losses} consecutive losses >= {max_consec} "
                f"(pause until conditions recover)"
            )

        # Max trades per day
        max_trades_day = int(cfg.get("max_trades_per_day", 8))
        if total_entries_today >= max_trades_day:
            halt.append(
                f"max_trades_per_day: {total_entries_today} entries today >= {max_trades_day}"
            )

        # Portfolio drawdown halt
        max_dd = float(cfg.get("max_portfolio_drawdown", -0.12))
        if drawdown < max_dd:
            halt.append(
                f"portfolio_drawdown: {drawdown:.2%} < {max_dd:.2%} floor "
                f"(account value=${account_value:.0f})"
            )

        return health, halt, warn

    # ── Exposure check ────────────────────────────────────────────────────────

    def check_exposure(
        self,
        account: Any,
        account_value: float,
        prices: Dict[str, float],
        cfg: Dict,
    ) -> Dict[str, Any]:
        """Compute current exposure metrics (informational, not a halt gate)."""
        positions = getattr(account, "positions", {})
        deployed = sum(
            pos.shares * prices.get(pos.ticker, getattr(pos, "entry_price", 0.0))
            for pos in positions.values()
        )
        heat_pct = deployed / max(account_value, 1.0) * 100.0

        sectors: Dict[str, int] = {}
        for pos in positions.values():
            sec = getattr(pos, "sector", "unknown")
            sectors[sec] = sectors.get(sec, 0) + 1

        return {
            "deployed": round(deployed, 2),
            "deployed_pct": round(heat_pct, 2),
            "open_positions": len(positions),
            "sectors": sectors,
        }

    # ── Data health ───────────────────────────────────────────────────────────

    def check_data_health(
        self,
        precomputed: Optional[Dict] = None,
        now: Optional[dt.datetime] = None,
        cfg: Optional[Dict] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """Run data health checks on a sample of precomputed ticker data."""
        halt: List[str] = []
        warn: List[str] = []
        health: Dict[str, Any] = {"checked": False}

        if not precomputed:
            return health, halt, warn

        cfg = cfg or {}
        max_stale = float(cfg.get("max_stale_data_hours", 6.0))
        max_nan = float(cfg.get("max_nan_rate", 0.30))
        max_move = float(cfg.get("max_abnormal_move_pct", 0.25))

        checker = DataHealthChecker(
            max_stale_hours=max_stale,
            max_nan_rate=max_nan,
            max_abnormal_move_pct=max_move,
        )
        batch = checker.check_batch(precomputed, now=now)
        health = {"checked": True, **batch}

        # Cycle 44 V-20: halt only when >20% of the sampled book is stale, not on a
        # single stale ticker (often one halted/holiday name). A single stale ticker
        # should be excluded from candidates, not block the whole run.
        _n_checked = batch.get("n_checked", 1) or 1
        _stale = batch.get("stale_count", 0)
        if _stale > max(1, int(0.20 * _n_checked)):
            halt.append(
                f"data_stale: {_stale}/{_n_checked} tickers (>20%) "
                f"have data older than {max_stale}h"
            )
        elif _stale > 0:
            warn.append(
                f"WARN_data_stale: {_stale}/{_n_checked} stale ticker(s) — excluded, not halting"
            )
        if batch.get("high_nan_count", 0) > batch.get("n_checked", 1) * 0.5:
            halt.append(
                f"high_nan_rate: majority of sampled tickers have NaN rate > {max_nan:.0%}"
            )
        if batch.get("abnormal_count", 0) > 0:
            warn.append(
                f"WARN_abnormal_moves: {batch['abnormal_count']} ticker(s) with "
                f"single-day moves > {max_move:.0%}"
            )

        return health, halt, warn

    # ── Model health ──────────────────────────────────────────────────────────

    def check_model_health(
        self,
        bundle: Optional[Dict] = None,
        bundle_path: Optional[str | Path] = None,
        output_dir: Optional[str | Path] = None,
        cfg: Optional[Dict] = None,
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """Run model health checks."""
        cfg = cfg or {}
        out_dir = output_dir or self.output_dir

        drift_log = None
        val_summary = None
        if out_dir:
            drift_log = Path(out_dir) / "ml_drift.json"
            # Look for validation_summary.json in ml_models/
            val_candidates = [
                Path("ml_models/latest/validation_summary.json"),
                Path("ml_models/stock_universe/validation_summary.json"),
            ]
            for vc in val_candidates:
                if vc.exists():
                    val_summary = vc
                    break

        checker = ModelHealthChecker(
            max_age_days=int(cfg.get("max_model_age_days", 45)),
            warn_age_days=int(cfg.get("model_age_warn_days", 30)),
            max_drift=float(cfg.get("ml_drift_halt_threshold", 0.20)),
        )
        health = checker.check(
            bundle_path=bundle_path,
            bundle=bundle,
            drift_log_path=drift_log,
            validation_summary_path=val_summary,
        )
        return health, health.pop("halt_reasons", []), health.pop("warn_reasons", [])

    # ── Market conditions (delegates to SafeTradeGuard) ───────────────────────

    def check_market_conditions(
        self,
        vix_level: Optional[float],
        spy_regime: str,
        account_drawdown: float,
        recent_trades: Optional[List[Dict]],
        model_created_at: Optional[str],
        regime_state: Optional[Any],
        cfg: Dict,
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """Delegate to SafeTradeGuard + MarketRegimeEngine no_trade."""
        from tradingagents.portfolio.safe_trade_guard import SafeTradeGuard

        guard = SafeTradeGuard(
            crisis_vix=float(cfg.get("crisis_vix", 35.0)),
            elevated_vix=float(cfg.get("elevated_vix", 25.0)),
            max_dd_pct=float(cfg.get("max_portfolio_drawdown", -0.12)),
            drift_threshold=float(cfg.get("ml_drift_halt_threshold", 0.20)),
            drift_min_trades=int(cfg.get("drift_min_trades", 15)),
            wr_floor=float(cfg.get("rolling_wr_floor", 0.30)),
            wr_floor_min_trades=int(cfg.get("wr_floor_min_trades", 10)),
            max_model_age_days=int(cfg.get("max_model_age_days", 45)),
        )
        allow, reasons = guard.check(
            vix_level=vix_level,
            spy_regime=spy_regime,
            account_drawdown=account_drawdown,
            recent_trades=recent_trades,
            model_created_at=model_created_at,
        )

        halt: List[str] = [r for r in reasons if not r.startswith("WARN_")]
        warn: List[str] = [r for r in reasons if r.startswith("WARN_")]

        # MarketRegimeEngine hard no_trade
        if regime_state is not None:
            if getattr(regime_state, "no_trade", False):
                regime_label = str(getattr(regime_state, "regime", "unknown"))
                crash = float(getattr(regime_state, "crash_risk_score", 0.0))
                halt.append(
                    f"regime_no_trade: regime={regime_label} crash_risk={crash:.2f} "
                    f"(MarketRegimeEngine.no_trade=True)"
                )

        market = {
            "vix_level": vix_level,
            "spy_regime": spy_regime,
            "regime": str(getattr(regime_state, "regime", "unknown")) if regime_state else spy_regime,
            "regime_score": float(getattr(regime_state, "regime_score", 0.80)) if regime_state else None,
            "crash_risk_score": float(getattr(regime_state, "crash_risk_score", 0.0)) if regime_state else None,
        }
        return market, halt, warn

    # ── Main: check_all ───────────────────────────────────────────────────────

    def check_all(
        self,
        account: Optional[Any] = None,
        prices: Optional[Dict[str, float]] = None,
        bundle: Optional[Dict] = None,
        bundle_path: Optional[str | Path] = None,
        precomputed: Optional[Dict] = None,
        candidates: Optional[List] = None,
        vix_level: Optional[float] = None,
        spy_regime: str = "unknown",
        regime_state: Optional[Any] = None,
        output_dir: Optional[str | Path] = None,
        now: Optional[dt.datetime] = None,
        # DL-5: grades_path for DriftDetector; validation_summary_path for WF-gap check
        grades_path: Optional[str | Path] = None,
        validation_summary_path: Optional[str | Path] = None,
    ) -> SafetyReport:
        """Run all safety checks and return a SafetyReport.

        Call once per scan cycle. Writes safety_report.json to output_dir.
        """
        self._write_config_defaults()
        cfg = self._load_config()
        now = now or dt.datetime.now()
        all_halt: List[str] = []
        all_warn: List[str] = []
        gates_active: List[str] = []

        # ── 1. Kill-switch ────────────────────────────────────────────────
        ks = self.check_kill_switch(cfg)
        if ks:
            all_halt.append(ks)
            gates_active.append("kill_switch")

        # ── 2. Model health ───────────────────────────────────────────────
        model_health, mh_halt, mh_warn = self.check_model_health(
            bundle=bundle, bundle_path=bundle_path, output_dir=output_dir, cfg=cfg
        )
        all_halt.extend(mh_halt)
        all_warn.extend(mh_warn)
        gates_active.extend(["model_" + r.split(":")[0] for r in mh_halt + mh_warn])

        # ── 2b. DriftDetector (DL-5) ─────────────────────────────────────
        # Previously check_all only read a scalar drift field — the most informative
        # degradation signal (calibration drift, high-conf failure, paper-vs-WF gap)
        # could never halt. Wire the full DriftDetector here.
        if grades_path is not None:
            try:
                from tradingagents.portfolio.drift_detector import DriftDetector
                from tradingagents.portfolio.prediction_grader import PredictionGrader
                _grader = PredictionGrader(account_dir=str(grades_path))
                _grades = _grader.load_saved()
                if _grades:
                    _detector = DriftDetector()
                    _val_path = validation_summary_path or (
                        Path(output_dir) / "validation_summary.json" if output_dir else None
                    )
                    _drift_path = (
                        Path(output_dir) / "ml_drift.json" if output_dir else None
                    )
                    _drift_report = _detector.check(
                        grades=_grades,
                        validation_summary_path=_val_path,
                        drift_log_path=_drift_path,
                    )
                    if _drift_report.has_drift:
                        for alert in _drift_report.alerts:
                            all_warn.append(f"WARN_drift: {alert}")
                        gates_active.append("drift_detector")
                    # RS-1 monotonicity check (GC-7)
                    try:
                        from tradingagents.portfolio.reliability_stats import ReliabilityStats
                        _rs2 = ReliabilityStats()
                        for ma in _rs2.alert_monotonicity(_grades):
                            all_warn.append(f"WARN_monotonicity: {ma}")
                        if any("MONOTONICITY" in w for w in all_warn):
                            gates_active.append("monotonicity")
                    except Exception as _me:
                        all_warn.append(f"WARN_monotonicity_error: {_me}")
            except Exception as _de:
                all_warn.append(f"WARN_drift_detector_error: {_de}")

        # ── 3. Data health ────────────────────────────────────────────────
        data_health: Dict[str, Any] = {"checked": False}
        if precomputed:
            data_health, dh_halt, dh_warn = self.check_data_health(
                precomputed=precomputed, now=now, cfg=cfg
            )
            all_halt.extend(dh_halt)
            all_warn.extend(dh_warn)
            gates_active.extend(["data_" + r.split(":")[0] for r in dh_halt + dh_warn])

        # ── 4. Account health ─────────────────────────────────────────────
        account_health: Dict[str, Any] = {}
        exposure: Dict[str, Any] = {}
        if account is not None:
            account_value = account.total_value(prices or {}) if hasattr(account, "total_value") else 0.0
            account_health, ah_halt, ah_warn = self.check_account_health(
                account=account, account_value=account_value,
                prices=prices or {}, cfg=cfg, now=now
            )
            all_halt.extend(ah_halt)
            all_warn.extend(ah_warn)
            gates_active.extend([r.split(":")[0] for r in ah_halt + ah_warn])
            exposure = self.check_exposure(account, account_value, prices or {}, cfg)

        # ── 5. Market conditions ──────────────────────────────────────────
        recent_trades = list(getattr(account, "trades", []))[-20:] if account else []
        model_created_at = (bundle or {}).get("created_at") if bundle else None
        account_drawdown = account_health.get("drawdown", 0.0)
        market, mc_halt, mc_warn = self.check_market_conditions(
            vix_level=vix_level,
            spy_regime=spy_regime,
            account_drawdown=account_drawdown,
            recent_trades=recent_trades,
            model_created_at=model_created_at,
            regime_state=regime_state,
            cfg=cfg,
        )
        all_halt.extend(mc_halt)
        all_warn.extend(mc_warn)
        gates_active.extend([r.split(":")[0] for r in mc_halt + mc_warn])

        # ── 5b. REGIME_COLLAPSE via ReliabilityStats (DL-9) ─────────────────
        # reliability_stats computes per-regime WR but the alerts were never fed to check_all.
        # A regime where the model loses 65%+ of the time should at minimum warn.
        if account is not None and grades_path is not None:
            try:
                from tradingagents.portfolio.reliability_stats import ReliabilityStats
                _rs = ReliabilityStats()
                _rt = list(getattr(account, "trades", []))
                if _rt:
                    _sr = _rs.compute(trades=_rt)
                    _regime_alerts = _rs.alert_regimes(_sr)
                    for ra in _regime_alerts:
                        all_warn.append(f"WARN_regime_collapse: {ra}")
                    if _regime_alerts:
                        gates_active.append("regime_collapse")
            except Exception as _rse:
                all_warn.append(f"WARN_reliability_stats_error: {_rse}")

        # ── 6. Candidate confidence floor (Cycle 44 V-21) ─────────────────
        # Previously `candidates` was accepted but ignored. Warn (config intent)
        # when no candidate clears the model-confidence floor — a low-quality slate.
        if candidates:
            floor = float(cfg.get("min_model_confidence_floor", 0.52))
            best_conf = 0.0
            for c in candidates:
                try:
                    best_conf = max(best_conf, float(getattr(c, "ml_probability", 0.0) or 0.0))
                except (TypeError, ValueError):
                    continue
            if best_conf < floor:
                all_warn.append(
                    f"WARN_low_slate_confidence: best candidate ml_prob={best_conf:.3f} "
                    f"< floor {floor} (no high-confidence setups this cycle)"
                )
                gates_active.append("low_slate_confidence")

        # Deduplicate gates_active
        gates_active = list(dict.fromkeys(g for g in gates_active if g))

        safe = len(all_halt) == 0

        report = SafetyReport(
            safe_to_trade=safe,
            halt_reasons=all_halt,
            warn_reasons=all_warn,
            gates_active=gates_active,
            model_health=model_health,
            data_health=data_health,
            account_health=account_health,
            exposure=exposure,
            market=market,
            checked_at=now.isoformat(),
        )

        # Write report to disk
        out = output_dir or self.output_dir
        if out:
            report.save(out)

        return report


# ── Convenience: write default kill-switch config ─────────────────────────────

def ensure_safety_config(output_dir: str | Path) -> Path:
    """Create safety_config.json with defaults if it doesn't exist.

    Returns the path.
    """
    p = Path(output_dir) / "safety_config.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(DEFAULT_SAFETY_CONFIG, f, indent=2)
    return p
