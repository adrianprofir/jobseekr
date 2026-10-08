#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh into the running Docker Compose stack.
# This REPLACES the current database contents and uploaded documents.
#
#   scripts/restore.sh backups/20261004-093000 --force
set -euo pipefail

cd "$(dirname "$0")/.."

source_dir="${1:-}"
if [[ -z "$source_dir" || ! -f "$source_dir/db.dump" || ! -f "$source_dir/documents.tar.gz" ]]; then
    echo "Usage: $0 <backup-directory> --force" >&2
    echo "The directory must contain db.dump and documents.tar.gz." >&2
    exit 2
fi
if [[ "${2:-}" != "--force" ]]; then
    echo "This replaces the current database and documents with $source_dir." >&2
    echo "Re-run with --force to continue." >&2
    exit 2
fi

staging="$(mktemp -d ./.restore.XXXXXX)"
cleanup() {
    result=$?
    rm -rf "$staging"
    docker compose start web || result=$?
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose stop web

docker compose exec -T db pg_restore --file=/dev/null < "$source_dir/db.dump"
tar -xzf "$source_dir/documents.tar.gz" -C "$staging"

echo "Restoring database from $source_dir/db.dump"
docker compose exec -T db sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' \
    < "$source_dir/db.dump"

echo "Restoring documents from $source_dir/documents.tar.gz"
tar -C "$staging" -cf - . | docker compose run --rm --no-deps -T --entrypoint sh web -c \
    'find /data/documents -mindepth 1 -delete && tar -C /data/documents -xf -'

echo "Restore complete."
