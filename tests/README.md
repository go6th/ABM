# `tests/`: the test suite

Synthetic data only, everywhere. No test needs a key, a network or a paid call: models are
scripted or the hermetic fake, providers answer from cassettes, and the database is a private
PostgreSQL you start yourself.

## Running

```bash
# a private database for the suite (pgvector image; trust auth; port 55440)
docker run --rm -d --name abm-p2-testdb -p 127.0.0.1:55440:5432 \
  -e POSTGRES_HOST_AUTH_METHOD=trust pgvector/pgvector:pg16

PG_CONTAINER=abm-p2-testdb PGPORT=55440 uv run --frozen pytest -m "not eval"      # everything
PG_CONTAINER=abm-p2-testdb PGPORT=55440 uv run --frozen pytest tests/isolation -x # the hard gate
uv run --frozen pytest tests/agents                                             # one folder
```

`make test` does the same against the compose database. Tests marked `requires_postgres` skip
without a database; `eval` tests run only in the nightly eval gate; `large_file` tests need
`RF_LARGE_FILE=1`.

## What each folder covers

| Folder | Covers | Main code under test |
| --- | --- | --- |
| `agents/` | the ABM graph: every agent, failure paths, budgets, the skill context, projection to the database | `src/agents/` |
| `api/` | fail-closed ports, explicit policies, safe error envelopes, the OpenAPI snapshot | `account_intelligence/api/` |
| `connectors/` | the provider broker layer, routing, cost, cache, vendor-name placement | `account_intelligence/connectors/` |
| `grading/` | the grading engine and the fact write path | `account_intelligence/grading/` |
| `tracker/` | the 33-row tracker, transitions, mutation kills | `account_intelligence/tracker/` |
| `workflow/`, `runs/`, `worker/` | the durable workflow, run lifecycle, the worker CLI | `workflow/`, `runs/`, `worker/` |
| `replica/` | the product screens' server side: Ask, workspace, sheets, credits, schedules, research jobs, resume, scheduled tasks | `account_intelligence/replica/`, routes |
| `documents/`, `research_files/`, `rag/` | uploads, parsing, indexing, retrieval | `documents/`, `research_files/`, `rag/` |
| `exports/` | the deliverable builders: synthetic corpus, oracles, lying content, render, mutation kills | `exports/` |
| `jobs/`, `people/`, `spend/`, `parallel/`, `llm/`, `verify/`, `evals/` | the pipelines, credit cap, scheduler, model routing, verifiers, the eval harness | the package of the same name |
| `phase3/` | the tenant plane: identity, sessions, roles, metering, admin, support, pilot (WS0 to WS10) | `identity/`, `tenancy/`, `admin/`, `metering/`, `pilot/` |
| `phase4/` | automation and signals: refresh, alerts, monitors, CRM, Open Door, governance, DR | `refresh/`, `alerts/`, `signals/`, `crm_writeback/`, `opendoor/` |
| `crosscut/` | roadmap section 11: conformance, data model spec, contracts, quality, privacy, security, unit cost, metrics, registers | `crosscut/` and the small packages |
| `obs/` | the telemetry contract, metrics export, alert rendering | `obs/` |
| `isolation/` | cross-tenant probes against every tenant object (the matrix in `isolation/matrix/`); a hard gate | the database |
| `db/`, `integration/`, `ops/` | data layer, scratch-database integration, backup and restore | migrations, `db/` |
| `system/` | whole runs on the fake model, static guards (headers, guarantees, docstrings, single writers, wiring contract, env example, replica stack) | the repository |
| `e2e/`, `fuzz/`, `unit/` | the Playwright stack server, OpenAPI-driven fuzzing, small unit tests | |
| `fakes/`, `fixtures/`, `conftest.py`, `scope.py` | in-memory fakes for every port, fixture files, the shared database fixtures | |

The `web/` app has its own tests: `web/tests/unit` (Vitest) and `web/e2e` (Playwright).
