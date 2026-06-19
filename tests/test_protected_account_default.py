"""Roth/retirement protection must be ON by default. normalize_holdings filters
protected accounts only when exclude_protected_accounts is True — if a refactor
flipped that default to False, the brain would start *proposing* trades on
retirement accounts (the broker kill-switch still blocks execution, but a Roth
proposal should never even surface). This tripwire locks the safe default.
"""
import inspect

from tradingagents.portfolio import holdings_brain as hb


def test_normalize_holdings_excludes_protected_by_default():
    sig = inspect.signature(hb.normalize_holdings)
    assert sig.parameters["exclude_protected_accounts"].default is True


def test_protection_layers_present():
    # The three protection layers must all still exist as callables/patterns.
    assert callable(hb.is_protected_account)
    assert callable(hb.is_non_equity_symbol)
    assert "roth" in hb.PROTECTED_ACCOUNT_PATTERNS
    assert "ira" in hb.PROTECTED_ACCOUNT_PATTERNS


def test_allowlist_default_deny_still_works(monkeypatch):
    monkeypatch.setenv("HOLDINGS_BRAIN_ALLOWED_ACCOUNTS", "Z30299153")
    # only the allowed number is tradeable; everything else protected
    assert hb.is_protected_account("Brokerage", "999999") is True
    assert hb.is_protected_account("Youth", "Z30299153") is False
