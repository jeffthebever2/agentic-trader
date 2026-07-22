# Production Runbook — Agentic Trader

Operating a system that moves real money. Read the whole thing before arming
live trading.

---

## 1. What is actually automatic

Be precise about this, because "automatic trading system" is easy to
misunderstand and the misunderstanding is expensive.

| Stage | Automatic? |
|---|---|
| Scanning ~19 social/news sources | **Yes** — `THEMATIC_AUTO_SCAN`, every 4h |
| Scoring, ranking, AI pick | **Yes** |
| Paper trading (thematic book + 30-portfolio competition) | **Yes** |
| Mechanical exits on the **paper** book | **Yes** — `THEMATIC_EXIT_LOOP` |
| Stop/target watching on the **real** book | **Yes**, propose-only — `HOLDINGS_BRAIN_ENABLED` |
| **Placing a real BUY** | **No** — human + step-up 2FA, unless `COPYTRADE_AUTONOMOUS` |
| **Placing a real SELL** | **No** — human + step-up 2FA, unless `THEMATIC_LIVE_EXIT_AUTONOMOUS` (armed separately) |

**The only route to a real order with no human in the loop is copy-trade
autonomous mode.** Everything else proposes and waits. The exit guard is
deliberately propose-only: `AUTO_LIVE_EXIT_FLAGS` is disjoint from what
`check_stops` emits, and a test pins that. Do not "fix" the mismatch unless you
have decided to let the machine sell your positions unattended.

> Consequence worth stating plainly: if nobody approves proposals, stops do not
> execute. An unattended stop breach is an unexecuted stop.

---

## 2. Go-live checklist

Run in order. Do not skip 4.

1. **Provision** — one node, one web process. `deploy/systemd/*.service`.
   Never `--workers N`, never replicas: every order lock, the paper-state lock
   and the alert cooldowns are in-process, so a second worker means duplicate
   live orders.
2. **Configure** — copy `.env.example` → `.env`. Every trading flag is
   documented there. `chmod 600 .env`.
3. **Keys** — at minimum one trusted quote provider (`FMP_API_KEY` or
   `FINNHUB_API_KEY`) and `STEP_UP_SECRET`. Without a trusted quote the
   pre-trade gate rejects every entry **and every exit** — you would not be able
   to close a position.
4. **Preflight** — start the server and read `GET /health/preflight`.
   `safe_for_live_trading` must be `true` and `critical_count` must be `0`.
   A CRITICAL finding **latches live execution off** at the compliance layer;
   the box will run paper-only until you fix the config and restart.
5. **Protect accounts** — put retirement/Roth account numbers in
   `FIDELITY_PROTECTED_ACCOUNTS`, leave `FIDELITY_REQUIRE_EXPLICIT_ACCOUNT=true`.
6. **Seed the broker session** — log in once through the UI to store the
   encrypted credentials and trust the device.
7. **Paper first.** Run with `LIVE_TRADING_ENABLED=false` for at least a full
   week. Confirm scans produce signals, exits fire, and the leaderboard moves.
8. **Smoke the live DOM — no money at risk.**

   ```bash
   python3 scripts/live_execution_smoke.py \
       --email you@example.com --account 123456789 --ticker F
   ```

   Drives the *entire* production order path against the real Fidelity ticket —
   navigate, select and verify the account, fill symbol / action / quantity /
   order type / limit price, click **Preview**, read the ticket back, and run
   the same `verify_order_ticket` gate the live path runs — then stops. The
   script has no code path that submits (a test enforces that), and the limit
   is set 50% below market so nothing could fill even hypothetically.

   This is what catches a Fidelity UI change *before* it breaks a real order.
   Re-run it after any Fidelity redesign and as a periodic canary. Every step
   must report `ok`.

9. **Arm** — set `LIVE_TRADING_ENABLED=true`, restart, re-check
   `/health/preflight`, then place one small trade manually end-to-end and
   confirm it appears in `/api/fidelity/positions` and in the pending-fill
   ledger. Submission and fill are the only things the smoke cannot prove.

### Minimum safe live configuration

```bash
LIVE_TRADING_ENABLED=true
FIDELITY_LOCAL_EXECUTION_ENABLED=true
FIDELITY_BROWSER_DISABLED=false
HOLDINGS_BRAIN_ENABLED=true        # REQUIRED — gates all three stop watchers
THEMATIC_AUTO_SCAN=true
THEMATIC_EXIT_LOOP=true
CF_ACCESS_REQUIRED=true
CF_ACCESS_LOCAL_DEV=false
WEB_SINGLE_INSTANCE_LOCK=true
FIDELITY_PROTECTED_ACCOUNTS=<your retirement accounts>
FMP_API_KEY=...
STEP_UP_SECRET=...
```

`HOLDINGS_BRAIN_ENABLED=true` is not optional. It gates the exit guard, the
holdings brain and the standalone runner — all three. With it off while live
trading is armed, real positions get **zero** stop checks per day. The preflight
treats that combination as CRITICAL and refuses to trade.

---

## 3. Kill switches, strongest first

1. **`LIVE_TRADING_HARD_BLOCKED = True`** in `tradingagents/compliance.py`.
   A source change, deliberately not settable from `.env` or the dashboard.
   Enforced *inside* `validate_live_order`, so no endpoint can bypass it.
2. **Startup preflight latch** — automatic on any CRITICAL finding. Also
   enforced inside the validator. Clears on restart once the config is fixed.
3. **`LIVE_TRADING_ENABLED=false`** — read fresh per call, so it takes effect
   without a restart.
4. **`FIDELITY_BROWSER_DISABLED=true`** — refuses to launch the browser at all.
5. **`systemctl stop agentictrader-web`**.

Per-trade step-up 2FA sits on top of all of these on every order endpoint.

---

## 4. Monitoring — what to alert on

| Probe | Alert when |
|---|---|
| `GET /health` | non-200 |
| `GET /health/loops` (admin) | any `alive: false` |
| `GET /health/preflight` (admin) | `critical_count > 0` |
| `logs/*.err` | `CRITICAL loop_supervisor` (a loop is crash-looping) |
| `logs/*.err` | `PHANTOM POSITIONS` (an order never filled but state was written) |
| `logs/*.err` | `AUTO-LIVE-EXIT ARMED BUT INERT` |
| `logs/*.err` | `SHUTTING DOWN WITH ... ORDER(S) STILL IN FLIGHT` |
| `tmp/exit_guard_heartbeat.json` | `status != "ok"`, or mtime older than 2× `EXIT_GUARD_INTERVAL_MIN` |

Loops back off exponentially (10s → 10min) when they fail repeatedly, so a
permanent failure is a quiet heartbeat rather than log spam — alert on the
first occurrence, not on volume.

---

## 5. Known limits — accept these or do not run it

These are properties of the design, not bugs to be surprised by later.

- **Single node, single process.** Cannot scale horizontally. In-process locks
  plus JSON state plus a stateful Playwright session.
- **Fidelity execution is browser automation, and it is the one surface that
  cannot be verified without spending real money.** Fidelity blocks true
  headless, so a real Chrome runs off-screen. Every *decision guard* on that
  path is tested against realistic page fixtures
  (`tests/test_live_order_gate.py`): the pre-submit ticket verifier (symbol,
  side, quantity, limit price), the post-submit confirmation reader, and the
  account guard. What no test can cover is Fidelity's live DOM. A UI change
  there breaks execution; the verifier fails closed, so the failure mode is a
  refused order rather than a wrong one. **Do a supervised live smoke — one
  small order, watched end-to-end — before trusting it unattended.**
- **Fills are verified against holdings, not order tickets.** Confirmation is
  broker *acceptance*; the pending-fill ledger reconciles against real holdings
  on the keepalive cycle. A never-filled DAY order therefore surfaces as a
  `PHANTOM POSITIONS` alert within a cycle, not instantly.
- **Stops are checked on a cadence, during the regular session only.**
  `EXIT_GUARD_INTERVAL_MIN` (default 15), 09:30 → close, trading days only. The
  calendar (`tradingagents/market_calendar.py`) is holiday- and early-close
  aware, so stops are never evaluated against quotes that have not moved since
  the previous close — that was a real bug.

  **A breach outside the session waits for the next open.** Friday 15:59 →
  Monday 09:30 is ~65.5 hours, longer over a holiday weekend. This is a
  deliberate choice, not an oversight: extending detection to 04:00–20:00 was
  tried and reverted because it is actively harmful here.
    * `run_exit_guard` calls `ratchet_stops`, which **mutates and persists**
      stop levels. A thin after-hours print ratchets the trail to a price that
      never really traded, and the guard then proposes liquidating a real
      winner at the next open.
    * `_trusted_quotes` is unbatched with a 2s cache, so 04:00–20:00 at 15-min
      cadence is ~448 FMP calls/day against a 250/day limit. Exhausting the only
      trusted provider makes PreTradeGate reject every order **including
      exits** — you could not close a position.
    * `_check_thematic_exits` prices from **daily** bars, so out-of-hours it
      re-reads a close already evaluated at 15:59.

  Widening this safely requires batching the quote fan-out and suppressing the
  ratchet outside the regular session first. `_brain_risk_window()` exists in
  `web/app.py` for that future work; nothing uses it today.
- **The ML gate is deliberately not enforcing.** Measured: rule-only
  +0.78%/trade vs full gate −0.05%/trade at walk-forward ROC ≈ 0.51. The bundle
  is loaded for drift monitoring only. Enabling it is a research decision.
- **Paper results flatter reality unless you set `PAPER_SLIPPAGE_BPS`.**
  Commission is genuinely zero (Fidelity US equities); spread is not modelled at
  0. Copy-trade mirrors the leaderboard winner into real money.

---

## 6. Incident response

**Suspected phantom position** (`PHANTOM POSITIONS` in the log)
1. Check the broker directly. Holdings are the source of truth.
2. If the shares are not there, the order never filled — remove the local
   position and let the next scan re-propose it.
3. If they are there, the ledger was stale; nothing to do.

**Duplicate or unexpected order**
1. `LIVE_TRADING_ENABLED=false` immediately (takes effect without restart).
2. Reconcile broker holdings against `tmp/thematic_paper/state.json`.
3. Check for a second web process: `pgrep -af run_web.py`. There must be one.

**Exit guard silent**
1. `tmp/exit_guard_heartbeat.json` — `status` and mtime.
2. `GET /health/loops` — is `exit_guard` alive?
3. `HOLDINGS_BRAIN_ENABLED` must be true, and the Fidelity session must be
   valid (an expired session throws per-account and the guard reports degraded).

**Restore from backup**
```bash
rclone copy <remote>/<archive> . && gpg -d <archive> | tar xz
```
Backups run nightly at 02:30 via `agentictrader-backup.timer` and cover the
state that cannot be regenerated: paper books, holdings-brain store, signals,
encrypted broker session/creds, `.env`, and the deployed model bundle.

---

## 7. Routine operations

```bash
# apply backend changes (the process loaded code + .env at startup)
systemctl restart agentictrader-web        # Linux
launchctl kickstart -k gui/$(id -u)/org.agentictrader.webserver   # macOS

# frontend changes are pure static — rebuild and hard-refresh
cd frontend && npm run build

# full test suite before any deploy
python3 -m pytest -q
```

Frontend `tsc -b` is strict: an untyped value fails the build. That is
intentional — do not loosen it to ship.
