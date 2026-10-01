# 04 Lessons: defects found while building, and what now catches them

Every row was a real failure in this repository. Read before writing a table, migration, enum, test or document. The platform skill's equivalent for the ARCH starter code is `../account-intelligence-platform/references/13-starter-code-and-known-defects.md`.

| # | Where | What went wrong | How it was found | What now prevents it |
| --- | --- | --- | --- | --- |
| L1 | Migration 0002 | Plain-string constraint names were run through the naming convention again: `ck_accounts_ck_accounts_lane` | Reading `pg_constraint` after upgrade; `alembic check` said nothing | `op.f()` on every name (CONVENTIONS "Writing migrations"); `test_every_check_constraint_is_tested` compares exact names |
| L2 | Autogenerate | An `sa.Enum(..., create_constraint=True)` column produced the CHECK three times | Reading the generated file | Enum columns as `String(64)` plus one explicit CHECK in migrations |
| L3 | Constraint tests | An INSERT reports only the first failing CHECK, in name order; `Example.com` blamed `domain_hostname`, hiding `domain_lowercase` | Designing the account tests | `constraint_holds()` evaluates each CHECK alone from its catalog definition |
| L4 | `runs` index | An index on `text("created_at DESC")` was named `ix_runs_account_id` (the convention cannot see a text expression) | Printing the table's indexes | Define such indexes after the class on mapped columns; test `indexdef` |
| L5 | `Run` model | `status` was `None` until flush, so `agree_cap()` refused a brand new run | Lifecycle unit tests | Defaults that methods read are set in `__init__` |
| L6 | `JobClass` | `from_label("Admin and owner role")` failed: the stored value is not the slug of the label | Label round-trip test over every enum | `from_label` checks explicit labels first |
| L7 | Step 8 brief | The CHECK as written (`status != 'queued' OR credit_cap IS NOT NULL`) inverted the stated rule | Reading the predicate literally | Implement the stated intent, flag the discrepancy, test both directions |
| L8 | Platform `check_rls_catalog.sql` | Inspects only schema `public` and assumes roles exist: a false pass on the `intel` schemas | Running it against the migrated database | `scripts/check_tenancy.py` checks the managed schemas |
| L9 | `config/checklist.yaml` 1.0.0 | Nine rows marked "no cadence in the sources" although service design s5.1 gives them | `scripts/check_alignment.py` against the platform assets | Alignment check in `gates.sh`; read whole source tables, not a truncated grep |
| L10 | Phase 0 benchmark | Fake provider only cited a source's first coverage row, capping coverage at 0.714 | Looking at the first output table | Sanity-read outputs; tests assert perfect-answer scores |
| L11 | Test sessions | Reading ORM attributes after the session closed raised `DetachedInstanceError` | Test failures | Capture ids and values inside the session |
| L12 | Drift probe test | Comparing a probe metadata to the real schema also reported every real table as removed | Failure after the first table existed | Filter the diff to the operation under test |
| L13 | Documents | The Write tool turned backslash-u escapes for U+2014 into literal em dashes in YAML and Python (and did it again while this lesson was being written) | Grep for the character | Write escapes through code, then `grep -c` for U+2014 and U+2013; `test_guards.py` scans docs |
| L14 | Phase 0 audit | The repo's earlier coverage tracker had 33 generic attribute rows, not PRD Appendix A | Comparing with the PRD | Checklist rows, priorities and passes pinned by tests and the alignment check |
| L15 | Shell | `pkill -f` killed the shell that ran it | Exit code 144 | Track PIDs (`03-environment.md`) |
| L16 | Counting | A step report said 51 account tests; the true number was 48 | Recount after a refactor | Quote counts from the pytest summary line, never from memory |

## Habits these lessons add up to

1. Inspect the real database catalog after every migration; tools that say "no changes" do not compare everything.
2. Test each rule alone, and add a test that fails when a rule exists without a test.
3. Read the brief's code literally and its intent carefully; when they differ, follow the intent and say so.
4. Prefer evidence from running something over evidence from reading it, and say which one you have.
