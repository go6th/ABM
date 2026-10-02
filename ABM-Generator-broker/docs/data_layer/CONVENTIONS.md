# Data layer conventions

How every table in the v1 data layer is built. Enforced by `src/account_intelligence/db/base.py`
and tested in `tests/db/test_conventions.py`.

## Identity

- Every table has a UUID primary key `id` with a server-side default
  `gen_random_uuid()`, so raw SQL inserts (fixtures, psql sessions, seed
  scripts) get ids without application code.
- uuid7 is not available on the supported Python range (3.11 to 3.12). When the
  project floor moves to Python 3.14+, swap in `uuid7` by adding a client-side
  `default=uuid.uuid7` to `UuidPkMixin.id`; the server default stays as the
  fallback for raw SQL. Ids are never renumbered and never reused.

## Timestamps

- `created_at` and `updated_at` are `timestamptz NOT NULL` with server default
  `now()`.
- `updated_at` additionally carries SQLAlchemy `onupdate=func.now()`. This is a
  **known, accepted limitation for v1**: the bump happens in SQLAlchemy, not in
  the database, so it fires for ORM and Core UPDATE statements only. Raw SQL
  updates leave `updated_at` alone. A database trigger would cover every path
  but the work item asks for a trigger-free approach; if raw SQL updates ever
  become normal, add a trigger in a migration and record it here.
- No record is ever deleted. Status columns (`expired`, `merged_into_...`,
  `excluded`) express removal; rows stay.

## Names

The Alembic naming convention (in `NAMING_CONVENTION`) produces deterministic
constraint and index names:

| Object | Pattern | Example |
| --- | --- | --- |
| Primary key | `pk_%(table_name)s` | `pk_sources` |
| Foreign key | `fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s` | `fk_facts_source_id_sources` |
| Unique | `uq_%(table_name)s_%(column_0_N_name)s` | `uq_accounts_tenant_id_domain` |
| Check | `ck_%(table_name)s_%(constraint_name)s` | `ck_sources_source_code_format` |
| Index | `ix_%(table_name)s_%(column_0_name)s` | `ix_facts_run_id` |

Named checks let later migrations drop or alter them by name safely.

## Types

- `JSONB` (from `src/account_intelligence/db/types.py`) for list-shaped and policy payloads (proof
  queries, certifications, prior employers). Never `JSON`.
- `SHA256_HEX` (`char(64)`) for digests: `url_hash`, `request_hash`,
  `content_sha256`.

## Tenancy and access

- Every tenant-owned table carries `tenant_id uuid NOT NULL` referencing
  `tenants(id)`, with `ENABLE` and `FORCE ROW LEVEL SECURITY` and a policy
  scoped `TO app_rw` using `app_tenant()`. The migration lint (R1) and
  `check_rls_catalog.sql` fail otherwise.
- `app_rw` is the only runtime role. Grants are explicit per table. Ledger-like
  tables (`internal.fetch_records`) get no UPDATE and no DELETE grant.
- Client-facing code reads only `client_safe` views; cost, provider codes and
  object keys live in `internal.fetch_records`, which no client-safe view
  touches.
