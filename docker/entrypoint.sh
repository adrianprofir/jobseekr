#!/bin/sh
# Apply database migrations before starting the app so a fresh `docker compose up`
# or an upgrade always runs against an up-to-date schema.
set -eu

python manage.py migrate --noinput
exec "$@"
