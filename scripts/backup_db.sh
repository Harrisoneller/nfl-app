#!/usr/bin/env bash
# Nightly Postgres backup. Run via cron at 4am UTC:
#   0 4 * * * /path/to/scripts/backup_db.sh
#
# Set BACKUP_DIR + DATABASE_URL in the cron env or your platform's
# scheduled-job configuration.

set -euo pipefail

DATABASE_URL="${DATABASE_URL:-postgresql://localhost/nflapp}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/nfl-app-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/nflapp-$stamp.sql.gz"

echo "→ Dumping to $out"
pg_dump "$DATABASE_URL" --no-owner --no-acl | gzip -9 > "$out"

echo "→ Cleaning backups older than $KEEP_DAYS days"
find "$BACKUP_DIR" -name "nflapp-*.sql.gz" -mtime "+$KEEP_DAYS" -delete

ls -lh "$BACKUP_DIR" | tail -5
echo "✓ Backup complete: $out"
