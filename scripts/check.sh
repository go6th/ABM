#!/usr/bin/env bash
set -euo pipefail

skill=".claude/skills/account-intelligence-platform"
export APP_RW_PASSWORD="${APP_RW_PASSWORD:-local-dev-only}"

bash "$skill/scripts/selftest.sh"
uv run --frozen python "$skill/scripts/validate_assets.py"
uv run --frozen python "$skill/scripts/lint_migrations.py" migrations
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy

database_url="${MIGRATOR_DATABASE_URL:-postgresql://postgres:dev@localhost:5432/ai}"
scratch_db="ai_check_$$"
scratch_url="$(uv run --frozen python -c \
    'import sys; from sqlalchemy.engine import make_url; print(make_url(sys.argv[1]).set(database=sys.argv[2]).render_as_string(hide_password=False))' \
    "$database_url" "$scratch_db")"
maintenance_url="$(uv run --frozen python -c \
    'import sys; from sqlalchemy.engine import make_url; print(make_url(sys.argv[1]).set(database="postgres").render_as_string(hide_password=False))' \
    "$database_url")"
runtime_url="$(uv run --frozen python -c \
    'import os, sys; from sqlalchemy.engine import make_url; print(make_url(sys.argv[1]).set(drivername="postgresql+asyncpg", username="app_rw", password=os.environ["APP_RW_PASSWORD"]).render_as_string(hide_password=False))' \
    "$scratch_url")"
checkpoint_runtime_url="$(uv run --frozen python -c \
    'import os, sys; from sqlalchemy.engine import make_url; print(make_url(sys.argv[1]).set(drivername="postgresql", username="app_rw", password=os.environ["APP_RW_PASSWORD"]).render_as_string(hide_password=False))' \
    "$scratch_url")"
psql "$database_url" -v ON_ERROR_STOP=1 -c "CREATE DATABASE $scratch_db"
trap 'psql "$database_url" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS '"$scratch_db"' WITH (FORCE)" >/dev/null' EXIT

export MIGRATOR_DATABASE_URL="$scratch_url"
export CHECKPOINT_DATABASE_URL="$scratch_url"
export TEST_DB="$scratch_db"
export ADMIN_DSN="$scratch_url"
export ADMIN_MAINT_DSN="${ADMIN_MAINT_DSN:-$maintenance_url}"

uv run --frozen alembic upgrade head
violations="$(psql "$scratch_url" -v ON_ERROR_STOP=1 -tA -f "$skill/scripts/check_rls_catalog.sql")"
if [[ -n "$violations" ]]; then
    printf '%s\n' "$violations" >&2
    exit 1
fi
printf '%s\n' "RLS catalog: no violations"
export DATABASE_URL="$runtime_url"
export CHECKPOINT_DATABASE_URL="$checkpoint_runtime_url"
uv run --frozen pytest tests/isolation -x
uv run --frozen pytest -m "not eval" --cov
