"""US market calendar — holidays, early closes, and the risk window.

The loops used to test only "Mon-Fri, 09:30-16:00 ET", which is wrong in both
directions and both cost money:

  * on a market holiday the gate returned True, so the exit guard evaluated
    stops against quotes that had not moved since the previous close — a
    stale-price decision on real positions;
  * a breach at Friday 15:59 was not looked at again until Monday 09:30, about
    65.5 hours, which is exactly when an after-hours earnings miss or a weekend
    headline does its damage.

Holidays are COMPUTED, so this pins real observed dates rather than a table that
silently expires.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradingagents.market_calendar import (
    ET, early_closes, holidays, hours_until_next_session, is_extended_session,
    is_regular_session, is_trading_day, next_session_open, session_close,
)


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).replace(tzinfo=ET)


# Verified against the published NYSE calendars.
NYSE_2025 = {"2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
             "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25"}
NYSE_2026 = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
             "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}


@pytest.mark.unit
@pytest.mark.parametrize("year,expected", [(2025, NYSE_2025), (2026, NYSE_2026)])
def test_holidays_match_the_published_nyse_calendar(year, expected):
    assert {d.isoformat() for d in holidays(year)} == expected


@pytest.mark.unit
def test_weekend_observance_shifts_the_holiday():
    """Jul 4 2026 is a Saturday → observed Friday Jul 3."""
    assert dt.date(2026, 7, 3) in holidays(2026)
    assert dt.date(2026, 7, 4) not in holidays(2026)


@pytest.mark.unit
def test_juneteenth_only_from_2022():
    assert dt.date(2021, 6, 18) not in holidays(2021)
    assert dt.date(2022, 6, 20) in holidays(2022)      # Jun 19 2022 was a Sunday


@pytest.mark.unit
def test_good_friday_tracks_easter():
    assert dt.date(2025, 4, 18) in holidays(2025)
    assert dt.date(2026, 4, 3) in holidays(2026)


@pytest.mark.unit
def test_early_closes_and_session_close_time():
    assert dt.date(2026, 11, 27) in early_closes(2026)          # day after Thanksgiving
    assert session_close(dt.date(2026, 11, 27)) == dt.time(13, 0)
    assert session_close(dt.date(2026, 7, 21)) == dt.time(16, 0)


@pytest.mark.unit
def test_no_session_on_a_holiday():
    """The headline bug: stops must NOT be evaluated on a holiday."""
    for stamp in ("2026-11-26T10:00", "2026-12-25T10:00", "2026-07-03T10:00"):
        assert is_regular_session(_t(stamp)) is False, stamp
        assert is_extended_session(_t(stamp)) is False, stamp


@pytest.mark.unit
def test_early_close_ends_the_regular_session_but_not_the_risk_window():
    assert is_regular_session(_t("2026-11-27T12:30")) is True
    assert is_regular_session(_t("2026-11-27T14:00")) is False
    assert is_extended_session(_t("2026-11-27T14:00")) is True


@pytest.mark.unit
@pytest.mark.parametrize("stamp,regular,extended", [
    ("2026-07-21T09:29", False, True),    # pre-market: risk yes, execution no
    ("2026-07-21T09:30", True,  True),    # open
    ("2026-07-21T16:00", True,  True),    # close
    ("2026-07-21T16:01", False, True),    # after-hours: risk yes, execution no
    ("2026-07-21T20:01", False, False),   # past extended close
    ("2026-07-21T03:59", False, False),   # before extended open
    ("2026-07-18T12:00", False, False),   # Saturday
])
def test_risk_window_is_wider_than_the_execution_window(stamp, regular, extended):
    assert is_regular_session(_t(stamp)) is regular
    assert is_extended_session(_t(stamp)) is extended


@pytest.mark.unit
def test_risk_window_never_narrower_than_the_execution_window():
    """Invariant: anything safe to execute in must also be watched."""
    day = dt.datetime(2026, 7, 20, 0, 0, tzinfo=ET)
    for _ in range(7 * 24 * 4):            # a week at 15-minute steps
        if is_regular_session(day):
            assert is_extended_session(day), day
        day += dt.timedelta(minutes=15)


@pytest.mark.unit
def test_the_weekend_blind_spot_is_measurable():
    """Friday 15:59 → Monday 09:30 is ~65.5h. This is what gating RISK work on
    the regular session alone used to cost."""
    gap = hours_until_next_session(_t("2026-07-17T15:59"))
    assert 65.0 < gap < 66.0


@pytest.mark.unit
def test_next_session_open_skips_holidays_and_weekends():
    # Thu 2026-11-26 is Thanksgiving → next open is Fri the 27th.
    assert next_session_open(_t("2026-11-25T17:00")).date() == dt.date(2026, 11, 27)
    # Fri 2026-07-03 observed holiday → next open is Mon the 6th.
    assert next_session_open(_t("2026-07-02T17:00")).date() == dt.date(2026, 7, 6)


@pytest.mark.unit
def test_naive_datetimes_are_treated_as_eastern():
    assert is_regular_session(dt.datetime(2026, 7, 21, 10, 0)) is True


@pytest.mark.unit
def test_utc_input_is_converted_not_misread():
    """14:00 UTC = 10:00 ET in July — a session, despite the raw clock."""
    utc = dt.datetime(2026, 7, 21, 14, 0, tzinfo=dt.timezone.utc)
    assert is_regular_session(utc) is True


@pytest.mark.unit
def test_trading_day_helper_agrees_with_holidays():
    assert is_trading_day(dt.date(2026, 7, 21)) is True
    assert is_trading_day(dt.date(2026, 7, 4)) is False        # Saturday
    assert is_trading_day(dt.date(2026, 12, 25)) is False
