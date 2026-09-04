#!/bin/sh
set -eu
if [ -n "${CLOUD_RUN_EXECUTION:-}" ]; then
  python /app/wait_for_postgres.py
fi
exec "$@"
