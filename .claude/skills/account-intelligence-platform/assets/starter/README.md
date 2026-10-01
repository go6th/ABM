# Starter code (from Architecture v2 chapter 18), with fixes and tests

What this folder is: the ARCH 18 listings extracted into files, **every line checked against the source text**, plus the pieces the listings assume but do not show. Port it into the real repo at the paths shown. It is a starting point for Phase 0 spikes and Phase 1 work packages 1.2 to 1.5, not a finished product.

| Path | Origin |
| --- | --- |
| `src/db/session.py`, `src/agents/runner.py`, `src/orchestrator/functions.py`, `src/api/auth.py`, `src/api/runs.py`, `tests/isolation/test_rls.py`, `Dockerfile`, `compose.yml`, `.github/workflows/ci.yml` | ARCH 18.2, 18.5 to 18.10, as published (formatted with ruff; `compose.yml` and `ci.yml` untouched) |
| `src/agents/graph.py` | ARCH 18.4 **plus fix for defect 1** (reducer on `spent_usd`) |
| `migrations/0002_rls.sql` | ARCH 18.3 **plus** `TO app_rw` scoping (defect 2), RLS for the tables the listing never covered, 16 hash partitions with HNSW indexes |
| `migrations/0001_core.sql` | **Added here.** The ARCH listings reference these tables but never define them |
| `tests/conftest.py`, `tests/isolation/test_rls_extra.py`, `tests/integration/*` | **Added here.** Fixtures the source mentions but does not show, and extra tests |
| `pytest.ini` | Added: one session event loop so pooled asyncpg connections work across tests |

## Run the tests
Needs PostgreSQL 16 with a **recent pgvector**: `halfvec` was missing in 0.6.0 (observed: `type "halfvec" does not exist`) and present in 0.8.0 (observed, along with the `hnsw.iterative_scan` setting). This is ARCH register item V3, so confirm the version on the database you actually use. Python 3.11+.
```bash
pip install "psycopg[binary]" sqlalchemy asyncpg pytest pytest-asyncio langgraph langgraph-checkpoint-postgres fastapi pyjwt inngest
# scratch server with pgvector, e.g. docker compose up -d db  (confirm the version: select name, default_version from pg_available_extensions where name='vector')
PGHOST=localhost PGPORT=5432 PGADMINUSER=postgres python -m pytest tests -q
psql "postgresql://postgres@localhost:5432/ai_test" -tA -f ../../scripts/check_rls_catalog.sql   # empty output = pass
```
The suite drops and recreates `ai_test` and the roles `app_rw` and `public_reader`; **never point it at a shared server**.

## Result when this folder was assembled (PostgreSQL 16.13, pgvector 0.8.0, langgraph 1.2.12)
20 passed, 2 expected failures. The two `xfail(strict=True)` tests are known gaps (defects 3 and 5): they document the problem in code and will start failing the build (strict xfail) the day someone fixes it, which is the signal to delete the marker.
Mutation checks: removing `TO app_rw` makes `test_public_reader_cannot_read_private_facts` fail and makes `check_rls_catalog.sql` report the unscoped policy; removing the reducer makes the graph test fail with `InvalidUpdateError`.

## Not covered (no live service available)
The Inngest slice loop (`functions.py`) is compile-checked only; the SDK call shapes are [VERIFY] V4. JWT verification (`auth.py`) is import-checked only. The Dockerfile, compose and CI files were not built or run. The graph's broker adapter is a stub that raises `NotImplementedError` (work package 1.4).
