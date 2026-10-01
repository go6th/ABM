# ABM Generator (`account-intelligence`)

Multi-tenant Account Intelligence SaaS — Phase 0 implementation (T1–T14).

## Layout

```
src/abm_generator/    application code (db access, ledgers, runner)
tests/                unit · isolation (hard gate) · spike · contract
docs/                 phase0 artefacts, ADRs, reference set, legal, golden set
migrations/           Alembic (owner role only)
scripts/              CI checkers
spikes/               spike 3 (cache ratio) and spike 4 (broker economics)
docker/               init SQL (roles)
```

## Quick start

```bash
make venv            # .venv + dev deps (python 3.12+)
make db-up           # postgres16 + pgvector via docker compose
make migrate         # alembic upgrade head as owner role
make isolation       # RLS isolation suite (hard gate)
make check           # every CI gate locally
```

## Non-negotiable invariants

1. Tenant queries run inside a transaction with transaction-local `app.tenant_id`.
2. RLS ENABLED **and FORCED** on every tenant-owned table.
3. Runtime role is non-owner and `NOBYPASSRLS`.
4. Every paid call goes through the tool ledger `(run_id, args_hash)`.
5. Untrusted text is read only by the tool-less extractor.
6. No client artefact names a broker or credit count.
7. No cross-account leakage of internal details.
8. Only Confirmed facts may be stated to a prospect.
9. Isolation tests run on every build against live Postgres.
10. CI fails on lint/type/REQ-ID/ADR/isolation failures.

See `docs/adr/` for the decision records and `docs/phase0/` for task artefacts.
