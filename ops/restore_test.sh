#!/usr/bin/env bash
# Restore test (Gate B requirement: a backup that hasn't been restored is not
# a backup). Restores the newest dump into the throwaway tmpfs test DB and
# asserts schema + row-count sanity.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/trading-desk}"
LATEST="$(ls -1t "$BACKUP_DIR"/trading_desk_*.sql.gz | head -1)"
echo "restoring: $LATEST"

cd "$REPO_DIR"
# Isolated compose project: never touches the dev DB container/volume.
COMPOSE=(docker compose -p tradingdesk-restore -f compose.yaml -f compose.test.yaml)
"${COMPOSE[@]}" up -d --wait

cleanup() { "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

CONTAINER="$("${COMPOSE[@]}" ps -q db)"
gunzip -c "$LATEST" | docker exec -i "$CONTAINER" psql -q -U desk -d trading_desk

check() {  # check <sql> <description>
  local result
  result="$(docker exec "$CONTAINER" psql -tA -U desk -d trading_desk -c "$1")"
  echo "  $2: $result"
  [ -n "$result" ] || { echo "FAIL: $2 returned nothing" >&2; exit 1; }
}

echo "sanity checks:"
check "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" "tables"
check "SELECT count(*) FROM documents" "documents rows"
check "SELECT count(*) FROM price_bars" "price_bars rows"
check "SELECT count(*) FROM assertions WHERE type IS NOT NULL" "typed assertions"

TABLES="$(docker exec "$CONTAINER" psql -tA -U desk -d trading_desk -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [ "$TABLES" -lt 19 ]; then
  echo "FAIL: expected >=19 tables, got $TABLES" >&2
  exit 1
fi
echo "restore test PASSED ($TABLES tables)"
