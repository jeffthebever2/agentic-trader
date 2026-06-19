"""Sentiment & Signal Validation — end-to-end scenario tests proving the system
reacts correctly to distinct market behaviors. These encode the expected reaction
(not just unit math) so a regression in any guard surfaces as a failed scenario.
Evidence for the audit's 'system reacts to market behavior' requirement."""
import asyncio

import web.api.thematic_auto as t


def _pick(**kw):
    base = {"ticker": "NVDA", "conviction": 7, "sentiment": 0.0,
            "catalyst": "Q4 earnings + product launch", "crowd_view": "neutral"}
    base.update(kw)
    return t._sanitize_picks([base], {"NVDA"})[0]


def _composite(p):
    return t.composite_score(p["conviction"], 300, p["sentiment"])


# ── 1. Strong bullish conviction → identified strongly, auto-tradeable ───────
def test_strong_buy_scored_high():
    p = _pick(conviction=9, sentiment=0.8, catalyst="signed $2B multi-year contract")
    assert "red_flag" not in p and "weak_catalyst" not in p
    assert _composite(p) >= 80          # clears auto_trade_score (82) territory


# ── 2. Strong selling pressure → bearish-capped, never auto-tradeable ────────
def test_strong_sell_pressure_capped():
    p = _pick(conviction=9, sentiment=-0.8, crowd_view="everyone dumping, sell sell")
    # deep-bearish sentiment hard-caps composite <= 45 even at high conviction/buzz
    assert t.composite_score(p["conviction"], 100000, p["sentiment"]) <= 45


# ── 3. Hard negative catalyst (fraud/probe) → vetoed regardless of buzz ──────
def test_red_flag_vetoes_bullish_number():
    p = _pick(conviction=9, sentiment=0.7, crowd_view="reddit hyping but SEC investigation rumored")
    assert p.get("red_flag") is True
    assert t.composite_score(p["conviction"], 100000, p["sentiment"]) <= 45


# ── 4. Temporary hype (no catalyst) → conviction capped, can't size up ───────
def test_hype_without_catalyst_capped():
    p = _pick(conviction=10, catalyst="momentum", crowd_view="going parabolic")
    assert p.get("weak_catalyst") is True and p["conviction"] <= 6


# ── 5. Pump-and-dump language → vetoed ───────────────────────────────────────
def test_pump_and_dump_vetoed():
    p = _pick(conviction=8, sentiment=0.6, crowd_view="classic pump and dump setup")
    assert p.get("red_flag") is True


# ── 6. Confirmed multi-source beats lone-feed froth (ranking) ────────────────
def _merge(**kwargs):
    async def _identity(tickers):
        return {x.upper() for x in tickers}
    ov, oh = t._validate_tickers, t._get_historical_scores
    t._validate_tickers = _identity
    t._get_historical_scores = lambda n_scans=5: {}
    try:
        return asyncio.run(t._merge_signals(**kwargs))
    finally:
        t._validate_tickers, t._get_historical_scores = ov, oh


def test_confirmed_name_outranks_lone_pump():
    ranked, _ = _merge(
        reddit={"PUMP": 22},                          # lone feed → solo dampener 0.7x
        ddg={"REAL": 10}, yahoo=["REAL"], twitter={"REAL": 8},  # cross-source confirmation
    )
    scores = dict(ranked)
    assert scores["REAL"] > scores["PUMP"]           # confirmation beats a lone pump


# ── 7. Strong buy emerges EARLY via breakout even on a single scan ───────────
def test_breakout_releases_early_entry(monkeypatch):
    monkeypatch.setenv("THEMATIC_BREAKOUT_CONFIRM", "true")
    # a fresh-high on 5x volume confirms the move without waiting for scan #2
    h = [100 + i*0.1 for i in range(25)]; c = [x-0.05 for x in h]; v = [1e6]*25
    h[-1], c[-1], v[-1] = 130, 129, 5e6
    assert t._ticker_breakout("IREN", fetch=lambda tk: {"highs": h, "closes": c, "volumes": v}) is True
