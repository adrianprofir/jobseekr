#!/usr/bin/env bash
# Back up the jobseekr database and uploaded documents from the running
# Docker Compose stack into backups/<timestamp>/ (or the directory given as $1).
#
#   scripts/backup.sh                 # -> backups/20261004-093000/
#   scripts/backup.sh /srv/backups/jobseekr     # -> /srv/backups/jobseekr/20261004-093000/
#
# Produces:
#   db.dump          PostgreSQL custom-format dump (restore with scripts/restore.sh)
#   documents.tar.gz all uploaded CVs and cover letters
set -euo pipefail

cd "$(dirname "$0")/.."

target_root="${1:-backups}"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$target_root/$stamp"
mkdir -p "$target"

trap 'docker compose start web' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose stop web

echo "Backing up database to $target/db.dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
    > "$target/db.dump"

echo "Backing up documents to $target/documents.tar.gz"
docker compose run --rm --no-deps -T --entrypoint tar web -C /data/documents -czf - . > "$target/documents.tar.gz"

# Reject empty database dumps and documents archives that fail gzip integrity checks.
test -s "$target/db.dump" || { echo "ERROR: database dump is empty" >&2; exit 1; }
gzip -t "$target/documents.tar.gz" || { echo "ERROR: documents archive is corrupt" >&2; exit 1; }

echo "Backup complete: $target"
ls -lh "$target"
