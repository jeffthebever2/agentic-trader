"""Pure correlation concentration guard. Catches hidden concentration the per-name
/ per-theme caps miss (a book of names that all gap together). Network-free."""
from tradingagents.portfolio import correlation as c


def test_pct_returns_and_pearson_identical():
    closes = [100, 101, 102, 101, 103, 104, 103, 105]
    r = c.pct_returns(closes)
    assert len(r) == len(closes) - 1
    assert c.pearson(r, r) == 1.0           # series perfectly correlated with itself


def test_anticorrelated():
    a = [100, 110, 100, 110, 100, 110, 100]
    b = [110, 100, 110, 100, 110, 100, 110]
    corr = c.pearson(c.pct_returns(a), c.pct_returns(b))
    assert corr is not None and corr < -0.9


def test_max_correlation_finds_the_clustered_name():
    cand = [10, 11, 10, 11, 10, 11, 10, 11]
    existing = {
        "TWIN": [20, 22, 20, 22, 20, 22, 20, 22],   # moves with candidate
        "INDEP": [50, 50.1, 50, 49.9, 50, 50.1, 50, 49.9],
    }
    res = c.max_correlation(cand, existing)
    assert res["with_ticker"] == "TWIN" and res["max_corr"] > 0.9


def test_correlation_ok_blocks_highly_correlated():
    cand = [10, 11, 10, 11, 10, 11, 10, 11]
    book = {"TWIN": [20, 22, 20, 22, 20, 22, 20, 22]}
    assert c.correlation_ok(cand, book, max_corr=0.85) is False   # too correlated
    assert c.correlation_ok(cand, {}, max_corr=0.85) is True       # empty book → ok


def test_fail_open_on_thin_data():
    # not enough overlap to compute → advisory guard allows
    assert c.correlation_ok([10, 11], {"X": [1, 2]}) is True


def test_garbage_safe():
    assert c.pearson([float("nan")] * 8, [1, 2, 3, 4, 5, 6, 7, 8]) is None
    assert c.correlation_ok([], {"X": [1, 2, 3, 4, 5, 6]}) is True
