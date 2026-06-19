"""Trade-request chart generator — TradingView-style PNGs for the SMS attachment.

Renders a candlestick chart (MA50/200, fib levels, entry/stop/target lines) so a
trade-request text carries a readable picture, not just numbers. The level math is
pure and unit-testable; rendering uses mplfinance on the Agg backend (headless).
Network-free: the caller injects the OHLCV DataFrame.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def _finite(xs: Sequence) -> list[float]:
    out: list[float] = []
    for x in xs or []:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def compute_levels(highs: Sequence[float], lows: Sequence[float],
                   closes: Sequence[float], *, lookback: int = 120) -> dict:
    """Key horizontal levels from the recent range — swing high/low + Fibonacci
    retracements (0.5 / 0.618 / 0.786) and the 1.618 extension. Pure.

    Retracements are measured from the recent swing LOW up to the swing HIGH, so
    they sit between them (support on a pullback); 1.618 is the upside extension —
    matching the TradingView reference charts."""
    h, l, c = _finite(highs), _finite(lows), _finite(closes)
    if not h or not l or not c:
        return {}
    hi = max(h[-lookback:])
    lo = min(l[-lookback:])
    if hi <= lo:
        return {}
    rng = hi - lo
    return {
        "swing_high": round(hi, 2),
        "swing_low": round(lo, 2),
        "fib_500": round(hi - 0.500 * rng, 2),
        "fib_618": round(hi - 0.618 * rng, 2),
        "fib_786": round(hi - 0.786 * rng, 2),
        "ext_1618": round(lo + 1.618 * rng, 2),
        "last": round(c[-1], 2),
    }


def render_trade_chart(
    ticker: str,
    df,
    *,
    entry: Optional[float] = None,
    stop: Optional[float] = None,
    target: Optional[float] = None,
    out_path: str,
    levels: Optional[dict] = None,
) -> Optional[str]:
    """Render a TradingView-style PNG to out_path and return it (None on failure).

    df: pandas DataFrame with a DatetimeIndex and Open/High/Low/Close[/Volume].
    Draws candles + MA50/200 + entry(green)/stop(red)/target(blue) lines + fib
    levels. Fails soft so a render error never blocks the trade-request SMS."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless
        import mplfinance as mpf
    except Exception:
        return None
    try:
        if df is None or len(df) < 5:
            return None
        need = {"Open", "High", "Low", "Close"}
        if not need.issubset(set(df.columns)):
            return None

        hlines, colors = [], []
        def _add(level, color):
            try:
                v = float(level)
            except (TypeError, ValueError):
                return
            if math.isfinite(v) and v > 0:
                hlines.append(v)
                colors.append(color)

        _add(entry, "#16a34a")          # entry — green
        _add(stop, "#ef4444")           # stop — red
        _add(target, "#2563eb")         # target — blue
        if levels:
            for k in ("fib_618", "fib_786", "ext_1618"):
                _add(levels.get(k), "#9ca3af")   # fib — grey

        mav = tuple(m for m in (50, 200) if len(df) >= m) or ()
        has_vol = "Volume" in df.columns

        style = mpf.make_mpf_style(
            base_mpf_style="yahoo",
            facecolor="#eef3fb", edgecolor="#cbd5e1", gridcolor="#dbe3ef",
            rc={"axes.labelsize": 9, "font.size": 9},
        )
        kw = dict(
            type="candle", style=style, mav=mav, volume=has_vol,
            title=f"\n{ticker}  ({levels.get('last') if levels else ''})",
            ylabel="", ylabel_lower="", figratio=(16, 9), figscale=1.1,
            tight_layout=True, savefig=dict(fname=out_path, dpi=110, bbox_inches="tight"),
        )
        if hlines:
            kw["hlines"] = dict(hlines=hlines, colors=colors, linewidths=1.1, linestyle="-")
        mpf.plot(df, **kw)
        return out_path
    except Exception:
        return None
