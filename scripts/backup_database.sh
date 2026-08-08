#!/bin/bash
# Daily Postgres backup: dumps news_db from the running postgres container,
# keeps a local copy on the droplet, and uploads a copy to DigitalOcean
# Spaces (S3-compatible object storage) for offsite protection against
# losing the whole droplet (disk failure, accidental `docker compose down
# -v`, etc.).
#
# Intended to run from cron on the droplet (registered manually via
# `crontab -e`, matching the existing poll/enrich cron precedent — see
# india-news-app-handoff.md §8). Not baked into docker-compose.prod.yml or
# run from inside a container.
#
# Requires a sourced credentials file — see .backup-env.example for the
# expected vars. That file lives at /root/.backup-env on the droplet
# (chmod 600, not committed anywhere).
#
# To restore a dump:
#   docker exec -i news_postgres_prod pg_restore -U news_user -d news_db --clean --if-exists < news_db_TIMESTAMP.dump
set -e

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-/root/.backup-env}"
if [ -f "$BACKUP_ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$BACKUP_ENV_FILE"
else
    echo "Missing credentials file: $BACKUP_ENV_FILE (see backend/.backup-env.example)" >&2
    exit 1
fi

: "${SPACES_BUCKET:?SPACES_BUCKET not set}"
: "${SPACES_ENDPOINT_URL:?SPACES_ENDPOINT_URL not set}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID not set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY not set}"

BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="news_db_${TIMESTAMP}.dump"
LOCAL_PATH="${BACKUP_DIR}/${FILENAME}"

mkdir -p "$BACKUP_DIR"

echo "================================================================="
echo "  Dumping news_db from news_postgres_prod ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
echo "================================================================="
# -Fc: custom format — compressed, supports selective/parallel restore via
# pg_restore, so no separate gzip step needed.
docker exec news_postgres_prod pg_dump -U news_user -Fc news_db > "$LOCAL_PATH"
echo "Wrote $LOCAL_PATH ($(du -h "$LOCAL_PATH" | cut -f1))"

echo "================================================================="
echo "  Uploading to DigitalOcean Spaces (s3://${SPACES_BUCKET}/${FILENAME})"
echo "================================================================="
aws s3 cp "$LOCAL_PATH" "s3://${SPACES_BUCKET}/${FILENAME}" --endpoint-url "$SPACES_ENDPOINT_URL"

echo "================================================================="
echo "  Local retention: deleting dumps older than ${RETENTION_DAYS} days"
echo "================================================================="
find "$BACKUP_DIR" -name "news_db_*.dump" -mtime "+${RETENTION_DAYS}" -print -delete

echo "================================================================="
echo "  Done."
echo "================================================================="
