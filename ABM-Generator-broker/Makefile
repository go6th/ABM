UV ?= uv
COMPOSE ?= docker compose
DATABASE_URL ?= postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/ai
MIGRATOR_DATABASE_URL ?= postgresql://postgres:dev@localhost:5432/ai
CHECKPOINT_DATABASE_URL ?= postgresql://app_rw:local-dev-only@localhost:5432/ai
APP_RW_PASSWORD ?= local-dev-only
MIGRATION_ENV = MIGRATOR_DATABASE_URL="$(MIGRATOR_DATABASE_URL)" \
	CHECKPOINT_DATABASE_URL="$(CHECKPOINT_DATABASE_URL)" \
	APP_RW_PASSWORD="$(APP_RW_PASSWORD)"

.DEFAULT_GOAL := help
.PHONY: help install up down db-shell migrate migration run fmt lint test check

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies and pre-commit hooks
	$(UV) sync --frozen
	$(UV) run --frozen pre-commit install

up: ## Start Postgres and the object store
	$(COMPOSE) up -d db objectstore

down: ## Stop local services
	$(COMPOSE) down

db-shell: ## Open psql in the database container
	$(COMPOSE) exec db psql -U postgres -d ai

migrate: ## Apply migrations to the local database
	$(MIGRATION_ENV) $(UV) run --frozen alembic upgrade head

migration: ## Create a migration: make migration m="describe change"
	$(MIGRATION_ENV) $(UV) run --frozen alembic revision -m "$(m)"

run: ## Run the API with reload on :8080
	$(UV) run --frozen --env-file .env uvicorn account_intelligence.api.main:app --reload --port 8080

fmt: ## Format and auto-fix
	$(UV) run --frozen ruff format .
	$(UV) run --frozen ruff check --fix .

lint: ## Ruff and mypy
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen ruff check .
	$(UV) run --frozen mypy

test: ## Isolation suite first, then everything except evals
	$(UV) run --frozen pytest tests/isolation -x
	$(UV) run --frozen pytest -m "not eval" --cov

check: ## Full pre-PR gate against a scratch database (needs psql)
	DATABASE_URL="$(DATABASE_URL)" \
	MIGRATOR_DATABASE_URL="$(MIGRATOR_DATABASE_URL)" \
	APP_RW_PASSWORD="$(APP_RW_PASSWORD)" bash scripts/check.sh
