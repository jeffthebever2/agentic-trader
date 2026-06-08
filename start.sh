#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Agentic Trader — Production Launcher
#
# Usage:
#   ./start.sh web          Start the dashboard (port 8001)
#   ./start.sh paper        Start 15-portfolio paper trading competition
#   ./start.sh train        Full training pipeline (ML + HMM + Qlib + validation)
#   ./start.sh retrain      Weekly retrain only (fastest model refresh)
#   ./start.sh all          Web dashboard + paper trading (background procs)
#   ./start.sh status       Show what's running
#   ./start.sh logs         Tail latest log files
#   ./start.sh stop         Kill all background processes
#
# Quick start (first time):
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt
#   cp .env.example .env     # fill in your keys
#   ./start.sh train         # trains all models (30–90 min)
#   ./start.sh all           # starts dashboard + paper trading
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
LOGS="$ROOT/logs"
PIDS="$ROOT/tmp/.pids"

mkdir -p "$LOGS" "$ROOT/tmp"

# ── Resolve Python ────────────────────────────────────────────────────────────
if [[ -f "$VENV/bin/python3" ]]; then
    PY="$VENV/bin/python3"
elif command -v python3 &>/dev/null; then
    PY="python3"
else
    echo "ERROR: python3 not found. Set up a venv: python3 -m venv .venv && source .venv/bin/activate"
    exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
ts() { date '+%Y-%m-%d %H:%M:%S'; }
info()  { echo "[ $(ts) ] $*"; }
error() { echo "[ $(ts) ] ERROR: $*" >&2; }

bg_start() {
    local name="$1"; shift
    local log="$LOGS/${name}.log"
    info "Starting $name → $log"
    "$@" >> "$log" 2>&1 &
    local pid=$!
    echo "$name $pid" >> "$PIDS"
    info "$name PID $pid"
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_web() {
    info "Starting web dashboard on http://localhost:8001"
    info "  Portfolios: http://localhost:8001/portfolios"
    cd "$ROOT"
    exec "$PY" run_web.py
}

cmd_paper() {
    info "Starting 15-portfolio paper trading competition"
    cd "$ROOT"
    exec "$PY" scripts/paper_trade_today.py "$@"
}

cmd_train() {
    info "Running full training pipeline (ML + HMM + Qlib + validation)"
    info "  This takes 30–90 minutes depending on hardware and ticker universe."
    info "  Resumable: ./start.sh train --resume (if interrupted)"
    cd "$ROOT"
    exec "$PY" scripts/train_everything.py \
        --tickers all_tickers.txt \
        --include-qlib-features \
        --profile safe \
        "$@"
}

cmd_retrain() {
    info "Running weekly retrain (fastest model refresh, Qlib features included)"
    cd "$ROOT"
    exec "$PY" scripts/retrain_weekly.py \
        --tickers all_tickers.txt \
        --include-qlib-features \
        "$@"
}

cmd_all() {
    info "Starting all production services"
    rm -f "$PIDS"
    touch "$PIDS"
    cd "$ROOT"
    bg_start "web"   "$PY" run_web.py
    sleep 2  # let web bind before paper starts
    bg_start "paper" "$PY" scripts/paper_trade_today.py
    info ""
    info "  Dashboard: http://localhost:8001"
    info "  Portfolio competition: http://localhost:8001/portfolios"
    info "  Logs: ./start.sh logs"
    info "  Stop: ./start.sh stop"
    info ""
    wait
}

cmd_status() {
    info "=== Service Status ==="
    if [[ -f "$PIDS" ]]; then
        while IFS= read -r line; do
            local name pid
            name="$(echo "$line" | awk '{print $1}')"
            pid="$(echo "$line" | awk '{print $2}')"
            if kill -0 "$pid" 2>/dev/null; then
                echo "  ● $name   PID $pid   RUNNING"
            else
                echo "  ○ $name   PID $pid   STOPPED"
            fi
        done < "$PIDS"
    else
        echo "  No managed processes found."
    fi
    echo ""
    info "=== Port 8001 ==="
    if lsof -ti:8001 &>/dev/null; then
        echo "  ● Web server RUNNING on :8001"
    else
        echo "  ○ Web server NOT running"
    fi
    echo ""
    info "=== ML Models ==="
    local latest="$ROOT/ml_models/latest"
    if [[ -d "$latest" ]]; then
        local report="$latest/training_report.json"
        if [[ -f "$report" ]]; then
            python3 -c "
import json, sys
r = json.load(open('$report'))
wf = r.get('walk_forward', {})
roc = wf.get('roc_auc') or r.get('models',{}).get('win_probability',{}).get('metrics',{}).get('roc_auc','N/A')
ts = r.get('trained_at', r.get('timestamp', 'unknown'))
qlib = 'YES' if any(f.startswith('qlib_') for f in r.get('feature_names', [])) else 'NO'
print(f'  Model: {ts}')
print(f'  WF ROC: {roc}')
print(f'  Qlib features: {qlib}')
" 2>/dev/null || echo "  model_bundle.joblib exists (can't read report)"
        else
            echo "  model_bundle.joblib exists (no training_report.json)"
        fi
    else
        echo "  NO MODEL DEPLOYED — run ./start.sh train first"
    fi
}

cmd_logs() {
    local name="${1:-}"
    if [[ -n "$name" ]]; then
        tail -f "$LOGS/${name}.log"
    else
        local latest
        latest="$(ls -t "$LOGS"/*.log 2>/dev/null | head -1 || true)"
        if [[ -n "$latest" ]]; then
            info "Tailing $latest"
            tail -f "$latest"
        else
            info "No logs yet. Available: $LOGS/"
        fi
    fi
}

cmd_stop() {
    info "Stopping all managed processes"
    if [[ -f "$PIDS" ]]; then
        while IFS= read -r line; do
            local name pid
            name="$(echo "$line" | awk '{print $1}')"
            pid="$(echo "$line" | awk '{print $2}')"
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" && info "Stopped $name (PID $pid)"
            fi
        done < "$PIDS"
        rm -f "$PIDS"
    fi
    # also kill anything on port 8001
    if lsof -ti:8001 &>/dev/null; then
        kill "$(lsof -ti:8001)" 2>/dev/null && info "Killed process on :8001"
    fi
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
CMD="${1:-help}"
shift || true

case "$CMD" in
    web)     cmd_web "$@" ;;
    paper)   cmd_paper "$@" ;;
    train)   cmd_train "$@" ;;
    retrain) cmd_retrain "$@" ;;
    all)     cmd_all "$@" ;;
    status)  cmd_status "$@" ;;
    logs)    cmd_logs "$@" ;;
    stop)    cmd_stop "$@" ;;
    help|--help|-h)
        sed -n '2,14p' "$0"
        ;;
    *)
        error "Unknown command: $CMD"
        echo "Run: ./start.sh help"
        exit 1
        ;;
esac
