"""Stale-while-revalidate snapshot cache for Fidelity holdings (display reads).
The Broker page must load instantly from the last snapshot, not spin up Playwright.
"""
import asyncio
import json

import pytest

import web.api.fidelity as fid

EMAIL = "cachetester@example.com"


@pytest.fixture(autouse=True)
def _tmp_snapshots(tmp_path, monkeypatch):
    # redirect snapshot files into a throwaway dir
    monkeypatch.setattr(fid, "_snapshot_path",
                        lambda email, kind: tmp_path / f"fidelity_{kind}_{email}.json")
    yield


def test_snapshot_roundtrip_and_meta():
    fid._write_snapshot(EMAIL, "positions", {"positions": [{"symbol": "NVDA"}], "count": 1})
    got = fid._read_snapshot(EMAIL, "positions")
    assert got["count"] == 1 and got["positions"][0]["symbol"] == "NVDA"
    assert "scraped_at" in got
    meta = fid._snapshot_meta(got)
    assert meta["cached"] is True and meta["stale"] is False and meta["age_seconds"] >= 0


def test_positions_served_from_cache_without_scraping(monkeypatch):
    fid._write_snapshot(EMAIL, "positions", {"positions": [{"symbol": "TSM"}], "count": 1})

    async def _boom(email):
        raise AssertionError("must NOT scrape when a fresh snapshot exists")
    monkeypatch.setattr(fid, "_scrape_positions", _boom)

    out = asyncio.run(fid.fidelity_positions({"email": EMAIL}))
    assert out["cached"] is True
    assert out["count"] == 1 and out["positions"][0]["symbol"] == "TSM"
    assert out["stale"] is False


def test_stale_snapshot_served_then_revalidates(monkeypatch):
    # write a snapshot then age it past the TTL
    fid._write_snapshot(EMAIL, "positions", {"positions": [], "count": 0})
    snap = fid._read_snapshot(EMAIL, "positions")
    snap["scraped_at"] = snap["scraped_at"] - (fid._POS_CACHE_TTL + 100)
    fid._snapshot_path(EMAIL, "positions").write_text(json.dumps(snap))

    scraped = {"n": 0}
    async def _fresh(email):
        scraped["n"] += 1
        return {"positions": [{"symbol": "AMD"}], "count": 1, "grid_loaded": True}
    monkeypatch.setattr(fid, "_scrape_positions", _fresh)

    async def run():
        out = await fid.fidelity_positions({"email": EMAIL})   # stale → serve + bg refresh
        assert out["cached"] is True and out["stale"] is True
        await asyncio.sleep(0.05)                              # let the bg task run
        return out
    asyncio.run(run())
    assert scraped["n"] == 1                                   # revalidated in background
    # next read now returns the refreshed snapshot
    fresh = fid._read_snapshot(EMAIL, "positions")
    assert fresh["count"] == 1 and fresh["positions"][0]["symbol"] == "AMD"


def test_force_refresh_scrapes(monkeypatch):
    fid._write_snapshot(EMAIL, "positions", {"positions": [{"symbol": "OLD"}], "count": 1})
    async def _fresh(email):
        return {"positions": [{"symbol": "NEW"}], "count": 1, "grid_loaded": True}
    monkeypatch.setattr(fid, "_scrape_positions", _fresh)
    out = asyncio.run(fid.fidelity_positions({"email": EMAIL}, refresh=True))
    assert out["cached"] is False and out["positions"][0]["symbol"] == "NEW"


def test_scrape_failure_serves_last_good_snapshot(monkeypatch):
    from fastapi import HTTPException
    fid._write_snapshot(EMAIL, "positions", {"positions": [{"symbol": "KEEP"}], "count": 1})
    # age it stale so the auto path tries to revalidate (and fails)
    snap = fid._read_snapshot(EMAIL, "positions")
    snap["scraped_at"] = 0
    fid._snapshot_path(EMAIL, "positions").write_text(json.dumps(snap))

    async def _fail(email):
        raise HTTPException(status_code=503, detail="grid did not load")
    monkeypatch.setattr(fid, "_scrape_positions", _fail)
    # auto path: serves stale snapshot, schedules a bg revalidate that fails silently
    out = asyncio.run(fid.fidelity_positions({"email": EMAIL}))
    assert out["positions"][0]["symbol"] == "KEEP"   # last good snapshot preserved
