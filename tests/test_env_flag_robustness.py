"""Feature-flag parsing must never silently disarm a trading loop.

Two dialects of env-boolean coercion coexisted in this repo:

  * permissive:  ``raw.strip().lower() in ("1","true","yes","on")``   (``env_bool``)
  * strict:      ``raw.lower() == "true"``                            (legacy)

The strict dialect is a live-money hazard because it is *asymmetric* against us.
``LIVE_TRADING_ENABLED`` was always parsed permissively, while the loop gates
that place stops and exits were parsed strictly — so a ``.env`` value of
``"true "`` (a trailing space, which dotenv preserves) armed real-money buying
while leaving ``THEMATIC_EXIT_LOOP`` and the standalone exit guard switched off.
The book would open positions and never close them. Same for ``FLAG=1``/``yes``.

Guards here:
  1. behavioural — every trading-critical gate honours whitespace and the
     ``1/yes/on`` aliases, so the exit path can never be armed less readily
     than the entry path;
  2. structural — the strict idiom cannot reappear in the money path.

Safety note: for flags that *default on* (``FIDELITY_REQUIRE_EXPLICIT_ACCOUNT``,
``WEB_SINGLE_INSTANCE_LOCK``, ``TRADINGAGENTS_FMP_ENABLED``) the strict idiom
also failed **open** — ``FLAG=1`` read as "not true" and disabled the guard.
``env_bool`` makes those fail closed. Both directions are covered below.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tradingagents.config import env_bool

ROOT = Path(__file__).resolve().parents[1]

# Values an operator plausibly writes meaning "on". dotenv keeps trailing
# whitespace, so " true" / "true " are the realistic footguns, not exotica.
TRUTHY_SPELLINGS = ["true", "true ", " true", "True", "TRUE", "1", "yes", "on", " on "]
FALSEY_SPELLINGS = ["false", "false ", "0", "no", "off", ""]

# Gates that must arm on any truthy spelling. Disarmed exits lose money;
# disarmed entries lose opportunity. Both belong here.
TRADING_GATES = [
    "THEMATIC_EXIT_LOOP",
    "THEMATIC_LIVE_EXIT_AUTONOMOUS",
    "THEMATIC_AUTO_SCAN",
    "HOLDINGS_BRAIN_ENABLED",
    "HOLDINGS_BRAIN_TAKEOVER",
    "COPYTRADE_ENABLED",
    "COPYTRADE_AUTONOMOUS",
    "LIVE_TRADING_ENABLED",
]

# Guards that ship ON. A set-but-unrecognised value must NOT silently disable
# them — that is the fail-open direction and it is the dangerous one.
DEFAULT_ON_GUARDS = [
    "FIDELITY_REQUIRE_EXPLICIT_ACCOUNT",
    "WEB_SINGLE_INSTANCE_LOCK",
    "TRADINGAGENTS_FMP_ENABLED",
]


@pytest.mark.unit
@pytest.mark.parametrize("flag", TRADING_GATES)
@pytest.mark.parametrize("value", TRUTHY_SPELLINGS)
def test_trading_gate_arms_on_any_truthy_spelling(monkeypatch, flag, value):
    monkeypatch.setenv(flag, value)
    assert env_bool(flag, False) is True, (
        f"{flag}={value!r} failed to arm — a loop gated on this stays dead"
    )


@pytest.mark.unit
@pytest.mark.parametrize("flag", TRADING_GATES)
@pytest.mark.parametrize("value", FALSEY_SPELLINGS)
def test_trading_gate_stays_off_on_falsey_spelling(monkeypatch, flag, value):
    monkeypatch.setenv(flag, value)
    assert env_bool(flag, False) is False, f"{flag}={value!r} armed unexpectedly"


@pytest.mark.unit
@pytest.mark.parametrize("flag", DEFAULT_ON_GUARDS)
@pytest.mark.parametrize("value", TRUTHY_SPELLINGS)
def test_default_on_guard_does_not_fail_open(monkeypatch, flag, value):
    """`FLAG=1` on a default-on guard must keep it ON (the legacy `== "true"`
    read this as "not true" and silently disabled the protection)."""
    monkeypatch.setenv(flag, value)
    assert env_bool(flag, True) is True, f"{flag}={value!r} disabled a default-on guard"


@pytest.mark.unit
def test_default_on_guard_unset_stays_on(monkeypatch):
    for flag in DEFAULT_ON_GUARDS:
        monkeypatch.delenv(flag, raising=False)
        assert env_bool(flag, True) is True


@pytest.mark.unit
def test_exit_gate_is_never_stricter_than_the_live_trading_gate(monkeypatch):
    """The core asymmetry regression: whatever spelling arms live trading must
    also arm the exit loops. Buying more readily than we can sell is the one
    failure mode this module exists to prevent."""
    for value in TRUTHY_SPELLINGS:
        monkeypatch.setenv("LIVE_TRADING_ENABLED", value)
        monkeypatch.setenv("THEMATIC_EXIT_LOOP", value)
        monkeypatch.setenv("HOLDINGS_BRAIN_ENABLED", value)
        entries_armed = env_bool("LIVE_TRADING_ENABLED", False)
        exits_armed = env_bool("THEMATIC_EXIT_LOOP", False)
        guard_armed = env_bool("HOLDINGS_BRAIN_ENABLED", False)
        assert not (entries_armed and not exits_armed), (
            f"{value!r} arms live trading but not the exit loop"
        )
        assert not (entries_armed and not guard_armed), (
            f"{value!r} arms live trading but not the exit guard"
        )


# ── structural guard ───────────────────────────────────────────────────────────

# `os.getenv(...)...lower() == "true"` in any spelling, with or without .strip().
_STRICT_IDIOM = re.compile(
    r"""(?:os\.)?(?:getenv|environ\.get)\([^)]*\)\s*
        (?:\.\s*strip\(\)\s*)?\.\s*lower\(\)\s*(?:==|!=)\s*["'](?:true|false)["']""",
    re.VERBOSE,
)

# Every module that gates, sizes, or places a trade. env.py documents the idiom
# in its docstring, which is the point of the module, so it is exempt.
_MONEY_PATH = ["tradingagents", "web", "scripts"]
_EXEMPT = {Path("tradingagents/config/env.py")}


@pytest.mark.unit
def test_strict_bool_idiom_absent_from_money_path():
    offenders = []
    for pkg in _MONEY_PATH:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT)
            if rel in _EXEMPT:
                continue
            text = path.read_text(errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if _STRICT_IDIOM.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Strict env-bool idiom found — use tradingagents.config.env_bool instead.\n"
        "It silently ignores 'true ' / '1' / 'yes', which can disarm an exit loop "
        "while live trading stays armed:\n  " + "\n  ".join(offenders)
    )
