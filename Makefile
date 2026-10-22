UV ?= uv
COMPOSE ?= docker compose
DATABASE_URL ?= postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/ai
MIGRATOR_DATABASE_URL ?= postgresql://postgres:dev@localhost:5432/ai
CHECKPOINT_DATABASE_URL ?= postgresql://app_rw:local-dev-only@localhost:5432/ai
APP_RW_PASSWORD ?= local-dev-only
TEST_DB ?= ai_test
E2E_TEST_DB ?= ai_e2e
E2E_ARGS ?=
TEST_ADMIN_DSN ?= postgresql://postgres:dev@localhost:5432/postgres
TEST_ENV = PGPASSWORD=dev APP_RW_PASSWORD="$(APP_RW_PASSWORD)" TEST_DB="$(TEST_DB)" ADMIN_MAINT_DSN="$(TEST_ADMIN_DSN)" MIGRATOR_DATABASE_URL="postgresql://postgres:dev@localhost:5432/$(TEST_DB)" CHECKPOINT_DATABASE_URL="postgresql://postgres:dev@localhost:5432/$(TEST_DB)" DATABASE_URL="postgresql+asyncpg://app_rw:$(APP_RW_PASSWORD)@localhost:5432/$(TEST_DB)"
MIGRATION_ENV = MIGRATOR_DATABASE_URL="$(MIGRATOR_DATABASE_URL)" \
	CHECKPOINT_DATABASE_URL="$(CHECKPOINT_DATABASE_URL)" \
	APP_RW_PASSWORD="$(APP_RW_PASSWORD)"

.DEFAULT_GOAL := help
.PHONY: help install up replica-up replica-down down reset emulators db-shell migrate migration run fmt lint types test check \
	test-isolation static infra-fmt infra-validate image build smoke deploy-staging obs-verify \
	seed e2e contract wiring scan verify doctor up-deps env-check load-smoke backup-restore fuzz \
	infra-test logs clean verify-wiring smoke-live release-check load-staging

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies and pre-commit hooks
	$(UV) sync --frozen
	$(UV) run --frozen pre-commit install

up: ## Build and start the COMPLETE local stack (db, migrate, api, worker, web, fake IdP, emulators)
	$(COMPOSE) up -d --build --wait --remove-orphans

up-deps: ## Start only the database, emulators and Redis (backend tests)
	$(COMPOSE) up -d --wait db gcs tasks redis

emulators: ## Start only the Cloud Storage and Cloud Tasks emulators (port contract tests)
	$(COMPOSE) up -d gcs tasks

replica-up: ## Start the replica stack on hermetic fakes (web :3010, api :8090; compose.replica.yml)
	$(COMPOSE) -f compose.replica.yml up -d --build --wait --remove-orphans

replica-down: ## Stop the replica stack
	$(COMPOSE) -f compose.replica.yml down

down: ## Stop local services
	$(COMPOSE) down

logs: ## Tail logs of all local stack services
	$(COMPOSE) logs -f

clean: ## Remove build artifacts, bytecode and cache directories (preserves .env, user data, skills)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .import_linter_cache .coverage htmlcov dist build mutants
	rm -rf web/.next web/node_modules/.cache

reset: ## Stop local services and DELETE their named volumes and database data
	$(COMPOSE) down -v --remove-orphans

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

types: ## Backend strict types and frontend TypeScript check
	$(UV) run --frozen mypy
	npm --prefix web run typecheck

test-isolation: ## PostgreSQL tenant isolation hard gate
	$(TEST_ENV) $(UV) run --frozen pytest tests/isolation -x

test: ## Isolation suite first, then everything except evals
	$(MAKE) test-isolation
	$(TEST_ENV) $(UV) run --frozen pytest -m "not eval and not live" --cov

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
	for env in staging prod; do \
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

infra-test: ## Offline terraform plan of the whole stack and module tests (mock providers, no cloud)
	for dir in stack modules/iam modules/secret_manager; do \
	  $(TF) -chdir=$$dir init -backend=false -input=false >/dev/null && \
	  $(TF) -chdir=$$dir test -no-color || exit 1; done

image: ## Build the api, worker and web images
	docker build -f docker/api.Dockerfile -t account-intelligence-api:local .
	docker build -f docker/worker.Dockerfile -t account-intelligence-worker:local .
	docker build -t account-intelligence-web:local web

smoke: image ## Run the image against the local database: non-root, /readyz 200, clean SIGTERM
	bash scripts/smoke_image.sh

deploy-staging: ## PRINT (never run) the commands that deploy staging; needs the owner's project
	@bash scripts/deploy_staging.sh

# The gates the release workflow applies (ci.yml images job and _deploy.yml smoke), runnable by
# hand so the emergency Cloud Build path (DEPLOYMENT.md section 2.2) ships nothing unchecked.
# Images: the local tags `make image` builds, or RELEASE_*_IMAGE=... for pushed digests. The smoke
# step needs the deployed environment's names in the environment (APP_URL, GCP_PROJECT_ID,
# GCP_REGION, CLOUD_RUN_{API,WORKER,WEB}_SERVICE, {API,WORKER,WEB}_DIGEST_REF) and runs only when
# APP_URL is set.
TRIVY_IMAGE ?= aquasec/trivy:0.75.0
RELEASE_API_IMAGE ?= account-intelligence-api:local
RELEASE_WORKER_IMAGE ?= account-intelligence-worker:local
RELEASE_WEB_IMAGE ?= account-intelligence-web:local
release-check: ## Image inspection, Trivy (fixable HIGH/CRITICAL) and the deployed smoke test; the emergency release gate
	bash docker/ci/image_checks.sh api "$(RELEASE_API_IMAGE)"
	bash docker/ci/image_checks.sh worker "$(RELEASE_WORKER_IMAGE)"
	bash docker/ci/image_checks.sh web "$(RELEASE_WEB_IMAGE)"
	for image in "$(RELEASE_API_IMAGE)" "$(RELEASE_WORKER_IMAGE)" "$(RELEASE_WEB_IMAGE)"; do \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro $(TRIVY_IMAGE) \
	    image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$$image" || exit 1; done
	@if [ -n "$$APP_URL" ]; then bash docker/ci/smoke_deployed.sh; \
	else echo "APP_URL unset: skipping docker/ci/smoke_deployed.sh (set the deployed environment's names to run it)"; fi


build: ## Build every image (the web image runs the Next.js production build)
	$(MAKE) image

seed: ## Add synthetic pilot tenant and account fixtures (does not start a run)
	APP_ENV=dev $(UV) run --env-file .env --frozen python scripts/seed_pilot.py

e2e: ## Run the Playwright suite with a dedicated synthetic API and test database
	$(COMPOSE) up -d --wait db gcs tasks redis
	$(COMPOSE) exec -T db psql -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$(E2E_TEST_DB)'" | grep -q 1 || $(COMPOSE) exec -T db createdb -U postgres $(E2E_TEST_DB)
	PGPASSWORD=dev TEST_DB=$(E2E_TEST_DB) ADMIN_MAINT_DSN=postgresql://postgres:dev@localhost:5432/postgres MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/$(E2E_TEST_DB) CHECKPOINT_DATABASE_URL=postgresql://postgres:dev@localhost:5432/$(E2E_TEST_DB) DATABASE_URL=postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/$(E2E_TEST_DB) APP_RW_PASSWORD=local-dev-only $(UV) run --frozen alembic upgrade head
	API_ORIGIN=http://127.0.0.1:8088 npm --prefix web run build
	PGPASSWORD=dev TEST_DB=$(E2E_TEST_DB) ADMIN_MAINT_DSN=postgresql://postgres:dev@localhost:5432/postgres MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/$(E2E_TEST_DB) CHECKPOINT_DATABASE_URL=postgresql://postgres:dev@localhost:5432/$(E2E_TEST_DB) DATABASE_URL=postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/$(E2E_TEST_DB) APP_RW_PASSWORD=local-dev-only E2E_API_ORIGIN=http://127.0.0.1:8088 E2E_WEB_ORIGIN=http://localhost:3008 npm --prefix web run e2e -- $(E2E_ARGS)

contract: ## Check OpenAPI, generated client, policy catalog, and static wiring inventory
	$(UV) run --frozen python scripts/export_openapi.py --check
	$(UV) run --frozen python scripts/gen_web_client.py --check
	$(UV) run --frozen python scripts/check_route_catalog.py
	$(UV) run --frozen python scripts/wiring_audit.py --check

wiring: ## Regenerate the static frontend/backend wiring inventory
	$(UV) run --frozen python scripts/wiring_audit.py

scan: ## Run secret, client-output, and canary-related security regression tests
	$(TEST_ENV) $(UV) run --frozen pytest tests/crosscut/test_x5_security.py tests/phase4/test_ws10_gates.py -q

verify: ## Run local quality, contract, isolation, test, Terraform, and build checks
	$(MAKE) lint types static contract test-isolation scan obs-verify
	$(TEST_ENV) $(UV) run --frozen pytest -m "not eval and not live"
	npm --prefix web run lint
	npm --prefix web test
	$(MAKE) infra-fmt infra-validate build

env-check: ## Validate the current environment against the typed registry (names only)
	$(UV) run --frozen --env-file .env python -m account_intelligence.config

load-smoke: ## k6 load smoke (Docker) against the running local stack; seeds the synthetic member first
	$(MAKE) doctor >/dev/null || true
	docker run --rm -i --add-host=host.docker.internal:host-gateway -v "$(CURDIR)/ops/load:/scripts:ro" grafana/k6:0.54.0 run /scripts/smoke.js

load-staging: ## k6 reader profile (ops/load/k6_profile.js) against BASE_URL with a synthetic SESSION_COOKIE; never run unasked
	@test -n "$$BASE_URL" || { echo "BASE_URL is required (the staging origin)"; exit 2; }
	@test -n "$$SESSION_COOKIE" || { echo "SESSION_COOKIE is required (a synthetic viewer's session, never a real user's)"; exit 2; }
	docker run --rm -i -e BASE_URL -e SESSION_COOKIE -v "$(CURDIR)/ops/load:/scripts:ro" grafana/k6:0.54.0 run /scripts/k6_profile.js

backup-restore: ## Backup/restore test: pg_dump -> pg_restore equivalence and isolation
	$(TEST_ENV) $(UV) run --frozen pytest tests/ops/test_backup_restore.py -q

fuzz: ## OpenAPI-driven API fuzz (Hypothesis) against the real app on the test database
	$(TEST_ENV) $(UV) run --frozen pytest tests/fuzz -q

verify-wiring: ## Hermetic end-to-end wiring matrix (LAYER | CHECK | RESULT | EVIDENCE); needs `make up`, no keys
	$(UV) run --frozen python scripts/verify_wiring.py

doctor: ## Diagnose local toolchain and synthetic service readiness without printing secrets
	$(UV) run --frozen python scripts/doctor.py
