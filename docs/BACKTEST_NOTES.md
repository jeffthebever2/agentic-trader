# Backtest notes (parked 2026-05-17)
Goal: long-hold portfolio +$2000 Nov14'25-May14'26 on $5000.
## Honest findings
- confirmed_pullback on 124 large caps: only 22 trades, -0.49%/trade, PF 0.63, loses to SPY. No edge this window/universe.
- Documented strategy edge: 68.8% WR, +27.9%/yr (~+13%/6mo ~= +$650 on $5k). +$2000 = +40%/6mo ~= 3x proven edge — not honestly reachable by tuning alone.
## Score-mode sweep (quality universe, thr70, 20/30d hold):
##### MODE=breakout #####
  $5,000 account simulation (Half-Kelly capped at 20.0%):
    Final value       : $5,000.00
    Total return      : +0.00%
    Trades taken      : 0
    Skipped signals   : 169
  Elapsed: 49s
##### MODE=mean_reversion #####
  $5,000 account simulation (Half-Kelly capped at 20.0%):
    Final value       : $5,000.00
    Total return      : +0.00%
    Trades taken      : 0
    Skipped signals   : 1,101
  Elapsed: 47s
##### MODE=oversold_bounce #####
  $5,000 account simulation (Half-Kelly capped at 20.0%):
    Final value       : $5,140.66
    Total return      : +2.81%
    Trades taken      : 20
    Skipped signals   : 116
  Elapsed: 35s
##### SWEEP DONE #####

## ML-gated account sim (full universe, $10k, Nov14'25-May14'26) — updated
- Wired ML strategies through real account engine (concurrency+cap+Half-Kelly).
- rule PF0.66 $0 | ml_filter PF0.56 $0 | ml_rank PF0.98 $0 | ml_loss_cap PF1.008 +$0.17.
- VERDICT: +$2000 NOT reachable this window/strategy. Properly sized = breakeven/negative.
- RF run-to-run noise moves PF ~1.0-1.24; edge within noise.
- ONLY real edge: low-VIX regime subset PF 1.45 (+0.48%/trade). Feb'26 regime shock dominates losses.
- Next honest lever: hard VIX-regime entry gate (trade only low_vol), not ML soft-score.

## LEAK-FREE walk-forward (full universe, 2024-01->2026-05, $10k) — 2026-05-18
Purged WF (train past-only, embargo>=hold). status=ok. THE honest numbers:
- rule (algorithm/pure_ai): 1558 tr, WR56%, PF1.02, +$13  -> ~0%/yr  FAIL
- ml_filter (machine_learning/ml_new/combined): 659, WR53%, PF0.89(losing), $0  FAIL
- ml_rank: 606, PF1.04, +$32  FAIL
- ml_loss_cap (long_hold): 1031, WR55%, PF1.12, +$298 (+3.0% OOS)  FAIL
KEY: leaky ML PF~1.19 -> leak-free PF 0.89. Leakage WAS inflating ML (user correct).
VERDICT: 35%/yr NOT achievable by any portfolio, properly evaluated. No edge.

## oversold_bounce leak-free (full universe 2024->2026, $10k) 2026-05-18
Signal: 4509 tr, WR 67.3%, +0.47%/tr, PF 1.57 (real, pure-rule, no leak).
Account sim: cap12%%->92 tr,-0.73%% | cap3%%->319 tr,+0.91%%(+0.82%%/yr).
Signal edge does NOT scale: realized ~+0.8%%/yr, capital-bound, selection gap.
VERDICT: best honest edge ~ low-single-digit %%/yr. 35%%/yr NOT reachable by
any tested signal/portfolio/sizing. Per-trade avg is not capturable at scale.

## 20%/yr push — honest panel sweep (2026-05-18, new harness)
AUDIT UPDATE 2026-05-18: this harness has since been fixed to use real replay
exit dates instead of `scan_date + days` calendar offsets, and to avoid 30-bar
hold truncation in the fast cache. The broad conclusion below (20%% not
robustly proven) remains conservative, but the exact percentages in this
section must be rerun before being treated as final reproducible numbers.

Harness: scripts/honest_sweep.py (parity-verified fast measure_outcome
replay), scripts/panel.py (liquid feature panel from the 2.76M-row enriched
scan), scripts/panel_run.py / panel_refine2.py. Account sim = fixed %-of-cash,
hard max-positions, conservative MAE-marked drawdown. Universe = tickers_liquid
.txt (median 500d $vol >= $39M). Span 2019-01 -> 2026-05 (7.3y).

- confirmed_pullback, full universe, 7.25y honest sweep: best ~+0.7%%/yr;
  time-split TRAIN +4%% -> TEST -4.66%% (overfit). No edge.
- 12 entry families on liquid universe: ALL negative -2%% to -21%%/yr;
  confirmed_pullback executions -4.88%%/yr (PF 0.89). The full-universe
  oversold "PF 1.57" does NOT survive realistic liquid + account sim.
- Pre-audit best OOS-robust family found: oversold_up50_200
  (rsi14<35 & mfi14<35 & sma200_dist>0 & sma50_dist>-0.05), liquid,
  t2.0/s1.5 ATR, hold 15, all-in single position ranked rsi9 ascending:
    ALL  +11.11%%/yr  tot +116.5%%  $+11,647  DD 32%%  WR 46%%  PF 1.25  n=211
    TRAIN +12.1%%/yr  ->  TEST +9.74%%/yr (PF 1.19, n=96) — PRE-AUDIT ONLY.
- Strict TRAIN-only selection (panel_refine2, TEST seen once): TRAIN-best
  t4.0/s2.0/h25 hits TRAIN +20.05%%/yr but TEST only +6.60%%/yr — 20%% is a
  train-period artifact, not robust.
VERDICT: 20%%/yr is NOT honestly/robustly achievable with these signals on a
realistic liquid universe with leak-free, realistic account simulation. The
old +7 to +11%%/yr panel numbers above are invalidated until rerun after the
exit-date and 30-bar cache fixes; do not quote them as current performance.
Caveats: liquid universe membership uses full-history dollar-vol + only
cache-surviving tickers (mild survivorship/look-ahead in UNIVERSE def, not in
entries/exits); all-in concentration is high-risk; ~29 trades/yr is thin.

## Canonical 20%/yr audit run (2026-05-18)

Command:
`python3 scripts/honest_20yr_research.py --max-stock-tickers 600`

Deliverables:
- `AUDIT_REPORT.md`
- `audit_manifest.json`

Verdict: **FAIL - 20%/yr is not proven** under the locked constraints
(stocks + ETFs only, no paid data, target 25-30% max drawdown, net of costs).

Closest highest-CAGR candidate:
- ETF momentum rotation: TEST +14.70% CAGR, 26.66% DD, PF 1.134,
  only 8 TEST rebalances. Fails 20% CAGR, PF, and sample-size gates.

Best sample-size-valid candidate:
- Low-frequency EMA ribbon stock rule: TEST +10.98% CAGR, 15.34% DD,
  PF 1.235, 421 TEST trades, CPCV median +9.97%, DSR 1.000.
  Fails the 20% CAGR target and decays more than 50% from TRAIN.

Corrected stock panel result:
- Train-selected oversold panel branch: TEST +5.24% CAGR, 13.70% DD,
  PF 1.158, 50 TEST trades. Fails CAGR and stock sample-size gates.

Current truth: do not quote any old +20%, +9-11%, or pre-audit panel percent
as current performance unless it appears in `AUDIT_REPORT.md`.

## 20%+ pass found with internally leveraged ETFs (2026-05-18)

Command:
`python3 scripts/honest_20yr_research.py --skip-panel --skip-lowfreq`

Verdict: **PASS**, but only for the explicitly labeled leveraged-ETF tactical
branch. This is not a pass for the original stock rule/ML algorithms.

Passing config:
- Strategy: leveraged ETF volatility-target momentum rotation.
- Universe: SPY, QQQ, SHY, TQQQ, QLD, UPRO, SSO, SPXL, TECL, SOXL, ROM,
  UWM, CURE.
- Signal: risk-on only when SPY close > SPY 200d SMA.
- Ranking: top 1 internally leveraged ETF by 66-trading-day momentum.
- Rebalance: every 5 trading days.
- Exposure: volatility target 0.31; unused exposure held in SHY.
- Costs: 7.5 bps per turnover.

Real TEST result:
- +34.40% CAGR, +133.94% total return, 25.48% max drawdown, PF 1.222,
  125 TEST trades/rebalances, CPCV median +10.41%, DSR 1.000.
- Full-span result: +23.27% CAGR, +3105.77% total return, 36.01% max
  drawdown, PF 1.170, 645 rebalances from 2009-10-20 to 2026-05-18.
- Selection was TRAIN-only from the expanded leveraged-ETF lookback/vol-target
  ladder logged in `audit_manifest.json` (`trial_count`: 768).

Caveat: leverage comes from leveraged ETF products, not margin borrowing.
This must be paper-traded before live use; leveraged ETFs can decay and gap
hard in stress regimes.

## NET-of-cost re-run (corrected engine, 2026-05-18)
Engine fixed: real bar exit dates, dynamic hold window, cache-id guard,
Roll-spread+slippage+commission ON by default. Parity PASS, 19 tests pass.
- oversold_up50_200 t2.0/s1.5/h15 all-in: GROSS +11.2%/yr -> NET +3.4%/yr
  (TRAIN -1.8 -> TEST +12.5, n=163). Not robust.
- Connors RSI(2) NET -24..-45%/yr; Double Seven NET -16%/yr (cost-killed).
- BEST honest: EMA-ribbon pullback + Chandelier (lowfreq.py), pp0.33/mp4,
  TRAIN-selected: FULL NET +23.16%/yr ($36,364, DD14.8%, PF1.45, n=1023);
  TRAIN +27.5% -> held-out TEST +10.98%/yr; CPCV(28 purged) median
  +9.97%/yr, 18% paths>=20%, Sharpe 1.03, DSR~1.0.
VERDICT: real ~+10-12%/yr net OOS edge exists (EMA-ribbon). 20%/yr NOT
robustly proven (only in-sample/full-period). Repro: python3 scripts/lowfreq.py
