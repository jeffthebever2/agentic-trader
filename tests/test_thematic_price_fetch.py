"""_fetch_prices must only cache real, usable prices. A 0 / negative / inf / NaN
quote must never enter the cache — downstream it becomes an entry price and
position-dollar denominator, so garbage would corrupt sizing and P&L math."""
import numpy as np
import pandas as pd

import web.api.thematic_portfolio as tp


def _fake_download(_tickers, **_kw):
    # Mimic yfinance multi-ticker frame: columns are a ("Close", ticker) MultiIndex.
    close = pd.DataFrame({
        "AAA": [1.0, 2.0],          # good → cached as 2.0
        "BAD0": [0.0, 0.0],         # zero → rejected
        "BADNAN": [np.nan, np.nan], # all-NaN → rejected
        "BADINF": [1.0, np.inf],    # inf → rejected
    })
    return pd.concat({"Close": close}, axis=1)


def test_fetch_prices_rejects_unusable(monkeypatch):
    tp._price_cache.clear()
    import yfinance as yf
    monkeypatch.setattr(yf, "download", _fake_download)

    out = tp._fetch_prices(["AAA", "BAD0", "BADNAN", "BADINF"])

    assert out.get("AAA") == 2.0
    for bad in ("BAD0", "BADNAN", "BADINF"):
        assert bad not in out
        assert bad not in tp._price_cache
