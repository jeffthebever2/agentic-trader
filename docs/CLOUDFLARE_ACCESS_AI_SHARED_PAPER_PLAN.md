# Cloudflare Access + Workers AI + Shared Paper Runner Plan

## Summary

Use Cloudflare as the production identity and AI platform. Cloudflare Access handles login on the domain, Workers AI becomes the default LLM provider, and user portfolios stay separate while paper trading becomes one shared system-wide runner visible to every logged-in user.

## Key Changes

- Keep Cloudflare Access as the production login layer:
  - Validate `Cf-Access-Jwt-Assertion` in FastAPI.
  - Map verified Cloudflare email to a local user record.
  - Keep a local development fallback for `localhost`.
- Add Cloudflare Workers AI as the main AI provider:
  - Add provider key `cloudflare` to the LLM factory and model catalog.
  - Reuse the existing OpenAI-compatible client path with base URL:
    `https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1`
  - Add settings keys: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, and optional `CLOUDFLARE_AI_GATEWAY_URL`.
  - Default quick model: `@cf/meta/llama-3.3-70b-instruct-fp8-fast`.
  - Default deep model: `@cf/openai/gpt-oss-120b`.
  - Change default app config `llm_provider` from `openai` to `cloudflare`.
- Update web UI provider selectors:
  - Put `Cloudflare Workers AI` first/default for Analyze and Backtest.
  - Rename paper AI controls from OpenRouter-specific wording to generic `AI model`.
  - Add Cloudflare AI account/token fields in Settings.
- Use Cloudflare AI Gateway optionally:
  - If `CLOUDFLARE_AI_GATEWAY_URL` is set, route AI calls through it for logging, caching, rate limiting, and provider observability.
  - Otherwise use Workers AI's OpenAI-compatible endpoint directly.

## Data Model

- Separate per user:
  - Manual portfolio positions.
  - Fidelity/Webull broker sessions.
  - User-specific settings/API key overrides.
  - Trade logs for manually managed portfolios.
- Shared across all users:
  - Paper runner process.
  - Paper strategy accounts.
  - Paper candidates, positions, equity curve, events, analytics, HIL approvals, and autostart.
  - Existing `tmp/paper_trading_today/`, `tmp/hil_state.json`, and `tmp/paper_autostart.json` remain system-level.
- Add role checks:
  - All logged-in users can view shared paper runner data.
  - Only `admin` users can start/stop the paper runner, change autostart, approve/reject HIL trades, or edit shared paper AI settings.

## Migration

- Create a local user registry from Cloudflare identities.
- Import existing manual portfolio and broker sessions into the first admin user.
- Keep existing paper trading files global and shared.
- Preserve current files as backups; do not delete legacy `.env`, `.fidelity_session.json`, or `tmp/paper_trading_today/`.

## Test Plan

- Unit tests:
  - Cloudflare Access JWT validation accepts valid issuer/audience/signature and rejects invalid tokens.
  - Cloudflare LLM provider builds an OpenAI-compatible client with the correct base URL and API key.
  - User A and User B manual portfolios remain isolated.
  - Paper status reads from shared system paths for both users.
  - Non-admin users cannot start/stop shared paper runner or resolve HIL trades.
- API tests:
  - `/api/auth/me` returns Cloudflare-backed user identity.
  - `/api/portfolio` differs per user.
  - `/api/paper/status`, `/api/paper/equity`, and `/api/paper/candidates-history` return the same data for all users.
  - Settings masks Cloudflare tokens and saves allowed keys.
- Playwright tests:
  - Dashboard loads through mocked Cloudflare identity.
  - Provider dropdown defaults to Cloudflare Workers AI.
  - Two user contexts see different portfolios but the same paper page.
  - Paper page remains console-error free.

## Assumptions

- Cloudflare Access remains the source of truth for login.
- Manual/broker portfolios are private per user.
- Paper trading is a shared system dashboard, not per-user.
- Admin controls protect shared runner actions.
- Workers AI uses Cloudflare's OpenAI-compatible endpoints:
  - https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
  - https://developers.cloudflare.com/workers-ai/models/
  - https://developers.cloudflare.com/ai-gateway/usage/rest-api/

## Setup

### 1. Prerequisites

- Cloudflare account with the trading domain on its DNS.
- Cloudflare Zero Trust team enabled (free tier is sufficient).
- Workers AI enabled on the account (no extra setup; pay-as-you-go).
- Optional: AI Gateway created for observability/caching.

### 2. Cloudflare Access (identity layer)

1. Zero Trust dashboard → **Access → Applications → Add an application → Self-hosted**.
2. **Application domain:** the host that serves the FastAPI app (e.g. `app.agentictrader.org`).
3. **Identity providers:** enable Google / GitHub / One-time PIN.
4. **Policy:** allow specific emails or an email-domain group; deny everyone else.
5. **Settings → Service Auth → Application Audience (AUD) Tag:** copy this string.
6. Note the team domain: `https://<team>.cloudflareaccess.com`.

Server validation uses Cloudflare's JWKS:
- Issuer: `https://<team>.cloudflareaccess.com`
- JWKS: `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`
- Audience: the AUD tag from step 5
- Header: `Cf-Access-Jwt-Assertion` (also accessible as cookie `CF_Authorization`)

### 3. Cloudflare Workers AI (LLM provider)

1. Dashboard → **AI → Workers AI**. Copy the **Account ID** from the right sidebar.
2. **My Profile → API Tokens → Create Token** → template **"Workers AI"** (scope: `Account.Workers AI:Read`).
   - Save the token; shown once.
3. (Optional) **AI → AI Gateway → Create gateway** named `tradingagents`. Copy gateway URL:
   `https://gateway.ai.cloudflare.com/v1/{ACCOUNT_ID}/tradingagents/compat`

### 4. Environment variables

Add to `.env` (or whatever the runtime reads):

```bash
# --- Cloudflare Access (identity) ---
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
CF_ACCESS_AUD=<AUD tag from Access app>
CF_ACCESS_REQUIRED=true                    # false on localhost dev

# --- Workers AI (LLM provider) ---
CLOUDFLARE_ACCOUNT_ID=<account id>
CLOUDFLARE_API_TOKEN=<token from step 3.2>
CLOUDFLARE_AI_GATEWAY_URL=                 # optional; e.g. https://gateway.ai.cloudflare.com/v1/{ACCOUNT_ID}/tradingagents/compat
CLOUDFLARE_DEFAULT_QUICK_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast
CLOUDFLARE_DEFAULT_DEEP_MODEL=@cf/openai/gpt-oss-120b

# --- App default provider ---
LLM_PROVIDER=cloudflare
```

Effective LLM base URL:
- If `CLOUDFLARE_AI_GATEWAY_URL` set → use it.
- Else → `https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1`

### 5. Local development fallback

- When request host is `localhost`/`127.0.0.1` **and** `CF_ACCESS_REQUIRED=false`, skip JWT validation and inject a fixed dev user (`dev@local`, role `admin`).
- All production hosts must enforce JWT validation regardless of env.

### 6. Tunnel / deployment

If serving from a local machine via Cloudflare Tunnel:

```bash
cloudflared tunnel create tradingagents
cloudflared tunnel route dns tradingagents app.agentictrader.org
# ~/.cloudflared/config.yml
# tunnel: tradingagents
# ingress:
#   - hostname: app.agentictrader.org
#     service: http://127.0.0.1:8000
#   - service: http_404
cloudflared tunnel run tradingagents
```

The Access policy from step 2 attaches to `app.agentictrader.org` and gates the tunnel automatically.

### 7. First-run migration

1. Start the app once with `CF_ACCESS_REQUIRED=true` and log in as the intended admin email.
2. Run migration script (to be added): seeds the local user registry from the Access identity and assigns role `admin` to the first login.
3. Import existing manual portfolio + broker sessions into that admin user.
4. Confirm shared paper-runner files (`tmp/paper_trading_today/`, `tmp/hil_state.json`, `tmp/paper_autostart.json`) remain at repo root, not per-user.

### 8. Smoke tests

```bash
# JWT validation
curl -H "Cf-Access-Jwt-Assertion: <token>" https://app.agentictrader.org/api/auth/me
# → {"email":"you@...","role":"admin"}

# Workers AI provider
curl -X POST "$CLOUDFLARE_AI_GATEWAY_URL/chat/completions" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"@cf/meta/llama-3.3-70b-instruct-fp8-fast","messages":[{"role":"user","content":"ping"}]}'

# Shared paper visibility
# Log in as User A and User B in separate browsers → /paper shows identical equity curve & candidates.
# Only admin role sees Start/Stop buttons.
```

### 9. Tunnel runbook (verified working)

This is the exact path that has been wired up in code (`run_web.py`, `web/app.py`).

**Origin server**

`run_web.py` now binds `0.0.0.0:8001` with `proxy_headers=True, forwarded_allow_ips="*"` so the FastAPI app sees `request.url.scheme == "https"` behind cloudflared and WebSocket upgrades resolve to `wss://`. Don't change these — cloudflared connects from localhost and is the only proxy in the chain.

**Named tunnel (production)**

```bash
cloudflared tunnel login
cloudflared tunnel create tradingagents
cloudflared tunnel route dns tradingagents app.agentictrader.org
```

`~/.cloudflared/config.yml`:
```yaml
tunnel: tradingagents
credentials-file: /Users/williamscott/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: app.agentictrader.org
    service: http://127.0.0.1:8001
    originRequest:
      noTLSVerify: true              # origin is plain HTTP on loopback
      connectTimeout: 30s
      # WebSockets work over cloudflared automatically; no flag needed.
  - service: http_404
```

Start it:
```bash
cloudflared tunnel run tradingagents
```

**Cloudflare Access on the hostname**

1. Zero Trust → Access → Applications → Add a self-hosted app for `app.agentictrader.org`.
2. Policy: allow only the email(s) that should reach the dashboard.
3. Copy the AUD tag.

**Auth bypass for Basic Auth (Access-fronted requests)**

The existing `BasicAuthMiddleware` now reads `Cf-Access-Authenticated-User-Email`. When Access is in front, set:

```bash
CF_ACCESS_TRUST_HEADERS=true
CF_ACCESS_EMAIL_ALLOWLIST=wtscott0603@gmail.com,teammate@example.com   # optional; empty = any Access-validated user
```

When both `CF_ACCESS_TRUST_HEADERS=true` and the request carries `Cf-Access-Authenticated-User-Email`, the middleware:
- Treats the user as authenticated, stores `request.state.user_email`.
- Skips the public-tunnel block list and Basic Auth challenge entirely.
- Rejects with `403` if the email is outside the allowlist.

**Security contract — required for the bypass to be safe**

`CF_ACCESS_TRUST_HEADERS=true` is only secure if the origin (`127.0.0.1:8001`) is unreachable from anywhere except cloudflared. Either:
- Bind to `127.0.0.1` instead of `0.0.0.0` if no LAN access needed, **or**
- Firewall port 8001 so only loopback can reach it.

If a direct attacker can connect to the origin, they can spoof `Cf-Access-Authenticated-User-Email` and bypass auth. The quick-tunnel block list (`trycloudflare.com` / `pinggy-free.link` / `loca.lt`) stays active for non-Access requests as defense-in-depth.

**Sendblue webhook over the tunnel**

Endpoint: `https://app.agentictrader.org/api/paper/sms/inbound`. The route is allow-listed in the middleware regardless of Access (Sendblue can't send the Access cookie) and is secured by `SENDBLUE_INBOUND_SECRET` checked inside the route handler. Configure in Sendblue dashboard → Webhooks → Inbound Messages.

**Quick tunnel (dev/testing only)**

`scripts/start_public_tunnel.sh` spins up an unauthenticated `*.trycloudflare.com` URL. The middleware blocks all paths except `/api/approve`, `/api/paper/hil/resolve`, `/api/paper/sms/inbound`. Do not set `CF_ACCESS_TRUST_HEADERS=true` while a quick tunnel is exposing the origin.

**Smoke test through the tunnel**

```bash
# Without Access cookie → 302 to Cloudflare Access login
curl -i https://app.agentictrader.org/

# With Access service-token (for CI / external callers)
curl -i -H "CF-Access-Client-Id: <id>" -H "CF-Access-Client-Secret: <secret>" \
  https://app.agentictrader.org/health

# Sendblue webhook (no Access)
curl -i -X POST https://app.agentictrader.org/api/paper/sms/inbound \
  -H "Content-Type: application/json" \
  -H "X-Sendblue-Signature: <secret>" \
  -d '{"content":"approve 1","from_number":"+15551234567"}'
```

### 10. Email addresses (operator policy)

| Address | Purpose | Direction |
|---|---|---|
| `support@agentictrader.org` | Human contact: access issues, account removal, privacy requests, bug reports | inbound + reply-to |
| `no-reply@agentictrader.org` | Automated system messages only | outbound only |

**Use `no-reply@` for:**
- Cloudflare Access / new login notices (if MX configurable on the Zero Trust side)
- Trade approval notification copies / HIL prompts
- Paper runner started / stopped alerts
- Daily summary digest
- Security notices: "new device", "session expired"
- Critical failures: broker session expired, API key failed, paper runner crashed

**Do not send via `no-reply@`:** every signal, every quote move, every log line — too noisy.

**Outbound mail headers must set:**
```
From:      Agentic Trader <no-reply@agentictrader.org>
Reply-To:  support@agentictrader.org
```

So humans hitting "Reply" land on support; the machine never reads its own inbox.

The dashboard footer (`Support | Terms | Privacy`) renders a `mailto:support@…` link. Both the Terms and Privacy pages list `support@…` as the contact for legal/access requests. Onboarding step 3 (non-admin variant) personalizes the mailto subject with the user's email.

### 11. Step-up 2FA before trades

Real-money trade endpoints (`POST /api/fidelity/trade`, `POST /api/webull/orders`)
require a fresh second factor in addition to admin role. Each user enrolls in
Settings → Trade security and picks the method used at trade time.

**Methods (per-user, selectable):**
- **TOTP** (recommended) — 6-digit code from Microsoft/Google Authenticator. `pyotp`. Offline, no extra infra.
- **Passkey** — WebAuthn (Face ID / fingerprint / hardware key). `webauthn` lib. Phishing-proof.

**Flow:**
1. Trade endpoint depends on `require_step_up` (admin + valid step-up token).
2. No token → `401` with header `X-Step-Up-Required: <method>` (or `428` if nothing enrolled).
3. Front-end `apiFetch` intercepts, runs the challenge (TOTP modal or passkey ceremony), mints a token via `/api/auth/2fa/step-up/*`, retries the trade once with `X-Step-Up-Token`.
4. Token is HMAC-signed, email-bound, 5-min TTL, stateless. Secret from `STEP_UP_SECRET` env or auto-persisted to `tmp/.step_up_secret`.

**Env:**
```bash
STEP_UP_SECRET=<random>                 # optional; auto-generated if unset
WEBAUTHN_RP_ID=app.agentictrader.org    # optional; defaults to request host
WEBAUTHN_ORIGIN=https://app.agentictrader.org  # optional; defaults to https://<rp_id>
```

**Endpoints:** `/api/auth/2fa/status`, `/totp/enroll|activate|disable`,
`/passkey/register/begin|complete`, `DELETE /passkey/{id}`, `/method`,
`/step-up/totp`, `/step-up/passkey/begin|complete`.

Localhost dev (`CF_ACCESS_REQUIRED!=true`) bypasses step-up for convenience.

### 12. Rollback

- Set `LLM_PROVIDER=openai` to revert AI provider without touching code.
- Set `CF_ACCESS_REQUIRED=false` and bypass tunnel to revert identity layer.
- All shared-paper files are unchanged on disk, so reverting is data-safe.
