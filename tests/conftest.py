"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "ALPHA_VANTAGE_API_KEY",
    "FMP_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, os.environ.get(env_var, "placeholder"))


@pytest.fixture(autouse=True)
def _isolate_supabase(monkeypatch):
    """Never let unit tests hit the real Supabase project.

    web/app.py loads .env with override=True at import time, which can
    re-inject live SUPABASE_* creds even after we delete them. Patch the
    backend's `enabled()` to False so the user/portfolio stores always use
    their local JSON fallback. Tests that want Supabase behavior re-patch
    `enabled` themselves.
    """
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    try:
        from web import supabase_store
        monkeypatch.setattr(supabase_store, "enabled", lambda: False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_alert_cooldown(monkeypatch, tmp_path):
    """Keep the per-ticker alert cooldown out of the real tmp/alert_cooldown.json.

    Each test gets its own empty cooldown file so cooldown state never leaks
    between tests (a recorded alert in one test would otherwise suppress an
    expected SMS in another).
    """
    try:
        from web import alert_cooldown
        monkeypatch.setattr(alert_cooldown, "_FILE", tmp_path / "alert_cooldown.json")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _ai_off_by_default(monkeypatch, tmp_path):
    """AI intent/validation/catalyst/red-flag calls are network — default them OFF
    in tests (placeholder API keys would otherwise enable real calls in unrelated
    thematic tests). Tests that exercise the AI path opt in explicitly.
    Also isolate the AI-backed caches to a temp dir so nothing persists."""
    monkeypatch.setenv("THEMATIC_AI_INTENT", "false")
    monkeypatch.setenv("THEMATIC_AI_EXIT_CHECK", "false")
    monkeypatch.setenv("HOLDINGS_BRAIN_LLM", "false")
    try:
        import web.api.thematic_auto as _ta
        monkeypatch.setattr(_ta, "_TICKER_VALID_FILE", tmp_path / "ticker_valid.json")
        monkeypatch.setattr(_ta, "_ticker_valid_cache", None)
        monkeypatch.setattr(_ta, "_SECTOR_CACHE_FILE", tmp_path / "sector_cache.json")
        monkeypatch.setattr(_ta, "_NEURON_USAGE_FILE", tmp_path / "neu.json")
        monkeypatch.setattr(_ta, "_OR_USAGE_FILE", tmp_path / "or.json")
    except Exception:
        pass


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
