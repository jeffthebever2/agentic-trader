param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$AppName = "Agentic Trader"
$DistDir = Join-Path $Root "dist"
$BuildDir = Join-Path $Root "build\agentic-launcher"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    & $Python -m pip install pyinstaller
}

if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
if (Test-Path (Join-Path $DistDir "$AppName.exe")) { Remove-Item (Join-Path $DistDir "$AppName.exe") -Force }
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "$AppName" `
    --distpath "$DistDir" `
    --workpath "$BuildDir" `
    "packaging\agentic_trader_launcher.py"

Write-Host "Built: $(Join-Path $DistDir "$AppName.exe")"
