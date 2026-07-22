"""Startup preflight — dangerous configuration COMBINATIONS must fail closed.

Every individual flag in this system is validated somewhere. The combinations
are what kill you, and each dangerous combination used to boot cleanly and
report healthy:

  * live trading armed with no stop watcher → real positions, 0 stop checks/day;
  * live trading armed with no trusted quote provider → every entry AND EXIT
    503s, so positions cannot be closed while the system looks alive;
  * autonomous entry armed with no watcher;
  * multi-instance allowed while every order lock is in-process.

A CRITICAL finding latches live execution off at the compliance layer, so the
failure mode is "refuses to trade real money" rather than "trades unsafely".
"""
from __future__ import annotations

import pytest

from tradingagents import compliance
from tradingagents.preflight import (
    SEVERITY_CRITICAL, SEVERITY_WARNING, format_findings, run_preflight,
)

#: A configuration that is safe for real money — every check satisfied.
SAFE_LIVE = {
    "LIVE_TRADING_ENABLED": "true",
    "FIDELITY_LOCAL_EXECUTION_ENABLED": "true",
    "FIDELITY_BROWSER_DISABLED": "false",
    "HOLDINGS_BRAIN_ENABLED": "true",
    "FMP_API_KEY": "x" * 32,
    "STEP_UP_SECRET": "y" * 64,
    "CF_ACCESS_REQUIRED": "true",
    "FIDELITY_PROTECTED_ACCOUNTS": "262502469",
}


def _codes(result) -> set:
    return {f.code for f in result.findings}


def _critical_codes(result) -> set:
    return {f.code for f in result.critical}


@pytest.mark.unit
def test_a_fully_correct_live_config_is_safe():
    r = run_preflight(SAFE_LIVE)
    assert r.safe_for_live_trading, format_findings(r)
    assert not r.critical


# ── the headline combination ──────────────────────────────────────────────────

@pytest.mark.unit
def test_live_trading_without_a_stop_watcher_is_critical():
    """HOLDINGS_BRAIN_ENABLED gates the exit guard, the holdings brain AND the
    standalone runner. With it off while orders can be placed, nothing evaluates
    a stop — not slowly, ZERO times per day."""
    env = dict(SAFE_LIVE, HOLDINGS_BRAIN_ENABLED="false")
    r = run_preflight(env)
    assert "LIVE_WITHOUT_STOP_WATCHER" in _critical_codes(r)
    assert not r.safe_for_live_trading


@pytest.mark.unit
def test_stop_watcher_check_respects_every_route_to_live_money():
    """Live money needs LIVE_TRADING_ENABLED *and* local execution *and* the
    browser. Turning off any one of them makes the combination harmless."""
    for disarm in ({"LIVE_TRADING_ENABLED": "false"},
                   {"FIDELITY_LOCAL_EXECUTION_ENABLED": "false"},
                   {"FIDELITY_BROWSER_DISABLED": "true"}):
        env = dict(SAFE_LIVE, HOLDINGS_BRAIN_ENABLED="false", **disarm)
        r = run_preflight(env)
        assert "LIVE_WITHOUT_STOP_WATCHER" not in _critical_codes(r), disarm


@pytest.mark.unit
def test_hard_block_downgrades_live_findings():
    """On a hard-blocked box no real order can be placed, so live-money
    combinations are informational rather than blocking."""
    env = dict(SAFE_LIVE, HOLDINGS_BRAIN_ENABLED="false")
    r = run_preflight(env, hard_blocked=True)
    assert not r.critical
    assert r.safe_for_live_trading


# ── the other criticals ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_no_trusted_quote_provider_is_critical():
    """PreTradeGate requires a trusted quote. With no provider key the gateway
    can only offer yfinance, which is untrusted for execution — so every entry
    AND every exit 503s and positions cannot be closed."""
    env = dict(SAFE_LIVE)
    for k in ("FMP_API_KEY", "FINNHUB_API_KEY", "TWELVE_DATA_API_KEY", "TWELVEDATA_API_KEY"):
        env.pop(k, None)
    r = run_preflight(env)
    assert "NO_TRUSTED_QUOTE_PROVIDER" in _critical_codes(r)


@pytest.mark.unit
@pytest.mark.parametrize("key", ["FMP_API_KEY", "FINNHUB_API_KEY", "TWELVE_DATA_API_KEY"])
def test_any_single_trusted_provider_satisfies_the_check(key):
    env = {k: v for k, v in SAFE_LIVE.items() if k != "FMP_API_KEY"}
    env[key] = "z" * 32
    assert "NO_TRUSTED_QUOTE_PROVIDER" not in _critical_codes(run_preflight(env))


@pytest.mark.unit
def test_missing_step_up_secret_is_critical():
    env = dict(SAFE_LIVE); env.pop("STEP_UP_SECRET")
    assert "STEP_UP_SECRET_MISSING" in _critical_codes(run_preflight(env))


@pytest.mark.unit
def test_disabling_single_instance_lock_is_critical_when_live():
    """Order locks, the paper-state lock and alert cooldowns are all in-process.
    A second worker means duplicate live orders."""
    env = dict(SAFE_LIVE, WEB_SINGLE_INSTANCE_LOCK="false")
    assert "SINGLE_INSTANCE_LOCK_DISABLED" in _critical_codes(run_preflight(env))


@pytest.mark.unit
def test_single_instance_lock_defaults_to_on_when_unset():
    assert "SINGLE_INSTANCE_LOCK_DISABLED" not in _codes(run_preflight(SAFE_LIVE))


@pytest.mark.unit
def test_autonomous_entry_without_watcher_is_critical():
    env = dict(SAFE_LIVE, HOLDINGS_BRAIN_ENABLED="false",
               COPYTRADE_ENABLED="true", COPYTRADE_AUTONOMOUS="true")
    assert "AUTONOMOUS_ENTRY_WITHOUT_WATCHER" in _critical_codes(run_preflight(env))


@pytest.mark.unit
def test_auth_posture_never_latches_trading_off():
    """AUTH_NOT_REQUIRED is a web-auth concern, not an order-safety invariant.

    As a CRITICAL it would latch entries off, so unsetting one unrelated env var
    would stop the system trading. Severity here decides whether real money keeps
    moving — reserve CRITICAL for things that make TRADING unsafe.
    """
    for extra in ({}, {"LIVE_TRADING_ENABLED": "false"}):
        r = run_preflight(dict(SAFE_LIVE, CF_ACCESS_REQUIRED="false", **extra))
        assert "AUTH_NOT_REQUIRED" not in _critical_codes(r)
        assert "AUTH_NOT_REQUIRED" in {f.code for f in r.warnings}
        assert r.safe_for_live_trading, "auth posture must not disarm trading"


# ── warnings ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_entries_without_exits_warns():
    env = dict(SAFE_LIVE, THEMATIC_AUTO_SCAN="true", THEMATIC_EXIT_LOOP="false")
    assert "ENTRIES_WITHOUT_EXITS" in {f.code for f in run_preflight(env).warnings}


@pytest.mark.unit
def test_orphaned_autonomous_flag_warns():
    env = dict(SAFE_LIVE, COPYTRADE_AUTONOMOUS="true", COPYTRADE_ENABLED="false")
    assert "COPYTRADE_AUTONOMOUS_ORPHANED" in {f.code for f in run_preflight(env).warnings}


@pytest.mark.unit
def test_copytrade_on_frictionless_leaderboard_warns():
    env = dict(SAFE_LIVE, COPYTRADE_ENABLED="true", PAPER_SLIPPAGE_BPS="0")
    assert "COPYTRADE_ON_FRICTIONLESS_LEADERBOARD" in {f.code for f in run_preflight(env).warnings}
    ok = dict(SAFE_LIVE, COPYTRADE_ENABLED="true", PAPER_SLIPPAGE_BPS="15")
    assert "COPYTRADE_ON_FRICTIONLESS_LEADERBOARD" not in _codes(run_preflight(ok))


# ── unset must never read as safely-on ────────────────────────────────────────

@pytest.mark.unit
def test_empty_environment_does_not_claim_live_safety():
    """A blank env must not produce live-money criticals (nothing is armed) and
    must not silently look like a validated live configuration."""
    r = run_preflight({})
    assert "LIVE_WITHOUT_STOP_WATCHER" not in _critical_codes(r)
    assert "AUTH_NOT_REQUIRED" in _codes(r)


@pytest.mark.unit
@pytest.mark.parametrize("spelling", ["true", "TRUE", "1", "yes", "on", " true "])
def test_flag_parsing_matches_env_bool_dialect(spelling):
    env = dict(SAFE_LIVE, HOLDINGS_BRAIN_ENABLED="false",
               LIVE_TRADING_ENABLED=spelling)
    assert "LIVE_WITHOUT_STOP_WATCHER" in _critical_codes(run_preflight(env))


# ── the latch actually blocks orders ──────────────────────────────────────────

@pytest.mark.unit
def test_preflight_latch_blocks_live_execution_at_the_validator():
    """A CRITICAL finding must not merely log — it has to stop real orders, and
    at the VALIDATOR so no endpoint can bypass it by forgetting the check."""
    order = {
        "symbol": "AAPL", "action": "Buy", "broker": "fidelity",
        "order_type": "Limit", "quantity": 1, "limit_price": 10.0,
        "execute": True, "quote_price": 10.0, "quote_source": "fmp",
    }
    compliance.clear_preflight_block()
    try:
        compliance.block_live_trading_for_preflight("LIVE_WITHOUT_STOP_WATCHER")
        decision = compliance.validate_live_order(order)
        assert decision.allowed is False
        assert "preflight" in decision.reason.lower()
        assert "LIVE_WITHOUT_STOP_WATCHER" in decision.reason
    finally:
        compliance.clear_preflight_block()


@pytest.mark.unit
def test_preflight_latch_never_blocks_a_sell():
    """The latch stops NEW RISK, never the ability to shed it.

    An unsafe configuration is a reason to stop opening positions — it is never a
    reason to trap the ones you already hold. Blocking sells would also disable
    production_safety's force-flatten, which routes through this same validator,
    turning a config typo into an inability to reduce exposure.
    """
    sell = {
        "symbol": "AAPL", "action": "Sell", "broker": "fidelity",
        "order_type": "Limit", "quantity": 1, "limit_price": 10.0,
        "execute": True, "quote_price": 10.0, "quote_source": "fmp",
    }
    compliance.clear_preflight_block()
    try:
        compliance.block_live_trading_for_preflight("LIVE_WITHOUT_STOP_WATCHER")
        reason = compliance.validate_live_order(sell).reason or ""
        assert "preflight" not in reason.lower(), (
            "the preflight latch must never block an exit — that traps risk"
        )
        # And a BUY in the same state is still refused.
        buy = dict(sell, action="Buy")
        assert compliance.validate_live_order(buy).allowed is False
    finally:
        compliance.clear_preflight_block()


@pytest.mark.unit
def test_latch_does_not_gate_the_master_toggle():
    """`live_trading_enabled()` is consulted by the EXIT endpoints, so latching it
    would block exits before the validator ever saw the order's side."""
    compliance.clear_preflight_block()
    try:
        import os
        os.environ["LIVE_TRADING_ENABLED"] = "true"
        compliance.block_live_trading_for_preflight("X")
        assert compliance.live_trading_enabled() is True
    finally:
        compliance.clear_preflight_block()
        os.environ.pop("LIVE_TRADING_ENABLED", None)


@pytest.mark.unit
def test_latch_does_not_block_previews():
    """Sizing/preview (execute=False) must still work so the operator can see
    what the system *would* do while fixing the configuration."""
    compliance.clear_preflight_block()
    try:
        compliance.block_live_trading_for_preflight("X")
        d = compliance.validate_live_order({
            "symbol": "AAPL", "action": "Buy", "broker": "fidelity",
            "order_type": "Limit", "quantity": 1, "limit_price": 10.0,
            "execute": False,
        })
        assert "preflight" not in (d.reason or "").lower()
    finally:
        compliance.clear_preflight_block()


@pytest.mark.unit
def test_latch_is_off_by_default_so_tests_and_cli_are_unaffected():
    compliance.clear_preflight_block()
    assert compliance.preflight_block_reason() == ""


@pytest.mark.unit
def test_latch_can_only_restrict_never_permit(monkeypatch):
    """With the master toggle off, clearing the latch must NOT enable trading."""
    compliance.clear_preflight_block()
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    assert compliance.live_trading_enabled() is False


# ── operator-facing config must stay documented ───────────────────────────────

@pytest.mark.unit
def test_every_trading_env_var_is_documented_in_env_example():
    """You cannot configure what is not written down.

    .env.example previously documented 103 of 210 variables and NOT ONE trading
    flag — which is how a deployment ends up with live trading armed and no stop
    watcher. Any new trading knob must ship with an entry here.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pat = re.compile(
        r"""(?:os\.)?(?:getenv|environ\.get)\(\s*["']([A-Z0-9_]+)["']"""
        r"""|env_(?:bool|int|float|str|list)\(\s*["']([A-Z0-9_]+)["']"""
    )
    read = set()
    for pkg in ("tradingagents", "web", "scripts"):
        for path in (root / pkg).rglob("*.py"):
            for m in pat.finditer(path.read_text(errors="ignore")):
                read.add(m.group(1) or m.group(2))

    documented = set()
    for line in (root / ".env.example").read_text(errors="ignore").splitlines():
        s = line.strip().lstrip("#").strip()
        if re.match(r"^[A-Z0-9_]+=", s):
            documented.add(s.split("=", 1)[0])

    trading = {"THEMATIC", "HOLDINGS", "COPYTRADE", "LIVE", "SIZER",
               "EXIT", "QUOTE", "FIDELITY", "PAPER", "DIVERS", "WEBULL"}
    missing = sorted(
        v for v in (read - documented)
        if any(tok in v for tok in trading)
    )
    assert not missing, (
        "Trading env vars read by code but absent from .env.example — an "
        "operator cannot configure what is not documented:\n  "
        + "\n  ".join(missing)
    )
