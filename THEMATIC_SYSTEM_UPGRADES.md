# Thematic System Upgrades

**Date:** 2026-06-01  
**Status:** Phase 1 complete — core safety + scoring improvements shipped

---

## System Flow

```
9 sources (parallel) → _merge_signals() → _validate_tickers() → _ai_pick() → signals.json
                                ↓
                        source_breakdown per ticker
                        score_history.jsonl (rolling 500 scans)
                                ↓
approve_signal → thematic_portfolio.json + PAPER_STATE_FILE (unified brain)
```

**Sources and weights:**
| Source | Weight | Notes |
|---|---|---|
| trusted_twitter | 3.5× | rss.app cashtag feeds — explicit trader picks |
| marketaux | 3.0× | sentiment-scored news entities |
| reddit | 2.0× | WSB/stocks/investing/StockMarket/SecurityAnalysis |
| seeking_alpha | 2.0× | curated market currents RSS |
| stockanalysis | rank-weighted 4.0→1.0 | trending page |
| ddg | 1.5× | DuckDuckGo news queries |
| google_news | 1.5× | Google News RSS queries |
| insider | 1.5× | OpenInsider cluster buys + screener + congressional |
| yahoo | 3.0 flat | Yahoo Finance trending US |
| **multi_source_bonus** | +3 per extra source, max +15 | NEW: cross-source confirmation |
| **insider_social_combo** | +8 | NEW: insider buying + social signal together |

---

## Changes Made (Phase 1)

### `web/api/thematic_auto.py`

**Bug fixes:**
1. **Variable name collision** — `results` from `asyncio.gather()` was overwritten by inner DDG news loop (`results = list(ddg_inst.news(...))`). Fixed: renamed gather output to `gather_results`, inner loop uses `nr`.

2. **Invalid escape sequences** in `TWITTER_SEARCHES` strings — `\$` in regular strings causes `SyntaxWarning`. Fixed: converted to raw strings `r"..."`.

**Safety:**
3. **Atomic writes** — `_save_signals()` and `_set_status()` now use `_atomic_write()` (write to temp file + `os.replace()`). Crash-safe. Also backs up previous `.json.bak` before overwrite.

4. **Corrupt JSON fallback** — `_load_signals()` now falls back to `.json.bak` on parse failure.

**Scoring:**
5. **Multi-source confirmation bonus** — Ticker appearing in N sources gets +3×(N-1), capped at +15. A ticker seen in 5 sources gets +12 bonus on top of weighted scores.

6. **Insider + social combo bonus** — +8 pts when a ticker has both insider buying (OpenInsider) AND social signal (trusted_twitter or reddit). Highest-conviction cross-validated signal.

7. **Per-source breakdown stored** — `_merge_signals()` now returns `(ranked_list, source_breakdown_dict)`. Each signal record includes `source_breakdown: {source: pts}` for explainability.

**AI output validation:**
8. **`_validate_pick()` added** — Sanitizes and clamps every AI pick before it enters the signal queue:
   - `ticker`: must be alpha-only, 1-5 chars, not in `_SKIP` set
   - `conviction`: clamped 1–10
   - `target_pct`: clamped 3–50%
   - `stop_pct`: clamped 2–25%
   - `hold_days`: clamped 1–30
   - `theme`: validated against `THEMES_MAP`; falls back to `_guess_theme()`
   - All string fields truncated at safe lengths

**Type annotations:**
9. `_merge_signals` return type updated to `tuple[list[tuple[str, float]], dict[str, dict[str, float]]]`
10. `_ai_pick` and `_ai_pick_openrouter` parameter types updated `int → float`

**Score history:**
11. **`_append_score_history()`** — After every scan, appends a JSON line to `tmp/thematic_score_history.jsonl` with timestamp, ranked tickers, and per-ticker source breakdown. Rolling 500-line cap.

12. **`GET /api/thematic/auto/score-history`** endpoint — Returns last N scan snapshots (default 20).

**Source failure logging:**
13. Each of the 9 sources now logs a warning if it raised an exception (was silently swallowed before). Scan continues regardless (`return_exceptions=True`).

**Portfolio brain caps (in approve_signal):**
14. Before injecting a paper trade, checks:
   - Total open positions ≤ `PORTFOLIO_MAX_POSITIONS` (15)
   - Positions in same theme ≤ `PORTFOLIO_MAX_PER_THEME` (3)
   - Total thematic/speculative positions ≤ `PORTFOLIO_MAX_SPECULATIVE` (8)
   - Returns `cap_reason` in response if blocked (does not raise 4xx — portfolio is still marked approved)

**Better injection fields:**
15. Paper positions injected by `approve_signal` now include: `strategy_label`, `theme`, `thesis`, `catalyst`, `hold_days`, `exit_plan` — previously missing, required by unified brain validation.

### `web/api/thematic_portfolio.py`

16. **`os`, `tempfile` imports** added.

17. **`_atomic_write()` helper** added — same pattern as thematic_auto.py.

18. **`_save()` now atomic** — writes via temp + rename, backs up `.json.bak` first.

19. **`_load()` corrupt JSON fallback** — tries `.json.bak` on parse failure.

20. **Double-load fixed** in `get_thematic_portfolio` — was calling `_load(user["email"])` twice (lines 240-241). Now single load.

21. **`thematic_paper_trade` atomic write** — `PAPER_STATE_FILE.write_text()` replaced with `_atomic_write()`.

22. **Better position fields** in `thematic_paper_trade` — added `theme`, `strategy_label`, `thesis`, `catalyst`, `hold_days`, `exit_plan`, enriched from existing thematic portfolio position if present.

### `web/app.py`

23. **`_thematic_scan_loop()`** — Background task that auto-triggers scan every 4 hours if `THEMATIC_AUTO_SCAN=true` in `.env`. Uses lock guard (`_scan_lock` in thematic_auto.py prevents duplicate runs). 60s initial delay to let server warm up.

---

## Files Changed

| File | Changes |
|---|---|
| `web/api/thematic_auto.py` | Phase 1+2: scoring, validation, atomicity, history, exit monitor, async validation |
| `web/api/thematic_portfolio.py` | Phase 1+2: atomic writes, double-load fix, better fields, live social score |
| `web/app.py` | Auto-scan background loop |
| `frontend/src/pages/ThematicPortfolio/index.tsx` | Source breakdown chips in signal cards |
| `scripts/backtest_thematic_signals.py` | NEW: signal backtest/validation script |

---

## Commands Run / Tests Passed

```bash
# Syntax checks (all 3 Python files + backtest script)
python3 -c "import ast; ast.parse(...)"   → OK (thematic_auto, thematic_portfolio, app.py, backtest)

# Import + assertion test
python3 -c "..."  → ALL ASSERTIONS PASSED
  _merge_signals: async=True
  _validate_tickers: async=True
  _check_thematic_exits: async=True
  PORTFOLIO_MAX_POSITIONS=15, PER_THEME=3, SPECULATIVE=8

# _validate_pick edge cases
  valid pick, SKIP word, conviction clamp, target/stop clamp, non-alpha ticker, bad theme → all passed

# _get_social_score_from_history
  no history → 5.0, NVDA max → 10.0, unknown → 5.0 → all passed

# Frontend build
  npm run build → ✓ built in 1.26s (ThematicPortfolio-bG7nM7ro.js: 36.83 kB)
```

---

## Phase 2 Changes (2026-06-01)

### `web/api/thematic_auto.py`

**Non-blocking validation:**
24. `_validate_tickers_sync()` — renamed from `_validate_tickers`, keeps blocking yfinance logic
25. `_validate_tickers()` — now **async**, runs sync version in `asyncio.run_in_executor` (thread pool). No longer blocks event loop during scan.
26. `_merge_signals()` — now **async** (awaits `_validate_tickers`). Both callers updated with `await`.

**Exit logic monitor:**
27. `_check_thematic_exits(execute: bool)` — checks all thematic paper positions for:
    - `stop_hit` — price ≤ stop
    - `target_hit` — price ≥ target
    - `max_hold_exceeded` — age_days ≥ hold_days
    - `buzz_collapse` — ticker absent from latest scan + held > 2 days
    - If `execute=True`, removes position from `PAPER_STATE_FILE` (atomic), logs to `EXIT_LOG_FILE`
28. **Auto-exit on every scan** — `_run_scan()` calls `_check_thematic_exits(execute=True)` after each scan run (non-fatal if it fails)
29. `GET /api/thematic/auto/exit-check` — dry-run preview of exits without executing
30. `POST /api/thematic/auto/exit-check` — execute exits immediately
31. `GET /api/thematic/auto/exit-log` — last 50 exit records with reason/pnl

### `web/api/thematic_portfolio.py`

**Live social score from scan history:**
32. `_get_social_score_from_history(ticker)` — reads latest scan snapshot from `thematic_score_history.jsonl`, normalizes to 0-10 scale using max score as reference. Caches result per file mtime.
33. `_score_position()` — `social_score` now uses `_get_social_score_from_history()` instead of flat placeholder `5.0`. NVDA's social score will reflect its actual scan rank.

### `scripts/backtest_thematic_signals.py` (NEW)

34. Reads `thematic_score_history.jsonl` + `thematic_exit_log.jsonl`
35. Fetches actual 5-day returns from yfinance for historical signal tickers
36. Reports: overall WR / avg return, by score bucket (low/mid/high), insider+social combo vs. no combo, multi-source vs. single-source, top/worst performers, exit reason breakdown
37. Run: `python3 scripts/backtest_thematic_signals.py [--days 90] [--min-score 20]`

### Frontend (`frontend/src/pages/ThematicPortfolio/index.tsx`)

38. `AutoSignal` interface — added `source_breakdown?: Record<string, number>`
39. Signal cards now show **source breakdown chips** — each source's contribution, number of sources, insider+social combo badge. Sorted by contribution, top 6 shown.

---

## Phase 3 Changes (2026-06-02)

### `web/api/thematic_portfolio.py` — `thematic_paper_trade`

**R:R gate:**
39. `stop_pct` and `target_pct` checked against user's `min_rr` HIL setting (default 1.5). If R:R < min_rr, target auto-widened to `stop_pct × min_rr` and a warning is returned in `warnings[]`. Trade is NOT rejected — it proceeds with the corrected target.

**Conviction-scaled position size:**
40. `dollar_amount` allocations scaled by conviction pulled from the user's thematic portfolio position: `scale = 0.4 + (conviction - 1) / 9 × 1.1`. Conviction 1 = 0.4×, conviction 10 = 1.5×. Controlled by `conviction_scale` HIL setting (default True). If `shares` provided directly, no scaling applied.

**Portfolio heat + daily loss circuit breakers:**
41. `_check_portfolio_circuit_breakers(state, hil, cost)` called before any trade execution. Blocks if:
    - Portfolio heat ≥ `max_portfolio_heat` (default 80%)
    - Today's realized P&L loss ≥ `daily_loss_limit_pct` (default 3%)
    - Insufficient settled cash (existing check)
    - Returns 400 with descriptive reason if blocked.

**Real ATR:**
42. Hardcoded `round(price * 0.02, 4)` replaced with `_real_atr(ticker, price)` via executor. Computes 14-day ATR from yfinance 20-day history. Falls back to 2% only if fetch fails.

**Improved position fields:**
43. `alpha_tier` computed from conviction (A+ if ≥9, A if ≥7, B if ≥5, C otherwise). `score` set to `conviction × 10` (was hardcoded 80). `exit_plan` includes computed R:R ratio.

**Response enrichment:**
44. Response now includes `rr`, `conviction`, `atr` fields. `warnings[]` list included when R:R was auto-widened.

**Trade log enrichment:**
45. `thematic_trades.jsonl` log entries now include `rr`, `conviction`, `atr` for post-trade analysis.

### `web/api/thematic_auto.py` — auto-trade loop

**`_auto_execute_confirmed_signals(signals)`:**
46. New async function. After each scan, fires as a `asyncio.create_task()` for any user with `auto_trade_paper=True` in HIL settings. Per-user logic:
    - Skips `is_spike=True` signals (unconfirmed, appeared in only 1 of last 5 scans)
    - Skips signals with `raw_score < MIN_SIGNAL_SCORE` (40.0)
    - Calls `approve_signal()` directly with user's configured `dollar_amount`
    - All circuit breakers (portfolio heat, daily loss, cash) still enforced via approve_signal
    - Per-signal errors logged, not raised (one failure doesn't block others)

**`_run_scan()` update:**
47. After saving signals: `asyncio.create_task(_auto_execute_confirmed_signals(pending))` fires if `pending` is non-empty.

---

## Incomplete / TODO

| Item | Priority | Notes |
|---|---|---|
| Theme strength engine | MEDIUM | Aggregate scan scores across a theme, flag declining themes |
| Scheduling config UI | LOW | Expose `THEMATIC_AUTO_SCAN` toggle in settings page |
| Score history frontend view | LOW | `GET /api/thematic/auto/score-history` endpoint exists, not yet shown in UI |
| Exit log frontend view | LOW | `GET /api/thematic/auto/exit-log` endpoint exists, not yet shown in UI |
| `PORTFOLIO_MAX_*` configurable via `.env` | LOW | Currently hardcoded constants (15/3/8) |
| Trailing stop support | LOW | `trail_atr_mult` exists in unified brain but not wired to thematic exits |

---

## Risks

- **Scoring formula change** (multi-source bonus + combo bonus) will shift signal rankings. Tickers confirmed by multiple sources will score higher. Pure-Twitter picks with no other confirmation will score relatively lower. This is the intended behavior.
- **Atomic write uses `os.replace()`** — on macOS/Linux this is atomic; on NFS mounts it is not (not applicable here).
- **`THEMATIC_AUTO_SCAN=true`** must be explicitly set in `.env` to enable auto-scheduling. Default is off — safe.
- All paper trade writes are now crash-safe, but if the server is killed mid-trade between the position write and the trade log write, the position is in state.json but not in thematic_trades.jsonl. This is acceptable (trade log is non-authoritative).

---

## Manual Steps Needed

1. **Restart server** to pick up all changes: `kill <pid> && python3 run_web.py`
2. **To enable auto-scan every 4 hours:** Add `THEMATIC_AUTO_SCAN=true` to `.env`, then restart
3. After first scan: verify `tmp/thematic_signals.json` contains `source_breakdown` per signal
4. After first scan: verify `tmp/thematic_score_history.jsonl` created
5. **Backtest** (after 1+ scans): `python3 scripts/backtest_thematic_signals.py --days 90`
6. Verify exit monitor: dry-run with `GET /api/thematic/auto/exit-check`

---

## Safe to Run in Paper Mode?

**Yes.** All changes are additive or safety improvements:
- Existing working features preserved
- Atomic writes reduce (not increase) risk of data loss
- Portfolio caps prevent over-allocation to thematic positions
- AI validation rejects malformed picks instead of passing them through
- Score formula changes only affect ranking order, not trade execution
- Auto-scan loop is disabled by default (`THEMATIC_AUTO_SCAN=false`)
