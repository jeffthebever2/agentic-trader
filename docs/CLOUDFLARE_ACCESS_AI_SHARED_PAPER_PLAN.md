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
