#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is required for the local staging deployment." >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    echo "Create an untracked .env from .env.example and set staging credentials first." >&2
    exit 1
fi

export ENV=staging
docker compose up -d db redis

MIGRATOR_DATABASE_URL="${STAGING_MIGRATOR_DATABASE_URL:-postgresql://postgres:dev@localhost:5432/ai}" \
CHECKPOINT_DATABASE_URL="${STAGING_CHECKPOINT_DATABASE_URL:-postgresql://postgres:dev@localhost:5432/ai}" \
APP_RW_PASSWORD="${APP_RW_PASSWORD:?APP_RW_PASSWORD must be set in the environment}" \
uv run --frozen alembic upgrade head

docker compose up -d --build api inngest
for attempt in $(seq 1 30); do
    if curl --fail --silent http://localhost:8080/healthz >/dev/null; then
        echo "Local staging smoke test passed at http://localhost:8080/healthz"
        exit 0
    fi
    sleep 2
done

echo "Local staging API did not become healthy; inspect docker compose logs api." >&2
exit 1
