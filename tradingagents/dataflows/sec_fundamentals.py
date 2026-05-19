"""SEC EDGAR companyfacts provider for keyless US fundamentals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import requests

from .config import get_config


SEC_USER_AGENT = "TradingAgents/0.2.4 contact@example.com"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

TAGS = {
    "overview": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "EarningsPerShareDiluted",
        "EntityCommonStockSharesOutstanding",
    ],
    "income": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "GrossProfit",
        "OperatingIncomeLoss",
        "NetIncomeLoss",
        "EarningsPerShareDiluted",
    ],
    "balance": [
        "Assets",
        "AssetsCurrent",
        "Liabilities",
        "LiabilitiesCurrent",
        "StockholdersEquity",
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "cashflow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInFinancingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
}


def _cache_dir() -> Path:
    path = Path(get_config()["data_cache_dir"]).expanduser() / "sec"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _headers() -> dict:
    cfg = get_config()
    return {
        "User-Agent": cfg.get("sec_user_agent") or SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }


def _get_ticker_map() -> dict[str, int]:
    cache = _cache_dir() / "company_tickers.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        response = requests.get(TICKER_URL, headers=_headers(), timeout=20)
        response.raise_for_status()
        raw = response.json()
        cache.write_text(json.dumps(raw), encoding="utf-8")
    return {
        item["ticker"].upper(): int(item["cik_str"])
        for item in raw.values()
        if "ticker" in item and "cik_str" in item
    }


def _facts(ticker: str) -> dict:
    cik = _get_ticker_map().get(ticker.upper())
    if not cik:
        raise ValueError(f"No SEC CIK found for {ticker}")
    cache = _cache_dir() / f"companyfacts-{cik:010d}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    response = requests.get(FACTS_URL.format(cik=cik), headers=_headers(), timeout=30)
    response.raise_for_status()
    data = response.json()
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _latest_fact(data: dict, tag: str, curr_date: str | None = None) -> dict | None:
    fact = data.get("facts", {}).get("us-gaap", {}).get(tag)
    if not fact:
        fact = data.get("facts", {}).get("dei", {}).get(tag)
    if not fact:
        return None

    units = fact.get("units", {})
    values = []
    for unit_name, unit_values in units.items():
        for item in unit_values:
            if curr_date and item.get("filed", "9999-99-99") > curr_date:
                continue
            if curr_date and item.get("end", "9999-99-99") > curr_date:
                continue
            values.append({**item, "unit": unit_name})
    if not values:
        return None
    values.sort(key=lambda item: (item.get("end", ""), item.get("filed", "")), reverse=True)
    return values[0]


def _render(ticker: str, title: str, tags: Iterable[str], curr_date: str | None = None) -> str:
    try:
        data = _facts(ticker)
        lines = [f"## SEC {title} for {ticker.upper()}", f"Entity: {data.get('entityName', ticker.upper())}", ""]
        found = 0
        for tag in tags:
            item = _latest_fact(data, tag, curr_date)
            if not item:
                continue
            found += 1
            lines.append(
                f"- {tag}: {item.get('val')} {item.get('unit', '')} "
                f"(period end {item.get('end', 'n/a')}, filed {item.get('filed', 'n/a')}, form {item.get('form', 'n/a')})"
            )
        if not found:
            return f"No SEC {title.lower()} facts found for {ticker}"
        return "\n".join(lines)
    except Exception as exc:
        return f"Error retrieving SEC {title.lower()} for {ticker}: {exc}"


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    return _render(ticker, "fundamentals", TAGS["overview"], curr_date)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(ticker, "income statement", TAGS["income"], curr_date)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(ticker, "balance sheet", TAGS["balance"], curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    return _render(ticker, "cash flow", TAGS["cashflow"], curr_date)
