"""Replay and tune thematic scanner signals from saved score history.

This module is intentionally pure: callers inject historical prices. The live app
can keep writing score-history JSONL, and this evaluator can later tune thresholds
without introducing lookahead into the scanner.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class SignalEvent:
    ts: str
    ticker: str
    score: float
    breakdown: dict[str, float]


def load_score_history_lines(lines: Iterable[str]) -> list[SignalEvent]:
    events: list[SignalEvent] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts = str(row.get("ts", ""))
        breakdown = row.get("breakdown") if isinstance(row.get("breakdown"), dict) else {}
        for item in row.get("ranked", []) or []:
            try:
                ticker, score = item[0], float(item[1])
            except Exception:
                continue
            if math.isfinite(score) and ticker:
                bd = breakdown.get(str(ticker).upper(), {})
                events.append(SignalEvent(ts=ts, ticker=str(ticker).upper(), score=score, breakdown=bd if isinstance(bd, dict) else {}))
    return events


def forward_return(closes: Sequence[float], entry_index: int, horizon: int) -> float | None:
    try:
        i = int(entry_index)
        h = int(horizon)
    except Exception:
        return None
    if i < 0 or h <= 0 or i + h >= len(closes):
        return None
    try:
        entry = float(closes[i])
        exit_px = float(closes[i + h])
    except Exception:
        return None
    if not (math.isfinite(entry) and math.isfinite(exit_px)) or entry <= 0:
        return None
    return exit_px / entry - 1.0


def evaluate_thresholds(
    events: Sequence[SignalEvent],
    price_history: Mapping[str, Sequence[float]],
    event_index: Mapping[tuple[str, str], int],
    *,
    thresholds: Sequence[float] = (40, 50, 60, 70, 80),
    horizon: int = 5,
) -> list[dict]:
    """Evaluate score cutoffs on point-in-time event indices.

    `event_index[(event.ts, event.ticker)]` must point at the close available at
    signal time. That explicit mapping prevents accidental future alignment.
    """
    rows: list[dict] = []
    for th in thresholds:
        returns: list[float] = []
        for ev in events:
            if ev.score < th:
                continue
            idx = event_index.get((ev.ts, ev.ticker))
            if idx is None:
                continue
            ret = forward_return(price_history.get(ev.ticker, []), idx, horizon)
            if ret is not None:
                returns.append(ret)
        if not returns:
            rows.append({"threshold": th, "n": 0, "mean_return": None, "hit_rate": None})
            continue
        wins = sum(1 for r in returns if r > 0)
        rows.append({
            "threshold": th,
            "n": len(returns),
            "mean_return": round(sum(returns) / len(returns), 6),
            "hit_rate": round(wins / len(returns), 4),
        })
    return rows
