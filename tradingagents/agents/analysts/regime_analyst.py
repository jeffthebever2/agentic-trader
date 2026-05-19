"""Optional market-regime analyst and tools."""

from __future__ import annotations

from langchain_core.tools import tool
import yfinance as yf


@tool
def get_market_regime() -> str:
    """Detect whether the broad market is bull, bear, or choppy."""
    try:
        sp500 = yf.download("^GSPC", period="1y", progress=False, auto_adjust=True)["Close"].dropna()
        sma200 = sp500.rolling(200).mean()
        current = float(sp500.iloc[-1])
        sma = float(sma200.iloc[-1])
        if current > sma * 1.05:
            return "BULL (S&P 500 > 200-day SMA by more than 5%)"
        if current < sma * 0.95:
            return "BEAR (S&P 500 < 200-day SMA by more than 5%)"
        return "CHOPPY (S&P 500 near 200-day SMA)"
    except Exception as exc:
        return f"UNKNOWN (market regime unavailable: {exc})"


@tool
def get_vix_regime() -> str:
    """Classify volatility regime using VIX."""
    try:
        vix = yf.download("^VIX", period="1mo", progress=False, auto_adjust=True)["Close"].dropna()
        current = float(vix.iloc[-1])
        if current < 15:
            return f"LOW_VOL (VIX {current:.1f})"
        if current > 25:
            return f"HIGH_VOL (VIX {current:.1f})"
        return f"NORMAL_VOL (VIX {current:.1f})"
    except Exception as exc:
        return f"UNKNOWN_VOL ({exc})"


@tool
def get_breadth_regime() -> str:
    """Return a simple breadth proxy."""
    return "BREADTH_PROXY_UNAVAILABLE (wire NYSE advance/decline data later)"


def create_regime_analyst(llm):
    def regime_analyst_node(state):
        prompt = (
            "Detect the current market regime and explain how it should change "
            "risk appetite for this stock. Use bull/bear/choppy language."
        )
        result = llm.bind_tools([get_market_regime, get_vix_regime, get_breadth_regime]).invoke([("human", prompt)])
        return {"messages": [result], "regime_report": getattr(result, "content", "")}

    return regime_analyst_node
