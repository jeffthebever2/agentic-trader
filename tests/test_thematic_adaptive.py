"""Thematic upgrades (2026-06-18): sentiment-aware score, adaptive sizing,
auto-trade threshold gate. Real-money-adjacent — keep these green."""
import web.api.thematic_auto as t


# ── Sentiment-aware composite score ─────────────────────────────────────────
def test_bullish_beats_bearish_same_buzz():
    bull = t.composite_score(9, 283, 0.7)
    bear = t.composite_score(9, 283, -0.8)
    assert bull > bear
    assert bull >= 90


def test_crowd_sell_hard_capped():
    # Deep-bearish (≤ -0.5) can never clear a 75 auto-trade gate, even at max buzz.
    assert t.composite_score(10, 100000, -0.6) <= 45
    assert t.composite_score(10, 100000, -1.0) <= 45


def test_neutral_sentiment_is_backward_compatible():
    # sentiment default 0 → conviction backbone only (no buzz). Backbone is c*7.5
    # (rebalanced 2026-06-21 from c*8.5 so raw buzz materially differentiates).
    assert t.composite_score(10, 0) == t.composite_score(10, 0, 0.0)
    assert t.composite_score(10, 0, 0.0) == 75


def test_score_clamped_0_100():
    assert 0 <= t.composite_score(10, 10**9, 1.0) <= 100
    assert 0 <= t.composite_score(1, 0, -1.0) <= 100


# ── Conservative pre-trade floors (2026-06-19) ──────────────────────────────
# Real money moves off these. They may only ever be RAISED, never lowered below
# the floors below without an explicit, reviewed decision. This test is the
# tripwire against an accidental loosening.
def test_min_signal_score_floor():
    # Raw-buzz buy gate (approve / will_buy). Floor raised 40→48.
    assert t.MIN_SIGNAL_SCORE >= 48.0


def test_auto_trade_score_floor():
    # Composite auto-trade / SMS-request gate default. Floor raised 75→80→82.
    # This is the money-moving auto-execute path; it may only ever be RAISED.
    from web.users import DEFAULT_THEMATIC_HIL
    assert DEFAULT_THEMATIC_HIL["auto_trade_score"] >= 82.0


# ── Adaptive sizing ─────────────────────────────────────────────────────────
HIL = {"base_position_pct": 4.0, "min_dollar": 25.0}


def test_adaptive_scales_with_score():
    lo = t._adaptive_dollar(25000, 60, 40, HIL)
    hi = t._adaptive_dollar(25000, 95, 150, HIL)
    assert hi > lo > 0


def test_adaptive_respects_10pct_cap():
    # Even a perfect score can't exceed MAX_POSITION_PCT_OF_ACCOUNT (10%).
    assert t._adaptive_dollar(25000, 100, 300, HIL) <= 2500.0


def test_adaptive_scales_with_account_size():
    small = t._adaptive_dollar(5000, 80, 80, HIL)
    big = t._adaptive_dollar(50000, 80, 80, HIL)
    assert big > small                      # bigger account → bigger position


def test_adaptive_zero_account_returns_zero():
    assert t._adaptive_dollar(0, 90, 100, HIL) == 0.0


def test_adaptive_floor_applies():
    # Tiny account → floored at min_dollar, never below.
    assert t._adaptive_dollar(100, 55, 40, HIL) >= 25.0


# ── Score helper used by the auto-trade gate ────────────────────────────────
def test_sig_score_prefers_stamped():
    assert t._sig_score({"score": 88}) == 88.0


def test_sig_score_recomputes_when_missing():
    s = t._sig_score({"conviction": 9, "raw_score": 283, "sentiment": 0.7})
    assert s >= 90


# ── Overnight SMS quiet hours (no trade-request texts at 2-5am) ──────────────
import datetime as _dt


def test_quiet_hours_overnight_suppressed(monkeypatch):
    monkeypatch.setenv("THEMATIC_SMS_QUIET_HOURS", "22-8")
    assert t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 2, 0))    # 2am
    assert t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 23, 0))   # 11pm
    assert t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 7, 59))   # just before 8


def test_quiet_hours_daytime_allowed(monkeypatch):
    monkeypatch.setenv("THEMATIC_SMS_QUIET_HOURS", "22-8")
    assert not t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 8, 0))   # 8am resumes
    assert not t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 12, 0))
    assert not t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 21, 0))


def test_quiet_hours_disabled_when_empty(monkeypatch):
    monkeypatch.setenv("THEMATIC_SMS_QUIET_HOURS", "")
    assert not t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 2, 0))


def test_quiet_hours_malformed_is_safe(monkeypatch):
    # Garbage spec must never raise / never wrongly suppress.
    monkeypatch.setenv("THEMATIC_SMS_QUIET_HOURS", "garbage")
    assert not t._in_sms_quiet_hours(_dt.datetime(2026, 6, 18, 2, 0))


# ── Scan source timeout (anti-hang: bound each scraper) ──────────────────────
def test_scan_source_timeout_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_SOURCE_TIMEOUT", raising=False)
    assert t._scan_source_timeout() == 25.0


def test_scan_source_timeout_env_override(monkeypatch):
    monkeypatch.setenv("THEMATIC_SOURCE_TIMEOUT", "40")
    assert t._scan_source_timeout() == 40.0


def test_scan_source_timeout_floor_and_garbage(monkeypatch):
    monkeypatch.setenv("THEMATIC_SOURCE_TIMEOUT", "1")     # below floor
    assert t._scan_source_timeout() == 5.0
    monkeypatch.setenv("THEMATIC_SOURCE_TIMEOUT", "junk")  # unparseable
    assert t._scan_source_timeout() == 25.0


# ── Stale "running" status guard (a crashed scan must not block forever) ─────
def test_status_not_stale_when_recent():
    now = 1_000_000.0
    assert not t._scan_status_stale({"status": "running", "ts": now - 30}, now=now)


def test_status_stale_when_old():
    # >5min in 'running' = a dead/killed scan; must be overridable.
    now = 1_000_000.0
    assert t._scan_status_stale({"status": "running", "ts": now - 600}, now=now)


def test_status_never_stale_when_not_running():
    now = 1_000_000.0
    assert not t._scan_status_stale({"status": "done", "ts": 0}, now=now)
    assert not t._scan_status_stale({"status": "idle"}, now=now)


def test_status_stale_threshold_env(monkeypatch):
    monkeypatch.setenv("THEMATIC_SCAN_STALE_SECONDS", "60")
    now = 1_000_000.0
    assert t._scan_status_stale({"status": "running", "ts": now - 90}, now=now)
    assert not t._scan_status_stale({"status": "running", "ts": now - 30}, now=now)


def test_status_stale_missing_ts_is_stale():
    # No/garbage ts on a 'running' status → treat as stale (safe: lets a scan start).
    assert t._scan_status_stale({"status": "running"}, now=1_000_000.0)
