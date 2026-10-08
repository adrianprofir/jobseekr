#!/bin/sh
# Housekeeping loop for the demo (DEMO_MODE): every SCHEDULER_INTERVAL_SECONDS
# (300 by default) delete expired demo sandboxes, then expired sessions.
# A failed run is logged and retried on the next pass; the loop never stops.
# Every pass ends by touching /tmp/scheduler-heartbeat, which a healthcheck can read.
set -u

interval="${SCHEDULER_INTERVAL_SECONDS:-300}"
echo "scheduler: running expire_sandboxes and clearsessions every ${interval}s"
while true; do
    python manage.py expire_sandboxes || echo "scheduler: expire_sandboxes failed (exit $?)" >&2
    python manage.py clearsessions || echo "scheduler: clearsessions failed (exit $?)" >&2
    touch /tmp/scheduler-heartbeat
    sleep "$interval"
done
