"""Pre-trade quote and execution-safety gate.

This module is intentionally provider-aware: historical/fallback feeds such as
yfinance/Yahoo are useful for research and watchlists, but they must not be the
sole evidence for an executable order.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str
    detail: dict


class PreTradeGate:
    """
    Args:
        max_quote_age_seconds: reject if snapshot older than this (default 3s)
        max_spread_bps:        reject if (ask-bid)/mid > this in basis points (default 75)
    """

    # Keep in sync with tradingagents.data.quote_gateway.TRUSTED_SOURCES.
    TRUSTED_SOURCES = frozenset({
        "alpaca",
        "alpaca_iex",
        "broker",
        "fidelity",
        # "fidelity_realtime" removed 2026-07-05: it was only ever produced by the
        # exit path self-stamping a scraped grid price with scrape-time now(),
        # which made the freshness gate a no-op. Execution evidence must come
        # from the quote gateway's trusted providers.
        "finnhub",
        "fmp",            # Financial Modeling Prep — the configured trusted provider
        "ibkr",
        "iex",
        "polygon",
        "sip",
        "twelve_data",
    })
    UNTRUSTED_EXECUTION_SOURCES = frozenset({
        "playwright",
        "scrape",
        "web_scrape",
        "yahoo",
        "yahoo_chart",
        "yahoo_ws",
        "yfinance",
        "yfinance_history",
    })

    def __init__(
        self,
        max_quote_age_seconds: int = 3,
        max_spread_bps: float = 75.0,
        max_price_drift_bps: float | None = None,
        min_risk_reward: float | None = None,
        require_trusted_source: bool = False,
        require_bid_ask: bool = False,
    ) -> None:
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_spread_bps = max_spread_bps
        self.max_price_drift_bps = max_price_drift_bps
        self.min_risk_reward = min_risk_reward
        self.require_trusted_source = require_trusted_source
        self.require_bid_ask = require_bid_ask

    @classmethod
    def _norm_source(cls, source: str | None) -> str:
        return (source or "").strip().lower().replace("-", "_").replace(" ", "_")

    @classmethod
    def _source_is_trusted(cls, source: str | None) -> bool:
        norm = cls._norm_source(source)
        return bool(norm) and norm in cls.TRUSTED_SOURCES

    @classmethod
    def _source_is_untrusted(cls, source: str | None) -> bool:
        norm = cls._norm_source(source)
        return not norm or norm in cls.UNTRUSTED_EXECUTION_SOURCES

    @classmethod
    def _count_trusted_backups(cls, backup_sources: Iterable[str] | None) -> int:
        return sum(1 for src in (backup_sources or []) if cls._source_is_trusted(src))

    def check(
        self,
        ticker: str,
        price_snapshot_time: dt.datetime,
        price: float,
        bid: float | None = None,
        ask: float | None = None,
        now: dt.datetime | None = None,
        quote_source: str | None = None,
        backup_sources: Iterable[str] | None = None,
        consensus_ok: bool | None = None,
        market_open: bool | None = None,
        signal_price: float | None = None,
        stop: float | None = None,
        target: float | None = None,
    ) -> GateResult:
        if now is None:
            now = dt.datetime.now()

        age_seconds = (now - price_snapshot_time).total_seconds()
        if age_seconds < -1:
            return GateResult(
                ok=False,
                reason="invalid_quote_time",
                detail={"ticker": ticker, "quote_age_seconds": round(age_seconds, 1)},
            )
        if age_seconds >= self.max_quote_age_seconds:
            return GateResult(
                ok=False,
                reason="stale_quote",
                detail={
                    "ticker": ticker,
                    "quote_age_seconds": round(age_seconds, 1),
                    "max_quote_age_seconds": self.max_quote_age_seconds,
                },
            )

        if market_open is False:
            return GateResult(
                ok=False,
                reason="market_closed",
                detail={"ticker": ticker},
            )

        if price <= 0:
            return GateResult(
                ok=False,
                reason="no_data",
                detail={"ticker": ticker, "price": price},
            )

        if self.require_trusted_source:
            trusted_primary = self._source_is_trusted(quote_source)
            trusted_backups = self._count_trusted_backups(backup_sources)
            backup_consensus = consensus_ok is True and trusted_backups >= 2
            if not trusted_primary and not backup_consensus:
                reason = "provider_untrusted" if self._source_is_untrusted(quote_source) else "no_consensus"
                return GateResult(
                    ok=False,
                    reason=reason,
                    detail={
                        "ticker": ticker,
                        "quote_source": quote_source or "",
                        "trusted_backup_sources": trusted_backups,
                        "consensus_ok": bool(consensus_ok),
                    },
                )

        if self.require_bid_ask and not (bid is not None and ask is not None and bid > 0 and ask >= bid):
            return GateResult(
                ok=False,
                reason="no_data",
                detail={"ticker": ticker, "missing": "bid_ask"},
            )

        spread_bps = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_bps = (ask - bid) / mid * 10_000 if mid > 0 else None
            if spread_bps is not None and spread_bps >= self.max_spread_bps:
                return GateResult(
                    ok=False,
                    reason="wide_spread",
                    detail={
                        "ticker": ticker,
                        "spread_bps": round(spread_bps, 1),
                        "max_spread_bps": self.max_spread_bps,
                        "bid": bid,
                        "ask": ask,
                    },
                )

        if signal_price and signal_price > 0 and self.max_price_drift_bps is not None:
            drift_bps = abs(price - signal_price) / signal_price * 10_000
            if drift_bps > self.max_price_drift_bps:
                return GateResult(
                    ok=False,
                    reason="price_drift_too_large",
                    detail={
                        "ticker": ticker,
                        "price": price,
                        "signal_price": signal_price,
                        "drift_bps": round(drift_bps, 1),
                        "max_price_drift_bps": self.max_price_drift_bps,
                    },
                )

        if self.min_risk_reward is not None and stop and target:
            live_risk = price - stop
            live_reward = target - price
            rr = live_reward / live_risk if live_risk > 0 else 0.0
            if live_risk <= 0 or rr < self.min_risk_reward:
                return GateResult(
                    ok=False,
                    reason="risk_reward_failed",
                    detail={
                        "ticker": ticker,
                        "price": price,
                        "stop": stop,
                        "target": target,
                        "live_risk_reward": round(rr, 3),
                        "min_risk_reward": self.min_risk_reward,
                    },
                )

        return GateResult(
            ok=True,
            reason="ok",
            detail={
                "ticker": ticker,
                "quote_age_seconds": round(age_seconds, 1),
                "quote_source": quote_source or "",
                "spread_bps": round(spread_bps, 1) if spread_bps is not None else None,
            },
        )
