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
        last = float(df["Close"].iloc[-1])

        style = mpf.make_mpf_style(
            base_mpf_style="yahoo",
            facecolor="#f4f7fc", edgecolor="#cbd5e1", gridcolor="#e6ecf5",
            rc={"font.size": 12, "ytick.labelsize": 12, "xtick.labelsize": 10},
        )
        kw = dict(
            type="candle", style=style, mav=mav, volume=has_vol,
            ylabel="", ylabel_lower="", figratio=(16, 9), figscale=1.35,
            tight_layout=True, returnfig=True,
        )
        if hlines:
            kw["hlines"] = dict(hlines=hlines, colors=colors, linewidths=1.4, linestyle="-")
        fig, axlist = mpf.plot(df, **kw)
        ax = axlist[0]  # price panel

        n = len(df)
        future = max(20, int(n * 0.35))
        x_now = n - 1
        label_x = n + future
        ax.set_xlim(-1, label_x + future * 0.30)   # room for right-edge labels

        # ── Expected-path arrow: a shallow dip to the buy zone, then up to target
        # (the 'down then up' pattern). Always resolves to the target for a long. ──
        tgt = float(target) if (target and math.isfinite(float(target)) and float(target) > 0) else last * 1.25

        # Expand the y-axis so the TARGET (often far above the recent range) and the
        # full projection arrow are visible — like the reference charts' headroom.
        try:
            _lo = min(float(df["Low"].min()), float(stop) if stop else last, tgt)
            _hi = max(float(df["High"].max()), tgt)
            _pad = (_hi - _lo) * 0.06 or 1.0
            ax.set_ylim(_lo - _pad, _hi + _pad)
        except Exception:
            pass
        # Dip to a SHALLOW buy zone (~3-4% pullback), never down to the stop and
        # never above current — a minor pullback before the run, not a stop-out.
        _stop_floor = float(stop) if (stop and math.isfinite(float(stop)) and 0 < float(stop) < last) else 0.0
        dip = max(last * 0.965, _stop_floor + (last - _stop_floor) * 0.35 if _stop_floor else last * 0.965)
        dip = min(dip, last * 0.995)               # always slightly below current
        dip_x, top_x = x_now + future * 0.28, x_now + future * 0.92
        ax.plot([x_now, dip_x], [last, dip], color="#0f172a", lw=3.2,
                solid_capstyle="round", zorder=12)                       # leg 1: dip
        ax.annotate("", xy=(top_x, tgt), xytext=(dip_x, dip),
                    arrowprops=dict(arrowstyle="-|>", color="#0f172a", lw=3.2,
                                    mutation_scale=30), zorder=12)        # leg 2: up to target

        # ── Big, plain right-edge labels ──
        def _label(y, text, color):
            try:
                v = float(y)
            except (TypeError, ValueError):
                return
            if math.isfinite(v) and v > 0:
                ax.annotate(f"  {text} ${v:,.2f}", xy=(label_x, v), va="center", ha="left",
                            fontsize=13, fontweight="bold", color=color, annotation_clip=False)
        _label(tgt, "TARGET", "#2563eb")
        _label(entry, "ENTRY", "#16a34a")
        _label(stop, "STOP", "#ef4444")

        # ── Clear title + one-line plain-language summary ──
        gain = f"   →  +{(tgt / last - 1) * 100:.0f}% to target" if last > 0 else ""
        ax.set_title(f"{ticker}    ${last:,.2f}{gain}", fontsize=17, fontweight="bold",
                     loc="left", pad=16, color="#0f172a")
        bits = []
        if entry: bits.append(f"Entry ${float(entry):,.2f}")
        if stop:  bits.append(f"Stop ${float(stop):,.2f}")
        if target: bits.append(f"Target ${float(target):,.2f}")
        if bits:
            fig.text(0.5, 0.94, "   •   ".join(bits), ha="center", fontsize=12, color="#334155")

        fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        try:
            import matplotlib.pyplot as _plt
            _plt.close(fig)
        except Exception:
            pass
        return out_path
    except Exception:
        return None
