# Account Intelligence

Multi-tenant platform that researches a sales account and returns a sourced, graded account
brief, a senior people list, and change alerts. Every fact carries its source URL and a grade;
only **Confirmed** facts may be stated to a prospect.

> Internal and proprietary. The product and architecture background is in
> [docs/product-overview.md](docs/product-overview.md).

**Status:** Phase 0 and Phase 1 integration on Google Cloud. One run path is wired end to end:
API, Cloud Tasks scheduler, worker slices on the Workflow v1 graph (13 stages), broker
connectors, tracker, grading, single settlement and a delivery note. Deliverable builders and the
production quality gate do not exist yet, so every run ends PARTIAL (`builders_not_configured`).
Status table: [docs/STATUS.md](docs/STATUS.md); open items: [docs/open-items.md](docs/open-items.md).

## Architecture

| Concern | Choice |
|---|---|
| API | FastAPI, JWT (RS256 via JWKS); tenant identity comes only from the verified token |
| Tenant isolation | PostgreSQL 16 forced row-level security; runtime role `app_rw` is not the owner and has no `BYPASSRLS`; tenant context set per transaction by `tenant_txn()` |
| Research runs | LangGraph with the Postgres checkpointer as source of truth; time-sliced and resumable |
| Orchestration | Cloud Tasks behind the `RunScheduler` port: named slice tasks, approval timeout (D-18) |
| Billing | Insert-only wallet ledger; credits reserved with the run row; one settlement per run |
| Storage | Cloud SQL PostgreSQL 16 with pgvector; Cloud Storage for raw provider payloads |
| LLM | `LlmPort`: Claude on Vertex AI or the Anthropic API; model ids only in config (D-19) |
| Cloud | Google Cloud, one project per environment, Terraform in `infra/` (ADR-0017, D-20) |
| Wiring | `src/account_intelligence/app/container.py` is the only place adapters are chosen |

Decisions are recorded as ADRs in [docs/decisions](docs/decisions/).

## Repository layout

```
src/account_intelligence/
├── app/container.py       the single wiring point (APP_ENV picks hermetic or Google Cloud adapters)
├── api/                   FastAPI: /v1/runs, /tasks/* (Cloud Tasks, OIDC), /healthz, /readyz
├── runs/lifecycle.py      run creation, transitions, finish and the single settlement
├── workflow/              Workflow v1: spec, engine, runner (slices), the 13 stage handlers
├── connectors/            broker connectors: ledger, budget, pacing, routing, five providers
├── tracker/               coverage rows with ledger-verified proof; the completion guard
├── grading/               the grading engine and the single fact write path
├── platform/              ports and adapters (hermetic and Google Cloud), LLM, checkpointer
├── safety/                the client-output scanner used on every export
├── agents/                versioned prompts, model output parsing, the grading facade
└── db/                    tenant_txn, models, vocabularies
src/jobs/                  the job posting pipeline
infra/                     Terraform modules and envs/{dev,staging,production}
config/                    versioned YAML (checklist, workflow, tracker, grading, providers, ...)
migrations/                Alembic revisions, each applying a reviewed SQL file
tests/                     isolation (hard gate), system (whole runs), and per-package suites
docs/                      ADRs, audit, runbooks, open items, generated references
```

## Getting started

Requires Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/), Docker, and `psql` for `make check`.

```bash
cp .env.example .env
make install          # dependencies and pre-commit hooks
make up               # Postgres (pgvector), Cloud Storage and Cloud Tasks emulators, Redis
make migrate          # schema, RLS policies, runtime role, checkpointer tables
make run              # API on http://localhost:8080
```

Run `make` to list every target.

## Testing

```bash
make lint             # ruff format, ruff check, mypy --strict
make test             # isolation suite first, then everything except evals
make check            # the full pre-PR gate on a throwaway database
make static           # import contracts (W1), docstrings, comments, guarantees, loops, bandit, vulture
make emulators        # Cloud Storage and Cloud Tasks emulators for the port contract tests
make infra-fmt infra-validate   # terraform fmt and validate (no backend, nothing applied)
make smoke            # build the image; non-root, /readyz 200, clean SIGTERM
make deploy-staging   # PRINTS the deploy commands; runs nothing
```

CI (`.github/workflows/ci.yml`) runs lint, the static gates, the isolation suite and every test
with a 90% coverage floor, Terraform validation and the image build; on main it deploys staging
through Workload Identity Federation after approval. Runbooks: [docs/runbooks](docs/runbooks/).

Database tests create and drop their own scratch database and use synthetic tenants only.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. It lists the rules that must
never be broken, starting with: isolation is enforced by the database, never by application code.

Report security issues as described in [SECURITY.md](SECURITY.md).
