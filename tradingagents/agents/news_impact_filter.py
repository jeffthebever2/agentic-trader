"""High-impact news/event guardrails."""

from __future__ import annotations

from typing import List, Tuple

import yfinance as yf



class NewsImpactFilter:
    high_impact_events = (
        "FOMC",
        "CPI",
        "Jobs Report",
        "GDP",
        "Earnings",
        "Merger",
        "Acquisition",
        "Executive Change",
        "Regulatory",
    )

    def check_upcoming_events(self, ticker: str, days_ahead: int = 7) -> List[str]:
        events = []
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                return events[:10]
            # yfinance >= 0.2 returns a dict; older versions returned a DataFrame
            if isinstance(cal, dict):
                for key, value in cal.items():
                    events.append(f"{key}: {value}")
            elif hasattr(cal, "empty") and not cal.empty:
                for idx, value in cal.iloc[:, 0].items():
                    events.append(f"{idx}: {value}")
        except Exception:
            pass
        return events[:10]

    def should_trade_this_stock(self, ticker: str) -> Tuple[bool, str]:
        upcoming = self.check_upcoming_events(ticker, days_ahead=2)
        if upcoming:
            return False, f"Major event(s) nearby: {'; '.join(upcoming)}"
        return True, "OK to trade"
