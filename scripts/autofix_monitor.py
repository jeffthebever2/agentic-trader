#!/usr/bin/env python3
"""
AutoFix Monitor — on first crash, Claude audits the error and decides whether to fix it.

Flow per crash:
  1. Detect crash (PID change or HTTP health fail or new errors while down)
  2. Immediately call Claude with error log in audit+fix mode
  3. Claude outputs AUTOFIX_DECISION: FIX or AUTOFIX_DECISION: SKIP with reasoning
  4. If FIX: Claude has already edited the files; launchd restarts the service
  5. If SKIP: log the reason, do nothing
  6. 10-min cooldown before next audit for same service (avoid spam on crash loops)

Rate limit: max 3 Claude calls per service per hour regardless of decision.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

# Add repo to path so notify.py is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.notify import notify_down, notify_fixed, notify_skip

# ── Config ────────────────────────────────────────────────────────────────────

REPO = Path("/Users/williamscott/Desktop/TradingAgents-0.2.4 copy")
LOGS = REPO / "logs"
FIX_LOG = LOGS / "autofix.log"
CLAUDE_BIN = "/usr/local/bin/claude"

SERVICES = {
    "webserver": {
        "label": "org.agentictrader.webserver",
        "health_url": "http://localhost:8001/health/deep",
        "err_log": LOGS / "webserver.err",
    },
    "papertrader": {
        "label": "org.agentictrader.papertrader",
        "health_url": None,
        "err_log": LOGS / "papertrader.err",
    },
    "tunnel": {
        "label": "org.agentictrader.tunnel",
        "health_url": None,
        "err_log": LOGS / "tunnel.err",
    },
}

AUDIT_COOLDOWN_SECS = 600   # 10 min between audits for same service
MAX_AUDITS_PER_HOUR = 3     # per service
POLL_INTERVAL       = 20    # seconds between health checks
ERROR_TAIL_LINES    = 80    # lines of log to send Claude


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(FIX_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def launchctl_status(label: str) -> tuple[str, str]:
    """Return (pid, last_exit_code). pid='-' = not running."""
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True, timeout=5)
        for line in out.splitlines():
            if label in line:
                parts = line.split()
                return (parts[0], parts[1]) if len(parts) >= 2 else ("-", "0")
    except Exception:
        pass
    return ("-", "0")


def http_ok(url: str) -> bool:
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() in ("200", "401", "403")
    except Exception:
        return False


def tail_log(path: Path, n: int = ERROR_TAIL_LINES) -> str:
    if not path.exists():
        return "(no log file)"
    try:
        r = subprocess.run(["tail", f"-{n}", str(path)], capture_output=True, text=True)
        return r.stdout or "(empty)"
    except Exception:
        return "(could not read log)"


def run_claude_audit(service_name: str, error_text: str) -> tuple[str, bool]:
    """
    Call Claude to audit the crash. Claude decides FIX or SKIP and acts.
    Returns (claude_output, fixed: bool).
    """
    prompt = f"""You are the automated repair system for the TradingAgents trading platform at:
  {REPO}

The '{service_name}' service just crashed. Audit the error and decide whether to fix it.

--- CRASH LOG (last {ERROR_TAIL_LINES} lines) ---
{error_text}
--- END LOG ---

INSTRUCTIONS:
1. Read the traceback carefully.
2. Decide: is this a real code bug that you can safely fix?
   - YES if: clear TypeError/AttributeError/ImportError/NameError/missing param with obvious fix
   - NO if: external dependency issue, data/config problem, environment issue, or uncertain root cause
3. If YES: find and edit the relevant file(s) to fix the bug. Be surgical — only fix the crash cause.
4. If NO: explain why you are skipping.
5. End your response with EXACTLY one of these two lines (no other text after it):
   AUTOFIX_DECISION: FIX
   AUTOFIX_DECISION: SKIP

Do NOT restart services. Do NOT make speculative changes. Do NOT modify working tests."""

    # Extract short error summary for notifications
    last_lines = error_text.strip().splitlines()
    err_summary = "\n".join(last_lines[-5:]) if last_lines else "(no log)"

    log(f"{service_name}: calling Claude to audit crash...")
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--dangerously-skip-permissions", "-p", prompt],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        output = (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        output = "Claude audit timed out after 600s\nAUTOFIX_DECISION: SKIP"
    except Exception as e:
        output = f"Error invoking Claude: {e}\nAUTOFIX_DECISION: SKIP"

    # Parse decision
    fixed = False
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line == "AUTOFIX_DECISION: FIX":
            fixed = True
            break
        if line == "AUTOFIX_DECISION: SKIP":
            fixed = False
            break

    decision = "FIX" if fixed else "SKIP"
    log(f"{service_name}: Claude decision = {decision}")

    # Log Claude's full reasoning (truncated)
    summary = output[:1200] + ("..." if len(output) > 1200 else "")
    log(f"{service_name}: Claude reasoning:\n{summary}")

    # Send fix notification only (down/skip alerts handled by trigger_audit)
    if fixed:
        try:
            fix_lines = [l for l in output.splitlines() if l.strip() and "AUTOFIX_DECISION" not in l]
            fix_snippet = "\n".join(fix_lines[-8:])
            notify_fixed(service_name, fix_snippet)
        except Exception as ne:
            log(f"{service_name}: fix notification error: {ne}")

    return output, fixed


# ── Per-service monitor state ─────────────────────────────────────────────────

class ServiceMonitor:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.last_pid: str = "-"
        self.last_audit_time: float = 0.0
        self.audit_times: deque[float] = deque()
        self.log_size: int = 0
        self.http_fail_streak: int = 0

    def _prune_audits(self) -> None:
        cutoff = time.time() - 3600
        while self.audit_times and self.audit_times[0] < cutoff:
            self.audit_times.popleft()

    def can_audit(self) -> bool:
        now = time.time()
        if now - self.last_audit_time < AUDIT_COOLDOWN_SECS:
            remaining = int(AUDIT_COOLDOWN_SECS - (now - self.last_audit_time))
            log(f"{self.name}: crash detected but in cooldown ({remaining}s remaining)")
            return False
        self._prune_audits()
        if len(self.audit_times) >= MAX_AUDITS_PER_HOUR:
            log(f"{self.name}: crash detected but hourly audit limit reached ({MAX_AUDITS_PER_HOUR}/hr)")
            return False
        return True

    def trigger_audit(self, exit_code: str = "0") -> None:
        if not self.can_audit():
            return

        health_url: str | None = self.cfg.get("health_url")

        # For SIGTERM exits (exit=-15), wait briefly and check HTTP health.
        # A clean launchctl restart generates SIGTERM but the service comes
        # back healthy within seconds — no alert needed for that case.
        if exit_code == "-15" and health_url:
            time.sleep(8)
            if http_ok(health_url):
                log(f"{self.name}: PID changed via SIGTERM but HTTP health OK — clean restart, no alert")
                return

        error_text = tail_log(self.cfg["err_log"])

        # Port-binding conflict: service is already restarting, nothing to fix.
        # Wait briefly — if it comes back healthy, skip audit and alert entirely.
        _port_conflict_markers = (
            "address already in use", "errno 48", "errno 98",
            "error while attempting to bind",
        )
        error_lower = error_text.lower()
        if any(m in error_lower for m in _port_conflict_markers):
            log(f"{self.name}: port-binding conflict detected — waiting 10s for recovery")
            time.sleep(10)
            if health_url and http_ok(health_url):
                log(f"{self.name}: recovered after port conflict — no alert")
                return
            log(f"{self.name}: still down after port conflict — skipping Claude audit (not a code bug)")
            return

        self.last_audit_time = time.time()
        self.audit_times.append(self.last_audit_time)

        output, fixed = run_claude_audit(self.name, error_text)
        if fixed:
            log(f"{self.name}: code fixed by Claude — launchd will restart service")
        else:
            log(f"{self.name}: Claude skipped fix — monitoring continues")

        # Only alert DOWN if Claude confirmed a real problem (FIX) or
        # could not determine it was a clean restart (SKIP with non-trivial log).
        if fixed:
            pass  # notify_fixed already sent inside run_claude_audit
        else:
            # Check if Claude's reasoning says it's a false alarm (clean restart)
            clean_keywords = ("no crash", "clean restart", "graceful shutdown",
                              "clean startup", "false alarm", "no error", "no traceback")
            reasoning_lower = output.lower()
            is_false_alarm = any(kw in reasoning_lower for kw in clean_keywords)
            if not is_false_alarm:
                # Real problem Claude can't fix — alert the user
                last_lines = error_text.strip().splitlines()
                err_summary = "\n".join(last_lines[-5:]) if last_lines else "(no log)"
                try:
                    notify_down(self.name, err_summary)
                    log(f"{self.name}: down alert sent (real crash, no auto-fix)")
                except Exception as ne:
                    log(f"{self.name}: down notification error: {ne}")
            else:
                log(f"{self.name}: false alarm (clean restart) — no alert sent")

    def has_new_errors(self) -> bool:
        path: Path = self.cfg["err_log"]
        try:
            size = path.stat().st_size
            grew = size > self.log_size
            self.log_size = size
            return grew
        except FileNotFoundError:
            return False

    def check(self) -> None:
        label: str = self.cfg["label"]
        health_url: str | None = self.cfg.get("health_url")

        pid, exit_code = launchctl_status(label)
        is_running = pid not in ("-", "")

        # Crash: PID changed (service restarted)
        if self.last_pid not in ("-", "") and is_running and pid != self.last_pid:
            log(f"{self.name}: CRASH DETECTED — restarted (pid {self.last_pid}→{pid}, exit={exit_code})")
            self.last_pid = pid
            self.trigger_audit(exit_code=exit_code)
            return

        # Crash: service is fully down + new errors appeared
        if not is_running and self.has_new_errors():
            log(f"{self.name}: CRASH DETECTED — down with new errors")
            self.last_pid = pid
            self.trigger_audit(exit_code="1")
            return

        self.last_pid = pid

        # HTTP health (webserver only) — 3 consecutive fails = crash event
        if health_url and is_running:
            if not http_ok(health_url):
                self.http_fail_streak += 1
                if self.http_fail_streak >= 3:
                    log(f"{self.name}: CRASH DETECTED — HTTP health failed {self.http_fail_streak}x")
                    self.http_fail_streak = 0
                    self.trigger_audit()
            else:
                self.http_fail_streak = 0

        # Sync log size on healthy run so we don't false-positive on startup
        self.has_new_errors()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log("AutoFix Monitor started — audit-on-first-crash mode")

    monitors = {name: ServiceMonitor(name, cfg) for name, cfg in SERVICES.items()}

    # Warm up: snapshot current log sizes and PIDs so startup state isn't flagged
    for m in monitors.values():
        m.has_new_errors()
        pid, _ = launchctl_status(m.cfg["label"])
        m.last_pid = pid
        log(f"  {m.name}: initial pid={pid}")

    while True:
        for m in monitors.values():
            try:
                m.check()
            except Exception as e:
                log(f"Monitor internal error ({m.name}): {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
