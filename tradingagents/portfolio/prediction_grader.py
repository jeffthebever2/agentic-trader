"""Prediction grader: compare ML predictions at entry vs actual trade outcomes.

For each closed paper trade, joins the BUY event (which carries ml_probability,
expected_return, large_loss_probability, alpha_tier, breakout_score, regime) with
the SELL event (pnl_pct, stop_hit, target_hit, max_drawdown_pct) and produces a
structured GradeResult.

Usage::

    grader = PredictionGrader(account_dir="paper_accounts/algorithm")
    grades = grader.grade_all()
    grader.save(grades)          # → paper_accounts/algorithm/prediction_grades.jsonl
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── GradeResult ───────────────────────────────────────────────────────────────

@dataclass
class GradeResult:
    """Comparison of ML prediction vs actual trade outcome."""

    ticker: str
    trade_id: str                 # "{ticker}_{entry_time[:10]}"
    graded_at: str

    # ── Prediction inputs (from BUY event) ───────────────────────────────────
    predicted_win_prob: float
    predicted_return: float
    predicted_ll_prob: float      # large_loss_probability
    alpha_tier: str               # A+, A, B, C
    alpha_score: float
    breakout_score: float
    regime_at_entry: str
    model_version: str

    # ── Actual outcomes (from SELL event or position data) ──────────────────
    actual_win: bool
    actual_return: float          # (exit - entry) / entry
    actual_max_drawdown: float    # pct below entry (positive = down)
    stop_hit: bool
    target_hit: bool
    hold_days: float
    regime_at_exit: str

    # ── Derived accuracy fields ──────────────────────────────────────────────
    win_prediction_correct: bool  # (predicted_win_prob >= 0.60) == actual_win
    return_error: float           # predicted_return - actual_return
    ll_prediction_correct: bool   # if predicted_ll_prob >= 0.30, did large loss occur?

    # ── Bucketing ────────────────────────────────────────────────────────────
    confidence_bucket: str        # "low" <0.60, "mid" 0.60–0.70, "high" >=0.70
    return_bucket: str            # "loss" <0, "small_gain" 0–2%, "gain" 2%+

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── PredictionGrader ──────────────────────────────────────────────────────────

class PredictionGrader:
    """Joins BUY + SELL events from a paper account's event log and produces GradeResults.

    Parameters
    ----------
    account_dir : str or Path
        Root of the strategy's paper account directory, e.g.
        ``paper_accounts/algorithm``. Looks for event logs under
        ``{account_dir}/**/event_log.jsonl``.
    grades_path : str or Path, optional
        Output path for prediction_grades.jsonl. Defaults to
        ``{account_dir}/prediction_grades.jsonl``.
    win_prob_threshold : float
        Threshold above which a trade is predicted to win (for accuracy).
        Should match the model's operating threshold (default 0.60).
    ll_threshold : float
        large_loss_probability above this → large loss predicted (default 0.30).
    large_loss_drawdown_pct : float
        Actual drawdown below this defines a "large loss" event (default 0.08 = 8%).
    """

    def __init__(
        self,
        account_dir: str | Path,
        grades_path: Optional[str | Path] = None,
        win_prob_threshold: float = 0.60,
        ll_threshold: float = 0.30,
        large_loss_drawdown_pct: float = 0.08,
    ):
        self.account_dir = Path(account_dir)
        self.grades_path = Path(grades_path) if grades_path else self.account_dir / "prediction_grades.jsonl"
        self.win_prob_threshold = win_prob_threshold
        self.ll_threshold = ll_threshold
        self.large_loss_drawdown_pct = large_loss_drawdown_pct

    # ── Load events ──────────────────────────────────────────────────────────

    def _load_events(self) -> List[Dict[str, Any]]:
        """Load all events from all event_log.jsonl files under account_dir."""
        events = []
        # Search all day subdirectories for event logs
        for event_file in sorted(self.account_dir.rglob("event_log.jsonl")):
            try:
                with open(event_file) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass
        return events

    # ── Build BUY / SELL maps ─────────────────────────────────────────────────

    def _extract_buy_sell(self, events: List[Dict]) -> tuple[Dict, List[Dict]]:
        """Return (buy_map, sell_list).

        buy_map: {(ticker, entry_time_str): event_dict}
        sell_list: list of SELL event dicts
        """
        buy_map: Dict[tuple, Dict] = {}
        sell_list: List[Dict] = []

        for ev in events:
            etype = ev.get("type", "")
            if etype == "BUY":
                ticker = ev.get("ticker", "")
                etime = str(ev.get("timestamp", ev.get("entry_time", "")))
                if ticker and etime:
                    buy_map[(ticker, etime)] = ev
            elif etype == "SELL":
                sell_list.append(ev)

        return buy_map, sell_list

    # ── Grade one trade ───────────────────────────────────────────────────────

    def _grade_one(
        self,
        sell_ev: Dict,
        buy_ev: Dict,
    ) -> Optional[GradeResult]:
        """Produce a GradeResult from a matched BUY + SELL pair."""
        try:
            ticker = str(sell_ev.get("ticker", buy_ev.get("ticker", "?")))

            # ── Prediction fields from BUY (Cycle 44 GC-4: fall back to SELL ──
            # event, which carries large_loss_probability / alpha_tier /
            # regime_at_entry / model_version; the BUY payload often omits them, so
            # grading on BUY-only collapsed every trade to tier "C" / "unknown").
            def _pred(key, *aliases, default=None):
                for k in (key, *aliases):
                    if k in buy_ev and buy_ev[k] is not None:
                        return buy_ev[k]
                for k in (key, *aliases):
                    if k in sell_ev and sell_ev[k] is not None:
                        return sell_ev[k]
                return default
            pred_wp = float(_pred("ml_probability", "ml_prob", default=0.50) or 0.50)
            pred_ret = float(_pred("expected_return", default=0.0) or 0.0)
            pred_ll = float(_pred("large_loss_probability", "large_loss_prob", default=0.0) or 0.0)
            alpha_tier = str(_pred("alpha_tier", "tier", default="C"))
            alpha_score = float(_pred("alpha_score", default=0.0) or 0.0)
            breakout_score = float(_pred("breakout_score", default=0.0) or 0.0)
            regime_entry = str(_pred("regime_at_entry", "spy_regime", default="unknown"))
            model_version = str(_pred("model_version", default="unknown"))
            entry_time = str(buy_ev.get("timestamp", buy_ev.get("entry_time", "")))

            # ── Actual outcome fields from SELL ───────────────────────────
            # Cycle 44 GC-5: emitters write pnl_pct as a fraction (e.g. 0.031). The
            # old `if abs>2.0: /100` heuristic would silently divide a genuine +250%
            # winner (2.5) down to 0.025 — corrupting exactly the trades that matter
            # most. Trust the fraction at source; no magnitude guess.
            pnl_pct = float(sell_ev.get("pnl_pct", sell_ev.get("return_pct", 0.0)) or 0.0)
            # PG-3: use 0.5% deadband matching the model's trained win definition (_return>0.005)
            # Previously used pnl>0 vs the model's trained 0.5% threshold → calibration drift
            # was measured against a different win definition than the model learned.
            actual_win = pnl_pct > 0.005
            actual_ret = pnl_pct

            # Max drawdown (how far price fell below entry while held)
            # PG-1: if MAE is absent, leave as None — don't default to 0 which fakes "no large loss"
            _mae_raw = sell_ev.get("max_drawdown_pct", sell_ev.get("max_adverse_pct"))
            if _mae_raw is not None:
                max_dd_pct = float(_mae_raw or 0.0)
                if max_dd_pct > 0:
                    max_dd_pct = -max_dd_pct  # normalise to negative
            else:
                max_dd_pct = None  # type: ignore[assignment]  # surfaced as None below

            # Cycle 44 GC-2: normalize exit_reason (emitters use STOP_LOSS /
            # TAKE_PROFIT, the grader matched lowercase "stop"/"target") so
            # stop_rate / target_rate stop collapsing to zero.
            _er = str(sell_ev.get("exit_reason", "")).upper()
            stop_hit = bool(sell_ev.get("stop_hit", "STOP" in _er))
            target_hit = bool(sell_ev.get("target_hit", ("TARGET" in _er or "TAKE_PROFIT" in _er)))
            regime_exit = str(sell_ev.get("regime_at_exit", sell_ev.get("spy_regime", "unknown")))

            # Hold time
            exit_time_str = str(sell_ev.get("timestamp", sell_ev.get("exit_time", "")))
            hold_days = 0.0
            if entry_time and exit_time_str:
                try:
                    t0 = dt.datetime.fromisoformat(entry_time[:19])
                    t1 = dt.datetime.fromisoformat(exit_time_str[:19])
                    hold_days = round((t1 - t0).total_seconds() / 86400.0, 2)
                except Exception:
                    pass

            # ── Derived accuracy ──────────────────────────────────────────
            win_pred_correct = (pred_wp >= self.win_prob_threshold) == actual_win
            return_error = round(pred_ret - actual_ret, 6)
            # PG-1: MAE absent → actual_large_loss unknown, exclude from accuracy
            if max_dd_pct is None:
                actual_large_loss = False  # placeholder; ll_pred_correct excluded below
                ll_pred_correct = None  # type: ignore[assignment]
            else:
                actual_large_loss = max_dd_pct <= -self.large_loss_drawdown_pct
                ll_pred_correct = ((pred_ll >= self.ll_threshold) == actual_large_loss)

            # ── Buckets ───────────────────────────────────────────────────
            if pred_wp >= 0.70:
                conf_bucket = "high"
            elif pred_wp >= 0.60:
                conf_bucket = "mid"
            else:
                conf_bucket = "low"

            if actual_ret <= 0:
                ret_bucket = "loss"
            elif actual_ret < 0.02:
                ret_bucket = "small_gain"
            else:
                ret_bucket = "gain"

            # Cycle 44 GC-6: include full entry timestamp (to the second), not just
            # the date, so two same-day round-trips of one ticker get distinct ids
            # instead of the second being dropped by the save() dedup.
            trade_id = f"{ticker}_{entry_time[:19]}" if entry_time else f"{ticker}_{exit_time_str[:19]}"
            graded_at = dt.datetime.now().isoformat()

            return GradeResult(
                ticker=ticker,
                trade_id=trade_id,
                graded_at=graded_at,
                predicted_win_prob=round(pred_wp, 4),
                predicted_return=round(pred_ret, 6),
                predicted_ll_prob=round(pred_ll, 4),
                alpha_tier=alpha_tier,
                alpha_score=round(alpha_score, 4),
                breakout_score=round(breakout_score, 2),
                regime_at_entry=regime_entry,
                model_version=model_version,
                actual_win=actual_win,
                actual_return=round(actual_ret, 6),
                actual_max_drawdown=round(max_dd_pct, 6) if max_dd_pct is not None else 0.0,
                stop_hit=stop_hit,
                target_hit=target_hit,
                hold_days=hold_days,
                regime_at_exit=regime_exit,
                win_prediction_correct=win_pred_correct,
                return_error=return_error,
                ll_prediction_correct=ll_pred_correct,
                confidence_bucket=conf_bucket,
                return_bucket=ret_bucket,
            )
        except Exception:
            return None

    # ── Match BUY/SELL by ticker + closest entry_time ─────────────────────────

    def _match_trades(
        self,
        buy_map: Dict[tuple, Dict],
        sell_list: List[Dict],
    ) -> List[tuple[Dict, Dict]]:
        """Return list of (sell_ev, buy_ev) matched pairs."""
        pairs = []
        for sell_ev in sell_list:
            ticker = sell_ev.get("ticker", "")
            entry_time = str(sell_ev.get("entry_time", ""))
            if not ticker or not entry_time:
                continue
            # Try exact match first
            key = (ticker, entry_time)
            buy_ev = buy_map.get(key)
            if buy_ev is None:
                # Fuzzy: find BUY for same ticker with closest timestamp
                candidates = [(k, v) for k, v in buy_map.items() if k[0] == ticker]
                if candidates:
                    # Prefer closest timestamp
                    try:
                        t0 = dt.datetime.fromisoformat(entry_time[:19])
                        best_k, best_v = min(
                            candidates,
                            key=lambda kv: abs(
                                (dt.datetime.fromisoformat(kv[0][1][:19]) - t0).total_seconds()
                            ),
                        )
                        diff_secs = abs(
                            (dt.datetime.fromisoformat(best_k[1][:19]) - t0).total_seconds()
                        )
                        if diff_secs < 300:  # within 5 min
                            buy_ev = best_v
                    except Exception:
                        pass
            if buy_ev is not None:
                pairs.append((sell_ev, buy_ev))
        return pairs

    # ── Public API ────────────────────────────────────────────────────────────

    def grade_all(self) -> List[GradeResult]:
        """Load events, match BUY/SELL pairs, grade all closed trades."""
        events = self._load_events()
        buy_map, sell_list = self._extract_buy_sell(events)
        pairs = self._match_trades(buy_map, sell_list)

        grades = []
        for sell_ev, buy_ev in pairs:
            g = self._grade_one(sell_ev, buy_ev)
            if g is not None:
                grades.append(g)
        return grades

    def grade_recent(self, days: int = 30) -> List[GradeResult]:
        """Grade only trades closed in the last N days."""
        all_grades = self.grade_all()
        cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
        return [g for g in all_grades if g.graded_at >= cutoff]

    def load_saved(self) -> List[GradeResult]:
        """Load previously saved grades from grades_path."""
        results = []
        if not self.grades_path.exists():
            return results
        with open(self.grades_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    results.append(GradeResult(**d))
                except Exception:
                    pass
        return results

    def save(self, grades: List[GradeResult]) -> None:
        """Append new grades to grades_path (skip already-saved trade_ids)."""
        existing_ids = {g.trade_id for g in self.load_saved()}
        new_grades = [g for g in grades if g.trade_id not in existing_ids]

        if not new_grades:
            return
        self.grades_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.grades_path, "a") as f:
            for g in new_grades:
                f.write(json.dumps(g.to_dict()) + "\n")

    def summary(self, grades: List[GradeResult]) -> Dict[str, Any]:
        """Return a compact accuracy summary dict."""
        if not grades:
            return {"n": 0}
        n = len(grades)
        win_rate = sum(1 for g in grades if g.actual_win) / n
        win_pred_acc = sum(1 for g in grades if g.win_prediction_correct) / n
        avg_ret = sum(g.actual_return for g in grades) / n
        avg_ret_err = sum(abs(g.return_error) for g in grades) / n
        # PG-1: exclude None ll_prediction_correct (MAE absent) from accuracy
        ll_gradeable = [g for g in grades if g.ll_prediction_correct is not None]
        ll_pred_acc = sum(1 for g in ll_gradeable if g.ll_prediction_correct) / max(len(ll_gradeable), 1)
        stop_rate = sum(1 for g in grades if g.stop_hit) / n
        target_rate = sum(1 for g in grades if g.target_hit) / n
        return {
            "n": n,
            "win_rate": round(win_rate, 4),
            "win_prediction_accuracy": round(win_pred_acc, 4),
            "avg_return": round(avg_ret, 4),
            "avg_return_error": round(avg_ret_err, 4),
            "ll_prediction_accuracy": round(ll_pred_acc, 4),
            "stop_rate": round(stop_rate, 4),
            "target_rate": round(target_rate, 4),
        }
