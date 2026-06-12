# Top 50 AI Trading Systems — Comprehensive Review, Claim Verification & Implementation Plan

**Date:** 2026-06-10
**Method:** GitHub API star/activity verification (live pull 2026-06-10), academic-paper cross-checks (arXiv), independent-review triangulation, live-fund track records. Every performance claim graded for truth.
**Purpose:** decide what this repo (a TradingAgents v0.2.4 derivative) should adopt, ignore, or guard against.

---

## Part 1 — The 50 Systems

### A. Open-source — LLM / multi-agent (stars verified via GitHub API 2026-06-10)

| # | System | Stars (verified) | Core claim | Verdict |
|---|--------|------------------|-----------|---------|
| 1 | **TradingAgents** (TauricResearch) | **84,823** ✅ | Multi-agent LLM beats baselines 6–25% cum. return | ⚠️ TRUE-BUT-WEAK: 3-month 2024 backtest, 5 mega-cap tech only; Sharpe 5–8 flagged anomalous by authors themselves; LLM training data covers backtest period (leakage); never live-deployed |
| 2 | **OpenBB** | 68,842 ✅ | Research/data workspace, no alpha claims | ✅ HONEST — tooling, not alpha |
| 3 | **ai-hedge-fund** (virattt) | 59,927 ✅ | Investor-persona agents | ✅ HONEST — author explicitly: "not for real trading," educational |
| 4 | **AI-Trader** (HKUDS) | 19,474 ✅ | "100% fully-automated agent-native trading" | ⚠️ UNVERIFIED — live arena exists, no audited returns |
| 5 | **FinGPT** | 20,455 ✅ | Open financial LLMs, sentiment alpha | ⚠️ MIXED — sentiment signals real but small; return claims from narrow backtests |
| 6 | **FinRobot** | 7,220 ✅ | AI agent platform for financial analysis | ✅ honest scope (analysis, not returns) |
| 7 | **FinRL** | 15,382 ✅ | DRL ensemble Sharpe 1.53 (paper) | ⚠️ FRAGILE — own FinRL-Meta admits survivorship bias + backtest overfitting; DRL agents "overfit individual validation sets"; later FinRL Contests added PBO rejection at 10% significance because of it |
| 8 | **RD-Agent** (Microsoft) | 13,397 ✅ | Automated factor/model R&D loop | ✅ promising engineering; alpha claims modest |
| 9 | **FinRL-Meta** | — | Market environments + benchmarks | ✅ HONEST — explicitly catalogs the 3 failure modes: low SNR, survivorship bias, backtest overfitting |
| 10 | **nofx / ATLAS / OpenFinClaw / Vibe-Trading** (agent wave 2025-26) | <5k each | self-evolving agent competition | ❌ UNPROVEN — no audited track records anywhere |

### B. Open-source — quant platforms & backtesters (stars verified)

| # | System | Stars | Claim | Verdict |
|---|--------|-------|-------|---------|
| 11 | **Qlib** (Microsoft) | 44,223 ✅ | AI quant platform; Alpha158/360 factor sets; published IC benchmarks | ✅ MOSTLY TRUE — benchmarks reproducible, IC ~0.03–0.05 honest (small, real) |
| 12 | **freqtrade** | 51,289 ✅ | Crypto bot framework + FreqAI ML | ✅ honest: framework, "no profit guarantee" in docs |
| 13 | **Lean / QuantConnect** | 19,792 ✅ | Institutional-grade engine | ✅ TRUE as engine; community strategy returns unaudited |
| 14 | **hummingbot** | 18,849 ✅ | Market-making/HFT framework | ✅ honest; MM profitability depends on spreads/inventory risk |
| 15 | **gs-quant** (Goldman) | 10,601 ✅ | Quant toolkit | ✅ tooling only |
| 16 | **StockSharp** | 10,068 ✅ | Trading platform | ✅ tooling only |
| 17 | **backtesting.py** | 8,494 ✅ (last push 2025-12) | lightweight backtester | ✅ honest; ⚠️ maintenance slowing |
| 18 | **jesse** | 8,002 ✅ | crypto research/live bot | ✅ honest framework |
| 19 | **vectorbt** | 7,848 ✅ | "thousands of backtests in seconds" | ✅ TRUE — vectorized sweeps are its real edge |
| 20 | **OctoBot** | 6,048 ✅ | AI/grid/DCA crypto bot | ⚠️ "AI" is thin (basic strategies + TA) |
| 21 | **Superalgos** | 5,502 ✅ | visual-scripting ML trading | ⚠️ complexity ≠ alpha; no audited results |
| 22 | **zvt** | 4,164 ✅ | modular quant framework | ✅ tooling |
| 23 | **backtrader** | ~17k | classic Python backtester | ✅ honest; effectively in maintenance mode |
| 24 | **zipline(-reloaded)** | ~19k orig | Quantopian engine | ✅ honest; Quantopian itself died 2020 — instructive: 300k users, crowdsourced alpha, still couldn't run a fund |
| 25 | **TensorTrade** | ~5k | RL trading framework | ⚠️ abandoned-ish; RL demos don't survive OOS |
| 26 | **rqalpha** | ~6k | CN-market backtester | ✅ tooling |
| 27 | **PGPortfolio** | ~2k | DRL portfolio mgmt (paper: 4-fold returns crypto 2017) | ❌ FAILED REPLICATION — community reruns on later data lose money; classic regime-overfit |
| 28 | **stockpredictionai** (borisbanushev) | ~4k | GAN+RL stock prediction "demo" | ❌ NOTORIOUS — no runnable code for headline results; do not imitate |
| 29 | **abu** | ~14k | CN quant system | ✅ tooling |
| 30 | **catalyst / zenbot / Gekko / blackbird** | — | crypto bots c.2017-18 | ❌ ALL DEAD — survivor lesson: bot frameworks die when incentives/markets shift |
| 31 | **pyfolio / alphalens / empyrical** | — | Quantopian analytics suite | ✅ TRUE — industry-standard tearsheet/factor analysis; maintained forks |
| 32 | **skfolio** | ~4k | sklearn-style portfolio optimization | ✅ solid, honest |
| 33 | **DeepDow** | ~1k | deep-learning portfolio weights | ⚠️ research toy |
| 34 | **TradingGym / btgym / TradzQAI** | <2k | RL training environments | ⚠️ research toys |
| 35 | **crypto-signal** | ~5k | TA signal bot | ✅ honest TA tooling |

### C. Commercial platforms & funds — where real money meets claims

| # | System | Headline claim | Verified truth |
|---|--------|---------------|----------------|
| 36 | **Trade Ideas / Holly AI** | strategies selected at 60%+ WR, 2:1 RR; ~25%/yr simulated | ⚠️ PARTLY TRUE — publishes full trade log (rare transparency), but independent reviewers note display cherry-picking; **real-user returns ~15–20% after frictions vs 25% simulated — a 5–10pp sim-to-live gap** |
| 37 | **TrendSpider** | TA automation | ✅ honest tooling claims |
| 38 | **Tickeron** | "75% win rate, 56%+ annualized" multi-agent AI | ❌ MARKETING — backtest/forward-test only, no third-party audit, sells the claim in its own blog |
| 39 | **Danelfin** | "70.24% win rate for AI score ≥8 (3-mo holds)" | ⚠️ BACKTESTED, NOT LIVE — provides an audit tool (good); small 60-day forward test showed +3.2% vs SPX (modest, n tiny) |
| 40 | **Kavout** (K-Score) | ML stock scores | ⚠️ same pattern: backtest claims, no audited live record |
| 41 | **Composer** | no-code "AI" strategies | ⚠️ strategies are user-built; platform makes no alpha claim itself |
| 42 | **Alpha Picks** (Seeking Alpha) | +308.3% since 2022-07, 73% WR | ✅ **CLOSEST TO VERIFIED** — live documented positions, performance calculated by S&P Global (independent third party). Note: launched into a bull market; short history |
| 43 | **AIEQ** (Amplify AI-Powered Equity ETF, IBM Watson) | "AI picks stocks" — live ETF since Oct 2017 | ❌ **THE DEFINITIVE LIVE TEST: UNDERPERFORMS.** Avg annual ~7.07% vs S&P 11.68% since inception; 0.80% fee vs 0.09%; higher vol; lower Sharpe (1.56 vs 2.19 recent). 8+ years of real money says the AI added negative value after fees |
| 44 | **Numerai** | crowdsourced meta-model hedge fund | ✅ REAL — 2024: +25.45% net, one down month; 2025: +8% net vs 3% quant-index through Oct; AUM $60M→$550M; JPM AM pledge up to $500M. Caveat: short public series, capacity untested at $1B+ |
| 45 | **WorldQuant BRAIN** | crowdsourced alphas | ✅ real institutional pipeline; individual "alphas" mostly decay |
| 46 | **Darwinex** | trader-strategy marketplace w/ risk engine | ✅ honest model: verified track records, most DARWINs underperform — instructive base rate |
| 47 | **Collective2** | strategy marketplace | ⚠️ verified trade logs, but survivorship in the directory is massive |
| 48 | **eToro AI portfolios** | copy/AI portfolios | ⚠️ disclosed: majority of retail CFD accounts lose money |
| 49 | **Quantopian** (RIP 2020) | crowdsourced hedge fund | ❌ FAILED — 300k quants, free data, real capital: fund shut down. The strongest single data point against "more backtests → alpha" |
| 50 | **Renaissance Medallion** (reference anchor) | ~66% gross/39% net annualized for 30 yrs | ✅ real but: closed fund, capacity-capped, thousands of PhD-years, HFT-adjacent infra. Existence proof that systematic alpha exists; not evidence anyone selling you a bot has it |

---

## Part 2 — What the Verified Evidence Actually Says

**Academic stress-tests (the most important findings):**

1. **FINSABER (arXiv 2505.07078, KDD 2026):** systematic backtests over **two decades and 100+ symbols** show previously reported LLM-trading advantages **deteriorate significantly** under broader cross-sections and longer evaluation. LLM strategies are *overly conservative in bull markets, overly aggressive in bear markets*. Selective evaluation on historical winners (TSLA/AMZN/mega-tech) embeds survivorship + lookahead bias.
2. **LLM lookahead leakage:** pretrained models "know" how the backtest period played out (tests on anonymized data confirm bias exists); news timestamps record publication, not pipeline-ingestion, inflating backtests.
3. **TradingAgents paper itself** (our upstream): 3-month window, 5 mega-caps, Sharpe 5–8 the authors flag as anomalous, 11 LLM calls/decision, never live.
4. **FinRL line:** original ensemble Sharpe 1.53 → later FinRL work had to bolt on *probability-of-backtest-overfitting rejection at 10% significance* because agents overfit validation sets. Community replication of DRL portfolio papers (e.g. PGPortfolio) fails on out-of-sample periods.
5. **Reproducibility base rate:** when 168 teams reran 6 published finance papers with original code+data, only **52% of 1,000 tests** reached the same conclusion.

**Live-money truth anchors (ranked by evidential quality):**

| Anchor | Years live | Result | Lesson |
|--------|-----------|--------|--------|
| AIEQ ETF | 8.5 | loses to SPY after fees | "AI stock picking" at scale ≈ market minus costs |
| Quantopian | 9 | shut down | crowdsourced backtests ≠ fund |
| Numerai | ~8 | beats quant peers recently | ensemble-of-uncorrelated-models + meta-model + skin-in-the-game CAN work |
| Holly AI | ~9 | sim 25% → live 15–20% | expect a **5–10pp sim-to-live haircut** |
| Alpha Picks | 3.5 | +308% (3rd-party calc) | verified ledgers are possible; short history, bull-market launch |
| Medallion | 30+ | legendary | alpha exists, but at capacity+infra levels nobody sells |

**The pattern across all 50:** every system that publishes **audited live results** either (a) underperforms (AIEQ), (b) outperforms modestly vs peers with real risk controls (Numerai), or (c) shows a large sim-to-live gap (Holly). Every system claiming 70%+ win rates or 50%+ annual returns is **selling backtests**. No exceptions found.

---

## Part 3 — Implementation Plan for This Repo

Context: this repo already implements much of what the honest systems recommend — walk-forward + CPCV purge/embargo, triple-barrier labels, isotonic calibration, prediction ledger, pre-trade gate + multi-provider quote gateway, Kelly sizing + DD throttle, deflated Sharpe, Qlib integration, model readiness report. The plan below adopts what the evidence supports and guards against the failure modes the evidence exposes.

### P0 — Anti-self-deception infra (the evidence says this is where everyone dies)

1. **PBO — Probability of Backtest Overfitting** (Bailey/López de Prado; what FinRL had to add).
   - Build on existing `tradingagents/validation/cpcv.py`: CSCV-based PBO estimate over strategy-config sweeps; **reject any config with PBO > 10%** (FinRL Contests threshold).
   - Wire into `model_readiness_report.py` as a hard gate line.
   - Effort: small (CPCV scaffolding exists). Value: highest.
2. **FINSABER-style breadth/duration test.**
   - Extend backtest harness to mandatory regime-split reporting: bull/bear/sideways sub-period returns vs SPY/QQQ, full ticker universe (no winner-only lists), ≥3yr windows.
   - Add the specific FINSABER failure probes: "conservative-in-bull" (capture ratio up-months) and "aggressive-in-bear" (capture ratio down-months).
   - Effort: medium. Closes the exact trap the LLM-trading literature fell into.
3. **Sim-to-live gap report** (the Holly 5–10pp lesson).
   - Daily audit already grades predictions; add an explicit `sim_vs_live` section: ledger entry price vs backtest-assumed price, slippage, spread, gateway-vs-yfinance delta (shadow log already accumulating in `tmp/quote_shadow_log.jsonl`).
   - Publish a running haircut estimate; apply it to every readiness-report CAGR figure.
   - Effort: small (data already logged).

### P1 — Adopt what verified winners actually do

4. **Numerai-pattern ensemble** (the one live outperformer with a transferable mechanism): many weakly-correlated models + meta-model + performance-staked weights.
   - Maps to the existing model-tournament + unified-brain authority weighting (roadmap B6/B7/B9): weight model heads by **rolling out-of-sample contribution**, decay stale models. Correlation-penalize redundant heads (don't average 5 models that saw the same features — the "bad ensemble" the improvement-notes file warns about).
   - Effort: medium; tournament infra exists.
5. **Qlib factor ICs done honestly** — finish `qlib_integration/factor_ic.py` pipeline; accept that good IC is 0.03–0.05 and judge factors on IC stability, not magnitude. Alpha158 subset as candidate features for the next retrain.
6. **vectorbt-style vectorized sweeps** for the pending geometry work (target 1.2→2.0 ATR re-sim, ll_cap sweep, risk_pct sweep): current per-config backtests are too slow to sweep honestly, which biases toward under-testing. Either adopt vectorbt for the MFE/MAE harvest sim or vectorize the existing engine's exit loop.

### P2 — LLM-specific guards (we ARE a TradingAgents derivative)

7. **Anonymization guard for LLM analysis** (from the "Blindfolded LLMs" line of work): when LLM agents grade historical setups or do postmortems, strip tickers/dates → prevents the model "remembering 2024." Cheap: a wrapper on the prompt builders.
8. **News ingestion-lag honesty:** stamp news items with *fetch time*, not publication time, in features and backtests (the documented inflation source). Audit `dataflows/` for which timestamp is used.
9. **LLM cost/latency budget:** upstream paper needs 11 LLM calls/decision. Keep LLM in the candidate-explanation and veto seat, not the per-tick seat (already the architecture — keep it that way).

### P3 — Track-record discipline (Alpha Picks lesson)

10. **Exportable verified ledger:** the prediction ledger is append-only and timestamped pre-outcome — add a signed monthly export (hash-chained JSONL) so the track record is tamper-evident. This is the only thing that separates a real claim from a Tickeron claim.
11. **Kill-criteria, pre-registered:** define now, in writing, what makes us stop: e.g. 6-month live paper underperformance vs SPY after the sim-to-live haircut, or PBO>10% on the deployed config. Quantopian died from never defining this.

### Anti-goals (evidence says do NOT build)

- ❌ DRL end-to-end trading agents (PGPortfolio/TensorTrade replication failures; FinRL's own overfitting mitigations are an admission)
- ❌ Chasing 70%+ WR configurations (every audited system lands 55–73% WR with modest returns; high-WR claims are the #1 marketing tell)
- ❌ More LLM agents in the decision loop (FINSABER: advantages vanish OOS; costs are real)
- ❌ Crowdsourcing/strategy-marketplace mechanics (Quantopian, Collective2, Darwinex base rates)

### Sequencing

| Order | Item | Effort | Why first |
|-------|------|--------|-----------|
| 1 | PBO gate (#1) | S | cheapest insurance against everything else on this list |
| 2 | Sim-to-live gap report (#3) | S | data already flowing |
| 3 | Regime-split FINSABER probes (#2) | M | required before trusting any sweep |
| 4 | Vectorized sweeps (#6) | M | unblocks B2/B18 geometry work |
| 5 | Ensemble authority weighting (#4) | M | roadmap B6/B7 synergy |
| 6 | News-timestamp audit (#8) + anonymization (#7) | S | LLM-path integrity |
| 7 | Hash-chained ledger export (#10) + kill criteria (#11) | S | credibility |

---

## Sources

- [GitHub API star counts — pulled live 2026-06-10] (TauricResearch/TradingAgents, virattt/ai-hedge-fund, microsoft/qlib, AI4Finance-Foundation/FinRL et al.)
- [FINSABER: Can LLM-based Financial Investing Strategies Outperform the Market in Long Run? (arXiv 2505.07078, KDD 2026)](https://arxiv.org/abs/2505.07078)
- [TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv 2412.20138)](https://arxiv.org/pdf/2412.20138)
- [TradingAgents explained — caveats (beginnersinai.org)](https://beginnersinai.org/tradingagents-explained/)
- [A Test of Lookahead Bias in LLM Forecasts (arXiv)](https://arxiv.org/html/2512.23847v1)
- [Can Blindfolded LLMs Still Trade? Anonymization-First Portfolio Optimization (arXiv)](https://arxiv.org/html/2603.17692v1)
- [Agentic Trading: When LLM Agents Meet Financial Markets (arXiv)](https://arxiv.org/html/2605.19337v1)
- [FinRL Contests: Benchmarking Data-driven FinRL Agents (arXiv 2504.02281)](https://arxiv.org/html/2504.02281v3)
- [FinRL-Meta: Market Environments and Benchmarks (arXiv 2211.03107)](https://arxiv.org/pdf/2211.03107)
- [The Probability of Backtest Overfitting — Bailey, Borwein, López de Prado, Zhu](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf)
- [AIEQ vs SPY comparison (PortfoliosLab)](https://portfolioslab.com/tools/stock-comparison/AIEQ/SPY) · [AIEQ performance (Morningstar)](https://www.morningstar.com/etfs/arcx/aieq/performance) · [AIEQ vs S&P since inception (buyupside)](https://www.buyupside.com/performanceguides/pgcontentscomparestocksp500performancetableV2.php?guidename=Artificial+Intelligence+(AI)+Stocks&folder=ai&prefix=AI&stocknamedis=Amplify+AI+Powered+Equity+ETF&stocksymbol=AIEQ&rownumber=9&numberdividendsperyear=4&currentannualdividend=0.05+(0.12%25))
- [Numerai raises $30M, 2024/2025 returns (fintech.global)](https://fintech.global/2025/11/24/numerai-lands-30m-to-scale-ai-powered-hedge-fund/) · [Numerai $500M AUM coverage (ventureburn)](https://ventureburn.com/numerai-raises-30m/)
- [Trade Ideas Holly AI — how it actually works (DayTradingToolkit)](https://daytradingtoolkit.com/trading-tools-tutorials/trade-ideas-holly-ai-explained/) · [AI bots: real vs marketing hype](https://daytradingtoolkit.com/day-trading-basics/ai-trading-bots-truth-vs-hype) · [Trade Ideas review (liberatedstocktrader)](https://www.liberatedstocktrader.com/trade-ideas-review/)
- [Danelfin 70% win-rate review (alphagaindaily)](https://alphagaindaily.com/en/blog/danelfin-ai-stock-review) · [Danelfin audit tool](https://audit.danelfin.com/) · [Danelfin review (WallStreetZen)](https://www.wallstreetzen.com/blog/danelfin-review/)
- [Tickeron 75% win-rate claim (tickeron.com — primary-source marketing)](https://tickeron.com/trading-investing-101/revolutionizing-trading-with-multiagent-ai-achieve-75-win-rates/)
- [Alpha Picks vs Danelfin — verified track record comparison (traderhq)](https://traderhq.com/alpha-picks-vs-danelfin/)
- [Best open-source AI trading agents (Pinggy)](https://pinggy.io/blog/best_ai_trading_agents/) · [awesome-ai-in-finance (GitHub)](https://github.com/georgezouq/awesome-ai-in-finance) · [HKUDS/AI-Trader (GitHub)](https://github.com/HKUDS/AI-Trader)
- [Best AI trading bots 2026 (StockBrokers.com)](https://www.stockbrokers.com/guides/ai-stock-trading-bots)
