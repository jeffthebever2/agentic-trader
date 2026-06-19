"""Breakout confirmation (IREN-class fast-lane). A new-high-on-heavy-volume
breakout is a price confirmation substitute for the 2-scan social gate. Pure
detector + flag-gated injectable fetch — rejects no-volume drifts and high-volume
churn that isn't a new high (keeps froth out)."""
import web.api.thematic_auto as t


def _bars(n=25, base=100.0):
    highs = [base + i*0.1 for i in range(n)]
    closes = [base + i*0.1 - 0.05 for i in range(n)]
    vols = [1_000_000.0]*n
    return highs, closes, vols


def test_volume_breakout_to_new_high_detected():
    h, c, v = _bars()
    # today: gap to clear new high on 5x volume
    h[-1] = 130.0; c[-1] = 129.0; v[-1] = 5_000_000.0
    sig = t._breakout_signal(h, c, v)
    assert sig["is_breakout"] is True
    assert sig["rvol"] >= 3.0 and sig["new_high"] is True


def test_new_high_without_volume_rejected():
    h, c, v = _bars()
    h[-1] = 130.0; c[-1] = 129.0; v[-1] = 1_000_000.0   # new high but normal volume
    sig = t._breakout_signal(h, c, v)
    assert sig["is_breakout"] is False
    assert sig["new_high"] is True and sig["rvol"] < 3.0


def test_high_volume_without_new_high_rejected():
    h, c, v = _bars()
    c[-1] = 100.0; v[-1] = 9_000_000.0   # churn, not a new high
    sig = t._breakout_signal(h, c, v)
    assert sig["is_breakout"] is False


def test_insufficient_bars_safe():
    sig = t._breakout_signal([1,2], [1,2], [1,2])
    assert sig["is_breakout"] is False


def test_garbage_bars_safe():
    sig = t._breakout_signal([float("nan")]*25, [None]*25, ["x"]*25)
    assert sig["is_breakout"] is False


# ── flag-gated wrapper ──────────────────────────────────────────────────────
def test_ticker_breakout_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_BREAKOUT_CONFIRM", raising=False)
    h, c, v = _bars(); h[-1]=130; c[-1]=129; v[-1]=5_000_000.0
    # even with a real breakout, returns False while disabled
    assert t._ticker_breakout("IREN", fetch=lambda tk: {"highs":h,"closes":c,"volumes":v}) is False


def test_ticker_breakout_enabled(monkeypatch):
    monkeypatch.setenv("THEMATIC_BREAKOUT_CONFIRM", "true")
    h, c, v = _bars(); h[-1]=130; c[-1]=129; v[-1]=5_000_000.0
    assert t._ticker_breakout("IREN", fetch=lambda tk: {"highs":h,"closes":c,"volumes":v}) is True
    # fetch failure → graceful False
    def _boom(tk): raise RuntimeError("net")
    assert t._ticker_breakout("IREN", fetch=_boom) is False
