#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[update] Agentic Trader update-only mode"
echo "[update] This script will not start the dashboard or any tunnel."

if ! command -v git >/dev/null 2>&1; then
  echo "[update][error] git is not installed or not on PATH." >&2
  exit 1
fi

if [ ! -d ".git" ]; then
  echo "[update][error] $ROOT is not a git checkout." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "[update][error] Local changes are present. Commit, stash, or discard them before updating."
  echo "[update] Changed files:"
  git status --short
  exit 1
fi

echo "[update] Pulling latest code..."
git pull --ff-only

if command -v uv >/dev/null 2>&1; then
  echo "[update] Syncing Python environment with uv..."
  uv sync --extra web --extra dev
else
  echo "[update] uv not found; using pip editable install..."
  python3 -m pip install -e ".[web,dev]"
fi

echo "[update] Done. Nothing was started."
