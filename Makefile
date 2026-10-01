PY      := .venv/bin/python
PIP     := .venv/bin/pip
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/pytest
PSQL    := docker compose exec -T db psql -U postgres -d ai

.PHONY: help venv install db-up db-down db-wait migrate downgrade lint format typecheck \
        unit isolation spike-durability check quality test requirements verification \
        adrs conflicts golden phase0-docs verification-clean clean

help:
	@echo "ABM Generator - Phase 0"
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

test: unit isolation

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
