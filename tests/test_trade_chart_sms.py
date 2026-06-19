"""Trade-request chart attaches to the SMS as media_url. Flag-gated, injectable
OHLCV, graceful failure. send_sms/send_sendblue carry media_url to Sendblue."""
import asyncio

import pandas as pd
import numpy as np

import web.api.thematic_auto as t


def _df(n=80):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    base = np.linspace(100, 140, n)
    return pd.DataFrame({"Open": base, "High": base + 2, "Low": base - 2,
                         "Close": base + 0.5, "Volume": [1e6] * n}, index=idx)


def test_chart_disabled_by_default(monkeypatch):
    monkeypatch.delenv("THEMATIC_CHART_SMS", raising=False)
    url = asyncio.run(t._generate_signal_chart("MSFT", {"stop_pct": 8, "target_pct": 30},
                                               fetch=lambda tk, period="1y": _df()))
    assert url is None


def test_chart_generates_public_url(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMATIC_CHART_SMS", "true")
    monkeypatch.delenv("THEMATIC_CHART_UPLOAD", raising=False)  # want the tunnel URL path
    monkeypatch.setenv("PUBLIC_DASHBOARD_URL", "https://example.test")
    monkeypatch.setattr(t, "_CHART_DIR", tmp_path)
    url = asyncio.run(t._generate_signal_chart("MSFT", {"stop_pct": 8, "target_pct": 30},
                                               fetch=lambda tk, period="1y": _df()))
    assert url and url.startswith("https://example.test/charts/MSFT_")
    fname = url.rsplit("/", 1)[-1]
    assert (tmp_path / fname).exists() and (tmp_path / fname).stat().st_size > 5000


def test_chart_fetch_failure_graceful(monkeypatch):
    monkeypatch.setenv("THEMATIC_CHART_SMS", "true")
    url = asyncio.run(t._generate_signal_chart("X", {}, fetch=lambda tk, period="1y": None))
    assert url is None


def test_send_sms_carries_media_url(monkeypatch):
    import scripts.sms_alerts as sa
    captured = {}
    def _fake_sb(to, message, media_url=None):
        captured.update(to=to, media_url=media_url)
        return {"success": True}
    monkeypatch.setattr(sa, "send_sendblue", _fake_sb)
    sa.send_sms("+1555", "hi", None, "https://example.test/charts/x.png")
    assert captured["media_url"] == "https://example.test/charts/x.png"


# ── public upload delivery (CF-Access workaround) ────────────────────────────
def test_chart_uses_tunnel_url_when_upload_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMATIC_CHART_SMS", "true")
    monkeypatch.delenv("THEMATIC_CHART_UPLOAD", raising=False)
    monkeypatch.setenv("PUBLIC_DASHBOARD_URL", "https://app.example")
    monkeypatch.setattr(t, "_CHART_DIR", tmp_path)
    url = asyncio.run(t._generate_signal_chart("MSFT", {"stop_pct": 8, "target_pct": 30},
                                               fetch=lambda tk, period="1y": _df()))
    assert url.startswith("https://app.example/charts/")   # tunnel URL


def test_chart_uses_public_upload_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMATIC_CHART_SMS", "true")
    monkeypatch.setenv("THEMATIC_CHART_UPLOAD", "true")
    monkeypatch.setattr(t, "_CHART_DIR", tmp_path)
    url = asyncio.run(t._generate_signal_chart(
        "MSFT", {"stop_pct": 8, "target_pct": 30},
        fetch=lambda tk, period="1y": _df(),
        uploader=lambda path: "https://files.host/abc.png",
    ))
    assert url == "https://files.host/abc.png"


def test_chart_upload_failure_falls_back_to_tunnel(monkeypatch, tmp_path):
    monkeypatch.setenv("THEMATIC_CHART_SMS", "true")
    monkeypatch.setenv("THEMATIC_CHART_UPLOAD", "true")
    monkeypatch.setenv("PUBLIC_DASHBOARD_URL", "https://app.example")
    monkeypatch.setattr(t, "_CHART_DIR", tmp_path)
    url = asyncio.run(t._generate_signal_chart(
        "MSFT", {"stop_pct": 8, "target_pct": 30},
        fetch=lambda tk, period="1y": _df(),
        uploader=lambda path: None,          # upload fails
    ))
    assert url.startswith("https://app.example/charts/")   # graceful fallback
