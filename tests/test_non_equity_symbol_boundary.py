"""is_non_equity_symbol — money-market / mutual-fund filter. Locks the heuristic
boundary (real 4-letter equities ending X like NFLX are NOT excluded; 5-letter
funds ending X and *XX sweeps ARE) and that a non-string ticker can't crash it."""
from tradingagents.portfolio.holdings_brain import is_non_equity_symbol as nq


def test_money_market_and_funds_excluded():
    assert nq("SPAXX") is True           # money-market sweep (XX)
    assert nq("FDRXX") is True
    assert nq("FSPGX") is True           # 5-letter mutual/index fund (ends X)
    assert nq("FXAIX") is True


def test_real_equities_not_excluded():
    for sym in ("NFLX", "AAPL", "NVDA", "AMD", "TSLA", "GOOGL"):
        assert nq(sym) is False, f"{sym} wrongly flagged non-equity"


def test_name_keywords_excluded():
    assert nq("ZZZZ", "Vanguard Total Bond Fund") is True
    assert nq("ABCD", "Held in money market") is True
    assert nq("ABCD", "Acme Corp") is False


def test_non_string_ticker_does_not_crash():
    assert nq(None) is False
    assert nq(0) is False
    assert nq(12345) is False            # int ticker: coerced, no crash
