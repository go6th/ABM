# `migrations/`: the database schema

The schema is plain SQL, applied in order by Alembic.

- `00NN_<name>.sql` is the migration; `00NN_<name>.down.sql` undoes it where that is safe. The
  SQL carries the table and column comments, so the schema documents itself (`\d+` in psql).
- `versions/00NN_<name>.py` is the Alembic wrapper: it reads the SQL file next to it and runs it.
  Alembic only tracks the revision chain; it never generates SQL here.
- `env.py` runs the migrations as the `migrator` role (`MIGRATOR_DATABASE_URL`) and, in the cloud,
  grants the definer-role memberships inside the single migration transaction.
- `script.py.mako` is the template `make migration m="…"` uses for a new wrapper.

Rules every migration follows (enforced by `tests/isolation`, `tests/db` and the migration lint
in `.pre-commit-config.yaml`):

1. Every tenant table has a non-null `tenant_id` and `FORCE ROW LEVEL SECURITY` from the
   migration that creates it (`0002_rls.sql` sets the pattern).
2. The runtime role `app_rw` is never the owner and has no `BYPASSRLS`; it reaches privileged
   work only through `SECURITY DEFINER` functions that carry a reviewed comment.
3. Ledgers are insert-only. Superseded rows are marked, never deleted.
4. One module writes each table: declare the owner in `config/ownership.yaml`.
5. After adding a migration, bump `EXPECTED_REVISION` in
   `src/account_intelligence/api/routes/health.py` so `/readyz` refuses to serve on an old schema.

How they run: locally the compose `migrate` service runs `alembic upgrade head` before the api
starts; in the cloud the `ai-staging-migrate` Cloud Run Job runs the same command from the new
api image before the services are updated (`DEPLOYMENT.md`).

Landmarks: `0001_core` (tenants, accounts, facts, wallets), `0002_rls`, `0004_runs`,
`0005_sources`, `0008_tracker`, `0009_grading`, `0012_job_pipeline`, `0014_people_pipeline`,
`0041_abm_agents` and `0042_abm_distill` (the research graph), `0045_chat`, `0047_local_auth`,
`0049_research_cache`, `0050_durable_runs`, `0051_research_files`, `0053_replica` (the product
screens), `0063_private_workspaces`, `0067_resumable_research`, `0074_retrieval_indexes`, `0077_table_store`, `0078_raw_files`, `0086_link_archive`, `0087_account_delete`, `0088_account_bundles` (current).
