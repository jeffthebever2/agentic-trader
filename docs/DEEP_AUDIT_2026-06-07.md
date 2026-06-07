# Deep Audit — 2026-06-07

## Executive Summary

This tool has the architecture of a complete trading system but three critical failure layers prevent it from functioning as one:

1. **The one trade it ever executed corrupted state** — account is permanently locked at -57% drawdown and will never trade again without manual reset.
2. **The ML model's confident signals are worse than random** — high-confidence predictions win 39.5% vs. 50% for a coin flip.
3. **There is no real brokerage connection** — Fidelity runs via browser automation that fails on network reconnect; no real orders have ever been placed.

Everything below is evidence, not opinion.

---

## Part 1: Portfolio State — What Actually Happened

### The Six Strategy Accounts (algorithm, machine_learning, ml_new, combined, pure_ai, long_hold)

**Every account: `cash=$10,000`, `trades=[]`, `positions={}`.**

These accounts have never been touched. The unified brain runs instead, but it uses its own separate state file (`tmp/paper_trading_today/unified_brain/state.json`) and never writes to the per-strategy state files. The dashboard shows six "portfolios" — all six are ghosts. They have no trades, no positions, no history.

### The Unified Brain Account

This is the one that actually ran. Timeline:

```
Lines 1–200 of papertrader.log:
  cash=$10,000 × 14 consecutive status lines
  ERROR: build_candidates() missing 2 required positional arguments
  (repeated 100+ times — no trades possible)

Line 227:
  SAFETY_HALT: regime_no_trade: regime=unknown

Line 236 (June 3, 10:07 AM):
  Brain: 4 accepted, 0 rejected, 8 watchlist
  BUY HXL: tier=A alpha=0.553 shares=22 price=87.64 stop=87.29 tp=95.39
  ERROR: 'str' object has no attribute 'ticker'

Line 245 (June 3, 10:27 AM):
  SAFETY_HALT: portfolio_drawdown: -19.28% < -12.00% floor (account value=$8,072)

Line 259 (June 3 EOD):
  Account: cash=$4,300 positions=0 pnl=0.00
```

**What happened:** The brain queued 4 trades. The execution loop deducted cash for each buy, partially processed them, then crashed mid-cycle on `'str' object has no attribute 'ticker'`. Cash was deducted ($10,000 → $4,300 = 4 positions × ~$1,425 average), but position records were not fully created before the exception. State was saved with `cash=$4,300, positions={}`. The $5,700 vanished into a phantom state — deducted but untracked.

**Current state:**

```json
unified_brain/state.json: { "starting_cash": 4300.0, "cash": 4300.0, "trades": [], "positions": {} }
unified_brain/safety_report.json: {
  "safe_to_trade": false,
  "drawdown": -0.57,
  "account_value": 4300.0,
  "starting_cash": 10000.0   ← safety check reads 10k from account object, not state
}
```

The safety gate computes: `(4300 - 10000) / 10000 = -57%`, which exceeds the -12% floor. The system will never trade again in this state.

**The account is permanently halted by a bug, not a real loss.**

### Why the Drawdown Is Permanent

The `portfolio_drawdown` safety gate has no reset mechanism. Once `account_value < starting_cash × (1 - max_dd)`, the gate fires every scan cycle and blocks all orders. The only escape is manual intervention:

```bash
# Reset the unified brain state:
echo '{"starting_cash": 10000.0, "cash": 10000.0, "realized_pnl": 0.0, "positions": {}, "trades": []}' \
  > tmp/paper_trading_today/unified_brain/state.json
```

Without this, the system will run the `papertrader.log` loop forever: scan → SAFETY_HALT → sleep 900s → repeat.

---

## Part 2: Why the Strategy Is Unprofitable

### 2.1 Score Saturation — Zero Rank Ordering

`backtest_results_20260531_002700.json`:
```
score_bucket_analysis: { "100-105": { "count": 260, "win_rate": 0.492 } }
```

Every single signal scores in the "100-105" bucket. The breakout scanner is a binary gate: a stock either passes the threshold (score ≥ 70, say) or doesn't. Once it passes, everything scores 100. There is no rank ordering above the threshold. 

**Consequence:** The system cannot distinguish "barely passes" from "extremely strong setup". Position sizing cannot be proportional to signal strength because all signals appear equal. The 49.2% win rate on these signals is a coin flip.

### 2.2 The ML Model's Confident Predictions Are Inverted

`ml_models/latest/training_report.json`:
```
walk_forward.roc_auc:          0.5121   (gate: 0.49)
walk_forward.high_conf_n:      43
walk_forward.high_conf_win_rate: 0.3953
```

The model's overall predictive power is 0.5121 — barely above 0.49 gate, essentially a coin flip. But the critical failure is the high-confidence trades: when the model assigns probability ≥ 0.60, its win rate is **39.5%**. At maximum confidence, the model is *worse* than random.

This means:
- The ML filter adds negative value when it is most confident
- Any "combined" strategy that weighs ML signal higher on high-confidence trades is systematically damaged by this
- The model likely has a calibration inversion — what it thinks is bullish is actually marginally bearish in the test period

**Secondary signals of model failure:**
- 17 features PSI-pruned for drift — over a third of the feature set is stale
- Only 76 test rows — extreme noise in all WF metrics
- `ml_analysis: {}` in backtest output — the gate analysis section was never populated

### 2.3 Negative Alpha vs. Buy-and-Hold

```
avg_alpha_vs_spy: -0.0051
beat_spy_rate:    48.9%
```

The strategy underperforms passive SPY holding on average. Beating SPY 48.9% of the time is below 50% — you would have better expected returns just holding SPY. This is the fundamental test a strategy must pass and this one fails it.

### 2.4 Stop Placement — 75% of Winners Dip First

```
pct_winners_went_negative_first: 0.7517
```

75% of ultimately-winning trades went below entry price before recovering. This means the stops are placed inside normal price noise — they either stop out winners before recovery, or they're set so tight that position sizing becomes too small to be meaningful. The ATR multiplier used for stops is likely too small (probably 0.5-1× ATR when 1.5-2× is the typical standard).

### 2.5 Single-Regime Blindness

```
regime_breakdown: { "bull": 254, "unknown": 6, "bear": 0, "neutral": 0 }
```

254 of 260 backtest trades were placed in "bull" regime. The strategy has never been tested in bear or neutral regimes because the regime gate correctly blocks trading in those, but this also means the strategy's win rate of ~49% applies only to bull-market conditions. No evidence it works under different conditions.

### 2.6 Tiny Universe at Wrong Price Level

The scanner processes 5,881 symbols but only 37 scored on June 4 (0.6% pass rate). This is appropriate selectivity for a breakout scanner, but the filter stack should be verified — several prior retrain cycles found that min-advance and min-price filters were cutting 66% of the data (420 rows remaining), which is too small for reliable model training.

---

## Part 3: Why This Is a Dashboard, Not a Tool

### 3.1 No Real Brokerage Connection

The Fidelity integration (`web/api/fidelity.py`) uses Playwright browser automation:
- Navigates to `https://digital.fidelity.com/ftgw/digital/portfolio/summary`
- Classified as `UNTRUSTED_EXECUTION_SOURCE` in `PreTradeGate`
- Requires manual WebSocket auth + TOTP codes
- Keepalive loop fails repeatedly with `ERR_INTERNET_DISCONNECTED` (from webserver.err)
- The actual trade function (`fidelity_trade()`) requires `--trade-fidelity-execute` flag

No real orders have been placed through this system. The `paper_accounts/algorithm/` directory is completely empty. All "paper trading" runs against an in-memory Python account simulation, not a real paper brokerage account.

### 3.2 The Execution Bus Is Broken

The `'str' object has no attribute 'ticker'` error (papertrader.log line 237) is in the unified brain execution path. When the brain outputs accepted candidates, at least one is reaching the trade execution loop as a raw ticker string instead of a `Candidate` object. This is likely a format mismatch between how candidates are serialized/deserialized between the screening step and the execution step.

Before this error was hit, the system had a different blocker: `build_candidates() missing 2 required positional arguments: 'trade_date' and 'bundle'` — over 100 consecutive scan cycles failed completely. The function signature changed but the call site was not updated.

### 3.3 End-of-Day Statistics Are Corrupted

`tmp/paper_trading_today/20260605/end_of_day_statistics.json`:
```json
{
  "starting_cash": 4300.0,
  "ending_cash": 10000.0,
  "final_value": 10000.0,
  "day_pnl": 5700.0,
  "return_pct": 1.3256,
  "closed_trades": 0,
  "winning_trades": 0
}
```

The system reports a `day_pnl` of +$5,700 (132% return) with zero closed trades. This is because the EOD statistics compare `starting_cash` (the unified brain's $4,300 corrupted floor) against the per-strategy accounts' `ending_cash` of $10,000. The dashboard is showing a phantom $5,700 gain every day. The reported performance numbers cannot be trusted.

### 3.4 The Dashboard Shows Data From Different Sources

The web dashboard appears to pull from:
- Per-strategy state files (all at $10,000, 0 trades) for individual strategy views
- EOD statistics files (corrupted by the starting_cash mismatch) for daily summary
- The safety_report.json (correct, showing -57% drawdown) for the safety status view

These three sources are inconsistent with each other. A user looking at the dashboard would see: six strategy accounts all at $10,000 (healthy), a safety alert showing -57% drawdown (alarming), and EOD statistics showing a +132% day (impossible).

### 3.5 No Automated Daily Lifecycle

The paper trader runs continuously (`papertrader.log` shows 900s sleeps). It does not have:
- An automated pre-market setup run
- A post-market position close + statistics generation run  
- An automated daily retrain trigger
- Any notification when the system stops making trades (the SAFETY_HALT has been firing for days with no alert sent)

### 3.6 ML Retraining Has No Feedback Loop

Retrain (cycle 46+) runs against historical price data. The paper trading results — which should validate whether the live predictions are directionally correct — are never fed back into the training pipeline. The model trains on 2017–2026 history but has no way to learn from the live paper trading signals it generates daily.

---

## Part 4: What to Fix and In What Order

### Tier 1 — Fix Before Anything Else (the account is bricked)

**P1-A: Reset unified brain state**
```bash
cat > tmp/paper_trading_today/unified_brain/state.json << 'EOF'
{
  "starting_cash": 10000.0,
  "cash": 10000.0,
  "realized_pnl": 0.0,
  "positions": {},
  "trades": [],
  "peak_equity": 10000.0,
  "settled_cash": 10000.0,
  "unsettled_cash": 0.0,
  "settlement_queue": [],
  "gfv_count": 0,
  "gfv_events": [],
  "gfv_restricted": false,
  "freeriding_count": 0,
  "freeriding_events": [],
  "clv_count": 0,
  "clv_events": [],
  "day_trades_today": [],
  "day_trade_history": [],
  "pdt_flagged": false
}
EOF
```

**P1-B: Fix `'str' object has no attribute 'ticker'`**

In the unified brain execution loop, find where accepted candidates are being passed to the trade executor. A string (raw ticker) is reaching code that expects a `Candidate` object with a `.ticker` attribute. The bug is almost certainly a list comprehension or dict lookup that returns the key (string) instead of the value (Candidate). Find the line in `scripts/paper_trade_today.py` near the execution block that iterates over accepted candidates and add a guard:
```python
# Before this pattern:
for cand in accepted:
    execute_trade(cand)  # crashes if cand is str

# Add:
for cand in accepted:
    if isinstance(cand, str):
        logger.error(f"Candidate {cand!r} is a string, not a Candidate object — skipping")
        continue
    execute_trade(cand)
```
Then trace back to where the string got into the list and fix the source.

### Tier 2 — Fix the Strategy Edge

**P2-A: Fix score saturation**

The breakout scanner needs a continuous score, not binary pass/fail. Options:
1. Return a multi-factor composite score (volume rank × momentum rank × pattern rank) instead of a flat 100
2. Use the score as a position-sizing multiplier (higher score = larger position)

This single fix converts the strategy from random coin-flip to something that can actually rank signals by quality.

**P2-B: Fix stop placement**

With 75% of winners dipping below entry, stops are inside noise. Change the ATR multiplier for stop calculation from whatever it currently is to at least 1.5× ATR. This will increase the risk per trade but dramatically reduce winner-to-loser conversion from stop outs.

**P2-C: Investigate ML confidence inversion**

The model needs a calibration audit. Run `sklearn.calibration.calibration_curve` on the test set probabilities. If the high-confidence class is mapping to low win rates, either:
1. Recalibrate using Platt scaling or isotonic regression
2. Invert the signal threshold (use the model's "bearish" signal as a veto, not a confirm)
3. Retire the ML filter until a retrain produces a model where high-confidence = higher win rate

**P2-D: Fix EOD statistics**

The `starting_cash` used for EOD statistics must be the true initial capital ($10,000), not the current unified brain state value. The daily PnL calculation should be `final_value - initial_capital`, where `initial_capital` is a constant stored at session start, not read from the potentially-corrupted state file.

### Tier 3 — Build the Execution Layer

**P3-A: Replace Playwright Fidelity with a real paper broker**

Alpaca has a free paper trading API with a proper REST + WebSocket interface:
```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

client = TradingClient(api_key, api_secret, paper=True)
order = MarketOrderRequest(symbol="AAPL", qty=10, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
client.submit_order(order)
```

This gives real paper fills, real position tracking, real account balance — no browser automation, no TOTP, no `ERR_INTERNET_DISCONNECTED`.

**P3-B: Wire per-strategy accounts to Alpaca paper**

Each of the six strategies (algorithm, ml, combined, etc.) should map to a separate Alpaca paper account or sub-portfolio. This surfaces real per-strategy performance in a way the current in-memory simulation cannot.

### Tier 4 — Close the Loop

**P4-A: Feed live paper trade results back to retraining**

Every closed paper trade should log:
```json
{"date": "2026-06-07", "ticker": "HXL", "entry": 87.64, "exit": 89.10, "pnl_pct": 0.017, 
 "ml_score": 0.63, "breakout_score": 100, "regime": "bull", "outcome": 1}
```

These records become ground truth for the next retrain cycle. Currently the model trains on 2017–2026 history but gets no feedback from its live predictions.

**P4-B: Add a SAFETY_HALT alert**

When the drawdown gate fires and blocks trading, nothing notifies the operator. A simple email/SMS via the existing notification stack (SendBlue is already wired) should fire when the system has been in continuous SAFETY_HALT for more than 1 trading day.

---

## Appendix: Bug Inventory

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| B1 | CRITICAL | State corruption — $5,700 lost to phantom positions, drawdown gate permanently locked | `unified_brain/state.json` |
| B2 | CRITICAL | `'str' object has no attribute 'ticker'` — execution crashes on accepted candidates | `paper_trade_today.py` exec loop |
| B3 | HIGH | EOD stats compare wrong starting_cash — reports +132% daily return with 0 trades | EOD statistics generator |
| B4 | HIGH | ML high-confidence win rate = 39.5% (inverted) — model hurts when most confident | `ml_models/latest/` |
| B5 | HIGH | Score saturation — all signals score 100, no rank ordering | `breakout_scanner.py` scoring |
| B6 | MEDIUM | Stop placement too tight — 75% of winners dip negative before recovering | Position sizing / stop calc |
| B7 | MEDIUM | Fidelity keepalive fails on reconnect — `ERR_INTERNET_DISCONNECTED` | `web/api/fidelity.py` |
| B8 | MEDIUM | Per-strategy accounts never receive trades — unified brain has separate state | `unified_brain.py` / `paper_trade_today.py` |
| B9 | MEDIUM | `ml_analysis: {}` and `gate_analysis: {}` sections empty in backtest output | `backtest_engine.py` |
| B10 | LOW | No SAFETY_HALT notification — system silently stops trading for days | `paper_trade_today.py` |
| B11 | LOW | `build_candidates()` signature mismatch (fixed in current log, but was 100+ failures) | `unified_brain.py` |

---

*Generated: 2026-06-07. Source data: `logs/papertrader.log`, `logs/webserver.err`, `tmp/paper_trading_today/unified_brain/state.json`, `tmp/paper_trading_today/unified_brain/safety_report.json`, `tmp/paper_trading_today/20260605/end_of_day_statistics.json`, `ml_models/latest/training_report.json`, `backtest_results_20260531_002700.json`.*
