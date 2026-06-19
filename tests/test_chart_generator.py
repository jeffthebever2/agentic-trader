"""Trade-request chart generator. Pure level math (fib retracements + extension)
unit-tested; the mplfinance render is exercised to confirm it produces a real PNG
and fails soft on bad input."""
import os

from tradingagents.portfolio import chart as ch


def test_compute_levels_fib_ordering():
    # range 100..200: fibs sit between, 1.618 extension above
    highs = [200] * 5 + [180] * 5
    lows = [100] * 5 + [120] * 5
    closes = [150] * 10
    lv = ch.compute_levels(highs, lows, closes)
    assert lv["swing_high"] == 200 and lv["swing_low"] == 100
    assert lv["fib_500"] == 150.0
    assert lv["fib_618"] == 138.2
    assert lv["fib_786"] == 121.4
    assert lv["ext_1618"] == 261.8         # 100 + 1.618*100
    # ordering: low < 786 < 618 < 500 < high < extension
    assert lv["swing_low"] < lv["fib_786"] < lv["fib_618"] < lv["fib_500"] < lv["swing_high"] < lv["ext_1618"]


def test_compute_levels_empty_safe():
    assert ch.compute_levels([], [], []) == {}
    assert ch.compute_levels([100], [100], [100]) == {}   # hi == lo


def test_render_produces_png(tmp_path):
    import pandas as pd
    import numpy as np
    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = np.linspace(100, 140, n)
    df = pd.DataFrame({
        "Open": base, "High": base + 2, "Low": base - 2, "Close": base + 0.5,
        "Volume": [1_000_000] * n,
    }, index=idx)
    out = str(tmp_path / "chart.png")
    lv = ch.compute_levels(df["High"].tolist(), df["Low"].tolist(), df["Close"].tolist())
    res = ch.render_trade_chart("TEST", df, entry=138, stop=128, target=160, out_path=out, levels=lv)
    assert res == out
    assert os.path.exists(out) and os.path.getsize(out) > 5000   # a real image


def test_render_fails_soft_on_bad_df(tmp_path):
    out = str(tmp_path / "x.png")
    assert ch.render_trade_chart("T", None, out_path=out) is None
    import pandas as pd
    bad = pd.DataFrame({"foo": [1, 2, 3]})
    assert ch.render_trade_chart("T", bad, out_path=out) is None
