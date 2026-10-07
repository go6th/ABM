# Mutation check (Phase 4 harness item 13)

mutmut 3.8.0, 2026-10-07, scratch export of the branch, Python 3.11, tests that need no database
(`-m "not requires_postgres"`: mutmut's in-process runner cannot host the async database tests,
as in Phase 3). Modules: `policy`, `delta`, `escalate`, `fingerprint`, `score`, `caps`,
`throttle`, `sensitive`, `lint`, the CRM `client` guard. `slots` and `sourcecodes` are
database-only and have **no mutation evidence** (proven by their database tests).

| Module | Killed | Survived | Kill rate |
|---|---|---|---|
| governance.throttle | 61 | 0 | 100% |
| alerts.sensitive | 8 | 0 | 100% |
| alerts.caps | 32 | 1 | 97.0% |
| signals.score | 73 | 4 | 94.8% |
| signals.fingerprint | 57 | 4 | 93.4% |
| refresh.escalate | 52 | 4 | 92.9% |
| crm_writeback.client | 18 | 2 | 90.0% |
| refresh.delta | 156 | 24 | 86.7% |
| alerts.lint | 12 | 2 | 85.7% |
| refresh.policy | 83 | 16 | 83.8% |
| **Total** | **552** | **57** | **90.6%** |

The first run killed 466 of 609 (76.5%). Survivors that were real gaps were closed with tests
(`tests/phase4/test_edges.py`, and the pure WS5 tests moved out of a database-marked module): the
create-only guard now refuses any non-allowed public method, score components, defaults and
recency boundaries, policy provenance and bounds, and fingerprints pinned to their canonical form.
The remaining survivors are mostly exception message text, equivalent mutants (for example the
codec name `ASCII` for `ascii`) and import-time decorator paths the runner cannot re-execute.
