# Honest 20%/Yr Audit Report

Generated: 2026-05-18T14:44:20

## Verdict: PASS

Locked constraints: stocks + ETFs only, no paid data, target max drawdown 25-30%, no fabricated pass.

Closest current candidate: `leveraged_etf_vol_target_rotation`.
TEST result: +34.40% CAGR, 25.48% max drawdown, PF 1.222, 125 trades.

## Candidate Table

| Candidate | Family | TEST CAGR | TEST DD | TEST PF | TEST Trades | CPCV Med | DSR | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| leveraged_etf_vol_target_rotation | Leveraged ETF | +34.40% | 25.48% | 1.222 | 125 | +10.41% | 1.000 | PASS |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +33.43% | 24.71% | 1.222 | 125 | +10.13% | 1.000 | PASS |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +21.42% | 35.71% | 1.151 | 125 | +9.55% | 1.000 | FAIL: TEST drawdown above 35% hard kill; TEST drawdown above 30% pass target |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +18.02% | 41.08% | 1.131 | 66 | +11.56% | 1.000 | FAIL: TEST drawdown above 35% hard kill; TEST profit factor below 1.15 |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +17.62% | 40.08% | 1.130 | 66 | +11.26% | 1.000 | FAIL: TEST drawdown above 35% hard kill; TEST profit factor below 1.15 |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +17.20% | 39.07% | 1.130 | 66 | +11.04% | 1.000 | FAIL: TEST drawdown above 35% hard kill; TEST profit factor below 1.15 |
| etf_momentum_rotation | ETF | +14.70% | 26.66% | 1.134 | 8 | +6.13% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST profit factor below 1.15 |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +11.92% | 44.46% | 1.098 | 66 | +11.35% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST drawdown above 35% hard kill |
| leveraged_etf_vol_target_rotation | Leveraged ETF | +11.87% | 43.20% | 1.099 | 66 | +11.18% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST drawdown above 35% hard kill |
| etf_momentum_rotation | ETF | +7.74% | 20.06% | 1.102 | 11 | +5.45% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST profit factor below 1.15 |
| etf_momentum_rotation | ETF | +6.64% | 20.06% | 1.094 | 10 | +4.70% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST profit factor below 1.15 |
| etf_momentum_rotation | ETF | +3.32% | 22.46% | 1.052 | 10 | +4.24% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST profit factor below 1.15 |
| etf_momentum_rotation | ETF | +0.30% | 39.80% | 1.023 | 10 | +4.50% | 1.000 | FAIL: TEST CAGR below 15% research floor; TEST drawdown above 35% hard kill |
| confirmed_pullback_best_prior_shape | Stock | -12.83% | 34.98% | 0.547 | 372 | -5.79% | 0.000 | FAIL: TEST CAGR below 15% research floor; TEST profit factor below 1.15 |
| confirmed_pullback_default | Stock | -44.32% | 81.24% | 0.301 | 962 | -21.66% | 0.000 | FAIL: TEST CAGR below 15% research floor; TEST drawdown above 35% hard kill |

## Success Bar

- TEST CAGR >= 20%.
- TEST max drawdown <= 30%.
- Net of spread/slippage/commission.
- Look-ahead-free sizing.
- CPCV median > 0 and DSR >= 0.95.
- TEST sample size >= 100 stock trades or >= 40 ETF trades.

## Caveats

- Stock results remain survivorship-caveated because the available ticker universe is not point-in-time.
- ETF results use free Yahoo/yfinance data, not paid institutional data.
- Leveraged ETF results use internally leveraged ETF products; no margin borrowing is modeled.
- Any old percentage not reproduced in this report is stale or pre-audit and should not be quoted as current performance.

## Path From Here

- If the report verdict is PASS, paper-trade the exact passing config before live capital.
- If the report verdict is FAIL, the next honest attempts should focus on regime/breadth overlays for the low-frequency EMA ribbon branch and broader ETF tactical variants with enough rebalances to clear the sample-size gate.
- If those still fail, the honest escalation options are paid point-in-time stock data, shorts/inverse exposure, options, or explicitly tested external leverage.

## Commands

- `python3 scripts/honest_20yr_research.py --max-stock-tickers 600`
- `python3 scripts/honest_20yr_research.py --skip-panel --skip-lowfreq`
- `python3 scripts/honest_20yr_research.py --from-manifest docs/audit_manifest.json`
