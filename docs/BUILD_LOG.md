# Build log

Running log of what was built, the evidence, and decisions touched. Newest entry first. Format and rules: `.claude/skills/account-intelligence/SKILL.md` section 4. Phase status files: `docs/PHASE0_STATUS.md`, `docs/data_layer/PLAN.md`.

## Open decisions and risks

| ID | Question | Recommended default | State |
| --- | --- | --- | --- |
| R-01 | Tenant `tenant_id` and forced RLS on every `intel` table now, or in Phase 3? | Now (option A in the reconciliation register) | Open: ask before the next table |
| R-08 | `credit_cap` sits in the client-readable `intel` schema | Move to `intel_internal`, or keep and never select it in client reads | Open |
| R-09 | Display format of stable source ids | Integer in the database; display `S-0001` | Open, low impact |
| R-13 | Raw object key layout | `raw/<tenant_id>/<account_id>/<sha256>.json` | Open: decide at the object storage step |
| D1 to D3 | Workflow engine, model split, people source | See `docs/decision_log.md` | Proposed, awaiting sign-off |

## 2026-10-01: enhanced skill and platform skill installed

- Built: `.claude/skills/account-intelligence/` (entry point: reconciliation register R-01 to R-15, repo state, data layer playbook, environment, lessons L1 to L16, `gates.sh`, `check_alignment.py`, `check_tenancy.py`); moved `account-intelligence-platform/` into `.claude/skills/`; marked the older skills as reference and superseded.
- Found: `config/checklist.yaml` 1.0.0 missed five service design s5.1 cadences (nine rows); fixed in 1.1.0. The platform `check_rls_catalog.sql` gives a false pass on the `intel` schemas (it inspects `public` only); replaced for this repo by `check_tenancy.py`.
- Tenancy report on the migrated schema: `intel.accounts` has `tenant_id` but no RLS; `intel.runs` has neither (R-01).
- Tests: `tests/app/test_skills.py`, `tests/app/db/test_skill_tenancy.py`; `bash .claude/skills/account-intelligence/scripts/gates.sh` with `PG_BIN` and `TERRAFORM` set.

## 2026-10-01: data layer v1, steps 1 to 8

- Steps 1, 2, 4 to 8 built and committed (`5be9c63` to `8cd123b`): plan; local Postgres and MinIO; Alembic; conventions; enums; `intel.accounts` (0002); `intel.runs` (0003). Step 3 was never received.
- Evidence: database tests run against Postgres 16.4 from embedded binaries (`PG_BIN`); `alembic check` clean; downgrade to base clean. `make up` not run here (no docker access).
- Defects found and fixed: L1 to L7, L11, L12 in `.claude/skills/account-intelligence/references/04-lessons.md`.

## 2026-10-01: roadmap Phase 0

- Built and tested; items needing a person are listed in `docs/PHASE0_STATUS.md`.
