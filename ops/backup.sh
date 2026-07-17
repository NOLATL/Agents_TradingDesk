#!/usr/bin/env bash
# Nightly DB backup (STAGE_2_KICKOFF backup section; DECISIONS.md 2026-07-17).
#   1. pg_dump the project DB -> gzip -> dated file in BACKUP_DIR (rolling KEEP_DAYS)
#   2. rclone copy the SEALED dump to the Google Drive remote (offsite)
# Idempotent: re-running on the same date overwrites the same local file and
# rclone copy skips unchanged files. Safe to run twice (CLAUDE.md requirement).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/trading-desk}"
KEEP_DAYS="${KEEP_DAYS:-14}"
RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive:trading-desk-backups}"
RCLONE_BIN="${RCLONE_BIN:-$HOME/.local/bin/rclone}"
STAMP="$(date +%Y-%m-%d)"
DUMP_FILE="$BACKUP_DIR/trading_desk_${STAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# 1. Logical dump from the Compose container (localhost:55432).
docker exec agents_tradingdesk-db-1 pg_dump -U desk -d trading_desk \
  | gzip > "${DUMP_FILE}.partial"
mv "${DUMP_FILE}.partial" "$DUMP_FILE"   # seal: only complete files keep the real name
echo "dump: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

# 2. Rolling retention on the local card.
find "$BACKUP_DIR" -name 'trading_desk_*.sql.gz' -mtime "+$KEEP_DAYS" -delete

# 3. Offsite: copy the sealed file via the Drive API (never a live sync).
if "$RCLONE_BIN" listremotes 2>/dev/null | grep -q "^${RCLONE_REMOTE%%:*}:"; then
  "$RCLONE_BIN" copy "$DUMP_FILE" "$RCLONE_REMOTE" --drive-chunk-size 64M
  echo "offsite: copied to $RCLONE_REMOTE"
else
  echo "offsite: SKIPPED — rclone remote '${RCLONE_REMOTE%%:*}' not configured" >&2
  exit 3
fi
