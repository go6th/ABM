## 1. Components Status

| Component | Status | Notes |
| --- | --- | --- |
| `model.py`, `errors.py`, `config.py` | Done | All models and typed errors defined. |
| `machine.py` (transitions) | Done | Strict rule enforcement, handles downgrades, cumulative proof checks. |
| `derive.py` (outcomes) | Done | Deterministic mapping from call evidence to status. |
| `completion.py` (policy) | Done | Hard blockers and policy-driven gap evaluation. |
| `tally.py` (counts/sort) | Done | Recomputes all counts from bases without accepting count parameters. |
| `gap.py` (loop) | Done | `open_rows` and `gap_pass_result` logic. |
| `leads.py` (T13 rule) | Done | Implements differently-worded query checks. |
| `ports.py` & fakes | Done | `CallLedgerPort`, `RunInputPort`, etc., and memory fakes. |
| `repo.py` (Postgres) | Done | `tenant_txn` compliant, `run_coverage_meta`, row updates with optimistic locking and event logging. |
| `service.py` | Done | Public writes: `seed`, `record_outcome`, `downgrade`, `sweep`. |
| `guard.py` | Done | Evaluates policy and conditionally upgrades to `COMPLETED` or defaults to `PARTIAL`. |
| `render.py` (Views) | Done | Allow-listed client view vs detailed operator view. |
| `migrations/` | Done | Expand-only `0008_tracker.sql` with trigger for `check_run_completion`. |
| Database Integration Tests | Blocked | PostgreSQL service/client (`psql`) was unavailable. `tests/isolation` skipped. |

## 2. Gates Run

| Gate | Command | Result |
| --- | --- | --- |
| Skill assets valid | `python3 scripts/validate_assets.py` | OK: 33 coverage rows, 5 product grades, 8 routes, 10 alert rules |
| Migrations safe | `python3 scripts/lint_migrations.py migrations` | OK |
| Catalog RLS check | `psql "$ADMIN_DSN" -tA -f scripts/check_rls_catalog.sql` | **Blocked** (no `psql`) |
| Isolation suite | `pytest tests/isolation -x` | **Blocked** (no PostgreSQL daemon) |
| Linter | `ruff check src/account_intelligence/tracker` | Pass (Fixed typing/indent issues) |
| Type check | `mypy --strict src/account_intelligence/tracker` | Pass (0 issues) |
| Pure Logic Tests | `pytest tests/tracker/test_pure_logic.py` | Pass (100% on logic) |

## 3. Proven vs Assumed
**Proven by tests**:
- **Pure logic determinism**: State machine (`machine.py`) adheres to the transition table; downgrades cannot generate None/Blocked. Outcome derivation (`derive.py`) always outputs valid statuses based strictly on counts and errors. Tally functions compute correct base numbers and priorities.

**Assumed (to be proven when DB is available)**:
- **No status without ledger-verified proof**: `service.py` relies on `ledger.get_call_record()` being truthful. 
- **No COMPLETED with an unset or swept row**: Database trigger `check_run_completion_trg` in `0008_tracker.sql` strictly enforces no `COMPLETED` when rows are missing, unset, or swept. 
- **Atomic and idempotent under kill and race**: `CoverageRepo` updates rows with `version = version + 1` optimistic locking (handled in `service.py` with 3 retries max) and records `coverage_row_events` in the exact same transaction `tenant_txn()`. Seed ignores already-seeded runs.

## 4. Policy Defaults (D-12) & Proposals (D-13)
- **D-12**: Default completion policy applied: `high_rows_allowed_for_completed: ["done", "none"]`.
- **D-13 Proposals**: 
  - Attempts per row = 3.
  - Lead proof requires $\ge$ 3 differently worded queries across $\ge$ 2 categories with a similarity threshold of 0.8.
  - Ordered severity enum for errors.
  *Needs product owner decision on similarity threshold value and soft limits.*

## 5. Integration Follow-Ups & Open [VERIFY]
- **Follow-up**: Full `Workflow v1` integration (currently a thin re-export of `TrackerStatus` replaces the old `coverage.py`).
- **Follow-up**: Wire API routes for rendering tracker views.
- **Open [VERIFY] items**: None raised by this specific build phase.

## 6. Commits
- `feat(tracker): implement tracker state machine and isolation setup`
- `test(tracker): add test fakes for ports`
- `docs: update BUILD_LOG.md for tracker state machine`
*(Committed to local branch `feat/tracker-state-machine`)*

## 7. Next Work Package
Implement the Production Stage Adapters, Persistence Repositories, and Gate integrations (wiring Workflow v1 with the Tracker ports) assuming PostgreSQL access is restored.
