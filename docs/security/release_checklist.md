# Release checklist: tenant plane (Phase 3)

Every item is a command or a recorded artefact; a red item blocks the release.

| # | Check | Command or evidence |
|---|---|---|
| 1 | Migrations lint and round-trip; expand only | `python .claude/skills/account-intelligence-platform/scripts/lint_migrations.py migrations`; `alembic upgrade head`, `downgrade -1`, `upgrade head` on a scratch database |
| 2 | RLS catalog clean | `psql -f .claude/skills/account-intelligence-platform/scripts/check_rls_catalog.sql` returns no rows |
| 3 | Isolation matrix covers every object | `python scripts/gen_isolation_matrix.py --check` |
| 4 | Isolation probes pass | `pytest tests/isolation` |
| 5 | Route catalog | `python scripts/check_route_catalog.py` |
| 6 | Tenant-plane tests | `pytest tests/phase3` |
| 7 | Full suite | `pytest -m "not eval"` |
| 8 | Static checks | `ruff check`, `mypy --strict`, `lint-imports`, `scripts/check_guarantees.py`, `scripts/check_comments.py`, `scripts/gen_loops.py --check` |
| 9 | Web | `npm run typecheck`, `npm run lint`, `npx vitest run`, `npm run build` in `web/` |
| 10 | End to end and accessibility | the Playwright suite against the release candidate (when built; OI) |
| 11 | Client output | `scripts/scan_client_output.py` on every tenant-visible output of the end-to-end run |
| 12 | Export parity and gate | the parity check and the quality gate over the exported package of a synthetic run |
| 13 | Observability | `scripts/check_runbooks.py`, `scripts/gen_observability.py --check`, canary scan clean |
| 14 | Config | every config file has `source` or `status`; no secret values in config, only references |
| 15 | Dependencies | `pip-audit` and `npm audit --omit=dev` reviewed; container scan |
| 16 | Penetration test | no open Critical or High finding (`PENTEST_SCOPE.md`) |
| 17 | Decisions | D-61 to D-70 signed, or the release notes list which proposals are live |
| 18 | Rollback | the previous image and the down migration tested on staging |
