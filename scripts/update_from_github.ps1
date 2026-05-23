$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "[update] Agentic Trader update-only mode"
Write-Host "[update] This script will not start the dashboard or any tunnel."

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "[update][error] git is not installed or not on PATH."
}

if (-not (Test-Path ".git")) {
    throw "[update][error] $Root is not a git checkout."
}

$Status = git status --porcelain
if ($Status) {
    Write-Host "[update][error] Local changes are present. Commit, stash, or discard them before updating."
    Write-Host "[update] Changed files:"
    git status --short
    exit 1
}

Write-Host "[update] Pulling latest code..."
git pull --ff-only

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "[update] Syncing Python environment with uv..."
    uv sync --extra web --extra dev
} else {
    Write-Host "[update] uv not found; using pip editable install..."
    python -m pip install -e ".[web,dev]"
}

Write-Host "[update] Done. Nothing was started."
