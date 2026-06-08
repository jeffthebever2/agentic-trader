import json

from web.api import settings


def test_settings_split_writes_safe_key_and_proposes_risky_key(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openrouter\n"
        "LIVE_TRADING_ENABLED=false\n",
        encoding="utf-8",
    )
    cc_path = tmp_path / "change_control.jsonl"

    monkeypatch.setattr(settings, "ROOT", tmp_path)

    safe, proposals = settings._split_change_controlled_updates(
        {
            "LLM_PROVIDER": "cloudflare",
            "LIVE_TRADING_ENABLED": "true",
        },
        proposed_by="unit-test",
        cc_path=cc_path,
    )

    assert safe == {"LLM_PROVIDER": "cloudflare"}
    assert len(proposals) == 1
    assert proposals[0]["key"] == "LIVE_TRADING_ENABLED"
    assert proposals[0]["setting"] == "live_trading_enabled"

    lines = cc_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["setting"] == "live_trading_enabled"
    assert record["current_value"] == "false"
    assert record["proposed_value"] == "true"
    assert record["status"] == "pending"


def test_settings_split_does_not_propose_unchanged_risky_key(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("TRADINGAGENTS_MAX_POSITIONS=5\n", encoding="utf-8")
    cc_path = tmp_path / "change_control.jsonl"

    monkeypatch.setattr(settings, "ROOT", tmp_path)

    safe, proposals = settings._split_change_controlled_updates(
        {"TRADINGAGENTS_MAX_POSITIONS": "5"},
        proposed_by="unit-test",
        cc_path=cc_path,
    )

    assert safe == {}
    assert proposals == []
    assert not cc_path.exists()
