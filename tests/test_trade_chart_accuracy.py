"""HIL trade-chart accuracy: real levels in, honest fib geometry, no fabrication.

The chart backs a real-money approval, so it must draw exactly the levels it is
given (a missing target/stop is omitted, never invented) and the fib levels must
sit correctly between the swing low and high.
"""
import pytest

from tradingagents.portfolio.chart import compute_levels, render_trade_chart


def test_compute_levels_geometry():
    highs = [10, 12, 15, 14, 13]
    lows = [8, 9, 11, 10, 9]
    closes = [9, 11, 14, 13, 12]
    lv = compute_levels(highs, lows, closes, lookback=10)
    assert lv["swing_high"] == 15
    assert lv["swing_low"] == 8
    # Retracements sit between low and high; deeper retrace => lower price.
    assert lv["swing_low"] < lv["fib_786"] < lv["fib_618"] < lv["fib_500"] < lv["swing_high"]
    # 1.618 is an upside extension above the swing high.
    assert lv["ext_1618"] > lv["swing_high"]


def test_compute_levels_empty():
    assert compute_levels([], [], []) == {}


def _make_df(n=180):
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0.1, 1.2, size=n))
    close = np.maximum(close, 5)
    df = pd.DataFrame({
        "Open": close + rng.normal(0, 0.4, n),
        "High": close + abs(rng.normal(1, 0.4, n)),
        "Low": close - abs(rng.normal(1, 0.4, n)),
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, n),
    }, index=idx)
    return df


def test_render_with_full_levels(tmp_path):
    pytest.importorskip("mplfinance")
    df = _make_df()
    last = float(df["Close"].iloc[-1])
    out = str(tmp_path / "c.png")
    res = render_trade_chart(
        "TEST", df, entry=round(last, 2), stop=round(last * 0.92, 2),
        target=round(last * 1.3, 2), out_path=out,
    )
    assert res == out
    assert (tmp_path / "c.png").stat().st_size > 1000


def test_render_without_target_does_not_fabricate(tmp_path):
    """A missing target must still render (entry+stop only) and never raise —
    the old code invented last*1.25; the new code omits it."""
    pytest.importorskip("mplfinance")
    df = _make_df()
    last = float(df["Close"].iloc[-1])
    out = str(tmp_path / "c2.png")
    res = render_trade_chart(
        "TEST", df, entry=round(last, 2), stop=round(last * 0.92, 2),
        target=None, out_path=out,
    )
    assert res == out
    assert (tmp_path / "c2.png").stat().st_size > 1000


def test_render_too_few_bars_returns_none(tmp_path):
    pd = pytest.importorskip("pandas")
    out = str(tmp_path / "c3.png")
    df = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0, 1], "Close": [1, 2]})
    assert render_trade_chart("X", df, out_path=out) is None
