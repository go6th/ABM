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
.PHONY: help install up down emulators db-shell migrate migration run fmt lint test check \
	static infra-fmt infra-validate image smoke deploy-staging obs-verify

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies and pre-commit hooks
	$(UV) sync --frozen
	$(UV) run --frozen pre-commit install

up: ## Start Postgres, the Google emulators and Redis
	$(COMPOSE) up -d db gcs tasks redis

emulators: ## Start only the Cloud Storage and Cloud Tasks emulators (port contract tests)
	$(COMPOSE) up -d gcs tasks

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

TF ?= docker run --rm -u $$(id -u):$$(id -g) -v "$(CURDIR)/infra":/infra -w /infra hashicorp/terraform:1.9

static: ## Import contracts, docstrings, comments, guarantees, loops, security scan
	$(UV) run --frozen lint-imports
	$(UV) run --frozen interrogate src
	$(UV) run --frozen python scripts/check_comments.py
	$(UV) run --frozen python scripts/check_guarantees.py
	$(UV) run --frozen python scripts/gen_loops.py --check
	$(UV) run --frozen bandit -q -r src -c pyproject.toml
	$(UV) run --frozen vulture src scripts/vulture_whitelist.py --min-confidence 80
	$(UV) run --frozen python -m compileall -q src scripts migrations

infra-fmt: ## terraform fmt (check) over infra/
	$(TF) fmt -recursive -check

infra-validate: ## terraform init -backend=false and validate for every environment
	for env in dev staging production; do \
	  $(TF) -chdir=envs/$$env init -backend=false -input=false >/dev/null && \
	  $(TF) -chdir=envs/$$env validate -no-color || exit 1; done

obs-verify: ## Telemetry contract, generated Terraform diff, runbook lint, canary suite, obs tests
	$(UV) run --frozen python scripts/check_telemetry.py
	$(UV) run --frozen python scripts/gen_observability.py --check
	$(UV) run --frozen python scripts/check_runbooks.py
	$(UV) run --frozen pytest tests/obs -p no:cacheprovider -q
	$(TF) fmt -check -recursive modules/observability
	$(TF) -chdir=modules/observability init -backend=false -input=false >/dev/null && \
	  $(TF) -chdir=modules/observability validate -no-color

image: ## Build the service image
	docker build -t account-intelligence:local .

smoke: image ## Run the image against the local database: non-root, /readyz 200, clean SIGTERM
	bash scripts/smoke_image.sh

deploy-staging: ## PRINT (never run) the commands that deploy staging; needs the owner's project
	@bash scripts/deploy_staging.sh
