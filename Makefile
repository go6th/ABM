UV ?= uv
COMPOSE ?= docker compose
DATABASE_URL ?= postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/ai
MIGRATOR_DATABASE_URL ?= postgresql://postgres:dev@localhost:5432/ai
CHECKPOINT_DATABASE_URL ?= $(DATABASE_URL)
APP_RW_PASSWORD ?= local-dev-only

.PHONY: up down migrate test lint check deploy-staging

up:
	$(COMPOSE) up -d db

down:
	$(COMPOSE) down

migrate:
	MIGRATOR_DATABASE_URL="$(MIGRATOR_DATABASE_URL)" \
	CHECKPOINT_DATABASE_URL="$(CHECKPOINT_DATABASE_URL)" \
	APP_RW_PASSWORD="$(APP_RW_PASSWORD)" $(UV) run --frozen alembic upgrade head

test:
	$(UV) run --frozen pytest tests/isolation -x
	$(UV) run --frozen pytest -m "not eval" --cov=src --cov-fail-under=80

lint:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen mypy src

check:
	DATABASE_URL="$(DATABASE_URL)" \
	MIGRATOR_DATABASE_URL="$(MIGRATOR_DATABASE_URL)" \
	APP_RW_PASSWORD="$(APP_RW_PASSWORD)" bash scripts/check.sh

deploy-staging:
	bash scripts/deploy_staging.sh
