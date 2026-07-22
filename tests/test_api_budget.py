"""Tests for the monthly API budget guard (thematic revamp cost-safety)."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from tradingagents.screening.api_budget import ApiBudget


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


# a fixed epoch in 2026-07 (UTC)
JUL = time.mktime(time.strptime("2026-07-06", "%Y-%m-%d"))
AUG = time.mktime(time.strptime("2026-08-02", "%Y-%m-%d"))


def _budget(cap=25.0, clock=None):
    return ApiBudget("xapi", Path(tempfile.mkdtemp()), monthly_cap=cap,
                     now_fn=clock or _Clock(JUL))


def test_record_and_remaining():
    b = _budget(cap=25)
    assert b.remaining() == 25 and b.allow() is True
    b.record(10)
    assert b.spent() == 10 and b.remaining() == 15


def test_hard_stop_at_cap():
    b = _budget(cap=25)
    b.record(25)
    assert b.allow() is False
    b.record(5)  # overspend recorded but allow stays False
    assert b.allow() is False and b.remaining() == 0.0


def test_warn_alert_fires_once():
    b = _budget(cap=25)          # warn at 80% = 20
    b.record(21)
    msg = b.take_alert()
    assert msg and "80%" not in msg or msg  # a warn message
    assert "budget" in msg.lower()
    assert b.take_alert() is None            # latched — only once


def test_stop_alert_fires_once_and_implies_warn():
    b = _budget(cap=25)
    b.record(25)
    msg = b.take_alert()
    assert msg and "EXHAUSTED" in msg
    assert b.take_alert() is None            # only once
    # warn already implied → never a second (warn) alert this month
    b.record(1)
    assert b.take_alert() is None


def test_no_alert_below_warn():
    b = _budget(cap=25)
    b.record(5)
    assert b.take_alert() is None


def test_monthly_reset():
    clk = _Clock(JUL)
    d = Path(tempfile.mkdtemp())
    b = ApiBudget("xapi", d, monthly_cap=25.0, now_fn=clk)
    b.record(25)
    assert b.allow() is False
    b.take_alert()
    # roll to August → fresh budget + fresh alerts
    clk.t = AUG
    b2 = ApiBudget("xapi", d, monthly_cap=25.0, now_fn=clk)
    assert b2.spent() == 0.0 and b2.allow() is True and b2.take_alert() is None


def test_persistence():
    d = Path(tempfile.mkdtemp())
    b1 = ApiBudget("xapi", d, monthly_cap=25.0, now_fn=_Clock(JUL))
    b1.record(12)
    b2 = ApiBudget("xapi", d, monthly_cap=25.0, now_fn=_Clock(JUL))
    assert b2.spent() == 12
