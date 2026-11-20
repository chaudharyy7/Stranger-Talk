#!/usr/bin/env bash
# MongoDB backup:  MONGODB_URI=... ./scripts/backup.sh
# Restore:         mongorestore --uri "$MONGODB_URI" --gzip --archive=backups/<file>.archive --drop
# Needs `mongodump` (MongoDB Database Tools). Schedule it (cron / Railway cron service) and copy
# the archive to storage OUTSIDE the database host (S3, B2, another server...).
set -euo pipefail

: "${MONGODB_URI:?Set MONGODB_URI}"
DB="${MONGODB_DB:-anonymous_chat}"
DIR="${BACKUP_DIR:-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$DIR"
FILE="$DIR/${DB}-$(date -u +%Y%m%dT%H%M%SZ).archive"
mongodump --uri "$MONGODB_URI" --db "$DB" --gzip --archive="$FILE"
echo "Backup written: $FILE"
find "$DIR" -name "*.archive" -mtime +"$KEEP_DAYS" -delete
