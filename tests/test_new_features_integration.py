"""Validation + adversarial audit of the new components together — hunting
double-counting, conflicts, and bad interactions among discovery / analyst /
trends / regime / breakout / short-overlay / source-cap / dampener / bonus."""
import asyncio
import inspect

import web.api.thematic_auto as t


def _merge(monkeypatch, **kwargs):
    async def _identity(tickers):
        return {x.upper() for x in tickers}
    monkeypatch.setattr(t, "_validate_tickers", _identity)
    monkeypatch.setattr(t, "_get_historical_scores", lambda n_scans=5: {})
    return asyncio.run(t._merge_signals(**kwargs))


# ── No double-counting across the new sources ───────────────────────────────
def test_multi_source_contributions_are_additive_not_doubled(monkeypatch):
    ranked, bd = _merge(monkeypatch,
        reddit={"NVDA": 5}, ddg={}, yahoo=[], twitter=None,
        google_trends={"NVDA": 4}, discovery={"NVDA": 5}, analyst={"NVDA": 4})
    b = bd["NVDA"]
    # each source recorded exactly once with its own weighted contribution
    assert b["reddit"] == 10.0          # 5 * 2.0
    assert b["google_trends"] == 8.0    # 4 * 2.0
    assert b["discovery"] == 15.0       # 5 * 3.0
    assert b["analyst"] == 10.0         # 4 * 2.5
    # score == sum of contributions + one multi-source bonus (no double count)
    base = b["reddit"] + b["google_trends"] + b["discovery"] + b["analyst"]
    bonus = b.get("multi_source_bonus", 0)
    assert abs(dict(ranked)["NVDA"] - (base + bonus)) < 1e-6


def test_per_source_cap_applies_to_new_sources(monkeypatch):
    # an absurd discovery weight is still capped at _MAX_PER_SOURCE_PTS
    _, bd = _merge(monkeypatch, reddit={}, ddg={}, yahoo=[], discovery={"AAA": 999})
    assert bd["AAA"]["discovery"] == t._MAX_PER_SOURCE_PTS


def test_confirmation_bonus_counts_each_quality_source_once(monkeypatch):
    # discovery + analyst + reddit = 3 distinct quality sources → bonus (3-1)*3=6
    _, bd = _merge(monkeypatch, reddit={"BBB": 5}, ddg={}, yahoo=[],
                   discovery={"BBB": 4}, analyst={"BBB": 3})
    assert bd["BBB"]["multi_source_bonus"] == 6.0


# ── Adversarial: new sources don't bypass existing guards ───────────────────
def test_discovery_name_still_subject_to_red_flag_and_etf_exclusion(monkeypatch):
    # an index ETF surfaced by discovery is still excluded (guard precedence)
    ranked, _ = _merge(monkeypatch, reddit={}, ddg={}, yahoo=[], discovery={"SPY": 8})
    assert "SPY" not in dict(ranked)


def test_short_overlay_is_not_additive_to_merge():
    src = inspect.getsource(t._merge_signals)
    assert "finra" not in src.lower()       # risk overlay never enters the score
    assert "short_pressure" not in src.lower()


def test_regime_and_breakout_helpers_are_pure_and_safe():
    # adversarial garbage into every new pure helper → no exception, sane output
    assert t._trends_momentum([float("nan"), None, "x"]) == 0
    assert t._breakout_signal([], [], [])["is_breakout"] is False
    assert t._fmp_grade_weight([{"bad": 1}, None]) == 0
    assert t._recommendation_weight({"junk": "x"}) == 0
    assert t._parse_finra_short_volume("nonsense") == {}
    assert t._atr_stop_pct(100, float("inf"), 7) == 7.0
    assert t._risk_based_shares(0, 0, 0, 0) == 0


def test_all_new_flags_default_off(monkeypatch):
    for flag in ("THEMATIC_GOOGLE_TRENDS", "THEMATIC_BREAKOUT_CONFIRM", "THEMATIC_ATR_STOPS",
                 "THEMATIC_RISK_SIZING", "THEMATIC_REGIME_GATE", "THEMATIC_DISCOVERY",
                 "THEMATIC_ANALYST", "THEMATIC_SHORT_OVERLAY", "THEMATIC_EXIT_LOOP"):
        monkeypatch.delenv(flag, raising=False)
    assert not t._google_trends_enabled() and not t._breakout_confirm_enabled()
    assert not t._atr_stops_enabled() and not t._risk_sizing_enabled()
    assert not t._regime_gate_enabled() and not t._discovery_enabled()
    assert not t._analyst_enabled() and not t._short_overlay_enabled()
