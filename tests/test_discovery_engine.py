"""Price/volume discovery engine (pure) — the IREN-at-$5 solver. Validates RS,
volume expansion, 52w-high proximity, accumulation, the composite, and the
candidate gate (requires a real breakout: near/at highs + volume + outperformance)."""
from tradingagents.portfolio import discovery as d


def _rising_bars(n=120, start=10.0, step=0.2, vol=1_000_000.0):
    closes = [start + step * i for i in range(n)]
    highs = [c + 0.1 for c in closes]
    vols = [vol] * n
    return {"closes": closes, "highs": highs, "volumes": vols}


def _flat_bench(n=120, level=400.0):
    return [level] * n


def test_relative_strength():
    strong = list(range(10, 130))            # +1100%-ish
    bench = [100 + 0.01 * i for i in range(120)]
    assert d.relative_strength(strong, bench) > 1.0
    assert d.relative_strength([100] * 10, bench) is None   # thin


def test_volume_expansion():
    v = [1e6] * 25
    v[-1] = 5e6
    assert abs(d.volume_expansion(v) - 5.0) < 1e-6
    assert d.volume_expansion([1e6, 2e6]) is None


def test_high_proximity_and_new_high():
    closes = [10 + i for i in range(60)]     # steadily up → today is the high
    highs = [c + 0.1 for c in closes]
    hp = d.high_proximity(closes, highs)
    assert hp["new_high"] is True and hp["pct_of_high"] >= 1.0


def test_accumulation_positive_on_up_volume():
    # up days carry big volume, down days small → strong accumulation
    closes = [10, 11, 12, 11, 13, 14, 13, 15, 16, 17] * 3
    vols = [2e6 if closes[i] > closes[i-1] else 1e5 for i in range(len(closes))]
    vols[0] = 1e6
    assert d.accumulation(closes, vols) > 0.3


def test_discovery_score_bounds_and_degradation():
    assert 0 <= d.discovery_score(rs=None, vol_exp=None, high_prox=0.5, accum=0) <= 100
    hot = d.discovery_score(rs=1.4, vol_exp=4.0, high_prox=1.01, accum=0.8)
    cold = d.discovery_score(rs=0.9, vol_exp=1.0, high_prox=0.7, accum=-0.8)
    assert hot > 80 and cold < 20


def test_breakout_name_qualifies():
    bars = _rising_bars()
    bars["volumes"][-1] = 4_000_000.0        # volume surge on the breakout day
    res = d.is_discovery_candidate(bars, _flat_bench())
    assert res["qualifies"] is True
    assert res["new_high"] is True and res["vol_expansion"] >= 1.5


def test_quiet_drift_without_volume_rejected():
    bars = _rising_bars()                     # new highs but NO volume surge
    res = d.is_discovery_candidate(bars, _flat_bench())
    assert res["qualifies"] is False          # needs volume expansion too


def test_laggard_without_outperformance_rejected():
    bars = _rising_bars()
    bars["volumes"][-1] = 4_000_000.0
    strong_bench = list(range(10, 130))       # benchmark up as much → RS ~1, no edge
    res = d.is_discovery_candidate(bars, [float(x) for x in strong_bench])
    assert res["qualifies"] is False


def test_garbage_bars_safe():
    res = d.is_discovery_candidate({"closes": [], "highs": [], "volumes": []}, [])
    assert res["qualifies"] is False
