#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="Agentic Trader"
DIST_DIR="$ROOT/dist"
BUILD_DIR="$ROOT/build"
DMG_PATH="$DIST_DIR/Agentic-Trader-macOS.dmg"

if ! command -v pyinstaller >/dev/null 2>&1; then
  python3 -m pip install pyinstaller
fi

rm -rf "$BUILD_DIR/agentic-launcher" "$DIST_DIR/$APP_NAME.app" "$DMG_PATH"
mkdir -p "$DIST_DIR"

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "assets/logo.icns" \
  --add-data "assets/TauricResearch.png:assets" \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR/agentic-launcher" \
  packaging/agentic_trader_launcher.py

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DIST_DIR/$APP_NAME.app" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Built: $DMG_PATH"
