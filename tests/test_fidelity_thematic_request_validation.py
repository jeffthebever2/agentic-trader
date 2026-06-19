"""Input bounds on the REAL-MONEY Fidelity thematic order model. A stop_pct>=100
gives a stop price <= $0; negative stop/target inverts them; negative dollar or
out-of-range pct corrupts sizing. These must be rejected at the model boundary —
the compliance quote gate does not police stop/target shape. Only tighten."""
import pytest
from pydantic import ValidationError

from web.api.fidelity import FidelityThematicTradeRequest as Req


def _ok(**kw):
    base = {"ticker": "NVDA"}
    base.update(kw)
    return Req(**base)


def test_defaults_valid():
    m = _ok()
    assert m.stop_pct == 5.0 and m.target_pct == 10.0
    assert m.dollar_amount is None and m.pct_of_account is None


def test_stop_pct_bounds():
    _ok(stop_pct=8.0)
    for bad in (0, -5, 100, 150):
        with pytest.raises(ValidationError):
            _ok(stop_pct=bad)


def test_target_pct_bounds():
    _ok(target_pct=60.0)
    for bad in (0, -10, 1001):
        with pytest.raises(ValidationError):
            _ok(target_pct=bad)


def test_dollar_amount_positive():
    _ok(dollar_amount=500.0)
    _ok(dollar_amount=None)
    for bad in (0, -100.0):
        with pytest.raises(ValidationError):
            _ok(dollar_amount=bad)


def test_pct_of_account_bounds():
    _ok(pct_of_account=2.5)
    _ok(pct_of_account=None)
    for bad in (0, -1, 150):
        with pytest.raises(ValidationError):
            _ok(pct_of_account=bad)
