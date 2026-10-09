# Account Intelligence

**Internal and proprietary. Keep this repository private.** Product and source specifications are
local-only under `docs/sources/` and are not committed.

Account Intelligence is a multi-tenant research platform that stores sourced account facts, grades
claims, tracks coverage, and supports tenant and operator workflows. The current checkout is **not
production-ready**: the configured backend suite passes, but the complete web/API/worker/identity
stack and authenticated user flows have not been proven. Read [the release report](docs/RELEASE_REPORT.md)
before using it beyond synthetic local development.

## Current status

| Traceability status | Requirements |
|---|---:|
| Done | 1 |
| Partial | 161 |
| Missing | 13 |
| Blocked by external input or environment | 6 |

The matrix is in [docs/TRACEABILITY.md](docs/TRACEABILITY.md). It maps requirements to code and
proofs; `Partial` means the full requirement has not been demonstrated.

## Architecture

```mermaid
flowchart LR
  Browser --> Web[Next.js client]
  Web --> API[FastAPI API]
  API --> DB[(PostgreSQL + forced tenant RLS)]
  API --> Lifecycle[Run lifecycle and ledger]
  Lifecycle --> Workflow[Resumable workflow]
  Workflow --> Ports[Provider, model, storage, task ports]
  Ports --> Local[Hermetic adapters and fakes]
  Ports -. configured deployment only .-> Cloud[Google Cloud adapters]
```

`src/account_intelligence/app/container.py` selects adapters. The database is the source of truth
for tenant isolation and run lifecycle. Every environment variable is declared once in the typed
registry (`src/account_intelligence/config.py`); a process with a missing, forbidden or mistyped
variable refuses to start. The list of names (never values) is
[docs/ENVIRONMENT.md](docs/ENVIRONMENT.md).

## Local development

Requires Python 3.12, `uv`, Docker Compose, Node.js 22, and npm. From the repository root:

```bash
uv sync --frozen
make up        # builds and starts db, migrate, api, worker, web, fake IdP, GCS/Tasks emulators, Redis
make doctor    # every service healthy, endpoints answer, a fake-IdP token round-trips through the API
```

The default stack is hermetic: no container reads `.env`, so no provider or model is called. To use
real keys locally, put them in the ignored `.env` (variables in docs/ENVIRONMENT.md) and run
`docker compose -f compose.yml -f compose.live.yml up -d`. Keep all data synthetic.

Endpoints: web <http://localhost:3000>, API <http://localhost:8080>, worker
<http://localhost:8081>, fake IdP <http://localhost:9090>. `make down` stops the stack; `make reset`
also deletes its database volume.

## Checks

```bash
make lint
make static
uv run --frozen pytest tests/unit -q
npm --prefix web run typecheck && npm --prefix web run lint && npm --prefix web test
uv run --frozen python scripts/wiring_audit.py --check
```

Three captured post-fix full backend runs passed. Each isolation phase passed 378/378. The latest
run passed 3,343 tests, skipped 12 provider-source verification cases, deselected 2 eval tests, and
reported 94.17% coverage. An earlier run also skipped three local-service checks while the
emulators were offline; a follow-up passed those 9/9. Browser E2E, API fuzz, full accessibility,
production load, and backup/restore remain unproven. See
[docs/RELEASE_REPORT.md](docs/RELEASE_REPORT.md) for exact evidence and remaining gates.

## Project docs

- [Deployment preparation and required owner inputs](docs/DEPLOY.md)
- [Operations guide](docs/OPERATIONS.md) and [runbooks](docs/runbooks/)
- [Current status](docs/STATUS.md), [open items](docs/open-items.md), and
  [decision/conflict registers](docs/decisions/conflicts.md)
- [Static frontend/backend wiring inventory](docs/WIRING.md)
- [Build history](BUILD_LOG.md)

## External inputs that remain

Before any real deployment or pilot, the owner must provide Google Cloud project IDs, region,
billing and residency choices, Workload Identity Federation setup and approvers, identity-provider
and Workspace details, provider/model registrations and secrets, retention/legal decisions, and
on-call ownership. A real provider call, real identity-provider login, cloud deployment, production
load test, external penetration test, or pilot-tenant run has not been performed.

Do not use real tenant data or credentials in local tests. See [SECURITY.md](SECURITY.md) for
security reporting.
