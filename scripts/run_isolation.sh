#!/usr/bin/env bash
# Run the backend tenant-isolation suite against a database.
set -u
cd "$(dirname "$0")/.."

DB="${1:-ai_test}"
ADMIN_MAINT_DSN="postgresql://postgres:dev@localhost:5432/postgres"
MIGRATOR_DATABASE_URL="postgresql://postgres:dev@localhost:5432/${DB}"
CHECKPOINT_DATABASE_URL="postgresql://postgres:dev@localhost:5432/${DB}"
DATABASE_URL="postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/${DB}"

export PGPASSWORD=dev
export APP_RW_PASSWORD=local-dev-only
export TEST_DB="${DB}"
export ADMIN_MAINT_DSN
export MIGRATOR_DATABASE_URL
export CHECKPOINT_DATABASE_URL
export DATABASE_URL

exec uv run --frozen pytest tests/isolation -q --ignore=tests/isolation/matrix
