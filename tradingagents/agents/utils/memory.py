"""Append-only markdown decision log for TradingAgents."""

from typing import List, Optional
from pathlib import Path
from datetime import datetime
import json
import re

from tradingagents.agents.utils.rating import parse_rating


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        memory_dir = Path(cfg.get("structured_memory_dir") or (self._log_path.parent if self._log_path else "~/.tradingagents/memory")).expanduser()
        memory_dir.mkdir(parents=True, exist_ok=True)
        self._decisions_path = memory_dir / "decisions.json"
        self._outcomes_path = memory_dir / "outcomes.json"
        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        rating = parse_rating(final_trade_decision)
        self._store_structured_decision(ticker, trade_date, final_trade_decision, rating)
        if not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> List[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> List[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(self, ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
        """Return formatted past context string for agent prompt injection."""
        entries = [e for e in self.load_entries() if not e.get("pending")]
        performance = self.get_performance_context(ticker)
        if not entries:
            return performance

        same, cross = [], []
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross and not performance:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        if performance:
            parts.append(performance)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures, and appends a REFLECTION section.  Uses
        a temp-file + os.replace() so a crash mid-write never corrupts the log.
        """
        if not self._log_path or not self._log_path.exists():
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        pending_prefix = f"[{trade_date} | {ticker} |"
        raw_pct = f"{raw_return:+.1%}"
        alpha_pct = f"{alpha_return:+.1%}"

        updated = False
        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            if (
                not updated
                and tag_line.startswith(pending_prefix)
                and tag_line.endswith("| pending]")
            ):
                # Parse rating from the existing pending tag
                fields = [f.strip() for f in tag_line[1:-1].split("|")]
                rating = fields[2]
                new_tag = (
                    f"[{trade_date} | {ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {holding_days}d]"
                )
                rest = "\n".join(lines[1:])
                new_blocks.append(
                    f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                )
                self._store_structured_outcome(
                    ticker=ticker,
                    entry_date=trade_date,
                    exit_date=datetime.now().date().isoformat(),
                    raw_return=raw_return,
                    alpha_return=alpha_return,
                    holding_days=holding_days,
                    exit_reason="DEFERRED_REFLECTION",
                )
                updated = True
            else:
                new_blocks.append(block)

        if not updated:
            return

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    def batch_update_with_outcomes(self, updates: List[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            matched = False
            for (trade_date, ticker), upd in list(update_map.items()):
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    new_tag = (
                        f"[{trade_date} | {ticker} | {rating}"
                        f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    self._store_structured_outcome(
                        ticker=ticker,
                        entry_date=trade_date,
                        exit_date=datetime.now().date().isoformat(),
                        raw_return=upd["raw_return"],
                        alpha_return=upd["alpha_return"],
                        holding_days=upd["holding_days"],
                        exit_reason="DEFERRED_REFLECTION",
                    )
                    del update_map[(trade_date, ticker)]
                    matched = True
                    break

            if not matched:
                new_blocks.append(block)

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    # --- Helpers ---

    def _apply_rotation(self, blocks: List[str]) -> List[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: List[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> Optional[dict]:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"

    # --- Structured sidecar memory ---

    def _read_json_db(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {}

    def _write_json_db(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _store_structured_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
        rating: str,
    ) -> None:
        data = self._read_json_db(self._decisions_path)
        ticker = ticker.upper()
        data.setdefault(ticker, [])
        if any(d.get("analysis_date") == trade_date for d in data[ticker]):
            return
        data[ticker].append({
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "analysis_date": trade_date,
            "recommendation": rating,
            "confidence": self._extract_confidence(final_trade_decision),
            "decision_text": final_trade_decision,
        })
        self._write_json_db(self._decisions_path, data)

    def _store_structured_outcome(
        self,
        ticker: str,
        entry_date: str,
        exit_date: str,
        raw_return: float,
        alpha_return: float = 0.0,
        holding_days: int = 0,
        exit_reason: str = "UNKNOWN",
    ) -> None:
        data = self._read_json_db(self._outcomes_path)
        ticker = ticker.upper()
        data.setdefault(ticker, [])
        if any(o.get("entry_date") == entry_date and o.get("exit_reason") == exit_reason for o in data[ticker]):
            return
        data[ticker].append({
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "pnl": float(raw_return) * 100,
            "alpha": float(alpha_return) * 100 if alpha_return is not None else 0.0,
            "holding_days": holding_days,
            "exit_reason": exit_reason,
        })
        self._write_json_db(self._outcomes_path, data)

    def log_outcome(
        self,
        ticker: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        reason: str,
    ) -> None:
        raw_return = ((exit_price - entry_price) / entry_price) if entry_price else 0.0
        self._store_structured_outcome(
            ticker=ticker,
            entry_date=entry_date,
            exit_date=exit_date,
            raw_return=raw_return,
            exit_reason=reason,
        )

    def get_decision_accuracy(self, ticker: str = None) -> dict:
        decisions = self._read_json_db(self._decisions_path)
        outcomes = self._read_json_db(self._outcomes_path)
        tickers = [ticker.upper()] if ticker else sorted(set(decisions) | set(outcomes))

        stats = {}
        for symbol in tickers:
            outs = outcomes.get(symbol, [])
            if not outs:
                continue
            wins = [float(o.get("pnl", 0.0)) for o in outs if float(o.get("pnl", 0.0)) > 0]
            losses = [float(o.get("pnl", 0.0)) for o in outs if float(o.get("pnl", 0.0)) < 0]
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            stats[symbol] = {
                "total_trades": len(outs),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": len(wins) / len(outs) if outs else 0.0,
                "avg_win": gross_win / max(len(wins), 1),
                "avg_loss": sum(losses) / max(len(losses), 1),
                "profit_factor": gross_win / gross_loss if gross_loss else (float("inf") if gross_win else 0.0),
            }
        return stats

    def get_performance_context(self, ticker: str) -> str:
        stats = self.get_decision_accuracy(ticker)
        symbol = ticker.upper()
        if symbol not in stats:
            return ""
        s = stats[symbol]
        profit_factor = s["profit_factor"]
        pf_text = "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}x"
        return (
            f"Past structured performance for {symbol}: "
            f"{s['total_trades']} outcomes, win rate {s['win_rate']:.1%}, "
            f"avg win +{s['avg_win']:.2f}%, avg loss {s['avg_loss']:.2f}%, "
            f"profit factor {pf_text}."
        )

    @staticmethod
    def _extract_confidence(text: str) -> float:
        match = re.search(r"confidence[^0-9]*(\d+(?:\.\d+)?)\s*%?", text or "", re.I)
        if not match:
            return 0.5
        value = float(match.group(1))
        return value / 100 if value > 1 else value
