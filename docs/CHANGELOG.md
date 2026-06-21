# Changelog

All notable changes to **Agentic Trader** (a private fork of TradingAgents) are
documented here. Entries after 1.1.0 are this fork's private work.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes within the 0.x line are called out explicitly.

## [1.2.0] — 2026-06-21

Consolidated release notes for the fork's daily development from 2026-06-03 → 2026-06-21.
(Replaces the former root-level `*_UPDATES.md` / `*_LOG.md` diaries, now removed.)

### Added

- **Live broker execution + compliance kill-chain** — real-money orders now flow through
  `tradingagents/compliance.py::validate_live_order`: LIMIT-only, ≤10% of account per
  position, ≤$50k/order, and a trusted+fresh execution quote via `PreTradeGate`. Two
  master switches (`LIVE_TRADING_HARD_BLOCKED` source constant + `LIVE_TRADING_ENABLED`
  env) plus per-trade step-up 2FA on every order endpoint. Trusted quote sources
  (`finnhub`/`twelve_data`/`fmp`) via `tradingagents/data/quote_gateway.py`. See
  `SECURITY.md`.
- **Holdings Brain** (`tradingagents/portfolio/holdings_brain.py`, `web/api/holdings_brain.py`,
  `/api/thematic/brain/*`) — AI assessment of real broker holdings (hold/trim/add/exit/
  set-stop) that queues propose-only HIL proposals. Roth/retirement/non-equity accounts
  protected by three layers (instrument filter, account-type denylist, broker kill-switch;
  `FIDELITY_PROTECTED_ACCOUNTS`).
- **Webull integration** (`web/api/webull_portfolio.py`) — status/login/MFA/trade-pin/
  positions/orders via the `fidelity-api`/webull library.
- **Performance tracker** (`web/api/performance.py`, `frontend/src/pages/Performance/`) —
  deposit-adjusted P&L, cash-flow ledger, realized-from-tradelog, daily auto-capture from
  real Fidelity data; Overview/Calendar/Holdings dashboard.
- **Step-up 2FA methods** — added PBKDF2 passcode (salted, lockout) and passkey/WebAuthn
  alongside TOTP (`web/twofa.py`, `web/api/twofa_routes.py`).
- **TradingView-style trade charts → SMS** (`tradingagents/portfolio/chart.py`,
  `GET /api/market/trade-chart.png`) — auto-generated trade charts attached to SMS alerts;
  added `trendspyg` (Google Trends) dependency and a public-host upload fallback.
- **Portfolio-aware position sizing** (`tradingagents/portfolio/position_sizer.py`) —
  whole-book multi-factor sizer (conviction × quality × inverse-vol × correlation, hard
  caps per-position/sector/heat/cash) on the thematic HIL path.
- **Sentiment-weighted buzz** (`tradingagents/screening/buzz_score.py`) and a **tweet-intent
  classifier** (`tradingagents/screening/tweet_intent.py`) — buzz now reflects conviction
  (bull/bear/neutral) and stops padding on crowd selling pressure.
- **Alert cooldown + signal persistence** (`web/alert_cooldown.py`) — per-ticker cooldown,
  persisted pending queue (TTL), scan min-interval — kills thematic alert spam / turnover.
- **Fidelity holdings cache** — stale-while-revalidate snapshot cache so the Broker page
  loads instantly; keepalive warms it. Display reads only; execution still scrapes fresh.
- **Free-AI features** — shared `$0`-budget LLM helper (Cloudflare free models + OpenRouter
  fallback) for ticker validation, catalyst materiality, news-driven exit rescue, and
  red-flag deepening.

### Changed

- **Fidelity reliability** — auto re-login (no TOTP), encrypted credential store,
  trust-device support, cache-lie fix; SPAXX core money-market now recognized in cash
  scrape so live sizing counts it.
- **Thematic scoring** — fixed score compression (trusted-twitter flat-cap, composite
  reweighting), new `MIN_COMPOSITE_SCORE` gate (`THEMATIC_MIN_SIGNAL_SCORE`), sentiment-
  aware composite, adaptive sizing, let-winners-run trailing stops.
- **Frontend** — Broker / HIL / Performance / Settings page redesigns on the shared
  design-token system; HIL split into Approvals | Settings with a Holdings-Brain
  proposals card.

### Fixed

- **Thematic scanner was silently frozen** (Jun 2 → Jun 19, stale cache shown as live) —
  added per-source scrape timeout, moved sync DDGS off the event loop, and override of a
  stale `running` status that had blocked all future scans.
- **Thematic turnover / alert spam** — pending queue + brain proposals were wiped and
  rebuilt every cycle with no cooldown, causing one-scan flip-flop alerts.

### Security

- Hardened the live-order kill-chain and made every order endpoint require per-trade
  step-up 2FA; enforced protected-account (Roth/retirement) isolation at three layers.
  See `SECURITY.md` for the full model.

## [1.1.0] — 2026-06-02

### Added

- **Thematic portfolio Phase 3 safety gates** — `POST /api/thematic/trade` now
  enforces: R:R gate (auto-widens target to `min_rr` instead of rejecting, returns
  `warnings[]`); conviction-scaled position size (conviction 1→0.4×, 10→1.5×);
  portfolio heat + daily loss circuit breakers via `_check_portfolio_circuit_breakers`;
  real 14-day ATR from yfinance (replaced hardcoded `price × 0.02` proxy).
- **Auto-trade loop** — after each scan, users with `auto_trade_paper=True` in HIL
  settings automatically have confirmed signals (2+ scan appearances, score ≥ 40)
  executed via `_auto_execute_confirmed_signals()`. Spike-only signals never auto-execute.
- **Thematic Phase 1+2** (shipped 2026-06-01) — 34 improvements including: multi-source
  confirmation bonus (+3 per extra source, cap +15); insider+social combo bonus (+8);
  per-source breakdown stored per signal; `_validate_pick()` sanitizes all AI output;
  non-blocking yfinance ticker validation via executor; auto-exit monitor checking stop/
  target/max-hold/buzz-collapse/buzz-decay after every scan; atomic writes throughout;
  social score pulled from live scan history; 4-hour auto-scan loop (`THEMATIC_AUTO_SCAN=true`).
- **15 thematic data sources** — added StockAnalysis, Marketaux, Press Releases
  (BusinessWire/PRN/GlobeNewswire), Finviz (top gainers + unusual vol), RSS news feeds
  (MarketWatch/CNBC/WSJ/NYT/Bloomberg), Yahoo Finance movers, Brave Search, scan memory
  (historical persistence bonus), Google News RSS, SeekingAlpha RSS to original 9 sources.
- **Frontend: Signals + Logs pages** — new nav items with error boundaries; Signals page
  shows thematic signal queue with source breakdown chips; Logs page streams server logs.
- **Frontend nav restructure** — error boundary wrappers on every page; nav items reordered.
- **`ta` CLI operational toolkit** — `ta ml retrain`, `ta paper run`, `ta paper status`
  and related subcommands for managing trading operations from the terminal.
- **Self-healing autofix monitor** (`scripts/autofix_monitor.py`) — watches for broken
  paper trading processes, auto-restarts, sends SMS/email alerts on failure.
- **Daily log rotation** via launchd (`scripts/rotate_logs.py`) — 10 MB cap, 7 gzip
  archives, runs at 00:05 daily.
- **Redundancy layer** — tunnel launchd plist, state file backups, deep health-check
  endpoint; CodeQL security scan integration.
- **Bulletproof news** — 3-source fallback chain (Google News RSS → yfinance → DDG) for
  Dashboard and CandidatePanel news feeds.
- **Cloudflare AI gateway** support for thematic auto-picker; falls back to OpenRouter.
- **`scripts/backtest_thematic_signals.py`** — validates thematic signal quality from
  scan history + exit log; reports WR/return by score bucket, source combo, and exit reason.

### Changed

- **Cycle 46 — retrain backtest row count fix** — removed `--min-price 15.0` and
  `--min-adv 500000` from the training backtest command. These filters cut ~66% of data,
  reducing training rows to ~420 (well below quality-gate minimum). The 2026-05-30 retrain
  failed (gate: WF ROC ≥ 0.49) because of this filtering. Removed from
  `scripts/retrain_weekly.py` backtest_cmd; price/ADV filters remain at inference time.
- **Cycle 44 — stop geometry fix** — live screener `_ATR_STOP` corrected 0.7 → 1.0 ATR
  to match ML label geometry. EV/trade improved +0.117%/trade (+87%) on 1,554-trade
  realized-MAE replay. `min_rr` lowered 1.2 → 1.15 to handle cent-rounding noise.
- **Cycle 45 — portfolio audit remediation** — ~40 fixes across 15 files: vol-penalty
  normalized; B-16 heat taper in allocator; SR-8 Kelly fix (confidence as output
  multiplier, not p-discount); DL-1 ATR-path safety layers; E-9 conditional time-exit;
  E-12 breakeven lock; sector cap reads real sector; correlation off-by-one; prediction
  grader exit-reason normalization; production safety high-water-mark drawdown.
- **CandidatePanel redesigned** as signal cards with conviction badges and news chips.
- **Retrain pipeline** — `retrain_weekly.py` now uses `--skip-thursday --skip-vix-low-vol
  --skip-extended-bounce --target-mult 1.2 --stop-mult 1.0` in backtest command.

### Fixed

- **Autofix monitor false-positive alerts** — argparse `%` in help strings caused
  `TypeError: not all format codes converted` when autofix monitor printed help.
- **Paper trading VIX low-vol filter** — was computed but not applied to paper_trade_today.py
  (Cycle 7 fix carried through to all execution paths).
- **Thematic paper trade fake ATR** — `round(price * 0.02, 4)` replaced with real 14-day ATR.
- **Double-load in `get_thematic_portfolio`** — was calling `_load(user["email"])` twice.
- **Variable name collision in scan** — `results` from `asyncio.gather()` overwritten by
  inner DDG loop; renamed to `gather_results`.

## [1.0.0] — 2026-05-21

### Added

- **First official release** of TradingAgents as a complete, production-ready
  multi-agent LLM trading framework. Represents the culmination of full-stack
  development covering core agent orchestration, data integration, paper trading
  simulation, live trading verification, web dashboard, API, and comprehensive
  documentation.
- All features from v0.2.4 and prior versions now stable and integrated.

## [0.2.4] — 2026-04-25

### Added

- **Structured-output decision agents.** Research Manager, Trader, and Portfolio
  Manager now use `llm.with_structured_output(Schema)` on their primary call
  and return typed Pydantic instances. Each provider's native structured-output
  mode is used (`json_schema` for OpenAI / xAI, `response_schema` for Gemini,
  tool-use for Anthropic, function-calling for OpenAI-compatible providers).
  Render helpers preserve the existing markdown shape so memory log, CLI
  display, and saved reports keep working unchanged. (#434)
- **LangGraph checkpoint resume** — opt-in via `--checkpoint`. State is saved
  after each node so crashed or interrupted runs resume from the last
  successful step. Per-ticker SQLite databases under
  `~/.tradingagents/cache/checkpoints/`. `--clear-checkpoints` resets them. (#594)
- **Persistent decision log** replacing the per-agent BM25 memory. Decisions
  are stored automatically at the end of `propagate()`; the next same-ticker
  run resolves prior pending entries with realised return, alpha vs SPY, and
  a one-paragraph reflection. Override path with `TRADINGAGENTS_MEMORY_LOG_PATH`.
  Optional `memory_log_max_entries` config caps resolved entries; pending
  entries are never pruned. (#578, #563, #564, #579)
- **DeepSeek, Qwen (Alibaba DashScope), GLM (Zhipu), and Azure OpenAI**
  providers, plus dynamic OpenRouter model selection.
- **Docker support** — multi-stage build with separate dev and runtime images.
- **`scripts/smoke_structured_output.py`** — diagnostic that exercises the
  three structured-output agents against any provider so contributors can
  verify their setup with one command.
- **5-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell) used
  consistently by Research Manager, Portfolio Manager, signal processor, and
  the memory log; Trader keeps 3-tier (Buy / Hold / Sell) since transaction
  direction is naturally ternary.
- **Pytest fixtures** — lazy LLM client imports plus placeholder API keys so
  the test suite runs cleanly without credentials. (#588)

### Changed

- **`backend_url` default is now `None`** rather than the OpenAI URL. Each
  provider client falls back to its native default. The previous default
  leaked the OpenAI URL into non-OpenAI clients (e.g. Gemini), producing
  malformed request URLs for Python users who switched providers without
  overriding `backend_url`. The CLI flow is unaffected.
- All file I/O passes explicit `encoding="utf-8"` so Windows users no longer
  hit `UnicodeEncodeError` with the cp1252 default. (#543, #550, #576)
- Cache and log directories moved to `~/.tradingagents/` to resolve Docker
  permission issues. (#519)
- `SignalProcessor` reads the rating from the Portfolio Manager's rendered
  markdown via a deterministic heuristic — no extra LLM call.
- OpenAI structured-output calls default to `method="function_calling"` to
  avoid noisy `PydanticSerializationUnexpectedValue` warnings emitted by
  langchain-openai's Responses-API parse path. Same typed result, no warnings.

### Fixed

- Empty memory no longer triggers fabricated past-lessons in agent prompts;
  the memory-log redesign makes this structurally impossible since only the
  Portfolio Manager consults memory and only when entries exist. (#572)
- Tool-call logging processes every chunk message, not just the last one, and
  memory score normalization handles empty score arrays. (#534, #531)

### Removed

- `FinancialSituationMemory` (the per-agent BM25 system) and the dead
  `reflect_and_remember()` plumbing; subsumed by the persistent decision log.
- Hardcoded Google endpoint that caused 404 when `langchain-google-genai`
  changed its API path. (#493, #496)

### Contributors

Thanks to everyone who shaped this release through code, design, and reports:

- [@claytonbrown](https://github.com/claytonbrown) — checkpoint resume (#594), test fixtures (#588), design feedback on cost tracking (#582) and structured validation (#583)
- [@Bcardo](https://github.com/Bcardo) — memory-log redesign (#579), empty-memory hallucination report (#572), encoding fix proposal (#570)
- [@voidborne-d](https://github.com/voidborne-d) — memory persistence design (#564), portfolio manager state fix (#503)
- [@mannubaveja007](https://github.com/mannubaveja007) — structured-output feature request (#434)
- [@kelder66](https://github.com/kelder66) — RAM-only memory issue (#563)
- [@Gujiassh](https://github.com/Gujiassh) — tool-call logging fix (#534), test stub PR (#533)
- [@iuyup](https://github.com/iuyup) — memory score normalization fix (#531)
- [@kaihg](https://github.com/kaihg) — Google base_url fix (#496)
- [@32ryh98yfe](https://github.com/32ryh98yfe) — Gemini 404 report (#493)
- [@uppb](https://github.com/uppb) — OpenRouter dynamic model selection (#482)
- [@guoz14](https://github.com/guoz14) — OpenRouter limited-model report (#337)
- [@samchenku](https://github.com/samchenku) — indicator name normalization (#490)
- [@JasonOA888](https://github.com/JasonOA888) — y_finance pandas import fix (#488)
- [@tiffanychum](https://github.com/tiffanychum) — stale import cleanup (#499)
- [@zaizou](https://github.com/zaizou) — Docker permission issue (#519)
- [@Stosman123](https://github.com/Stosman123), [@mauropuga](https://github.com/mauropuga), [@hotwind2015](https://github.com/hotwind2015) — Windows encoding bug reports (#543, #550, #576)
- [@nnishad](https://github.com/nnishad), [@atharvajoshi01](https://github.com/atharvajoshi01) — encoding fix proposals (#568, #549)

## [0.2.3] — 2026-03-29

### Added

- **Multi-language output** for analyst reports and final decisions, with a
  CLI selector. Internal agent debate stays in English for reasoning quality. (#472)
- **GPT-5.4 family models** in the default catalog, with deep/quick model split.
- **Unified model catalog** as a single source of truth for CLI options and
  provider validation.

### Changed

- `base_url` is forwarded to Google and Anthropic clients so corporate proxies
  work consistently across providers. (#427)
- Standardised the Google `api_key` parameter to the unified `api_key` form.

### Fixed

- Backtesting fetchers no longer leak look-ahead data when `curr_date` is in
  the middle of a fetched window. (#475)
- Invalid indicator names from the LLM are caught at the tool boundary instead
  of crashing the run. (#429)
- yfinance news fetchers respect the same exponential-backoff retry as price
  fetchers. (#445)

### Contributors

- [@ahmedk20](https://github.com/ahmedk20) — multi-language output (#472)
- [@CadeYu](https://github.com/CadeYu) — model catalog typing (#464)
- [@javierdejesusda](https://github.com/javierdejesusda) — unified Google API key parameter (#453)
- [@voidborne-d](https://github.com/voidborne-d) — yfinance news retry (#445)
- [@kostakost2](https://github.com/kostakost2) — look-ahead bias report (#475)
- [@lu-zhengda](https://github.com/lu-zhengda) — proxy/base_url support request (#427)
- [@VamsiKrishna2021](https://github.com/VamsiKrishna2021) — invalid indicator crash report (#429)

## [0.2.2] — 2026-03-22

### Added

- **Five-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell)
  introduced for the Portfolio Manager.
- **Anthropic effort level** support for Claude models.
- **OpenAI Responses API** path for native OpenAI models.

### Changed

- `risk_manager` renamed to `portfolio_manager` to match the role description
  shown in the CLI display.
- Exchange-qualified tickers (e.g. `7203.T`, `BRK.B`) preserved across all
  agent prompts and tool calls.
- Process-level UTF-8 default attempted for cross-platform consistency
  (note: this approach did not actually take effect; replaced in v0.2.4 with
  explicit per-call `encoding="utf-8"` arguments).

### Fixed

- yfinance rate-limit errors are retried with exponential backoff. (#426)
- HTTP client SSL customisation is supported for environments that need
  custom certificate bundles. (#379)
- Report-section writes handle list-of-string content gracefully.

### Contributors

- [@CadeYu](https://github.com/CadeYu) — exchange-qualified ticker preservation (#413)
- [@yang1002378395-cmyk](https://github.com/yang1002378395-cmyk) — HTTP client SSL customisation (#379)

## [0.2.1] — 2026-03-15

### Security

- Patched `langchain-core` vulnerability (LangGrinch). (#335)
- Removed `chainlit` dependency affected by CVE-2026-22218.

### Added

- `pyproject.toml` build-system configuration; the project now installs via
  modern packaging tooling.

### Removed

- `setup.py` — dependencies consolidated to `pyproject.toml`.

### Fixed

- Risk manager reads the correct fundamental report source. (#341)
- All `open()` calls receive an explicit UTF-8 encoding (initial pass).
- `get_indicators` tool handles comma-separated indicator names from the LLM. (#368)
- `Propagation` initialises every debate-state field so risk debaters never
  see missing keys.
- Stock data parsing tolerates malformed CSVs and NaN values.
- Conditional debate logic respects the configured round count. (#361)

### Contributors

- [@RinZ27](https://github.com/RinZ27) — `langchain-core` security patch (#335)
- [@Ljx-007](https://github.com/Ljx-007) — risk manager fundamental-report fix (#341)
- [@makk9](https://github.com/makk9) — debate-rounds config issue (#361)

## [0.2.0] — 2026-02-04

This is the largest release since the initial public version. The framework
moved from single-provider to a multi-provider architecture and grew several
production-ready surfaces.

### Added

- **Multi-provider LLM support** (OpenAI, Google, Anthropic, xAI, OpenRouter,
  Ollama) via a factory pattern, with provider-specific thinking configurations.
- **Alpha Vantage** integration as a configurable primary data provider, with
  yfinance as a community-stability fallback.
- **Footer statistics** in the CLI: real-time tracking of LLM calls, tool
  calls, and token usage via LangChain callbacks.
- **Post-analysis report saving** — the framework writes per-section markdown
  files (analyst reports, debate transcripts, final decision) when a run
  completes.
- **Announcements panel** — fetches updates from `api.tauric.ai/v1/announcements`
  for the CLI welcome screen.
- **Tool fallbacks** so a single vendor outage does not stop the pipeline.

### Changed

- Risky / Safe risk debaters renamed to **Aggressive / Conservative** for
  consistency with the displayed agent labels.
- Default data vendor switched to balance reliability and quota across
  community deployments.
- Ollama and OpenRouter model lists updated; default endpoints clarified.

### Fixed

- Analyst status tracking and message deduplication in the live display.
- Infinite-loop guard in the agent loop; reflection and logging hardened.
- Various data-vendor implementation bugs and tool-signature mismatches.

### Contributors

This release is the first with substantial outside contributions; many community
PRs from late 2025 also landed here.

- [@luohy15](https://github.com/luohy15) — Alpha Vantage data-vendor integration (#235)
- [@EdwardoSunny](https://github.com/EdwardoSunny) — yfinance fetching optimisations (#245)
- [@Mirza-Samad-Ahmed-Baig](https://github.com/Mirza-Samad-Ahmed-Baig) — infinite-loop guard, reflection, and logging fixes (#89)
- [@ZeroAct](https://github.com/ZeroAct) — saved results path support (#29)
- [@Zhongyi-Lu](https://github.com/Zhongyi-Lu) — `.env` gitignore (#49)
- [@csoboy](https://github.com/csoboy) — local Ollama setup (#53)
- [@chauhang](https://github.com/chauhang) — initial Docker support attempt (#47, later reverted; the merged Docker support shipped in v0.2.4)

## [0.1.1] — 2025-06-07

### Removed

- Static site assets that had been bundled with v0.1.0; the public site now
  lives separately.

## [0.1.0] — 2025-06-05

### Added

- **Initial public release** of the TradingAgents multi-agent trading
  framework: market / sentiment / news / fundamentals analysts; bull and bear
  researchers; trader; aggressive, conservative, and neutral risk debaters;
  portfolio manager. LangGraph orchestration, yfinance data, per-agent
  BM25 memory, single-provider OpenAI integration, interactive CLI.

[1.0.0]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.4...v1.0.0
[0.2.4]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TauricResearch/TradingAgents/releases/tag/v0.1.0
