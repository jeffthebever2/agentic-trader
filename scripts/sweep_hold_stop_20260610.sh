#!/bin/zsh
# P3 hold/stop sweep + P1 training-data collection (2026-06-10 audit).
#
# Hypothesis: edge lives in survival-to-drift (timeout-bucket WR 98.7% per
# 2026-05-30 audit) while 38% of test trades die at the stop. Widening the
# stop and/or extending the hold should shift trades from the stop bucket to
# the timeout bucket. Three runs, holds 10/15/20 computed inside each:
#   A: stop 1.0  (baseline, matches live)  + exports cycle-47 training CSV
#   B: stop 1.25
#   C: stop 1.5
# Cache .backtest_cache is warm (2018-05 -> 2026-06-15), so no downloads.
set -u
cd "$(dirname "$0")/.."
PY=python3
COMMON=(--tickers tickers_liquid.txt --start 2019-07-01 --end 2026-05-29
        --hold-periods 10 15 20 --primary-hold 10 --target-mult 1.2
        --min-price 0 --no-generate-charts)
TS=$(date +%Y%m%d_%H%M%S)
LOGDIR="sweep_logs_${TS}"
mkdir -p "$LOGDIR"

echo "=== Run A: stop 1.0 baseline + training CSV export ==="
$PY backtest.py "${COMMON[@]}" --stop-mult 1.0 \
    --export-csv "retrain_trades_cycle47_${TS}.csv" \
    > "$LOGDIR/run_A_stop1.0.log" 2>&1
mv backtest_results_*.json "$LOGDIR/results_A_stop1.0.json" 2>/dev/null

echo "=== Run B: stop 1.25 ==="
$PY backtest.py "${COMMON[@]}" --stop-mult 1.25 \
    --no-ml-analysis --no-ml-walk-forward --no-diagnostics \
    > "$LOGDIR/run_B_stop1.25.log" 2>&1
mv backtest_results_*.json "$LOGDIR/results_B_stop1.25.json" 2>/dev/null

echo "=== Run C: stop 1.5 ==="
$PY backtest.py "${COMMON[@]}" --stop-mult 1.5 \
    --no-ml-analysis --no-ml-walk-forward --no-diagnostics \
    > "$LOGDIR/run_C_stop1.5.log" 2>&1
mv backtest_results_*.json "$LOGDIR/results_C_stop1.5.json" 2>/dev/null

echo "=== Sweep complete: $LOGDIR ==="
