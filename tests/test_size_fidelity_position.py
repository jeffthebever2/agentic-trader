"""_size_fidelity_position is the real-money share sizer. It must fail closed on
non-finite inputs (a NaN price slips past 'price <= 0' and crashes int(alloc/
price)), never exceed available cash, and respect the 10% compliance cap."""
from web.api.fidelity import _size_fidelity_position as size


def test_normal_sizing():
    shares, cost = size(100_000, 50_000, 100.0, dollar_amount=5_000)
    assert shares == 50 and cost == 5_000.0


def test_respects_ten_percent_cap():
    # dollar_amount 99,999 but 10% of 100k = 10,000 → capped.
    shares, cost = size(100_000, 50_000, 100.0, dollar_amount=99_999)
    assert cost <= 100_000 * 0.10
    assert shares == 100


def test_never_exceeds_available_cash():
    shares, cost = size(100_000, 250.0, 100.0, dollar_amount=5_000)
    assert cost <= 250.0


def test_nonfinite_inputs_fail_closed():
    nan, inf = float("nan"), float("inf")
    assert size(nan, 50_000, 100.0, dollar_amount=5_000) == (0, 0.0)
    assert size(100_000, nan, 100.0, dollar_amount=5_000) == (0, 0.0)
    assert size(100_000, 50_000, nan, dollar_amount=5_000) == (0, 0.0)
    assert size(100_000, 50_000, inf, dollar_amount=5_000) == (0, 0.0)


def test_nonpositive_price_or_value():
    assert size(100_000, 50_000, 0.0, dollar_amount=5_000) == (0, 0.0)
    assert size(0, 50_000, 100.0) == (0, 0.0)
    assert size(100_000, 0, 100.0, dollar_amount=5_000) == (0, 0.0)


def test_inf_dollar_amount_does_not_crash():
    # inf dollar_amount must be ignored, falling back to the 10% cap.
    shares, cost = size(100_000, 50_000, 100.0, dollar_amount=float("inf"))
    assert shares == 100 and cost == 10_000.0
