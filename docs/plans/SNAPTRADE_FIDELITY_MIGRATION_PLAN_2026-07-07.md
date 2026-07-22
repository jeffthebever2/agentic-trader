# SnapTrade Fidelity Migration Plan

Date: 2026-07-07

Status: planning only. No implementation is authorized by this document.

## Executive Decision

SnapTrade is a strong candidate to replace the fragile Fidelity browser/API scraping layer for authentication, account discovery, balances, holdings, transactions, and order history. It is not currently safe to plan a full Fidelity trade-execution replacement without vendor confirmation, because SnapTrade's public Fidelity integration FAQ says its Fidelity integration does not offer the ability to place trades.

Recommended path:

1. Use SnapTrade for Fidelity read-only account data first.
2. Keep all live order paths behind the existing HIL, step-up 2FA, and `validate_live_order` kill-chain.
3. Do not route Fidelity live orders through SnapTrade until SnapTrade confirms Fidelity `Place Trade` support for this app, this API key, and the specific connected Fidelity account.
4. If Fidelity trading is not available through SnapTrade, either retain the current Fidelity Playwright execution path as an explicitly labeled fallback or move live API execution to another SnapTrade-supported broker.

## Source Findings

Official SnapTrade evidence:

- SnapTrade's general API docs say the platform can retrieve account data and place trades, but exact support depends on the brokerage.
- SnapTrade's trading guide says connections are read-only by default and require `connectionType=trade` or `trade-if-available` for trading.
- SnapTrade's place equity order endpoint exists at `POST /trade/place`, but the endpoint warns brokerages may reject invalid or insufficient-fund orders and recommends using order impact plus manual account refresh.
- SnapTrade's public Fidelity page says Fidelity can connect through OAuth and supports 2FA, but its FAQ says: "No, SnapTrade's integration with Fidelity does not offer the ability to place trades."
- SnapTrade's public broker support page lists Fidelity as generally available and notes up-to-24-hour Fidelity holdings delay, executed-only order history, and OAuth.

Sources:

- https://snaptrade.com/brokerage-integrations/fidelity-api
- https://docs.snaptrade.com/docs/trading-with-snaptrade
- https://docs.snaptrade.com/reference/Trading/Trading_placeForceOrder
- https://support.snaptrade.com/brokerages
- https://support.snaptrade.com/Fidelity-119feaa69a1c80b19bd7fe108ef40e62

## Current Fidelity Surface In This Repo

Primary files:

- `web/api/fidelity.py`: Playwright login, positions, balances, account selection, live order entry, thematic entry/exit, trade log.
- `web/api/thematic_auto.py`: HIL approval path that optionally routes thematic signals to Fidelity.
- `web/api/performance.py`: daily real Fidelity snapshots and performance capture.
- `tradingagents/compliance.py`: live-order kill-chain.
- `tradingagents/portfolio/holdings_brain.py`: normalized real broker holdings and proposals.
- `tradingagents/portfolio/fidelity_portfolio.py`: older fidelity-api wrapper.

Existing invariants to preserve:

- Live trading remains off unless `LIVE_TRADING_ENABLED=true` and source hard block allows it.
- Every money-moving order passes `validate_live_order`.
- Every live order requires step-up 2FA.
- Protected/retirement accounts remain blocked.
- Execution quotes must come from trusted quote sources, not broker display data alone.
- Paper portfolios never touch broker routes.

## Target Architecture

Introduce a broker abstraction instead of replacing `web/api/fidelity.py` directly.

```text
UI / HIL
  -> broker service facade
      -> SnapTradeBrokerAdapter
      -> FidelityPlaywrightAdapter fallback, optional
  -> tradingagents.compliance.validate_live_order
  -> audit log / performance capture / holdings brain
```

Core interface:

- `list_connections(user)`
- `connect_url(user, connection_type)`
- `list_accounts(user)`
- `get_account_detail(user, account_id)`
- `get_balances(user, account_id)`
- `get_positions(user, account_id)`
- `get_orders(user, account_id)`
- `get_activities(user, account_id)`
- `check_order_impact(order)` if supported
- `place_order(order)` only when broker capability says trading is enabled
- `refresh_account(user, account_id)`

Do not expose SnapTrade SDK objects to the rest of the app. Normalize into local models matching current app needs.

## Capability Gate

Add a first-class broker capability registry before any live routing:

```json
{
  "broker": "fidelity",
  "provider": "snaptrade",
  "read_accounts": true,
  "read_balances": true,
  "read_positions": true,
  "read_transactions": true,
  "read_orders": true,
  "place_equity_order": false,
  "fractional_order": "unknown",
  "extended_hours": "unknown",
  "data_delay": "up_to_24h_for_holdings"
}
```

Rules:

- If `place_equity_order=false`, all order endpoints return a clear skipped/unsupported result.
- The UI must display "SnapTrade Fidelity: data only" until proven otherwise.
- `trade-if-available` can be used during connection, but it must not imply trading is enabled.
- Runtime capability must come from SnapTrade account/broker metadata or a manually verified allowlist, not marketing copy.

## Migration Phases

### Phase 0: Vendor Verification

Goal: answer whether this specific app can place Fidelity trades through SnapTrade.

Tasks:

- Create SnapTrade account and API keys.
- Ask SnapTrade support in writing:
  - Does Fidelity support `connectionType=trade` for this app?
  - Does Fidelity support `POST /trade/place` for stocks/ETFs?
  - Are Fidelity Youth accounts supported?
  - Are orders read-only, executed-only history, or full order management?
  - Are holdings delayed by 24 hours for all Fidelity accounts?
  - Are balances current enough for cash-account buying-power decisions?
- Record response in `docs/plans/` before enabling any execution.

Exit criteria:

- Written yes/no on Fidelity order placement.
- Test connection in sandbox or non-production account.
- Capability registry updated.

### Phase 1: Read-Only SnapTrade Adapter

Replace browser scraping for read operations only.

Scope:

- Account list.
- Account detail.
- Balances.
- Positions.
- Transactions/activities.
- Executed order history.
- Manual refresh.

Do not change:

- Fidelity live order execution.
- Thematic HIL execution.
- Compliance logic.
- Step-up requirements.

Trade-off:

- Gains OAuth and less brittle data access.
- Loses freshness if Fidelity holdings are delayed up to 24 hours; performance and holdings brain must mark SnapTrade Fidelity positions as potentially stale.

### Phase 2: Read Path Parity

Build parity against current Fidelity snapshots.

Validation:

- Compare current Playwright positions vs SnapTrade positions.
- Compare account value/cash.
- Compare executed orders.
- Confirm protected account filtering still works.
- Confirm money-market/core cash handling does not double count SPAXX/FDRXX-style holdings.

Exit criteria:

- SnapTrade read data passes parity thresholds for 10 market days.
- Any stale-data field is surfaced in UI and logs.

### Phase 3: SnapTrade-Backed Holdings Brain

Move `holdings_brain` read source from Fidelity Playwright snapshots to normalized SnapTrade snapshots.

Rules:

- The brain remains propose-only.
- No order route changes.
- Protected accounts and non-equity filters stay in pure logic.
- Stale holdings must reduce confidence or block add/trim proposals where freshness matters.

Trade-off:

- More stable adoption/reconciliation.
- Possible one-day lag can make exit/trim proposals stale unless paired with live quotes.

### Phase 4: Optional SnapTrade Trading Pilot

Only if Phase 0 confirms Fidelity trading support.

New execution flow:

1. HIL approval creates local intended order.
2. Local compliance validates:
   - account rule profile, including Fidelity Youth restrictions
   - protected account block
   - quote freshness
   - limit-only buy policy
   - max order dollars
   - max position percent
   - settled-cash constraints for cash/Youth accounts
3. SnapTrade order impact/preview runs if available.
4. Human sees final preview and confirms with step-up 2FA.
5. Submit SnapTrade order with idempotent UUID.
6. Manual refresh account.
7. Poll/list orders until broker order id and status are captured.
8. Write local audit log.

Do not use SnapTrade's order endpoint as a sizing engine. The docs say `place` does not compute account impact; local sizing and order impact must remain separate.

### Phase 5: Deprecate Playwright Execution

Only after successful paper/sandbox/live pilot.

Requirements:

- 30 days without execution mismatch.
- Confirmed cancellation/rejection handling.
- Confirmed duplicate-order protection.
- Confirmed cash buying power handling.
- Confirmed Fidelity account selection.
- Rollback switch remains available.

## Trade-Offs

### SnapTrade Advantages

- OAuth-style connection portal for Fidelity.
- No local browser session or TOTP pause loop.
- Normalized accounts, balances, holdings, orders, and transactions.
- Multi-broker path: the same adapter design can support E*TRADE, Schwab Trading, TradeStation, Tradier, Alpaca, Webull, etc.
- Less UI breakage from Fidelity website changes.

### SnapTrade Risks

- Fidelity trading appears unavailable publicly unless SnapTrade says otherwise.
- Fidelity holdings may be delayed up to 24 hours.
- Trading support varies by brokerage and account.
- Order impact is separate from order placement.
- SnapTrade forwards idempotency to brokers but does not enforce uniqueness itself.
- You add a new third-party dependency and user-secret storage burden.
- Billing and connected-user costs must be tracked.

### Current Playwright Advantages

- Already wired into the app.
- Can place Fidelity orders today when the existing kill-chain allows it.
- Full control over local UI verification and ticket handling.

### Current Playwright Risks

- Fragile against Fidelity DOM changes.
- Browser session/TOTP state can break.
- Harder to run headless/reliably.
- More risk of account selection bugs without strict verification.
- More operational overhead.

## Security And Compliance Requirements

SnapTrade must not weaken local safety.

Required controls:

- Store `clientId`, `consumerKey`, user ID, and user secret securely.
- Never put SnapTrade user secrets in frontend state.
- Rotate user secrets if compromised.
- Add per-user SnapTrade disconnect/delete flow.
- Keep step-up 2FA for every money-moving action.
- Keep `validate_live_order` as the final local gate before order submission.
- Keep protected-account filtering.
- Keep Fidelity Youth restrictions.
- Keep trusted quote gate; SnapTrade order data is not a substitute for execution quote validation.
- Log request IDs, local order IDs, SnapTrade order IDs, brokerage order IDs, and status transitions.

## API Design Plan

New routes should be provider-neutral:

- `GET /api/broker/providers`
- `POST /api/broker/snaptrade/connect-url`
- `GET /api/broker/connections`
- `GET /api/broker/accounts`
- `GET /api/broker/accounts/{id}/positions`
- `GET /api/broker/accounts/{id}/balances`
- `GET /api/broker/accounts/{id}/orders`
- `GET /api/broker/accounts/{id}/activities`
- `POST /api/broker/accounts/{id}/refresh`
- `POST /api/broker/accounts/{id}/orders/preview`
- `POST /api/broker/accounts/{id}/orders/place`

Keep legacy `/api/fidelity/*` routes as compatibility wrappers until frontend migration is complete.

## Data Model Mapping

Map SnapTrade fields into local canonical models:

- `BrokerConnection`
- `BrokerAccount`
- `BrokerBalance`
- `BrokerPosition`
- `BrokerOrder`
- `BrokerActivity`
- `BrokerCapability`
- `BrokerOrderIntent`
- `BrokerOrderResult`

Every model must include:

- provider
- broker slug
- account id
- account display name
- account number mask only
- freshness timestamp
- raw provider id
- raw payload pointer or redacted audit copy

## Testing Plan

Unit tests:

- Capability registry blocks unsupported Fidelity trading.
- SnapTrade payloads normalize into current holdings model.
- SnapTrade stale holdings mark freshness warnings.
- Protected accounts are excluded.
- Youth product rules remain blocked.
- Order payload builder emits only allowed actions/order types.
- Idempotency UUID is generated and persisted.

Integration tests with mocked SnapTrade:

- Connect URL creation.
- Account list.
- Position sync.
- Order history sync.
- Manual refresh.
- Unsupported Fidelity trade returns clear `unsupported` result.
- Supported-broker trade path calls local compliance before SnapTrade.

Manual acceptance:

- Connect Fidelity account.
- Confirm account list and selected accounts.
- Confirm balances and positions against Fidelity UI.
- Confirm holdings lag behavior.
- Confirm disconnect/reconnect.
- If trading is supported, submit a tiny limit order only after explicit approval.

## Rollback Plan

Feature flags:

- `BROKER_PROVIDER=fidelity_playwright|snaptrade|hybrid`
- `SNAPTRADE_ENABLED=false`
- `SNAPTRADE_FIDELITY_READ_ONLY=true`
- `SNAPTRADE_ALLOW_TRADING=false`
- `FIDELITY_PLAYWRIGHT_FALLBACK=true`

Rollback steps:

1. Set `SNAPTRADE_ALLOW_TRADING=false`.
2. Set `BROKER_PROVIDER=fidelity_playwright` for reads if SnapTrade data is stale/broken.
3. Keep SnapTrade connected but disabled for app actions.
4. Preserve audit logs for reconciliation.

## Open Questions

- Does SnapTrade currently allow Fidelity order placement for this specific app/API key?
- Is Fidelity Youth visible and supported through SnapTrade?
- Are Fidelity balances real-time enough to enforce settled-cash buying power?
- Does SnapTrade expose account category strongly enough to distinguish Youth, IRA, retail cash, and margin?
- Which Fidelity assets are returned as positions: stocks, ETFs, mutual funds, fixed income, options, core cash?
- Can SnapTrade return order status before execution, or only executed orders for Fidelity?
- What is the paid-plan cost per connected user and per brokerage refresh?

## Recommendation

Do not market this as "replace Fidelity trading with SnapTrade" yet. The accurate plan is:

- Replace fragile Fidelity read/sync flows with SnapTrade first.
- Keep live Fidelity execution on the existing kill-chain until SnapTrade confirms Fidelity trading support.
- If Fidelity trading remains unavailable through SnapTrade, use SnapTrade to improve account data and consider a different SnapTrade-supported broker for API-native execution.

## Fidelity-Only Trading Decision

Date: 2026-07-07

User requirement: keep trading the existing Fidelity account. Do not treat "open a new broker" as the primary answer.

### Finding

I did not find a public, official Fidelity retail trading API that lets an individual self-directed Fidelity brokerage account place stock/ETF orders programmatically.

What exists:

- **SnapTrade Fidelity**: good candidate for Fidelity account data, but SnapTrade's Fidelity FAQ says its Fidelity integration does not place trades.
- **Fidelity WorkplaceXchange**: official Fidelity API marketplace, but the public catalog is workplace/retirement/payroll oriented, not retail brokerage order placement.
- **Fidelity Wealthscape / Integration Xchange**: official institutional/advisor/custody ecosystem, not a normal retail self-directed brokerage API for this app.
- **Fidelity Trader+ / Active Trader tools**: official trading platforms, but not a documented public API for this repository to call directly.
- **Unofficial `fidelity-api` package**: can place Fidelity orders, but it is Playwright/browser automation, not a real direct broker API. It is similar in risk profile to the current local path.
- **Old Wealth-Lab Pro automated trading docs**: show Fidelity has supported automated order flows inside approved Fidelity software, but they do not provide a modern public API endpoint for this project.

### Practical Meaning

For "trade with Fidelity and do not change brokers," option 4 is the real plan:

1. Keep local Fidelity execution.
2. Replace as much fragile read/sync work as possible with SnapTrade Fidelity data.
3. Harden the local Fidelity order path instead of rewriting it around a nonexistent public retail API.
4. Keep all Fidelity orders behind local compliance, quote validation, HIL approval, account selection verification, and step-up 2FA.

### What "Harden Local Fidelity Execution" Means

No new broker. No pretending SnapTrade can submit Fidelity orders.

Planned work:

- Split Fidelity into two providers:
  - `SnapTradeFidelityDataProvider` for balances, holdings, orders, and transactions.
  - `LocalFidelityExecutionProvider` for order entry only.
- Build a strict order ticket verifier before submit:
  - account mask matches expected account
  - symbol matches order intent
  - side matches buy/sell
  - quantity or dollars matches intent
  - order type and limit price match intent
  - estimated cost is within tolerance
- Add a pre-submit screenshot/audit payload.
- Add a post-submit order-status reconciler using SnapTrade data when available and local Fidelity fallback when SnapTrade lags.
- Treat SnapTrade holdings as stale if Fidelity reports delayed data.
- Keep a kill switch: `FIDELITY_LOCAL_EXECUTION_ENABLED=false`.

### Sources Checked

- SnapTrade Fidelity page: says Fidelity connects via OAuth/2FA but SnapTrade Fidelity does not offer trade placement.
- Fidelity WorkplaceXchange: official API catalog focused on workplace investing, retirement, HR, payroll, and participant data.
- Fidelity Wealthscape Integration Xchange: official advisor/institutional integration platform.
- Fidelity Trader+ pages: official Fidelity trading tools, not a public API.
- PyPI/GitHub `fidelity-api`: unofficial Playwright API that can place orders by controlling the Fidelity website.
- Fidelity Wealth-Lab Pro guide: historical approved automated trading inside Fidelity software, not a reusable public retail API.

## Broker Search Update: Tradable Alternatives

Date: 2026-07-07

Goal: find at least one realistic broker/API path that can place trades so the app does not have to depend on fragile Fidelity browser automation forever.

### Best Answer

There are tradable alternatives, but they require a different broker. This section is secondary because the current requirement is Fidelity-only.

Recommended order:

1. Keep **Fidelity local Playwright execution** if the user wants to keep trading the existing Fidelity/Youth account.
2. **E*TRADE through SnapTrade** only if the user later decides a broker move is acceptable and wants SnapTrade to handle both data and order routing.
3. **Alpaca direct API** only if the user later decides broker movement is acceptable and wants the cleanest developer-first trading API with paper/live parity.
4. **Tradier direct API** only if the user later decides broker movement is acceptable and wants a retail brokerage API with equities/options.

### Candidate Matrix

| Candidate | Can place trades? | SnapTrade fit | Account/data fit | Main trade-offs |
|---|---:|---|---|---|
| **Fidelity via SnapTrade** | **No, assume unavailable** | Great for read-only data | OAuth, 2FA, balances, holdings, transactions, but holdings can lag up to 24h | SnapTrade's public Fidelity FAQ says no trade placement. Use for data only unless support confirms otherwise. |
| **E*TRADE via SnapTrade** | **Yes** | Strong | SnapTrade lists stock/ETF/options trade placement, equity/options trade impact, OAuth, idempotent client order IDs, realtime holdings | Quotes are delayed 15 minutes in SnapTrade, so keep external trusted quote gate. One active connection limitation per E*TRADE account. |
| **Alpaca direct API** | **Yes** | Good, but direct is cleaner | Official Alpaca docs support live and paper Trading API accounts and order creation | Best developer experience, but it means opening/funding Alpaca. SnapTrade Alpaca requires own keys and notes copy-trading apps are not eligible unless RIA licensed. |
| **Alpaca via SnapTrade** | **Yes** | Strong if keys accepted | SnapTrade lists stock/ETF/options, trade impact, fractional dollars/shares, real-time quotes, idempotent client order IDs | Requires own Alpaca key; compliance/licensing caveat for copy-trading apps. |
| **Tradier direct API** | **Yes** | Direct is better than SnapTrade here | Tradier says account holders get API access for account data, market data, and equity/options trades | SnapTrade Tradier requires own key and mentions a one-time $500 Tradier charge. Direct Tradier avoids that if account API access is enough. |
| **Tradier via SnapTrade** | **Yes** | Possible, not first choice | SnapTrade lists stock/ETF/options and equity trade impact | Own key + $500 one-time fee noted by SnapTrade; no idempotent order placement. |
| **TradeStation** | **Yes** | Possible | SnapTrade and TradeStation both document equities/options/futures order routing | Requires own broker API keys; no SnapTrade idempotent order placement. More setup friction. |
| **Webull via SnapTrade** | **Yes for supported accounts** | Possible but avoid for this use case | SnapTrade lists stock/ETF/options and real-time quotes | SnapTrade says Webull cash accounts are read but not supported for trading. That conflicts with the Fidelity-cash/Youth style this project is trying to preserve. |
| **Schwab Trading via SnapTrade** | **Yes, gated** | Not first choice | SnapTrade lists stock/ETF/options and real-time quotes | Requires commercial Schwab API keys and ThinkOrSwim-enabled accounts; no order impact; no idempotent order placement. |

### Sources For The Candidate Matrix

- SnapTrade Fidelity page: says Fidelity connects via OAuth/2FA but does not offer trade placement.
- SnapTrade brokerage support page: lists supported brokerages and product-capability pages.
- SnapTrade E*TRADE page: lists `Place Trade ETF Option Stock`, `Trade Impact Equity Option`, OAuth, realtime holdings, and idempotent client order IDs.
- SnapTrade Alpaca page: lists `Place Trade ETF Option Stock`, equity trade impact, fractional shares/dollars, real-time quotes, idempotent client order IDs, and own-key/RIA caveat.
- SnapTrade Webull page: lists trading support but says Webull cash accounts are read in and not supported for trading.
- SnapTrade Tradier page: lists trading support but notes own key and a one-time Tradier charge.
- Alpaca official docs: live Trading API accounts and order-creation endpoint exist; orders may be rejected if account is not authorized or buying power is insufficient.
- Tradier official docs/site: API supports account data, market data, and equity/options order placement.
- TradeStation official docs/site: API supports order execution/routing for equities, options, and futures.

### Practical Recommendation

If the user is staying with Fidelity:

1. **Keep local Fidelity execution.**
   - This is the only working Fidelity order-placement route found for this repo.
   - It should be hardened and isolated to execution only.

2. **Use SnapTrade Fidelity for data.**
   - Use it for accounts, balances, holdings, transactions, and executed order history.
   - Do not use it for order submission.

3. **Do not choose Tradier, Alpaca, E*TRADE, Schwab, Webull, Public, or TradeStation as the answer to "trade my Fidelity account."**
   - Those are different brokers. They may trade through their own accounts, not through Fidelity.

If the user later becomes willing to open a new broker account:

1. **Pick Alpaca direct** for the fastest, least fragile API-native implementation.
   - Best for algorithmic trading.
   - Has paper/live workflow.
   - Has idempotent order IDs.
   - Fits the repo's existing compliance architecture well.

2. **Pick E*TRADE via SnapTrade** if the user specifically wants SnapTrade to be the broker abstraction layer.
   - Strongest SnapTrade retail-broker candidate found.
   - Must keep external quote gateway because SnapTrade reports E*TRADE quotes as delayed.
   - Must test cash buying power, order impact, and account category before live use.

3. **Do not pick Webull cash via SnapTrade** for the current cash/Youth-style workflow.
   - SnapTrade explicitly says Webull cash accounts are not supported for trading.

If the user will not open/move to a tradable API broker:

- Keep local Fidelity execution.
- Use SnapTrade Fidelity for read-only account data.
- Label SnapTrade Fidelity clearly as `data_only`.

### Decision Gate Before Any Implementation

Before implementing order routing for a new broker, collect:

- Broker account opened and approved.
- API access confirmed.
- Account type identified: cash, margin, IRA, youth, paper.
- Sandbox/paper account available, if any.
- Order impact/preview support confirmed.
- Idempotency behavior confirmed.
- Cash buying power field confirmed.
- Order status polling behavior confirmed.
- Any day-trading, settlement, or product restrictions documented.

No live order implementation starts until those are checked off.

---

## ✅ IMPLEMENTATION STATUS (2026-07-07)

Prompt = plan (verified): keep local Playwright execution; SnapTrade Fidelity = **data_only**; harden the local order path; reconcile fills via SnapTrade when available.

**Shipped (full suite 1591 green, +28 broker tests):**
- **Capability gate** — `tradingagents/brokers/capability.py`: SnapTrade↔Fidelity pinned `place_equity_order=false` (`data only`); `can_place_orders` fails closed for unknown pairs.
- **Order-ticket verifier** (the core hardening) — `tradingagents/brokers/order_verifier.py`: pre-submit `verify_order_ticket(intent, preview_text)` checks account mask / symbol / side / quantity / order type / limit price / est-cost-vs-cap AND that the live Fidelity preview page reflects the intent. Fails closed.
- **Wired into the Playwright path** — `web/api/fidelity.py`: both the BUY inner and the EXIT inner now (a) honor a dedicated kill switch `FIDELITY_LOCAL_EXECUTION_ENABLED` (default on), (b) run the ticket verifier before the Place-Order click and BLOCK on mismatch, (c) capture a pre-submit **screenshot + masked audit record** (`tmp/fidelity_order_audit.jsonl`, `tmp/order_audit/*.png`, 0600). Human approval + step-up 2FA + `validate_live_order` unchanged.
- **Fill reconciler** — `tradingagents/brokers/reconcile.py`: pure `reconcile_fill(intent, snaptrade_orders)` matches a submitted order to SnapTrade executed history (filled/pending/not_found/no_data + discrepancies). Ready for the post-submit hook once SnapTrade data is live.
- **Dormant data_only provider** — `web/broker/snaptrade_data.py`: read-only, `SNAPTRADE_ENABLED`-gated (default off), lazy SDK import, **no place/preview method at all**, normalized `BrokerPosition/Balance/Order` with a `stale` flag (unknown freshness ⇒ stale, fail-safe).
- **Provider-neutral routes** — `web/api/broker_routes.py` (`/api/broker/capabilities`, `/api/broker/fidelity/status`): surface the `data only` label + capability. Data-fetch route returns dormant/501 until linkage.
- **Env** — `.env.example`: `FIDELITY_LOCAL_EXECUTION_ENABLED`, `SNAPTRADE_ENABLED`, `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`.

**Still required before SnapTrade data goes live (Phase 0/1, needs vendor):** SnapTrade account + API keys; per-user `userId`/`userSecret` storage + connect-URL/disconnect flow; wire the read routes + the post-submit reconciler to real SnapTrade calls; parity validation (Phase 2). No execution ever routes through SnapTrade — data only.
