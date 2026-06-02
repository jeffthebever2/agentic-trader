"""Fidelity brokerage portfolio reader via fidelity-api (Playwright-based).

Credentials are read from environment variables FIDELITY_USERNAME and
FIDELITY_PASSWORD.  The browser runs headless by default; set
FIDELITY_HEADLESS=false to watch it operate.

Usage:
    portfolio = FidelityPortfolio()
    portfolio.connect()          # opens browser + logs in
    positions = portfolio.get_positions()
    summary   = portfolio.get_summary()
    portfolio.close()

Each position dict contains:
    ticker, shares, entry_price, current_price, current_value,
    cost_basis, gain_loss, gain_loss_pct, account
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

_MISSING_MSG = (
    "fidelity-api is not installed. Run: pip install fidelity-api\n"
    "Also install Playwright browsers: playwright install chromium"
)


class FidelityPortfolio:
    """Thin wrapper around fidelity-api's FidelityAutomation."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = True,
    ):
        self.username = username or os.getenv("FIDELITY_USERNAME", "")
        self.password = password or os.getenv("FIDELITY_PASSWORD", "")
        self.headless = headless
        self._browser = None
        self._positions: List[dict] = []
        self._accounts: dict = {}

    def connect(self, save_device: bool = True) -> bool:
        """Open the Playwright browser and log in to Fidelity.

        Returns True on success. Raises RuntimeError if fidelity-api is
        not installed or if credentials are missing.
        """
        try:
            from fidelity import fidelity as fid_module
            FidelityAutomation = fid_module.FidelityAutomation
        except ImportError:
            raise RuntimeError(_MISSING_MSG)

        if not self.username or not self.password:
            raise RuntimeError(
                "Fidelity credentials not set. "
                "Add FIDELITY_USERNAME and FIDELITY_PASSWORD to your .env file."
            )

        self._browser = FidelityAutomation(headless=self.headless, save_state=False)
        step1, step2 = self._browser.login(
            username=self.username,
            password=self.password,
            save_device=save_device,
        )

        if step1 and step2:
            logger.info("Fidelity login successful")
            return True

        if step1 and not step2:
            # 2FA required
            code = input("Fidelity 2FA code: ").strip()
            if self._browser.login_2FA(code):
                logger.info("Fidelity 2FA successful")
                return True
            raise RuntimeError("Fidelity 2FA failed.")

        raise RuntimeError("Fidelity login failed — check username/password.")

    def get_positions(self) -> List[dict]:
        """Return a normalized list of position dicts across all accounts."""
        if self._browser is None:
            raise RuntimeError("Call connect() first.")

        raw = self._browser.getAccountInfo()
        positions: List[dict] = []

        for account_key, account_data in (raw or {}).items():
            positions.extend(
                self._normalize_account(account_key, account_data)
            )

        self._positions = positions
        return positions

    def get_summary(self) -> dict:
        """Return a simple account summary: total value, cash, open positions."""
        positions = self._positions or self.get_positions()
        total_value = sum(p.get("current_value", 0.0) for p in positions)
        total_cost  = sum(p.get("cost_basis", 0.0)    for p in positions)
        total_gl    = total_value - total_cost
        total_gl_pct = (total_gl / total_cost * 100) if total_cost else 0.0

        return {
            "positions":    len(positions),
            "total_value":  total_value,
            "total_cost":   total_cost,
            "total_gl":     total_gl,
            "total_gl_pct": total_gl_pct,
        }

    def get_tickers(self) -> List[str]:
        """Return just the list of ticker symbols held."""
        positions = self._positions or self.get_positions()
        return [p["ticker"] for p in positions if p.get("ticker")]

    def close(self) -> None:
        """Close the Playwright browser session."""
        if self._browser is not None:
            try:
                self._browser.close_browser()
            except Exception:
                pass
            self._browser = None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _normalize_account(self, account_key: str, data) -> List[dict]:
        """Map whatever fidelity-api returns for one account into a flat list."""
        results: List[dict] = []

        # fidelity-api may return a pandas DataFrame or a dict/list of records
        try:
            import pandas as pd
            if isinstance(data, pd.DataFrame):
                for _, row in data.iterrows():
                    p = self._row_to_position(dict(row), account_key)
                    if p:
                        results.append(p)
                return results
        except ImportError:
            pass

        # Dict with a 'positions' or 'holdings' key
        if isinstance(data, dict):
            rows = (
                data.get("positions")
                or data.get("holdings")
                or data.get("stocks")
                or []
            )
            for row in rows:
                p = self._row_to_position(row, account_key)
                if p:
                    results.append(p)
            return results

        # List of dicts directly
        if isinstance(data, list):
            for row in data:
                p = self._row_to_position(row, account_key)
                if p:
                    results.append(p)

        # E2FP2: if data was non-empty but we got zero positions, warn loudly —
        # a funded account can't legitimately appear flat from a non-empty data blob.
        if not results and data:
            import logging as _log
            _log.getLogger("fidelity_portfolio").warning(
                "Account %s: non-empty data produced 0 positions — unknown schema shape. "
                "Re-buy / stop-management may be incorrect. Data type: %s",
                account_key, type(data).__name__,
            )

        return results

    @staticmethod
    def _row_to_position(row: dict, account: str) -> Optional[dict]:
        """Normalize one row to a standard position dict."""
        if not isinstance(row, dict):
            return None

        # Try common field name variants from fidelity-api / web scraping
        def pick(*keys):
            for k in keys:
                if k in row and row[k] is not None:
                    return row[k]
            return None

        ticker = pick("symbol", "ticker", "Symbol", "Ticker")
        if not ticker:
            return None

        ticker = str(ticker).upper().strip()
        # Skip cash / money market lines
        if ticker in ("--", "", "SPAXX", "FDRXX", "FZFXX"):
            return None

        try:
            shares        = float(pick("quantity", "shares", "Quantity", "Shares") or 0)
            entry_price   = float(pick("cost_per_share", "avg_cost", "Average Cost Basis",
                                       "CostBasis", "avg_price") or 0)
            current_price = float(pick("last_price", "current_price", "Last Price",
                                       "Price") or 0)
            current_value = float(pick("current_value", "market_value", "Market Value",
                                       "Value") or shares * current_price)
            cost_basis    = float(pick("cost_basis", "total_cost", "Total Cost Basis",
                                       "Cost") or shares * entry_price)
            gain_loss     = float(pick("gain_loss", "unrealized_gain_loss",
                                       "Gain/Loss Dollar") or current_value - cost_basis)
            gain_loss_pct = float(pick("gain_loss_pct", "unrealized_gain_loss_pct",
                                       "Gain/Loss Percent") or
                                  (gain_loss / cost_basis * 100 if cost_basis else 0))
        except (TypeError, ValueError) as exc:
            # E2FP1: never silently drop a row with a valid ticker — log it loudly
            import logging as _log
            _log.getLogger("fidelity_portfolio").warning(
                "Failed to parse numeric fields for ticker %s: %s (row=%s)", ticker, exc, row
            )
            return None

        return {
            "ticker":        ticker,
            "account":       str(account),
            "shares":        shares,
            "entry_price":   entry_price,
            "current_price": current_price,
            "current_value": current_value,
            "cost_basis":    cost_basis,
            "gain_loss":     gain_loss,
            "gain_loss_pct": gain_loss_pct,
        }
