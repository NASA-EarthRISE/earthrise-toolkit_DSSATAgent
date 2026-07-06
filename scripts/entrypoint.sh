#!/bin/bash
set -e

echo "=== EarthRISEAgents Entrypoint ==="

# Only run migrations from the web container (first arg contains "uvicorn")
if echo "$@" | grep -q "uvicorn"; then
    # Migration files are authored in dev and committed to the repo; the
    # container only APPLIES them. We do NOT run makemigrations at boot.
    # migrate and collectstatic must succeed — `set -e` aborts startup on
    # failure so the container never comes up in a half-migrated state.
    echo "Running migrations..."
    python manage.py migrate --noinput

    echo "Collecting static files..."
    python manage.py collectstatic --noinput

    # Optional startup backfill — best-effort, must not block startup.
    echo "Queueing startup time-series backfill..."
    python manage.py fetch_all_time_series 2>&1 || true
else
    # Wait briefly for web container to finish migrations
    echo "Waiting for migrations (5s)..."
    sleep 5
fi

# Execute the passed command (uvicorn, celery, or custom)
echo "Starting: $@"
exec "$@"
