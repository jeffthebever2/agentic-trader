"""Brokerage account numbers are sensitive financial identifiers and must never
appear in plaintext logs. _mask_account exposes only the last 4 digits."""
from web.api.fidelity import _mask_account


def test_masks_all_but_last_four():
    assert _mask_account("262502469") == "•••••2469"
    assert _mask_account("12345678") == "••••5678"


def test_short_or_empty_fully_masked():
    assert _mask_account("") == "••••"
    assert _mask_account(None) == "••••"
    assert _mask_account("123") == "••••"
    assert _mask_account("1234") == "••••"


def test_full_number_never_present_in_output():
    acct = "987654321"
    out = _mask_account(acct)
    assert acct not in out          # the full number never leaks
    assert out.endswith("4321")     # last 4 retained for support/debug
    assert len(out) == len(acct)    # length preserved, digits hidden
