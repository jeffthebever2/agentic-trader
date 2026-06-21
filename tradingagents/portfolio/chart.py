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
    levels, plus honest forward reward/risk bands (entry→target green, stop→entry
    red) and the real reward:risk in the title. entry/stop/target are drawn exactly
    as passed — a missing level is omitted, never fabricated. Fails soft so a render
    error never blocks the trade-request SMS."""
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

        # Use the REAL order levels exactly as given — never fabricate one that
        # wasn't passed (an invented target/stop would mislead a real-money review).
        def _lvl(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if (math.isfinite(f) and f > 0) else None
        e, s, t = _lvl(entry), _lvl(stop), _lvl(target)
        base = e if e is not None else last     # reward/risk measured from entry

        # Expand the y-axis so the real target/stop sit on-chart with headroom.
        try:
            ys = [float(df["Low"].min()), float(df["High"].max()), last]
            ys += [v for v in (e, s, t) if v is not None]
            _lo, _hi = min(ys), max(ys)
            _pad = (_hi - _lo) * 0.06 or 1.0
            ax.set_ylim(_lo - _pad, _hi + _pad)
        except Exception:
            pass

        # ── Honest reward / risk bands (no fabricated price path) ──
        # Shade the forward region only: green between entry→target (reward) and
        # red between stop→entry (risk). This shows the planned trade's actual
        # reward:risk geometry instead of a made-up "dip then rip" trajectory.
        if t is not None and t > base:
            ax.fill_between([x_now, label_x], base, t, color="#16a34a", alpha=0.10, zorder=0)
        if s is not None and s < base:
            ax.fill_between([x_now, label_x], s, base, color="#ef4444", alpha=0.10, zorder=0)

        # ── Big, plain right-edge labels (only for levels we actually have) ──
        def _label(v, text, color):
            if v is None:
                return
            ax.annotate(f"  {text} ${v:,.2f}", xy=(label_x, v), va="center", ha="left",
                        fontsize=13, fontweight="bold", color=color, annotation_clip=False)
        _label(t, "TARGET", "#2563eb")
        _label(e, "ENTRY", "#16a34a")
        _label(s, "STOP", "#ef4444")

        # ── Title + one-line summary with the real reward:risk ──
        rr = (t - e) / (e - s) if (e is not None and s is not None and t is not None and e > s) else None
        gain = f"   →  +{(t / base - 1) * 100:.0f}% to target" if (t is not None and base > 0) else ""
        rr_txt = f"   •   R:R {rr:.1f}" if rr is not None else ""
        ax.set_title(f"{ticker}    ${last:,.2f}{gain}{rr_txt}", fontsize=17, fontweight="bold",
                     loc="left", pad=16, color="#0f172a")
        bits = []
        if e is not None: bits.append(f"Entry ${e:,.2f}")
        if s is not None: bits.append(f"Stop ${s:,.2f}")
        if t is not None: bits.append(f"Target ${t:,.2f}")
        if rr is not None: bits.append(f"R:R {rr:.1f}")
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
