# Phase 3 exit criteria

Roadmap exit criteria for Phase 3, each marked **Met by test** (with the test that proves it) or
**Blocked (needs real-world step)** (with the exact step). Gates run 2026-10-07 on
`feat/phase3-tenant-product`.

| Exit criterion | State | Evidence or exact step |
|---|---|---|
| Isolation verified by automated tests | **Met by test** | `scripts/gen_isolation_matrix.py --check` (every table, view, definer function, route, storage layout, cache purpose, event and job covered); `pytest tests/isolation` (canaries in three tenants, cross-tenant read/write/update/delete/list probes, every route called as tenant B or a staff operator with the authorisation layer replaced, a deliberately broken policy caught); `check_rls_catalog.sql` returns no rows; Playwright: a second tenant sees nothing of the first |
| Isolation verified by an external penetration test | **Blocked (needs real-world step)** | choose a vendor, deploy staging, send `docs/security/PENTEST_SCOPE.md`, receive the report, close every Critical and High finding with a regression probe |
| Exports match the on-screen rows (automated check) and pass the gate | **Met by test** | `views.parity` re-reads every xlsx, docx and CSV and checks PDF row keys; a mismatch blocks the package (`tests/phase3/test_ws4_views.py`, hypothesis-generated accounts, a mutated export caught); every delivered package passes the client output scan and the gate |
| Seller usefulness metric instrumented | **Met by test** (definitions are proposals, D-68) | `tests/phase3/test_ws9_pilot.py`: identifier-only events, counted once per user, channel and day, invented or non-Confirmed subjects refused, rates with base and small-base label, counts-only operator aggregate. Partial: the client app does not show first-call questions yet (OI-P3-2) |
| Two pilot tenants live on real accounts | **Blocked (needs real-world step)** | sign two pilot customers; configure each customer's IdP (V-P3-IDP-1/2) and the staff IdP (V-P3-IDP-3); follow `docs/phase3/pilot_onboarding.md` with three to five real accounts each; the rehearsal on synthetic data is `scripts/seed_pilot.py` |

## Supporting gates

| Gate | Result |
|---|---|
| Skill self-test, `validate_assets.py` | OK |
| `lint_migrations.py` | OK, 52 files |
| `check_rls_catalog.sql` | 0 rows (both test databases) |
| `ruff check` | 2 errors outside Phase 3 (`scripts/gen_observability.py` lines 26 and 27, from the base branch) and 5 in the other session's RAG files; none in Phase 3 code |
| `mypy --strict` | no issues, 433 files |
| Web typecheck, lint, unit tests | OK; 5 unit tests |
| Playwright end to end | 8 passed (sign-in per role, add account, admin pages with axe, role limits, second tenant, skip link, sign-out, CSP). Not covered: starting a run, tables, exports through the UI |
| `pytest tests/phase3 tests/isolation` with coverage | 558 passed; every Phase 3 package at least 90% (total 94.1%); roles, query, limits, scopes, Drive ancestry, chain and render_safe 100%; parity 97.7% |
| Full `pytest -m "not eval"` | all pass except 17 failures in the other session's uncommitted RAG work (15 in `tests/rag`, 2 static guards on RAG files) |
| `check_route_catalog.py` | OK |
| `check_parity.py` | does not exist in the repo; parity runs inside every export package and in the WS4 tests |
| Client output scan | in the tests of every tenant-visible output (runs, usage, limits, audit export, exports, branding) and the Playwright admin pages |
| Mutation check | see `docs/phase3/mutation_report.md` (partial: database-backed tests could not run under mutmut) |
| Scale smoke | `docs/phase3/scale_smoke.md` (report only) and the plan regression test |
| No model import in the new code | `tests/phase3/test_ws0_foundations.py` greps the Phase 3 packages for LLM client imports |
