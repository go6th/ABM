PY      := .venv/bin/python
PIP     := .venv/bin/pip
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/pytest
PSQL    := docker compose exec -T db psql -U postgres -d ai

.PHONY: help venv install db-up db-down db-wait migrate downgrade lint format typecheck \
        unit isolation spike-durability check quality test requirements verification \
        adrs conflicts golden phase0-docs verification-clean clean \
        app-lint app-typecheck app-test app-check temporal-up worker hello \
        bench-model-split bench-people tf-validate deploy-dev deploy-staging \
        up down db-shell

help:
	@echo "Local infrastructure"
	@echo "  make up              start Postgres 16 and MinIO, wait until healthy, create the bucket"
	@echo "  make down            stop the services (volumes and data are kept)"
	@echo "  make db-shell        psql into the local database as the owner role"
	@echo "  make migrate         apply database migrations"
	@echo "  make test            app tests + legacy unit and isolation tests"
	@echo ""
	@echo "Account Intelligence - roadmap Phase 0 (app/)"
	@echo "  make app-check       ruff + format check + mypy + pytest for app/ (what CI runs)"
	@echo "  make temporal-up     local Temporal dev server via docker compose"
	@echo "  make worker          run the Temporal worker against TEMPORAL_ADDRESS"
	@echo "  make hello           start the hello-world workflow"
	@echo "  make bench-model-split  offline model split benchmark -> build/benchmarks"
	@echo "  make bench-people    offline people source benchmark -> build/benchmarks"
	@echo "  make tf-validate     terraform fmt + validate for dev, staging, production"
	@echo "  make deploy-staging  one-command staging deploy (DRY_RUN=1 to plan only)"
	@echo ""
	@echo "Legacy (earlier roadmap, src/abm_generator)"
	@echo "  make venv            create .venv and install dev deps"
	@echo "  make install         install package + dev tools into existing .venv"
	@echo "  make db-up           start postgres16+pgvector via docker compose"
	@echo "  make db-down         stop the database"
	@echo "  make db-wait         wait until postgres accepts connections"
	@echo "  make migrate         apply migrations as the migrator (owner) role"
	@echo "  make downgrade       roll back one migration"
	@echo "  make lint            ruff check (zero warnings)"
	@echo "  make format          ruff format + autofix"
	@echo "  make typecheck       mypy --strict on src/abm_generator"
	@echo "  make unit            unit tests"
	@echo "  make isolation       isolation suite against live Postgres (hard gate)"
	@echo "  make spike-durability  spike 2 durability tests (needs docker db)"
	@echo "  make test            unit + isolation"
	@echo "  make quality         lint + typecheck + coverage gate"
	@echo "  make golden          T9 golden-set checker"
	@echo "  make phase0-docs     T2/T5/T6/T14 document checker"
	@echo "  make check           everything CI runs (quality + gates + test)"

venv:
	python3 -m venv .venv && .venv/bin/pip install --upgrade pip
	$(MAKE) install

install:
	$(PIP) install -q -e ".[dev]"

db-up:
	docker compose up -d db
	$(MAKE) db-wait

db-down:
	docker compose down -v

db-wait:
	@until docker compose exec -T db pg_isready -U postgres -d ai -q; do sleep 1; done
	@echo "database ready"

migrate:
	DATABASE_URL="postgresql://postgres:dev@localhost:5432/ai" \
	MIGRATOR_DATABASE_URL="postgresql://postgres:dev@localhost:5432/ai" \
	$(PY) -m alembic upgrade head
	@if [ -f app/db/alembic.ini ]; then \
		MIGRATOR_DATABASE_URL="postgresql://postgres:dev@localhost:5432/ai" \
		$(PY) -m alembic -c app/db/alembic.ini upgrade head; \
	fi

downgrade:
	DATABASE_URL="postgresql://postgres:dev@localhost:5432/ai" \
	$(PY) -m alembic downgrade -1

lint:
	$(RUFF) check src tests scripts spikes migrations

format:
	$(RUFF) format src tests scripts spikes migrations
	$(RUFF) check --fix src tests scripts spikes migrations

typecheck:
	$(MYPY)

unit:
	$(PYTEST) tests/unit -x -q

isolation:
	$(PYTEST) tests/isolation -x -q

spike-durability:
	$(PYTEST) tests/spike -x -q -m spike

test: app-test unit isolation

requirements:
	$(PY) scripts/check_requirements.py

verification:
	$(PY) scripts/check_verification.py

adrs:
	$(PY) scripts/check_adrs.py

conflicts:
	$(PY) scripts/check_conflicts.py

golden:
	$(PY) scripts/check_golden_set.py

phase0-docs:
	$(PY) scripts/check_phase0_docs.py

quality: lint typecheck
	$(PYTEST) tests/unit --cov=src/abm_generator --cov-fail-under=80 -q

check: quality requirements verification adrs conflicts golden phase0-docs reference test
	@echo "ALL CI GATES PASSED"

verification-clean:
	$(PY) scripts/check_verification.py --strict-open

reference:
	$(PY) scripts/check_reference.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} +

# ---------------------------------------------------------------- roadmap Phase 0 (app/)
APP_PATHS := app tests/app

app-lint:
	$(RUFF) check $(APP_PATHS)
	$(RUFF) format --check $(APP_PATHS)

app-typecheck:
	$(MYPY) -p app

app-test:
	$(PYTEST) tests/app -q

app-check: app-lint app-typecheck app-test
	@echo "APP CHECKS PASSED"

temporal-up:
	docker compose up -d temporal

worker:
	$(PY) -m app.worker worker

hello:
	$(PY) -m app.worker hello "$${NAME:-world}"

bench-model-split:
	$(PY) -m app.benchmarks.model_split --out build/benchmarks

bench-people:
	$(PY) -m app.benchmarks.people_source --out build/benchmarks

TERRAFORM ?= terraform

tf-validate:
	$(TERRAFORM) fmt -recursive -check infra/terraform
	@for env in dev staging production; do \
		$(TERRAFORM) -chdir=infra/terraform/envs/$$env init -input=false -backend=false >/dev/null && \
		$(TERRAFORM) -chdir=infra/terraform/envs/$$env validate || exit 1; \
	done

deploy-dev:
	TERRAFORM=$(TERRAFORM) scripts/deploy.sh dev

deploy-staging:
	TERRAFORM=$(TERRAFORM) scripts/deploy.sh staging

# ---------------------------------------------------------------- local infrastructure
up:
	docker compose up -d --wait db minio
	docker compose run --rm minio-init
	@docker compose ps db minio

down:
	docker compose down

db-shell:
	docker compose exec db psql -U postgres -d ai
