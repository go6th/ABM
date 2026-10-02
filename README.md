# Account Intelligence

Multi-tenant platform that researches a sales account and returns a sourced, graded account
brief, a senior people list, and change alerts. Every fact carries its source URL and a grade;
only **Confirmed** facts may be stated to a prospect.

> Internal and proprietary. The product and architecture background is in
> [docs/product-overview.md](docs/product-overview.md).

**Status:** Phase 0 (Prove). The tenant-isolated data layer, run API, durable research graph
skeleton, and evidence grading engine exist. Broker adapters and deliverables do not yet. See the
[roadmap](docs/ROADMAP.md).

## Architecture

| Concern | Choice |
|---|---|
| API | FastAPI, JWT (RS256 via JWKS); tenant identity comes only from the verified token |
| Tenant isolation | PostgreSQL 16 forced row-level security; runtime role `app_rw` is not the owner and has no `BYPASSRLS`; tenant context set per transaction by `tenant_txn()` |
| Research runs | LangGraph with the Postgres checkpointer as source of truth; time-sliced and resumable |
| Orchestration | Inngest triggers runs, loops time slices, and waits for approvals |
| Billing | Insert-only wallet ledger; credits reserved atomically with the run row |
| Storage | pgvector, S3-compatible object store for raw provider payloads |

Decisions are recorded as ADRs in [docs/decisions](docs/decisions/).

## Repository layout

```
src/account_intelligence/
├── api/
│   ├── main.py            app factory; mounts routes and the Inngest endpoint
│   ├── auth.py            JWT verification and role checks
│   └── routes/            health.py (/healthz), runs.py (POST /v1/runs)
├── agents/                research graph, time-sliced runner, evidence grading
├── orchestrator/          Inngest functions
├── db/                    tenant_txn session, declarative base, models, vocabularies
├── storage/               object storage for raw payloads
└── config.py              settings from the environment
config/                    versioned YAML: grading, routing, lanes, checklist, output spec
migrations/                Alembic revisions; each applies a reviewed SQL file alongside it
tests/
├── isolation/             RLS hard gate; runs first and must pass
├── integration/           API, graph and ledger against a real database
├── db/                    schema conventions
└── unit/                  pure logic, no database
docs/                      overview, roadmap, ADRs, data layer conventions, spikes
.claude/skills/            engineering rules, migration lint, RLS catalog checks
```

## Getting started

Requires Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/), Docker, and `psql` for `make check`.

```bash
cp .env.example .env
make install          # dependencies and pre-commit hooks
make up               # Postgres (pgvector) and the object store
make migrate          # schema, RLS policies, runtime role, checkpointer tables
make run              # API on http://localhost:8080
```

Run `make` to list every target.

## Testing

```bash
make lint             # ruff format, ruff check, mypy --strict
make test             # isolation suite first, then everything except evals
make check            # the full pre-PR gate on a throwaway database
```

`make check` lints migrations for isolation, migrates a scratch database, verifies the RLS
catalog, and runs the tests with an 80% coverage floor. CI runs it on Python 3.11 and 3.12.

Database tests create and drop their own scratch database and use synthetic tenants only.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. It lists the rules that must
never be broken, starting with: isolation is enforced by the database, never by application code.

Report security issues as described in [SECURITY.md](SECURITY.md).
