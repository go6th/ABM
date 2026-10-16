# Account Intelligence (ABM Generator)

A multi-tenant web application that researches a sales account and returns a sourced, graded
brief, the people, job postings, technology and news sheets, an answer box over everything it
found, scheduled refreshes and change alerts. Each client is a sealed tenant: isolation is
enforced by PostgreSQL row-level security, never by application code. Client-facing output never
names a data tool; it says "6th agents".

**Where everything is:** [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) maps every folder, what
it is for and how the pieces connect. Each top-level folder has its own `README.md`.

## The code base in one glance

| Folder | What it is |
| --- | --- |
| [`src/`](src/README.md) | all application code: the FastAPI api and worker, the research graph, the deliverable builders |
| [`web/`](web/README.md) | the Next.js app: client workspace, sign-in, operator console |
| [`config/`](config/README.md) | every rule the code reads as data (grading, agents, budgets, telemetry, the owner's research skill) |
| [`migrations/`](migrations/README.md) | the database schema as numbered SQL, applied by Alembic |
| [`tests/`](tests/README.md) | 4,000+ tests on synthetic data; `tests/isolation` is the hard gate |
| [`scripts/`](scripts/README.md) | the gates and generators `make` and CI run |
| [`infra/`](infra/README.md) | Terraform for Google Cloud (Cloud Run, Cloud SQL, Cloud Tasks, Scheduler, Secret Manager) |
| [`docker/`](docker/README.md) | the api and worker images and the CI image scripts |
| [`docs/`](docs/README.md) | architecture, decisions, runbooks, provider contracts, security, privacy, generated references |
| [`ops/`](ops/README.md), [`evals/`](evals/README.md) | local operations tools; golden data and offline benchmarks |

Everything the 2026-10-16 audit found unused (old audits, stray scripts, the Helm chart, build
prompts, the replica copies) was moved out of this folder to `/home/sakib/unused code ABM generator/`, with an
inventory in its `README.md` and the irreversible clean-up steps in its `cleanup.sh`.

## Run it locally

Needs Docker Compose v2, Python 3.11 or 3.12 with [`uv`](https://docs.astral.sh/uv/), Node 22.

```bash
make install        # Python dependencies and the pre-commit hooks
make up             # db, redis, emulators, migrate, api :8080, worker :8081, web :3000, fake IdP :9090
make doctor         # what is ready, without printing any secret
```

The local stack is hermetic: no container reads `.env`, so no provider or model call can
happen. To use real keys locally, and only on purpose, because paid calls cost money:
`docker compose -f compose.yml -f compose.live.yml up -d`. A second, fully hermetic demo stack
(fake model, synthetic providers, own ports) is `make replica-up`.

Variables the code reads are documented in `.env.example` and `docs/ENVIRONMENT.md`. Secrets
live in `.env` locally and in Secret Manager in the cloud; never in a tracked file.

## Check it

```bash
make lint static contract      # ruff, mypy, import layers, docstrings, headers, OpenAPI and client drift
make test                      # isolation suite first, then everything except evals (needs the compose db)
npm --prefix web run typecheck && npm --prefix web run lint && npm --prefix web test
make verify                    # all of the above plus Terraform checks and the image builds
```

`tests/README.md` shows how to run the suite against a private PostgreSQL container instead.

## Deploy it

Staging runs in Google Cloud (project `abmgenerator`, us-central1) on Cloud Run from the images
in `docker/` and `web/Dockerfile`, with the infrastructure in `infra/`. The release steps are in
[DEPLOYMENT.md](DEPLOYMENT.md); operating it is in [RUNBOOK.md](RUNBOOK.md) and
`docs/deploy/RUNBOOK.md`. Nothing deploys by itself from a branch.

## Rules that never bend

[CONTRIBUTING.md](CONTRIBUTING.md) lists the twelve: isolation by the database, tenant identity
only from the verified token, no fact without a source and a computed grade, two outputs
(client and Internal Only), no vendor or person names in a client file, money as a ledger,
checkpoints as the source of truth, never send outreach, no silent omission, pull once, no
assumptions presented as facts. Report security issues as described in
[SECURITY.md](SECURITY.md).
