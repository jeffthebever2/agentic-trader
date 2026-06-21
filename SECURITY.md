# Security Policy

Agentic Trader can place **real orders with real money** through brokerage accounts
(Fidelity / Webull). This document describes the controls that stand between the
software and your capital, and how to report a vulnerability.

If you believe a control described here can be bypassed, **treat it as a security
incident** — see [Reporting](#reporting).

---

## Scope

- **Real-money execution** flows through `tradingagents/compliance.py`. Live trading is
  **disabled by default** and gated by the layers below.
- **Paper trading** carries no financial risk but shares state files and code paths with
  live execution — bugs there can still corrupt the data live decisions rely on.
- **Secrets** (`.env`, broker session files, 2FA secrets) grant account access and must
  never be committed, logged, or pasted into prompts.

---

## The compliance kill-chain

Every live order passes `validate_live_order` in `tradingagents/compliance.py` before it
can reach a broker. **Never weaken these gates.** The order is rejected unless all hold:

| Gate | Rule |
|------|------|
| **Order type** | LIMIT only — no market, short, margin, or options orders. |
| **Position cap** | ≤ `MAX_POSITION_PCT_OF_ACCOUNT` (10%) of account value. |
| **Notional cap** | ≤ $50,000 per order. |
| **Trusted quote** | A fresh quote from a trusted source via `PreTradeGate` (`require_trusted_source=True`). |

**Trusted execution-quote sources** = `{finnhub, twelve_data, fmp}` (via
`tradingagents/data/quote_gateway.py`). yfinance is **untrusted** for execution. In a
typical deployment only `FMP_API_KEY` is set, so FMP is the trusted source. The gateway
stamps `quote_time` as naive-local time, so order dicts must pass a naive-local `now` —
a UTC-vs-local skew will (correctly, conservatively) fail the freshness check. Broker
(Playwright) orders may widen freshness only via the supported per-order
`max_quote_age_seconds` (env `BROKER_QUOTE_MAX_AGE_SECONDS`, default 120s).

### Two independent master switches

Both must permit trading for any live order to proceed:

1. **`LIVE_TRADING_HARD_BLOCKED`** — a constant in source (`tradingagents/compliance.py`).
   The ultimate kill switch; changing it requires a code edit + deploy.
2. **`LIVE_TRADING_ENABLED`** — read fresh from `.env` on every call. The operational
   toggle, default `false`.

### Per-trade step-up 2FA

Every order endpoint additionally enforces step-up authentication (`require_step_up`)
before execution. Supported methods (`web/twofa.py`, `web/api/twofa_routes.py`): TOTP,
a PBKDF2 passcode (salted, with lockout), email, and passkey / WebAuthn. Step-up is
per-trade — approving one order does not arm the next.

---

## Account protection

- **Protected accounts** — Roth IRA, retirement, and non-equity accounts are never
  traded. Enforced in three layers: an instrument filter (e.g. SPAXX / money-market
  funds), an account-type denylist (`is_protected_account`), and a broker-execution
  kill-switch (`_assert_account_tradeable`). Configure via
  `FIDELITY_PROTECTED_ACCOUNTS`.
- **Per-ticker order locks** — `_get_order_lock` provides idempotency so a retry or
  double-submit cannot place two orders for the same ticker.
- **Account-number validation** — `_validate_account_number` guards which account an
  order can target.

---

## Autonomy boundary

Background loops in `web/app.py` are **propose-only**. The thematic scanner, Holdings
Brain, exit guard, and keepalive loops can *queue* HIL proposals but **cannot place
orders** — a human must approve, and the approval still passes the full kill-chain.

The single exception is the **autonomous live-exit loop**
(`THEMATIC_LIVE_EXIT_AUTONOMOUS`, default **off**): the only loop that can submit a live
order without a fresh human click. It still requires a valid step-up *arm record* and
obeys every compliance gate. Leave it off unless you have deliberately accepted that
risk.

---

## Secrets & session handling

- **`.env`** holds broker credentials, API keys, and 2FA secrets. It is sensitive and has
  been clobbered before — **back it up and append; never overwrite blindly.** Never commit
  it.
- **Broker sessions** — Fidelity stores per-user session files (`.fidelity_session_<hash>.json`).
  These are live-account bearer material; protect them like passwords and never commit them.
- **Credential store** — saved broker credentials are encrypted at rest
  (`cryptography`); auto re-login uses them without exposing TOTP in logs.
- **Set real secrets in production** — `run_web.py` preflight warns if `STEP_UP_SECRET`
  is left at its default. Don't ship the default.
- **Logs** — avoid logging quotes, order payloads, or tokens at info level. Do not paste
  API keys or session JSON into AI prompts.

---

## Network & access

- The web server binds `127.0.0.1:8001` (localhost only). Public exposure is via a
  Cloudflare tunnel (`cloudflared`) fronted by **Cloudflare Access** — authentication is
  an Access JWT / session cookie, not an in-app password form.
- `ALLOWED_ORIGINS` controls CORS; keep it scoped to the hosts you actually use.
- For local development, `CF_ACCESS_LOCAL_DEV=true` bypasses Access — never set this on a
  publicly reachable instance.

---

## Reporting

Found a way to bypass a gate, place an order that shouldn't be allowed, reach a protected
account, or exfiltrate a secret? **Do not open a public issue.** Open a private security
advisory or email the maintainer directly with reproduction steps. Treat any
compliance-gate bypass as critical.

---

## Disclaimer

This software is provided for research and personal use. It is **not financial advice**.
No model, scanner, or framework guarantees profitability. You are responsible for every
order that reaches your broker — review the controls above before enabling live trading.
