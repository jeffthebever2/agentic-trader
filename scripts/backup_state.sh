#!/usr/bin/env bash
# Encrypted off-node backup of Agentic Trader runtime state (D5).
#
# Backs up the state that lives ONLY on the node and cannot be regenerated:
#   tmp/*.json (paper books, holdings-brain, signals, copytrade), the encrypted
#   broker session/cred files, .env, and the deployed ML model bundle.
#
# The archive is GPG-symmetric-encrypted (passphrase from BACKUP_PASSPHRASE) so
# the off-node copy never holds plaintext secrets, then pushed with rclone to
# BACKUP_REMOTE (e.g. "b2:agentictrader-backups" or "s3:bucket/path").
#
# Env:
#   APP_DIR            (default /opt/agentictrader)
#   BACKUP_PASSPHRASE  (required) — symmetric encryption key
#   BACKUP_REMOTE      (required) — rclone remote:path
#   BACKUP_RETENTION   (default 30) — days to keep on the remote
#
# Restore:  rclone copy <remote>/<file> . && gpg -d <file> | tar xz
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/agentictrader}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
: "${BACKUP_REMOTE:?BACKUP_REMOTE must be set (rclone remote:path)}"
RETENTION="${BACKUP_RETENTION:-30}"

cd "$APP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="agentictrader-state-${STAMP}.tar.gz.gpg"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Collect the irreplaceable state. Missing paths are skipped, not fatal.
INCLUDE=()
for p in \
  tmp \
  .env \
  ml_models/latest \
  .fidelity_session_* \
  .fidelity_creds_*.json \
  .broker-session-key-* \
  tmp/broker_session.key ; do
  for match in $p; do
    [ -e "$match" ] && INCLUDE+=("$match")
  done
done

if [ "${#INCLUDE[@]}" -eq 0 ]; then
  echo "backup: nothing to archive under $APP_DIR" >&2
  exit 1
fi

tar czf - "${INCLUDE[@]}" \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase "$BACKUP_PASSPHRASE" -o "$WORK/$ARCHIVE"

# Owner-only locally before it leaves the box.
chmod 600 "$WORK/$ARCHIVE"

rclone copy "$WORK/$ARCHIVE" "$BACKUP_REMOTE/" --no-traverse

# Prune old off-node copies.
rclone delete "$BACKUP_REMOTE/" --min-age "${RETENTION}d" --include "agentictrader-state-*.tar.gz.gpg" || true

echo "backup: uploaded $ARCHIVE to $BACKUP_REMOTE (retention ${RETENTION}d)"
