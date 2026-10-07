# Mutation check (Phase 3 harness item 14)

Tool: mutmut 3.8.0 (`uv run --with "mutmut>=3.2,<4"`), run on 2026-10-07 in a scratch export of
the branch with Python 3.11. Modules: `identity.roles`, `views.query`, `views.parity`,
`connectors_client.scopes`, `metering.limits`, `audit.chain`.

## Limitation (stated, not hidden)

The database-backed async tests could not run inside mutmut's in-process runner: after the first
async database test, the engine's connections belonged to another event loop ("attached to a
different loop"); the same tests pass together under pytest (81 passed). After five attempts the
run used only the tests that need no database (`-m "not requires_postgres"`) from
`test_ws2_roles.py`, `test_strict_edges.py`, `test_ws4_render_safe.py`, `test_ws6_connectors.py`,
`test_ws7_metering.py` and `test_ws3_audit.py`. Consequences:

* `metering.limits` and `audit.chain` have **no mutation evidence** (their logic is SQL or
  database-backed; their behaviour is proven by the database tests, not by this check).
* `views.query` and `views.parity` were judged without their main oracle file
  (`test_ws4_views.py`, which needs the database), so their kill rates understate the suite.

## Results

| Module | Killed | Survived | Not reached | Kill rate (reached) |
|---|---|---|---|---|
| `identity.roles` | 59 | 0 | 0 | 100% (after adding pure last-admin and unknown-action tests) |
| `connectors_client.scopes` | 13 | 0 | 0 | 100% |
| `views.parity` | 197 | 142 | 0 | 58.1% |
| `views.query` | 162 | 174 | 60 | 48.2% |
| `metering.limits` | – | – | 257 | not evaluated |
| `audit.chain` | – | – | 161 | not evaluated |

The surviving `identity.roles` mutants of the first run were real gaps (the last-admin rule was
only tested through the database); `tests/phase3/test_strict_edges.py` now pins it without one.

## Follow-up

Run mutmut with a per-test event loop for the async database tests (or a subprocess runner) so
`limits`, `chain` and the full `query` and `parity` oracles count; then triage the `query` and
`parity` survivors.
