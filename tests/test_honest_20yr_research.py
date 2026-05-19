import json

import pandas as pd
import pytest

from scripts import honest_20yr_research as H20


def _portfolio(ann, dd=20.0, pf=1.4, n=120):
    return {
        "profit": 1000.0,
        "end": 11000.0,
        "total_ret": 10.0,
        "ann": ann,
        "n": n,
        "wr": 52.0,
        "pf": pf,
        "max_dd": dd,
        "start_date": "2023-07-03",
        "end_date": "2026-05-15",
        "years": 2.87,
    }


@pytest.mark.unit
def test_success_bar_rejects_near_20_percent_result():
    candidate = H20.CandidateResult(
        name="near_miss",
        family="Stock",
        config={},
        full=_portfolio(18.0, n=150),
        train=_portfolio(25.0, n=150),
        test=_portfolio(19.99, n=150),
        cpcv_paths=[6.0, 8.0, 10.0, 12.0, 14.0, 9.0],
    )

    scored = H20._score_candidate(candidate, trial_ann=[-5.0, 0.0, 4.0])

    assert scored.pass_20 is False
    assert any("decays" in reason or "CAGR" in reason for reason in scored.kill_reasons)


@pytest.mark.unit
def test_success_bar_accepts_only_full_qualified_result():
    candidate = H20.CandidateResult(
        name="qualified",
        family="Leveraged ETF",
        config={},
        full=_portfolio(24.0, dd=18.0, pf=1.5, n=90),
        train=_portfolio(23.0, dd=18.0, pf=1.5, n=50),
        test=_portfolio(21.0, dd=22.0, pf=1.3, n=40),
        cpcv_paths=[12.0, 16.0, 19.0, 22.0, 25.0, 18.0, 15.0, 20.0],
    )

    scored = H20._score_candidate(candidate, trial_ann=[-10.0, -5.0, 0.0, 4.0])

    assert scored.pass_20 is True
    assert scored.dsr is not None and scored.dsr >= 0.95
    assert scored.cpcv_median is not None and scored.cpcv_median > 0


@pytest.mark.unit
def test_report_and_manifest_are_blunt_about_failed_20_percent(tmp_path):
    candidate = H20.CandidateResult(
        name="best_honest_but_short",
        family="Stock",
        config={"rule": "example"},
        full=_portfolio(12.0, n=130),
        train=_portfolio(20.0, n=80),
        test=_portfolio(6.0, dd=31.0, pf=1.1, n=70),
        cpcv_paths=[-2.0, 1.0, 4.0, 8.0],
    )
    scored = H20._score_candidate(candidate, trial_ann=[0.0, 5.0, 10.0])

    report = tmp_path / "AUDIT_REPORT.md"
    manifest = tmp_path / "audit_manifest.json"
    H20.write_outputs(
        [scored],
        report_path=report,
        manifest_path=manifest,
        commands=["python3 scripts/honest_20yr_research.py --test"],
        started_at="2026-05-18T00:00:00",
    )

    text = report.read_text(encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "FAIL - 20% is not proven" in text
    assert "Any old percentage not reproduced in this report is stale" in text
    assert data["verdict"].startswith("FAIL")
    assert data["closest_candidate"]["name"] == "best_honest_but_short"


@pytest.mark.unit
def test_daily_metrics_compute_cagr_and_drawdown():
    idx = pd.bdate_range("2024-01-02", periods=260)
    returns = pd.Series(0.001, index=idx)
    returns.iloc[100] = -0.10

    metrics = H20._daily_metrics(returns, n_trades=12)

    assert metrics["n"] == 12
    assert metrics["ann"] > 0
    assert metrics["max_dd"] >= 9.0
