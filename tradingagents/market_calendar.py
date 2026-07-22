"""US equity market calendar — sessions, holidays and early closes.

The loops used to test only "Mon-Fri, 09:30-16:00 ET". That is wrong in both
directions and both cost money:

  * On a market holiday the gate returned True, so the exit guard evaluated
    stops against quotes that never updated — a stale-price decision on real
    positions, and the fill it proposes cannot happen.
  * A breach at Friday 15:59 was not looked at again until Monday 09:30 — about
    65.5 hours, longer over a three-day weekend. Everything an after-hours
    earnings miss or a weekend headline does is already realised by then.

Pure, stdlib-only, no data files. Holidays are COMPUTED rather than listed, so
the calendar cannot silently expire the way a hardcoded table does.

Rules implemented (NYSE/Nasdaq):
  * New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day,
    Juneteenth (from 2022), Independence Day, Labor Day, Thanksgiving, Christmas.
  * Weekend observance: Saturday → observed the preceding Friday; Sunday →
    observed the following Monday. Good Friday and the Monday holidays never
    move.
  * Early closes at 13:00 ET: July 3 (when the 4th is a weekday), the day after
    Thanksgiving, and December 24 (when it falls on a weekday).
"""
from __future__ import annotations

import datetime as _dt
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = _dt.time(9, 30)
REGULAR_CLOSE = _dt.time(16, 0)
EARLY_CLOSE = _dt.time(13, 0)
#: Extended session used for RISK checks (not for entries). Pre-market opens at
#: 04:00 and after-hours runs to 20:00 ET; a stop breach is real in both.
EXTENDED_OPEN = _dt.time(4, 0)
EXTENDED_CLOSE = _dt.time(20, 0)

#: Juneteenth became a market holiday in 2022.
_JUNETEENTH_FROM = 2022


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """`n`-th `weekday` (Mon=0) of `month`. n=-1 → last one in the month."""
    if n > 0:
        first = _dt.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + _dt.timedelta(days=offset + 7 * (n - 1))
    nxt = _dt.date(year + (month == 12), (month % 12) + 1, 1)
    last = nxt - _dt.timedelta(days=1)
    return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: _dt.date) -> _dt.date:
    """Weekend observance: Sat → preceding Fri, Sun → following Mon."""
    if day.weekday() == 5:
        return day - _dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + _dt.timedelta(days=1)
    return day


def _easter(year: int) -> _dt.date:
    """Gregorian Easter Sunday (Anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return _dt.date(year, month, day + 1)


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset:
    """Observed market holidays for `year`."""
    out = {
        _observed(_dt.date(year, 1, 1)),                     # New Year's Day
        _nth_weekday(year, 1, 0, 3),                         # MLK Day
        _nth_weekday(year, 2, 0, 3),                         # Presidents Day
        _easter(year) - _dt.timedelta(days=2),               # Good Friday
        _nth_weekday(year, 5, 0, -1),                        # Memorial Day
        _observed(_dt.date(year, 7, 4)),                     # Independence Day
        _nth_weekday(year, 9, 0, 1),                         # Labor Day
        _nth_weekday(year, 11, 3, 4),                        # Thanksgiving
        _observed(_dt.date(year, 12, 25)),                   # Christmas
    }
    if year >= _JUNETEENTH_FROM:
        out.add(_observed(_dt.date(year, 6, 19)))
    return frozenset(out)


@lru_cache(maxsize=64)
def early_closes(year: int) -> frozenset:
    """Days the market closes at 13:00 ET."""
    out = set()
    jul4 = _dt.date(year, 7, 4)
    if jul4.weekday() < 5:                                   # Jul 3 only if the 4th trades
        jul3 = jul4 - _dt.timedelta(days=1)
        if jul3.weekday() < 5:
            out.add(jul3)
    out.add(_nth_weekday(year, 11, 3, 4) + _dt.timedelta(days=1))  # day after Thanksgiving
    dec24 = _dt.date(year, 12, 24)
    if dec24.weekday() < 5:
        out.add(dec24)
    return frozenset(out - holidays(year))


def is_trading_day(day: _dt.date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def session_close(day: _dt.date) -> _dt.time:
    return EARLY_CLOSE if day in early_closes(day.year) else REGULAR_CLOSE


def _now_et(now: _dt.datetime | None = None) -> _dt.datetime:
    if now is None:
        return _dt.datetime.now(ET)
    return now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)


def is_regular_session(now: _dt.datetime | None = None) -> bool:
    """True during the regular 09:30 → close session (honours early closes)."""
    n = _now_et(now)
    if not is_trading_day(n.date()):
        return False
    return REGULAR_OPEN <= n.time() <= session_close(n.date())


def is_extended_session(now: _dt.datetime | None = None) -> bool:
    """True 04:00 → 20:00 ET on a trading day.

    Use for RISK work (stop checks, exit proposals): a breach is real in
    pre-market and after-hours, and waiting for 09:30 means discovering it after
    the damage. Do NOT use to gate entries — liquidity out here is thin.
    """
    n = _now_et(now)
    if not is_trading_day(n.date()):
        return False
    return EXTENDED_OPEN <= n.time() <= EXTENDED_CLOSE


def next_session_open(now: _dt.datetime | None = None) -> _dt.datetime:
    """Next regular open at or after `now`."""
    n = _now_et(now)
    day = n.date()
    if is_trading_day(day) and n.time() < REGULAR_OPEN:
        return _dt.datetime.combine(day, REGULAR_OPEN, tzinfo=ET)
    day += _dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(day):
            return _dt.datetime.combine(day, REGULAR_OPEN, tzinfo=ET)
        day += _dt.timedelta(days=1)
    raise RuntimeError("no trading day found within 30 days")


def hours_until_next_session(now: _dt.datetime | None = None) -> float:
    """Hours until the next regular open — the blind-spot length when risk work
    is gated on the regular session alone. Friday 15:59 → ~65.5."""
    n = _now_et(now)
    return max(0.0, (next_session_open(n) - n).total_seconds() / 3600.0)
