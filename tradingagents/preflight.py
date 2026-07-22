"""Startup safety preflight — refuse to trade real money in an incoherent config.

Every individual flag in this system is validated. The *combinations* are not,
and the dangerous states are all combinations:

  * live trading armed with no stop watcher → real positions nobody is watching;
  * live trading armed with no trusted quote provider → every order fails the
    pre-trade gate, so the system looks alive and silently never trades;
  * autonomous execution armed with no kill-switch reachability;
  * multi-instance allowed while every order lock is in-process → duplicate
    live orders.

Each of those boots cleanly today and reports healthy. This module makes the
combination itself a checked invariant.

Design:
  * PURE and dependency-free — takes an env mapping, returns findings. No I/O,
    no imports from the web tier, so it is trivially testable and can run in CI.
  * Findings carry a severity. CRITICAL means "unsafe to trade real money"; the
    caller is expected to FAIL CLOSED (block live execution), not just log.
  * Checks are written so that a MISSING value is treated as the unsafe case
    wherever ambiguity exists — an unset flag must never read as "safely on".

Wiring lives in ``web/app.py`` (startup + ``/health/preflight``) and
``tradingagents/compliance.py`` (``block_live_trading_for_preflight``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Quote providers PreTradeGate accepts as execution-grade. Keep in sync with
#: tradingagents.data.quote_gateway.TRUSTED_SOURCES.
_TRUSTED_QUOTE_KEYS = ("FMP_API_KEY", "FINNHUB_API_KEY",
                       "TWELVE_DATA_API_KEY", "TWELVEDATA_API_KEY")


@dataclass(frozen=True)
class PreflightFinding:
    code: str
    severity: str
    message: str
    remedy: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "remedy": self.remedy}


@dataclass(frozen=True)
class PreflightResult:
    findings: list

    @property
    def critical(self) -> list:
        return [f for f in self.findings if f.severity == SEVERITY_CRITICAL]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def safe_for_live_trading(self) -> bool:
        """False ⇒ the caller must block real execution."""
        return not self.critical

    def as_dict(self) -> dict:
        return {
            "safe_for_live_trading": self.safe_for_live_trading,
            "critical_count": len(self.critical),
            "warning_count": len(self.warnings),
            "findings": [f.as_dict() for f in self.findings],
        }


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY


def _set(env: Mapping[str, str], name: str) -> bool:
    return bool(str(env.get(name) or "").strip())


def run_preflight(env: Mapping[str, str],
                  *, hard_blocked: bool = False) -> PreflightResult:
    """Validate configuration COMBINATIONS. Pure.

    ``hard_blocked`` is ``compliance.LIVE_TRADING_HARD_BLOCKED``; when set, no
    real order can be placed regardless of config, so live-money combinations
    downgrade from CRITICAL to INFO rather than blocking a paper-only box.
    """
    out: list[PreflightFinding] = []

    live = _flag(env, "LIVE_TRADING_ENABLED")
    exec_enabled = _flag(env, "FIDELITY_LOCAL_EXECUTION_ENABLED", True)
    browser_off = _flag(env, "FIDELITY_BROWSER_DISABLED")
    # Real money can actually move only when all of these line up.
    live_reachable = live and exec_enabled and not browser_off and not hard_blocked

    stop_watcher = _flag(env, "HOLDINGS_BRAIN_ENABLED")
    entries = _flag(env, "THEMATIC_AUTO_SCAN")
    paper_exits = _flag(env, "THEMATIC_EXIT_LOOP")
    copytrade = _flag(env, "COPYTRADE_ENABLED")
    copytrade_auto = _flag(env, "COPYTRADE_AUTONOMOUS")

    def add(code, sev, msg, remedy=""):
        out.append(PreflightFinding(code, sev, msg, remedy))

    # ── 1. Unwatched real positions ──────────────────────────────────────────
    # The single most expensive combination: orders can be placed, and nothing
    # evaluates their stops. Not "checked slowly" — checked ZERO times per day.
    if live_reachable and not stop_watcher:
        add("LIVE_WITHOUT_STOP_WATCHER", SEVERITY_CRITICAL,
            "Live trading is reachable but HOLDINGS_BRAIN_ENABLED is off — the "
            "exit guard, holdings brain and standalone runner are ALL gated on it, "
            "so real positions would get 0 stop checks per day.",
            "Set HOLDINGS_BRAIN_ENABLED=true (it is propose-only and safe to "
            "enable), or disable live trading.")
    elif live and not stop_watcher and hard_blocked:
        add("LIVE_WITHOUT_STOP_WATCHER", SEVERITY_INFO,
            "LIVE_TRADING_ENABLED is on without a stop watcher, but "
            "LIVE_TRADING_HARD_BLOCKED prevents any real order.",
            "Enable HOLDINGS_BRAIN_ENABLED before lifting the hard block.")

    # ── 2. No trusted execution quote ────────────────────────────────────────
    # PreTradeGate requires a trusted, fresh quote. With no provider key the
    # gateway can only return yfinance, which is untrusted for execution, so
    # every order 503s — including EXITS. The system looks healthy and cannot
    # trade or get out.
    if live_reachable and not any(_set(env, k) for k in _TRUSTED_QUOTE_KEYS):
        add("NO_TRUSTED_QUOTE_PROVIDER", SEVERITY_CRITICAL,
            "Live trading is reachable but no trusted quote provider key is set "
            f"({', '.join(_TRUSTED_QUOTE_KEYS[:3])}). PreTradeGate would reject "
            "every entry AND every exit — positions could not be closed.",
            "Set FMP_API_KEY or FINNHUB_API_KEY.")

    # ── 3. Step-up 2FA unusable ──────────────────────────────────────────────
    if live_reachable and not _set(env, "STEP_UP_SECRET"):
        add("STEP_UP_SECRET_MISSING", SEVERITY_CRITICAL,
            "Live trading is reachable but STEP_UP_SECRET is unset — per-trade "
            "step-up 2FA cannot issue or verify tokens.",
            "Set STEP_UP_SECRET to a strong random value.")

    # ── 4. Multi-instance with in-process locks ──────────────────────────────
    if not _flag(env, "WEB_SINGLE_INSTANCE_LOCK", True):
        add("SINGLE_INSTANCE_LOCK_DISABLED",
            SEVERITY_CRITICAL if live_reachable else SEVERITY_WARNING,
            "WEB_SINGLE_INSTANCE_LOCK is off. Every order lock, the paper-state "
            "lock and the alert cooldowns are in-process, so a second worker or "
            "replica produces DUPLICATE LIVE ORDERS.",
            "Leave WEB_SINGLE_INSTANCE_LOCK unset/true and run exactly one web "
            "process (no --workers N, no replicas).")

    # ── 5. Autonomous execution ──────────────────────────────────────────────
    if copytrade_auto and not copytrade:
        add("COPYTRADE_AUTONOMOUS_ORPHANED", SEVERITY_WARNING,
            "COPYTRADE_AUTONOMOUS is on but COPYTRADE_ENABLED is off — the "
            "autonomous mirror never runs. This reads as armed but is inert.",
            "Set COPYTRADE_ENABLED=true, or clear COPYTRADE_AUTONOMOUS.")
    if copytrade_auto and live_reachable and not stop_watcher:
        add("AUTONOMOUS_ENTRY_WITHOUT_WATCHER", SEVERITY_CRITICAL,
            "Autonomous copy-trade can open REAL positions with no human in the "
            "loop while no stop watcher is running.",
            "Enable HOLDINGS_BRAIN_ENABLED before arming COPYTRADE_AUTONOMOUS.")

    # ── 6. Buy/sell asymmetry ────────────────────────────────────────────────
    # Being able to open but not close is strictly worse than trading nothing.
    if entries and not paper_exits:
        add("ENTRIES_WITHOUT_EXITS", SEVERITY_WARNING,
            "THEMATIC_AUTO_SCAN is on but THEMATIC_EXIT_LOOP is off — the system "
            "would generate entries while nothing evaluates mechanical exits.",
            "Set THEMATIC_EXIT_LOOP=true.")
    if paper_exits and not entries:
        add("EXITS_WITHOUT_ENTRIES", SEVERITY_INFO,
            "THEMATIC_EXIT_LOOP is on but THEMATIC_AUTO_SCAN is off — the system "
            "can only ever sell. Expected while decommissioned or winding down.",
            "Set THEMATIC_AUTO_SCAN=true to resume generating entries.")

    # ── 7. Account protection ────────────────────────────────────────────────
    if live_reachable and not _set(env, "FIDELITY_PROTECTED_ACCOUNTS"):
        add("NO_PROTECTED_ACCOUNTS", SEVERITY_WARNING,
            "Live trading is reachable and FIDELITY_PROTECTED_ACCOUNTS is empty — "
            "no account (e.g. a Roth/retirement account) is denylisted.",
            "List retirement account numbers in FIDELITY_PROTECTED_ACCOUNTS.")
    if live_reachable and not _flag(env, "FIDELITY_REQUIRE_EXPLICIT_ACCOUNT", True):
        add("IMPLICIT_ACCOUNT_ALLOWED", SEVERITY_WARNING,
            "FIDELITY_REQUIRE_EXPLICIT_ACCOUNT is off — an order may land on "
            "whichever account Fidelity has selected by default.",
            "Leave FIDELITY_REQUIRE_EXPLICIT_ACCOUNT unset/true.")

    # ── 8. Auth exposure ─────────────────────────────────────────────────────
    if not _flag(env, "CF_ACCESS_REQUIRED"):
        # WARNING, never CRITICAL — this is web-auth posture, not order safety.
        # As a CRITICAL it would latch entries off, so unsetting one unrelated
        # env var would stop the system trading. Severity here decides whether
        # real money keeps moving; reserve it for order-safety invariants.
        add("AUTH_NOT_REQUIRED", SEVERITY_WARNING,
            "CF_ACCESS_REQUIRED is off — requests are not required to carry a "
            "verified Cloudflare Access identity."
            + (" Live trading is reachable on this box." if live_reachable else ""),
            "Set CF_ACCESS_REQUIRED=true in any deployment reachable off-host.")
    if _flag(env, "CF_ACCESS_LOCAL_DEV") and live_reachable:
        add("LOCAL_DEV_IDENTITY_WITH_LIVE_TRADING", SEVERITY_WARNING,
            "CF_ACCESS_LOCAL_DEV is on while live trading is reachable — a "
            "local request can assume a developer identity.",
            "Turn CF_ACCESS_LOCAL_DEV off on any box that can trade real money.")

    # ── 9. Research integrity ────────────────────────────────────────────────
    if copytrade and _env_float(env, "PAPER_SLIPPAGE_BPS", 0.0) <= 0:
        add("COPYTRADE_ON_FRICTIONLESS_LEADERBOARD", SEVERITY_WARNING,
            "Copy-trade mirrors the paper leaderboard into real money while "
            "PAPER_SLIPPAGE_BPS is 0 — the ranking ignores spread, so it favours "
            "whichever strategy is most sensitive to the friction it omits.",
            "Set PAPER_SLIPPAGE_BPS=10-25.")

    return PreflightResult(out)


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        raw = env.get(name)
        return default if raw is None or str(raw).strip() == "" else float(raw)
    except (TypeError, ValueError):
        return default


def format_findings(result: PreflightResult) -> str:
    """Human-readable block for logs."""
    if not result.findings:
        return "preflight: all checks passed"
    lines = []
    for f in result.findings:
        lines.append(f"  [{f.severity.upper()}] {f.code}: {f.message}")
        if f.remedy:
            lines.append(f"      remedy: {f.remedy}")
    head = (f"preflight: {len(result.critical)} critical, "
            f"{len(result.warnings)} warning(s)")
    return head + "\n" + "\n".join(lines)
